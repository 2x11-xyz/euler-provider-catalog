from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (
    CatalogError,
    OBSERVED_DISCOVERY_KINDS,
    PROVIDER_ID_PATTERN,
    RELEASE_ID_PATTERN,
    catalog_release_id,
    canonical_json_bytes,
    read_json,
    require_array,
    require_object,
    require_string,
    sha256_hex,
    validate_model,
    validate_model_id,
    validate_timestamp,
    validate_version,
)
from .promotion_contract import validate_promotion_diff


RUNTIME_ARTIFACTS = ("catalog-v1.json", "manifest-v1.json", "provenance-v1.json")
MODEL_LIMITS = {
    "maximum_model_id_bytes": 256,
    "maximum_display_name_bytes": 256,
    "maximum_token_limit": 20_000_000,
}


@dataclass(frozen=True)
class ReleaseArtifacts:
    manifest: dict[str, Any]
    catalog: dict[str, Any]
    provenance: dict[str, Any]
    encoded: dict[str, bytes]


def _valid_provenance_path(path: str, provider_id: str, kind: str) -> bool:
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or path != parsed.as_posix()
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        return False
    if kind == "source_policy":
        return path == f"sources/{provider_id}.json"
    if kind == "curated":
        return path == f"curated/{provider_id}.json"
    if kind == "bootstrap":
        return path in {"bootstrap/catalog-v1.json", "bootstrap/metadata-v1.json"}
    return len(parsed.parts) == 3 and parsed.parts[:2] == ("observations", provider_id)


def _provenance_inputs_match_discovery(
    discovery_kind: str, kinds: list[str], input_paths: list[str]
) -> bool:
    observed = [kind for kind in kinds if kind in OBSERVED_DISCOVERY_KINDS]
    if discovery_kind in OBSERVED_DISCOVERY_KINDS:
        return (
            bool(observed)
            and set(observed) == {discovery_kind}
            and kinds.count("source_policy") == 1
            and kinds.count("curated") == 1
            and "bootstrap" not in kinds
        )
    if discovery_kind == "curated":
        return (
            not observed
            and kinds.count("source_policy") == 1
            and kinds.count("curated") == 1
            and "bootstrap" not in kinds
        )
    if discovery_kind == "bootstrap":
        return kinds == ["bootstrap", "bootstrap"] and set(input_paths) == {
            "bootstrap/catalog-v1.json",
            "bootstrap/metadata-v1.json",
        }
    return False


def _directory_entries(directory: Path) -> set[str]:
    try:
        return {path.name for path in directory.iterdir()}
    except OSError as error:
        raise CatalogError(f"cannot inspect {directory}: {error}") from error


def _validate_manifest(manifest: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "release_id",
        "generated_at",
        "minimum_euler_version",
        "artifacts",
    }
    if set(manifest) != required or manifest["schema_version"] != 1:
        raise CatalogError("release manifest has an invalid shape")
    release_id = require_string(manifest["release_id"], "manifest.release_id")
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise CatalogError("manifest.release_id is invalid")
    validate_timestamp(manifest["generated_at"], "manifest.generated_at")
    validate_version(manifest["minimum_euler_version"], "manifest.minimum_euler_version")
    artifacts = require_object(manifest["artifacts"], "manifest.artifacts")
    if set(artifacts) != {"catalog-v1.json", "provenance-v1.json"}:
        raise CatalogError("manifest artifacts are incomplete")
    for name, value in artifacts.items():
        metadata = require_object(value, f"manifest.artifacts.{name}")
        if set(metadata) != {"bytes", "sha256"}:
            raise CatalogError(f"manifest.artifacts.{name} has an invalid shape")
        byte_count = metadata["bytes"]
        digest = metadata["sha256"]
        invalid = (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or not 0 < byte_count <= 16 * 1024 * 1024
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        )
        if invalid:
            raise CatalogError(f"manifest.artifacts.{name} metadata is invalid")


