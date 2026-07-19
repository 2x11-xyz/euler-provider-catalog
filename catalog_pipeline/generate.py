from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .common import (
    CatalogError,
    OBSERVED_DISCOVERY_KINDS,
    catalog_release_id,
    canonical_json_bytes,
    sha256_hex,
    validate_model,
    validate_model_id,
    write_or_check,
)
from .config import SUPPORTED_PROVIDERS, load_curated, load_policy
from .normalize import normalize_provider
from .observation import load_observation


@dataclass(frozen=True)
class GeneratedArtifacts:
    documents: dict[str, dict[str, Any]]
    encoded: dict[str, bytes]


def _version_key(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3:
        raise CatalogError(f"invalid minimum Euler version: {version}")
    try:
        return tuple(int(part) for part in parts)  # type: ignore[return-value]
    except ValueError as error:
        raise CatalogError(f"invalid minimum Euler version: {version}") from error


def _timestamp_key(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp[:-1] + "+00:00")


def _validate_provider(provider: dict[str, Any], limits: dict[str, Any]) -> None:
    provider_id = provider["id"]
    models = provider["models"]
    if models != sorted(models, key=lambda model: model["id"]):
        raise CatalogError(f"{provider_id} models are not sorted")
    for index, model in enumerate(models):
        validate_model(model, limits, f"catalog.{provider_id}.models[{index}]")
    by_id = {model["id"]: model for model in models}
    default = by_id.get(provider["default_model"])
    if default is None or default["status"] != "active":
        raise CatalogError(f"{provider_id} default model is not active in the candidate")
    aliases = provider["aliases"]
    for index, alias in enumerate(aliases):
        validate_model_id(
            alias,
            int(limits["maximum_model_id_bytes"]),
            f"catalog.{provider_id}.aliases[{index}]",
        )
    if aliases != sorted(set(aliases)):
        raise CatalogError(f"{provider_id} aliases are not unique and sorted")
    if set(aliases) & set(by_id):
        raise CatalogError(f"{provider_id} aliases duplicate model ids")


def generate_artifacts(
    *,
    observations_dir: Path,
    sources_dir: Path,
    curated_dir: Path,
) -> GeneratedArtifacts:
    source_ids = tuple(sorted(path.stem for path in sources_dir.glob("*.json")))
    if source_ids != SUPPORTED_PROVIDERS:
        raise CatalogError(
            "source policies must exactly cover Euler providers: " + ", ".join(SUPPORTED_PROVIDERS)
        )

    providers: dict[str, dict[str, Any]] = {}
    provenance_providers: dict[str, dict[str, Any]] = {}
    observation_times: list[str] = []
    minimum_versions: list[str] = []

    for provider_id in SUPPORTED_PROVIDERS:
        policy, policy_raw = load_policy(sources_dir, provider_id)
        curated, curated_raw = load_curated(curated_dir, provider_id, policy["limits"])
        discovery = policy["discovery"]
        if discovery["kind"] in OBSERVED_DISCOVERY_KINDS:
            payloads, observed_at, evidence = load_observation(observations_dir, policy)
        else:
            payloads = {}
            observed_at = curated["reviewed_at"]
            evidence = []

        result = normalize_provider(policy, curated, payloads)
        provider = {
            "id": provider_id,
            "display_name": policy["display_name"],
            "default_model": curated["default_model"],
            "aliases": sorted(curated["aliases"]),
            "models": result.models,
        }
        _validate_provider(provider, policy["limits"])
        providers[provider_id] = provider

        evidence.extend(
            [
                {
                    "kind": "source_policy",
                    "path": f"sources/{provider_id}.json",
                    "bytes": len(policy_raw),
                    "sha256": sha256_hex(policy_raw),
                },
                {
                    "kind": "curated",
                    "path": f"curated/{provider_id}.json",
                    "bytes": len(curated_raw),
                    "sha256": sha256_hex(curated_raw),
                },
            ]
        )
        provenance_providers[provider_id] = {
            "discovery_kind": discovery["kind"],
            "documentation_urls": sorted(discovery["documentation_urls"]),
            "observed_at": observed_at,
            "inputs": sorted(evidence, key=lambda item: item["path"]),
            "observed_model_count": result.observed_model_count,
            "published_model_count": len(result.models),
            "curated_model_count": result.curated_model_count,
            "skipped": result.skipped,
            "warnings": result.warnings,
        }
        observation_times.append(observed_at)
        minimum_versions.append(policy["minimum_euler_version"])

    generated_at = max(observation_times, key=_timestamp_key)
    catalog = {"schema_version": 1, "providers": providers}
    provenance = {
        "schema_version": 1,
        "generated_at": generated_at,
        "generator": {"name": "euler-provider-catalog", "version": __version__},
        "providers": provenance_providers,
    }
    catalog_bytes = canonical_json_bytes(catalog)
    provenance_bytes = canonical_json_bytes(provenance)
    minimum_euler_version = max(minimum_versions, key=_version_key)
    manifest_artifacts = {
        "catalog-v1.json": {
            "bytes": len(catalog_bytes),
            "sha256": sha256_hex(catalog_bytes),
        },
        "provenance-v1.json": {
            "bytes": len(provenance_bytes),
            "sha256": sha256_hex(provenance_bytes),
        },
    }
    manifest = {
        "schema_version": 1,
        "release_id": catalog_release_id(
            generated_at=generated_at,
            minimum_euler_version=minimum_euler_version,
            artifacts=manifest_artifacts,
        ),
        "generated_at": generated_at,
        "minimum_euler_version": minimum_euler_version,
        "artifacts": manifest_artifacts,
    }
    documents = {
        "catalog-v1.json": catalog,
        "manifest-v1.json": manifest,
        "provenance-v1.json": provenance,
    }
    encoded = {
        "catalog-v1.json": catalog_bytes,
        "manifest-v1.json": canonical_json_bytes(manifest),
        "provenance-v1.json": provenance_bytes,
    }
    return GeneratedArtifacts(documents=documents, encoded=encoded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a centralized Euler provider catalog")
    parser.add_argument("--observations-dir", type=Path, default=Path("fixtures"))
    parser.add_argument("--sources-dir", type=Path, default=Path("sources"))
    parser.add_argument("--curated-dir", type=Path, default=Path("curated"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        artifacts = generate_artifacts(
            observations_dir=args.observations_dir,
            sources_dir=args.sources_dir,
            curated_dir=args.curated_dir,
        )
        write_or_check(args.output_dir, artifacts.encoded, check=args.check)
    except CatalogError as error:
        print(f"catalog generation failed: {error}", file=sys.stderr)
        return 1
    action = "verified" if args.check else "generated"
    print(f"{action} {len(artifacts.encoded)} centralized catalog artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
