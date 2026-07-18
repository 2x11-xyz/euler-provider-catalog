from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    CatalogError,
    canonical_json_bytes,
    read_json,
    require_array,
    require_object,
    sha256_hex,
    validate_timestamp,
)


def load_observation(
    observations_dir: Path, policy: dict[str, Any]
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    provider_id = policy["provider_id"]
    provider_dir = observations_dir / provider_id
    sidecar, _ = read_json(provider_dir / "observation.json", max_bytes=128 * 1024)
    sidecar = require_object(sidecar, f"{provider_id}/observation.json")
    if set(sidecar) != {"schema_version", "provider_id", "observed_at", "inputs"}:
        raise CatalogError(f"{provider_id}/observation.json has an invalid shape")
    if sidecar["schema_version"] != 1 or sidecar["provider_id"] != provider_id:
        raise CatalogError(f"observation identity mismatch for {provider_id}")
    observed_at = validate_timestamp(sidecar["observed_at"], f"{provider_id}.observed_at")

    configured = {endpoint["id"]: endpoint for endpoint in policy["discovery"]["endpoints"]}
    inputs = require_array(sidecar["inputs"], f"{provider_id}.inputs")
    if len(inputs) != len(configured):
        raise CatalogError(f"{provider_id} observation does not cover every configured endpoint")

    payloads: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    for entry_value in inputs:
        entry = require_object(entry_value, f"{provider_id}.input")
        expected_fields = {"endpoint_id", "file", "source_url", "bytes", "sha256"}
        if set(entry) != expected_fields:
            raise CatalogError(f"{provider_id} observation input has an invalid shape")
        endpoint = configured.get(entry["endpoint_id"])
        mismatched = (
            endpoint is None
            or entry["file"] != endpoint["file"]
            or entry["source_url"] != endpoint["url"]
        )
        if mismatched:
            raise CatalogError(f"{provider_id} observation input does not match source policy")
        if "/" in entry["file"] or entry["file"].startswith("."):
            raise CatalogError(f"{provider_id} observation input path is unsafe")

        payload, raw = read_json(
            provider_dir / entry["file"],
            max_bytes=int(policy["limits"]["max_response_bytes"]),
        )
        if entry["bytes"] != len(raw) or entry["sha256"] != sha256_hex(raw):
            raise CatalogError(
                f"{provider_id}/{entry['file']} does not match its observation digest"
            )
        endpoint_id = entry["endpoint_id"]
        if endpoint_id in payloads:
            raise CatalogError(f"{provider_id} observation repeats endpoint {endpoint_id}")
        payloads[endpoint_id] = payload
        evidence.append(
            {
                "kind": "official_api",
                "path": f"observations/{provider_id}/{entry['file']}",
                "source_url": entry["source_url"],
                "bytes": len(raw),
                "sha256": sha256_hex(raw),
            }
        )

    if set(payloads) != set(configured):
        raise CatalogError(f"{provider_id} observation is incomplete")
    evidence.sort(key=lambda item: item["path"])
    return payloads, observed_at, evidence


def build_sidecar(provider_dir: Path, policy: dict[str, Any], observed_at: str) -> dict[str, Any]:
    provider_id = policy["provider_id"]
    validate_timestamp(observed_at, "observed_at")
    inputs: list[dict[str, Any]] = []
    for endpoint in policy["discovery"]["endpoints"]:
        _, raw = read_json(
            provider_dir / endpoint["file"],
            max_bytes=int(policy["limits"]["max_response_bytes"]),
        )
        inputs.append(
            {
                "endpoint_id": endpoint["id"],
                "file": endpoint["file"],
                "source_url": endpoint["url"],
                "bytes": len(raw),
                "sha256": sha256_hex(raw),
            }
        )
    return {
        "schema_version": 1,
        "provider_id": provider_id,
        "observed_at": observed_at,
        "inputs": sorted(inputs, key=lambda item: item["endpoint_id"]),
    }


def sidecar_bytes(provider_dir: Path, policy: dict[str, Any], observed_at: str) -> bytes:
    return canonical_json_bytes(build_sidecar(provider_dir, policy, observed_at))