def validate_catalog(catalog: dict[str, Any]) -> None:
    if set(catalog) != {"schema_version", "providers"} or catalog["schema_version"] != 1:
        raise CatalogError("release catalog has an invalid shape")
    providers = require_object(catalog["providers"], "catalog.providers")
    if not 0 < len(providers) <= 32:
        raise CatalogError("release catalog provider count is invalid")
    for provider_id, value in providers.items():
        if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise CatalogError("release catalog contains an invalid provider id")
        provider = require_object(value, f"catalog.providers.{provider_id}")
        expected = {"id", "display_name", "default_model", "aliases", "models"}
        if set(provider) != expected or provider["id"] != provider_id:
            raise CatalogError(f"catalog provider {provider_id} has an invalid shape")
        display_name = require_string(provider["display_name"], f"{provider_id}.display_name")
        if len(display_name) > 128:
            raise CatalogError(f"{provider_id}.display_name is too long")
        default_model = validate_model_id(
            provider["default_model"], 256, f"{provider_id}.default_model"
        )
        aliases = require_array(provider["aliases"], f"{provider_id}.aliases")
        if len(aliases) > 64:
            raise CatalogError(f"{provider_id}.aliases contains too many entries")
        validated_aliases = [
            validate_model_id(alias, 256, f"{provider_id}.aliases[{index}]")
            for index, alias in enumerate(aliases)
        ]
        if validated_aliases != sorted(set(validated_aliases)):
            raise CatalogError(f"{provider_id}.aliases are not unique and sorted")
        models = require_array(provider["models"], f"{provider_id}.models")
        if not 0 < len(models) <= 10_000:
            raise CatalogError(f"{provider_id}.models count is invalid")
        validated_models = [
            validate_model(model, MODEL_LIMITS, f"{provider_id}.models[{index}]")
            for index, model in enumerate(models)
        ]
        ids = [model["id"] for model in validated_models]
        if not ids or ids != sorted(set(ids)):
            raise CatalogError(f"{provider_id}.models are empty, duplicated, or unsorted")
        by_id = {model["id"]: model for model in validated_models}
        if set(validated_aliases) & set(by_id):
            raise CatalogError(f"{provider_id}.aliases duplicate model ids")
        if default_model not in by_id or by_id[default_model]["status"] != "active":
            raise CatalogError(f"{provider_id} default model is not active")


def _validate_provenance(
    provenance: dict[str, Any], catalog: dict[str, Any], manifest: dict[str, Any]
) -> None:
    required = {"schema_version", "generated_at", "generator", "providers"}
    if set(provenance) != required or provenance["schema_version"] != 1:
        raise CatalogError("release provenance has an invalid shape")
    generated_at = validate_timestamp(provenance["generated_at"], "provenance.generated_at")
    if generated_at != manifest["generated_at"]:
        raise CatalogError("manifest and provenance timestamps disagree")
    generator = require_object(provenance["generator"], "provenance.generator")
    if set(generator) != {"name", "version"} or generator["name"] != "euler-provider-catalog":
        raise CatalogError("release provenance generator is invalid")
    require_string(generator["version"], "provenance.generator.version")
    providers = require_object(provenance["providers"], "provenance.providers")
    catalog_providers = catalog["providers"]
    if set(providers) != set(catalog_providers):
        raise CatalogError("catalog and provenance provider sets disagree")
    for provider_id, value in providers.items():
        provider = require_object(value, f"provenance.providers.{provider_id}")
        expected = {
            "discovery_kind",
            "documentation_urls",
            "observed_at",
            "inputs",
            "observed_model_count",
            "published_model_count",
            "curated_model_count",
            "skipped",
            "warnings",
        }
        if set(provider) != expected:
            raise CatalogError(f"{provider_id} provenance has an invalid shape")
        discovery_kind = provider["discovery_kind"]
        if discovery_kind not in OBSERVED_DISCOVERY_KINDS | {"curated", "bootstrap"}:
            raise CatalogError(f"{provider_id} provenance discovery kind is invalid")
        documentation = require_array(
            provider["documentation_urls"],
            f"provenance.providers.{provider_id}.documentation_urls",
        )
        if (
            not 0 < len(documentation) <= 16
            or len(documentation) != len(set(documentation))
            or any(
                not isinstance(url, str) or not url.startswith("https://") for url in documentation
            )
        ):
            raise CatalogError(f"{provider_id} provenance documentation is invalid")
        validate_timestamp(provider["observed_at"], f"{provider_id}.observed_at")
        for field in ("observed_model_count", "published_model_count", "curated_model_count"):
            count = provider[field]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise CatalogError(f"{provider_id} provenance {field} is invalid")
        if provider.get("published_model_count") != len(catalog_providers[provider_id]["models"]):
            raise CatalogError(f"{provider_id} provenance published count is invalid")
        inputs = require_array(provider.get("inputs"), f"provenance.providers.{provider_id}.inputs")
        if not 0 < len(inputs) <= 16:
            raise CatalogError(f"{provider_id} provenance input count is invalid")
        kinds = []
        input_paths: list[str] = []
        for index, value in enumerate(inputs):
            entry = require_object(value, f"provenance.providers.{provider_id}.inputs[{index}]")
            required_input = {"kind", "path", "bytes", "sha256"}
            if not required_input.issubset(entry) or not set(entry) <= required_input | {
                "source_url"
            }:
                raise CatalogError(f"{provider_id} provenance input has an invalid shape")
            kind = entry["kind"]
            byte_count = entry["bytes"]
            digest = entry["sha256"]
            invalid = (
                kind not in OBSERVED_DISCOVERY_KINDS | {"bootstrap", "curated", "source_policy"}
                or ("source_url" in entry) != (kind in OBSERVED_DISCOVERY_KINDS)
                or not isinstance(entry["path"], str)
                or not entry["path"]
                or len(entry["path"]) > 256
                or not _valid_provenance_path(entry["path"], provider_id, kind)
                or not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or not 0 < byte_count <= 16 * 1024 * 1024
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or (
                    "source_url" in entry
                    and (
                        not isinstance(entry["source_url"], str)
                        or not entry["source_url"].startswith("https://")
                    )
                )
            )
            if invalid:
                raise CatalogError(f"{provider_id} provenance input is invalid")
            kinds.append(kind)
            input_paths.append(entry["path"])
        if len(input_paths) != len(set(input_paths)):
            raise CatalogError(f"{provider_id} provenance input paths are duplicated")
        if not _provenance_inputs_match_discovery(discovery_kind, kinds, input_paths):
            raise CatalogError(f"{provider_id} provenance discovery inputs are inconsistent")
        skipped = require_object(provider["skipped"], f"{provider_id}.skipped")
        invalid_skipped = any(
            not isinstance(reason, str)
            or not reason
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for reason, count in skipped.items()
        )
        if invalid_skipped:
            raise CatalogError(f"{provider_id} provenance skipped counts are invalid")
        warnings = require_array(provider["warnings"], f"{provider_id}.warnings")
        if len(warnings) > 10_000 or any(
            not isinstance(warning, str) or len(warning) > 512 for warning in warnings
        ):
            raise CatalogError(f"{provider_id} provenance warnings are invalid")


