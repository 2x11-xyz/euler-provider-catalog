from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    CatalogError,
    canonical_json_bytes,
    catalog_release_id,
    read_json,
    require_array,
    require_object,
    require_string,
    sha256_hex,
    validate_timestamp,
    validate_version,
    write_or_check,
)
from .release import validate_catalog


@dataclass(frozen=True)
class BootstrapArtifacts:
    documents: dict[str, dict[str, Any]]
    encoded: dict[str, bytes]


def _canonical_input(path: Path, *, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    value, raw = read_json(path, max_bytes=max_bytes)
    document = require_object(value, str(path))
    if raw != canonical_json_bytes(document):
        raise CatalogError(f"{path} is not canonical JSON")
    return document, raw


def _load_metadata(path: Path) -> tuple[dict[str, Any], bytes]:
    metadata, raw = _canonical_input(path, max_bytes=64 * 1024)
    expected = {
        "generator_version",
        "minimum_euler_version",
        "provider_documentation_urls",
        "reviewed_at",
        "schema_version",
        "source_repository",
        "source_revision",
    }
    if set(metadata) != expected or metadata["schema_version"] != 1:
        raise CatalogError("bootstrap metadata has an invalid shape")
    validate_timestamp(metadata["reviewed_at"], "bootstrap.reviewed_at")
    validate_version(metadata["generator_version"], "bootstrap.generator_version")
    validate_version(metadata["minimum_euler_version"], "bootstrap.minimum_euler_version")
    repository = require_string(metadata["source_repository"], "bootstrap.source_repository")
    revision = require_string(metadata["source_revision"], "bootstrap.source_revision")
    if repository != "https://github.com/2x11-xyz/euler":
        raise CatalogError("bootstrap source repository is invalid")
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise CatalogError("bootstrap source revision is invalid")
    documentation = require_object(
        metadata["provider_documentation_urls"],
        "bootstrap.provider_documentation_urls",
    )
    if not 0 < len(documentation) <= 32:
        raise CatalogError("bootstrap documentation provider count is invalid")
    for provider_id, value in documentation.items():
        urls = require_array(value, f"bootstrap.documentation.{provider_id}")
        if (
            not 0 < len(urls) <= 16
            or urls != sorted(set(urls))
            or any(not isinstance(url, str) or not url.startswith("https://") for url in urls)
        ):
            raise CatalogError(f"bootstrap documentation for {provider_id} is invalid")
    return metadata, raw


def _input(kind: str, path: str, raw: bytes) -> dict[str, Any]:
    return {"kind": kind, "path": path, "bytes": len(raw), "sha256": sha256_hex(raw)}


def generate_bootstrap_artifacts(*, bootstrap_dir: Path) -> BootstrapArtifacts:
    catalog, catalog_raw = _canonical_input(
        bootstrap_dir / "catalog-v1.json", max_bytes=16 * 1024 * 1024
    )
    metadata, metadata_raw = _load_metadata(bootstrap_dir / "metadata-v1.json")
    validate_catalog(catalog)
    providers = require_object(catalog["providers"], "bootstrap.catalog.providers")
    documentation = metadata["provider_documentation_urls"]
    provider_ids = tuple(sorted(providers))
    if provider_ids != tuple(sorted(documentation)):
        raise CatalogError("bootstrap catalog and documentation provider sets disagree")

    provenance_providers: dict[str, dict[str, Any]] = {}
    for provider_id in provider_ids:
        model_count = len(providers[provider_id]["models"])
        inputs = [
            _input("bootstrap", "bootstrap/catalog-v1.json", catalog_raw),
            _input("bootstrap", "bootstrap/metadata-v1.json", metadata_raw),
        ]
        provenance_providers[provider_id] = {
            "discovery_kind": "bootstrap",
            "documentation_urls": metadata["provider_documentation_urls"][provider_id],
            "observed_at": metadata["reviewed_at"],
            "inputs": sorted(inputs, key=lambda item: item["path"]),
            "observed_model_count": 0,
            "published_model_count": model_count,
            "curated_model_count": 0,
            "skipped": {},
            "warnings": [
                "initial catalog imported from reviewed Euler built-ins at "
                f"{metadata['source_revision']}; no provider API observation is claimed"
            ],
        }

    provenance = {
        "schema_version": 1,
        "generated_at": metadata["reviewed_at"],
        "generator": {
            "name": "euler-provider-catalog",
            "version": metadata["generator_version"],
        },
        "providers": provenance_providers,
    }
    provenance_bytes = canonical_json_bytes(provenance)
    artifacts = {
        "catalog-v1.json": {"bytes": len(catalog_raw), "sha256": sha256_hex(catalog_raw)},
        "provenance-v1.json": {
            "bytes": len(provenance_bytes),
            "sha256": sha256_hex(provenance_bytes),
        },
    }
    manifest = {
        "schema_version": 1,
        "release_id": catalog_release_id(
            generated_at=metadata["reviewed_at"],
            minimum_euler_version=metadata["minimum_euler_version"],
            artifacts=artifacts,
        ),
        "generated_at": metadata["reviewed_at"],
        "minimum_euler_version": metadata["minimum_euler_version"],
        "artifacts": artifacts,
    }
    documents = {
        "catalog-v1.json": catalog,
        "manifest-v1.json": manifest,
        "provenance-v1.json": provenance,
    }
    encoded = {
        "catalog-v1.json": catalog_raw,
        "manifest-v1.json": canonical_json_bytes(manifest),
        "provenance-v1.json": provenance_bytes,
    }
    return BootstrapArtifacts(documents=documents, encoded=encoded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the reviewed first stable catalog")
    parser.add_argument("--bootstrap-dir", type=Path, default=Path("bootstrap"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        artifacts = generate_bootstrap_artifacts(bootstrap_dir=args.bootstrap_dir)
        write_or_check(args.output_dir, artifacts.encoded, check=args.check)
    except CatalogError as error:
        print(f"bootstrap generation failed: {error}", file=sys.stderr)
        return 1
    action = "verified" if args.check else "generated"
    print(f"{action} {len(artifacts.encoded)} bootstrap catalog artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
