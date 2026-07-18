from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    CatalogError,
    MODEL_METADATA_FIELDS,
    PROVIDER_ID_PATTERN,
    RELEASE_ID_PATTERN,
    canonical_json_bytes,
    read_json,
    require_array,
    require_object,
    sha256_hex,
    validate_model_id,
)


DECISIONS = {"addition_only", "blocked", "no_change", "review_required"}
REASONS = {
    "bootstrap",
    "curated_input_changed",
    "excessive_shrink",
    "model_addition",
    "model_lifecycle_changed",
    "model_metadata_changed",
    "model_removal",
    "provider_metadata_changed",
    "provider_set_changed",
    "source_policy_changed",
}
PROVIDER_FIELDS = {"aliases", "default_model", "display_name"}
STATUSES = {"active", "deprecated", "removed"}
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class PromotionPolicy:
    maximum_shrink_basis_points: int
    sha256: str


def load_promotion_policy(path: Path) -> PromotionPolicy:
    value, raw = read_json(path, max_bytes=64 * 1024)
    policy = require_object(value, str(path))
    if set(policy) != {"schema_version", "maximum_shrink_basis_points"}:
        raise CatalogError("promotion policy has an invalid shape")
    maximum = policy["maximum_shrink_basis_points"]
    if policy["schema_version"] != 1 or not isinstance(maximum, int) or isinstance(maximum, bool):
        raise CatalogError("promotion policy is invalid")
    if not 0 <= maximum <= 10_000:
        raise CatalogError("promotion policy shrink limit is invalid")
    if raw != canonical_json_bytes(policy):
        raise CatalogError("promotion policy is not canonical JSON")
    return PromotionPolicy(maximum_shrink_basis_points=maximum, sha256=sha256_hex(raw))


def shrink_basis_points(before_count: int, removed_count: int) -> int:
    if before_count == 0 or removed_count == 0:
        return 0
    return (removed_count * 10_000 + before_count - 1) // before_count


def promotion_reasons(
    providers: dict[str, dict[str, Any]], *, bootstrap: bool, maximum_shrink_basis_points: int
) -> set[str]:
    reasons: set[str] = {"bootstrap"} if bootstrap else set()
    for change in providers.values():
        if (change["before_model_count"] == 0) != (change["after_model_count"] == 0):
            reasons.add("provider_set_changed")
        if change["models_added"]:
            reasons.add("model_addition")
        if change["models_removed"]:
            reasons.add("model_removal")
        if change["lifecycle_changes"]:
            reasons.add("model_lifecycle_changed")
        if change["metadata_changes"]:
            reasons.add("model_metadata_changed")
        if change["provider_fields_changed"]:
            reasons.add("provider_metadata_changed")
        if change["source_policy_changed"]:
            reasons.add("source_policy_changed")
        if change["curated_input_changed"]:
            reasons.add("curated_input_changed")
        if change["shrink_basis_points"] > maximum_shrink_basis_points:
            reasons.add("excessive_shrink")
    return reasons


def promotion_decision(reasons: set[str], *, bootstrap: bool) -> str:
    if not reasons:
        return "no_change"
    if "excessive_shrink" in reasons:
        return "blocked"
    if not bootstrap and reasons == {"model_addition"}:
        return "addition_only"
    return "review_required"


def _nonnegative_int(value: Any, scope: str, *, maximum: int = 10_000) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        raise CatalogError(f"{scope} is invalid")
    return value


def _model_ids(value: Any, scope: str) -> list[str]:
    values = require_array(value, scope)
    validated = [
        validate_model_id(item, 256, f"{scope}[{index}]") for index, item in enumerate(values)
    ]
    if len(values) > 10_000 or validated != sorted(set(validated)):
        raise CatalogError(f"{scope} is not unique and sorted")
    return validated


def _enum_list(value: Any, allowed: set[str], scope: str) -> list[str]:
    values = require_array(value, scope)
    if any(item not in allowed for item in values) or values != sorted(set(values)):
        raise CatalogError(f"{scope} is invalid")
    return values


def _change_records(
    value: Any,
    scope: str,
    *,
    metadata: bool,
) -> list[dict[str, Any]]:
    values = require_array(value, scope)
    if len(values) > 10_000:
        raise CatalogError(f"{scope} contains too many entries")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        record = require_object(item, f"{scope}[{index}]")
        expected = {"id", "fields"} if metadata else {"id", "before", "after"}
        if set(record) != expected:
            raise CatalogError(f"{scope}[{index}] has an invalid shape")
        model_id = validate_model_id(record["id"], 256, f"{scope}[{index}].id")
        if metadata:
            fields = _enum_list(
                record["fields"], set(MODEL_METADATA_FIELDS), f"{scope}[{index}].fields"
            )
            if not fields:
                raise CatalogError(f"{scope}[{index}].fields is empty")
            records.append({"id": model_id, "fields": fields})
        else:
            before = record["before"]
            after = record["after"]
            if before not in STATUSES or after not in STATUSES or before == after:
                raise CatalogError(f"{scope}[{index}] lifecycle is invalid")
            records.append({"id": model_id, "before": before, "after": after})
    ids = [record["id"] for record in records]
    if ids != sorted(set(ids)):
        raise CatalogError(f"{scope} is not unique and sorted")
    return records