def load_release(directory: Path, *, stable: bool = False) -> ReleaseArtifacts:
    expected = set(RUNTIME_ARTIFACTS)
    if stable:
        expected.add("diff-v1.json")
    entries = _directory_entries(directory)
    if entries != expected:
        raise CatalogError(f"{directory} must contain exactly: {', '.join(sorted(expected))}")

    documents: dict[str, dict[str, Any]] = {}
    encoded: dict[str, bytes] = {}
    for name in RUNTIME_ARTIFACTS:
        value, raw = read_json(directory / name)
        document = require_object(value, f"{directory}/{name}")
        if raw != canonical_json_bytes(document):
            raise CatalogError(f"{directory}/{name} is not canonical JSON")
        documents[name] = document
        encoded[name] = raw

    manifest = documents["manifest-v1.json"]
    catalog = documents["catalog-v1.json"]
    provenance = documents["provenance-v1.json"]
    _validate_manifest(manifest)
    for name in ("catalog-v1.json", "provenance-v1.json"):
        metadata = manifest["artifacts"][name]
        raw = encoded[name]
        if metadata["bytes"] != len(raw) or metadata["sha256"] != sha256_hex(raw):
            raise CatalogError(f"{directory}/{name} does not match its manifest digest")
    validate_catalog(catalog)
    _validate_provenance(provenance, catalog, manifest)
    expected_release_id = catalog_release_id(
        generated_at=manifest["generated_at"],
        minimum_euler_version=manifest["minimum_euler_version"],
        artifacts=manifest["artifacts"],
    )
    if manifest["release_id"] != expected_release_id:
        raise CatalogError("manifest release id does not authenticate the release bytes")

    if stable:
        diff, raw = read_json(directory / "diff-v1.json")
        diff = require_object(diff, f"{directory}/diff-v1.json")
        if raw != canonical_json_bytes(diff):
            raise CatalogError(f"{directory}/diff-v1.json is not canonical JSON")
        validate_promotion_diff(diff, expected_to_release_id=manifest["release_id"])
    return ReleaseArtifacts(
        manifest=manifest,
        catalog=catalog,
        provenance=provenance,
        encoded=encoded,
    )
