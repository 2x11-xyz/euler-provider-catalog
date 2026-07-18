from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import CatalogError, MODEL_METADATA_FIELDS, canonical_json_bytes, write_or_check
from .promotion_contract import (
    PromotionPolicy,
    load_promotion_policy,
    promotion_decision,
    promotion_reasons,
    shrink_basis_points,
    validate_promotion_diff,
)
from .release import ReleaseArtifacts, load_release


def _governed_digest(release: ReleaseArtifacts, provider_id: str, kind: str) -> str | None:
    provider = release.provenance["providers"].get(provider_id)
    if provider is None:
        return None
    matches = [entry["sha256"] for entry in provider["inputs"] if entry["kind"] == kind]
    if not matches:
        return None
    if len(matches) != 1:
        raise CatalogError(f"{provider_id} provenance has ambiguous {kind} input")
    return matches[0]


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
        fields = [
            field
            for field in MODEL_METADATA_FIELDS
            if before_models[model_id].get(field) != after_models[model_id].get(field)
        ]
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
        "shrink_basis_points": shrink_basis_points(previous_count, len(removed)),
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
    bootstrap = previous is None
    minimum_version_changed = previous is not None and (
        previous.manifest["minimum_euler_version"] != candidate.manifest["minimum_euler_version"]
    )
    non_monotonic_release = previous is not None and (
        previous.manifest["release_id"] != candidate.manifest["release_id"]
        and datetime.fromisoformat(candidate.manifest["generated_at"][:-1] + "+00:00")
        <= datetime.fromisoformat(previous.manifest["generated_at"][:-1] + "+00:00")
    )
    reasons = promotion_reasons(
        providers,
        bootstrap=bootstrap,
        maximum_shrink_basis_points=policy.maximum_shrink_basis_points,
        minimum_version_changed=minimum_version_changed,
        non_monotonic_release=non_monotonic_release,
    )
    decision = promotion_decision(reasons, bootstrap=bootstrap)
    diff = {
        "schema_version": 1,
        "from_release_id": previous.manifest["release_id"] if previous else None,
        "from_generated_at": previous.manifest["generated_at"] if previous else None,
        "from_minimum_euler_version": (
            previous.manifest["minimum_euler_version"] if previous else None
        ),
        "to_release_id": candidate.manifest["release_id"],
        "to_generated_at": candidate.manifest["generated_at"],
        "to_minimum_euler_version": candidate.manifest["minimum_euler_version"],
        "decision": decision,
        "reasons": sorted(reasons),
        "promotion_policy": {
            "sha256": policy.sha256,
            "maximum_shrink_basis_points": policy.maximum_shrink_basis_points,
        },
        "providers": providers,
    }
    return validate_promotion_diff(diff)


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
        print(f"promotion classification failed: {error}", file=sys.stderr)
        return 1
    print(f"promotion decision: {diff['decision']}")
    return int(diff["decision"] == "blocked")


if __name__ == "__main__":
    raise SystemExit(main())