def _validate_provider_diff(provider_id: str, value: Any) -> dict[str, Any]:
    change = require_object(value, f"diff.providers.{provider_id}")
    expected = {
        "after_model_count",
        "before_model_count",
        "curated_input_changed",
        "lifecycle_changes",
        "metadata_changes",
        "models_added",
        "models_removed",
        "provider_fields_changed",
        "shrink_basis_points",
        "source_policy_changed",
    }
    if set(change) != expected:
        raise CatalogError(f"diff provider {provider_id} has an invalid shape")
    before_count = _nonnegative_int(change["before_model_count"], f"{provider_id}.before_count")
    after_count = _nonnegative_int(change["after_model_count"], f"{provider_id}.after_count")
    added = _model_ids(change["models_added"], f"{provider_id}.models_added")
    removed = _model_ids(change["models_removed"], f"{provider_id}.models_removed")
    if set(added) & set(removed) or after_count != before_count - len(removed) + len(added):
        raise CatalogError(f"diff provider {provider_id} model counts disagree")
    shrink = _nonnegative_int(change["shrink_basis_points"], f"{provider_id}.shrink_basis_points")
    if shrink != shrink_basis_points(before_count, len(removed)):
        raise CatalogError(f"diff provider {provider_id} shrink calculation disagrees")
    provider_fields = _enum_list(
        change["provider_fields_changed"], PROVIDER_FIELDS, f"{provider_id}.provider_fields"
    )
    lifecycle = _change_records(
        change["lifecycle_changes"], f"{provider_id}.lifecycle_changes", metadata=False
    )
    metadata = _change_records(
        change["metadata_changes"], f"{provider_id}.metadata_changes", metadata=True
    )
    changed_ids = {record["id"] for record in lifecycle + metadata}
    if changed_ids & (set(added) | set(removed)):
        raise CatalogError(f"diff provider {provider_id} changes overlap membership changes")
    for field in ("source_policy_changed", "curated_input_changed"):
        if not isinstance(change[field], bool):
            raise CatalogError(f"diff provider {provider_id} {field} is not boolean")
    return {
        "before_model_count": before_count,
        "after_model_count": after_count,
        "shrink_basis_points": shrink,
        "provider_fields_changed": provider_fields,
        "models_added": added,
        "models_removed": removed,
        "lifecycle_changes": lifecycle,
        "metadata_changes": metadata,
        "source_policy_changed": change["source_policy_changed"],
        "curated_input_changed": change["curated_input_changed"],
    }


def validate_promotion_diff(
    value: Any, *, expected_to_release_id: str | None = None
) -> dict[str, Any]:
    diff = require_object(value, "diff-v1.json")
    expected = {
        "decision",
        "from_release_id",
        "promotion_policy",
        "providers",
        "reasons",
        "schema_version",
        "to_release_id",
    }
    if set(diff) != expected or diff["schema_version"] != 1:
        raise CatalogError("promotion diff has an invalid shape")
    from_release_id = diff["from_release_id"]
    if from_release_id is not None and (
        not isinstance(from_release_id, str) or not RELEASE_ID_PATTERN.fullmatch(from_release_id)
    ):
        raise CatalogError("promotion diff from_release_id is invalid")
    to_release_id = diff["to_release_id"]
    if not isinstance(to_release_id, str) or not RELEASE_ID_PATTERN.fullmatch(to_release_id):
        raise CatalogError("promotion diff to_release_id is invalid")
    if expected_to_release_id is not None and to_release_id != expected_to_release_id:
        raise CatalogError("stable diff does not identify its release")
    policy = require_object(diff["promotion_policy"], "diff.promotion_policy")
    if set(policy) != {"sha256", "maximum_shrink_basis_points"}:
        raise CatalogError("promotion diff policy has an invalid shape")
    maximum = _nonnegative_int(
        policy["maximum_shrink_basis_points"], "diff.maximum_shrink_basis_points"
    )
    if not isinstance(policy["sha256"], str) or not SHA256_PATTERN.fullmatch(policy["sha256"]):
        raise CatalogError("promotion diff policy digest is invalid")
    raw_providers = require_object(diff["providers"], "diff.providers")
    if not 0 < len(raw_providers) <= 32:
        raise CatalogError("promotion diff provider count is invalid")
    providers: dict[str, dict[str, Any]] = {}
    for provider_id, provider in raw_providers.items():
        if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise CatalogError("promotion diff contains an invalid provider id")
        providers[provider_id] = _validate_provider_diff(provider_id, provider)
    reasons = _enum_list(diff["reasons"], REASONS, "diff.reasons")
    bootstrap = from_release_id is None
    expected_reasons = promotion_reasons(
        providers,
        bootstrap=bootstrap,
        maximum_shrink_basis_points=maximum,
    )
    if set(reasons) != expected_reasons:
        raise CatalogError("promotion diff reasons disagree with provider changes")
    if from_release_id == to_release_id and expected_reasons:
        raise CatalogError("promotion diff changes cannot reuse a release id")
    decision = diff["decision"]
    if decision not in DECISIONS or decision != promotion_decision(
        expected_reasons, bootstrap=bootstrap
    ):
        raise CatalogError("promotion diff decision disagrees with its reasons")
    return diff
