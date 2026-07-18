from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    CatalogError,
    canonical_json_bytes,
    read_json,
    require_object,
    sha256_hex,
    write_or_check,
)
from .release import ReleaseArtifacts, load_release


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


def _governed_digest(release: ReleaseArtifacts, provider_id: str, kind: str) -> str | None:
    provider = release.provenance["providers"].get(provider_id)
    if provider is None:
        return None
    matches = [entry["sha256"] for entry in provider["inputs"] if entry["kind"] == kind]
    if len(matches) != 1:
        raise CatalogError(f"{provider_id} provenance has ambiguous {kind} input")
    return matches[0]


def _shrink_basis_points(before_count: int, removed_count: int) -> int:
    if before_count == 0 or removed_count == 0:
        return 0
    return (removed_count * 10_000 + before_count - 1) // before_count


def _provider_diff(
    provider_id: str,
    previous: ReleaseArtifacts | None,
    candidate: ReleaseArtifacts,
) -> dict[str, Any]:
    before = previous.catalog["providers"].get(provider_id) if previous else None
    after = candidate.catalog["providers"].get(provider_id)
    before_models = {model["id"]: model for model in before["models"]} if before else {}
    after_models = {model["id"]: model for model in after["models"]} if after else {}
    before_ids = set(before_models)
    after_ids = set(after_models)
    common_ids = sorted(before_ids & after_ids)
    lifecycle_changes = [
        {
            "id": model_id,
            "before": before_models[model_id]["status"],
            "after": after_models[model_id]["status"],
        }
        for model_id in common_ids
        if before_models[model_id]["status"] != after_models[model_id]["status"]
    ]
    metadata_changes = []
    for model_id in common_ids:
        fields = sorted(
            field
            for field in set(before_models[model_id]) | set(after_models[model_id])
            if field not in {"id", "status"}
            and before_models[model_id].get(field) != after_models[model_id].get(field)
        )
        if fields:
            metadata_changes.append({"id": model_id, "fields": fields})
    provider_fields = []
    if before is not None and after is not None:
        provider_fields = sorted(
            field
            for field in ("aliases", "default_model", "display_name")
            if before[field] != after[field]
        )
    removed = sorted(before_ids - after_ids)
    previous_count = len(before_models)
    return {
        "before_model_count": previous_count,
        "after_model_count": len(after_models),
        "shrink_basis_points": _shrink_basis_points(previous_count, len(removed)),
        "provider_fields_changed": provider_fields,
        "models_added": sorted(after_ids - before_ids),
        "models_removed": removed,
        "lifecycle_changes": lifecycle_changes,
        "metadata_changes": metadata_changes,
        "source_policy_changed": previous is not None
        and _governed_digest(previous, provider_id, "source_policy")
        != _governed_digest(candidate, provider_id, "source_policy"),
        "curated_input_changed": previous is not None
        and _governed_digest(previous, provider_id, "curated")
        != _governed_digest(candidate, provider_id, "curated"),
    }


def classify_promotion(
    previous: ReleaseArtifacts | None,
    candidate: ReleaseArtifacts,
    policy: PromotionPolicy,
) -> dict[str, Any]:
    previous_providers = set(previous.catalog["providers"]) if previous else set()
    candidate_providers = set(candidate.catalog["providers"])
    provider_ids = sorted(previous_providers | candidate_providers)
    providers = {
        provider_id: _provider_diff(provider_id, previous, candidate)
        for provider_id in provider_ids
    }
    reasons: set[str] = set()
    if previous is None:
        reasons.add("bootstrap")
    if previous_providers != candidate_providers:
        reasons.add("provider_set_changed")
    for change in providers.values():
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
        if change["shrink_basis_points"] > policy.maximum_shrink_basis_points:
            reasons.add("excessive_shrink")

    catalog_unchanged = (
        previous is not None
        and previous.encoded["catalog-v1.json"] == candidate.encoded["catalog-v1.json"]
    )
    if catalog_unchanged:
        decision = "no_change"
    elif "excessive_shrink" in reasons:
        decision = "blocked"
    elif previous is not None and reasons == {"model_addition"}:
        decision = "addition_only"
    else:
        decision = "review_required"
    return {
        "schema_version": 1,
        "from_release_id": previous.manifest["release_id"] if previous else None,
        "to_release_id": candidate.manifest["release_id"],
        "decision": decision,
        "reasons": sorted(reasons),
        "promotion_policy": {
            "sha256": policy.sha256,
            "maximum_shrink_basis_points": policy.maximum_shrink_basis_points,
        },
        "providers": providers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify a provider catalog promotion candidate")
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--previous-dir", type=Path)
    parser.add_argument("--policy", type=Path, default=Path("promotion-policy.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        candidate = load_release(args.candidate_dir)
        previous = load_release(args.previous_dir, stable=True) if args.previous_dir else None
        policy = load_promotion_policy(args.policy)
        diff = classify_promotion(previous, candidate, policy)
        write_or_check(
            args.output_dir,
            {"diff-v1.json": canonical_json_bytes(diff)},
            check=args.check,
        )
    except CatalogError as error:
        print(f"promotion classification failed: {error}")
        return 1
    print(f"promotion decision: {diff['decision']}")
    return int(diff["decision"] == "blocked")


if __name__ == "__main__":
    raise SystemExit(main())
