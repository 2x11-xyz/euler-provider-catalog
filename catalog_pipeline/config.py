from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    CatalogError,
    PROVIDER_ID_PATTERN,
    REASONING_EFFORTS,
    read_json,
    require_array,
    require_object,
    require_string,
    validate_cost,
    validate_model,
    validate_model_id,
    validate_timestamp,
)


SUPPORTED_PROVIDERS = ("anthropic", "chatgpt", "openai", "openrouter", "xai")


def _string_array(value: Any, scope: str, *, allow_empty: bool = False) -> list[str]:
    items = require_array(value, scope)
    valid = all(isinstance(item, str) and item for item in items)
    if not valid or (not items and not allow_empty) or len(items) != len(set(items)):
        raise CatalogError(f"{scope} must contain unique non-empty strings")
    return items


def _validate_reasoning_policy(policy: dict[str, Any], provider_id: str) -> None:
    mapping = require_object(
        policy.get("reasoning_effort_map"), f"{provider_id}.reasoning_effort_map"
    )
    invalid_mapping = not mapping or any(
        not isinstance(upstream, str) or not upstream or canonical not in REASONING_EFFORTS
        for upstream, canonical in mapping.items()
    )
    if invalid_mapping:
        raise CatalogError(f"{provider_id}.reasoning_effort_map is invalid")
    defaults = _string_array(
        policy.get("default_reasoning_efforts"),
        f"{provider_id}.default_reasoning_efforts",
    )
    if any(effort not in REASONING_EFFORTS for effort in defaults):
        raise CatalogError(f"{provider_id}.default_reasoning_efforts is invalid")
    if defaults != sorted(defaults, key=REASONING_EFFORTS.index):
        raise CatalogError(f"{provider_id}.default_reasoning_efforts is not in canonical order")


def _validate_filters(policy: dict[str, Any], provider_id: str) -> None:
    normalizer = policy["normalizer"]
    filters = require_object(policy.get("filters"), f"{provider_id}.filters")
    if normalizer in {"anthropic", "openai", "xai"}:
        require_string(filters.get("required_object_type"), f"{provider_id}.required_object_type")
    if normalizer in {"openai", "xai"}:
        _string_array(filters.get("required_owned_by"), f"{provider_id}.required_owned_by")
        _string_array(
            filters.get("forbidden_id_prefixes", []),
            f"{provider_id}.forbidden_id_prefixes",
            allow_empty=True,
        )
    if normalizer in {"openrouter", "xai"}:
        _string_array(
            filters.get("required_output_modalities"),
            f"{provider_id}.required_output_modalities",
        )
    if normalizer == "openrouter":
        _string_array(
            filters.get("required_supported_parameters"),
            f"{provider_id}.required_supported_parameters",
        )
    if normalizer in {"anthropic", "openrouter"}:
        _validate_reasoning_policy(policy, provider_id)


def load_policy(sources_dir: Path, provider_id: str) -> tuple[dict[str, Any], bytes]:
    value, raw = read_json(sources_dir / f"{provider_id}.json", max_bytes=256 * 1024)
    policy = require_object(value, f"sources/{provider_id}.json")
    if policy.get("schema_version") != 1 or policy.get("provider_id") != provider_id:
        raise CatalogError(f"source policy identity mismatch for {provider_id}")
    if provider_id not in SUPPORTED_PROVIDERS or not PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise CatalogError(f"unsupported provider id: {provider_id}")
    require_string(policy.get("display_name"), f"{provider_id}.display_name")
    normalizer = require_string(policy.get("normalizer"), f"{provider_id}.normalizer")
    if normalizer not in {"anthropic", "curated", "openai", "openrouter", "xai"}:
        raise CatalogError(f"{provider_id}.normalizer is unsupported")
    require_string(policy.get("minimum_euler_version"), f"{provider_id}.minimum_euler_version")
    _validate_filters(policy, provider_id)

    discovery = require_object(policy.get("discovery"), f"{provider_id}.discovery")
    if discovery.get("kind") not in {"official_api", "curated"}:
        raise CatalogError(f"{provider_id}.discovery.kind is invalid")
    endpoints = require_array(discovery.get("endpoints"), f"{provider_id}.discovery.endpoints")
    documentation = require_array(
        discovery.get("documentation_urls"), f"{provider_id}.discovery.documentation_urls"
    )
    invalid_documentation = any(
        not isinstance(url, str) or not url.startswith("https://") for url in documentation
    )
    if not documentation or invalid_documentation:
        raise CatalogError(f"{provider_id} must name official HTTPS documentation")
    if discovery["kind"] == "official_api" and not endpoints:
        raise CatalogError(f"{provider_id} must name an official API endpoint")
    if discovery["kind"] == "curated" and endpoints:
        raise CatalogError(f"{provider_id} curated discovery cannot name API endpoints")

    endpoint_ids: set[str] = set()
    endpoint_files: set[str] = set()
    for endpoint in endpoints:
        endpoint = require_object(endpoint, f"{provider_id}.discovery.endpoint")
        endpoint_id = require_string(endpoint.get("id"), f"{provider_id}.endpoint.id")
        endpoint_url = require_string(endpoint.get("url"), f"{provider_id}.endpoint.url")
        endpoint_file = require_string(endpoint.get("file"), f"{provider_id}.endpoint.file")
        unsafe_endpoint = (
            not endpoint_url.startswith("https://")
            or "/" in endpoint_file
            or endpoint_file.startswith(".")
        )
        if unsafe_endpoint:
            raise CatalogError(f"{provider_id} endpoint is not safely bounded")
        if endpoint_id in endpoint_ids or endpoint_file in endpoint_files:
            raise CatalogError(f"{provider_id} has duplicate endpoint metadata")
        endpoint_ids.add(endpoint_id)
        endpoint_files.add(endpoint_file)

    limits = require_object(policy.get("limits"), f"{provider_id}.limits")
    for field in (
        "max_response_bytes",
        "minimum_observed_models",
        "minimum_published_models",
        "maximum_published_models",
        "maximum_model_id_bytes",
        "maximum_display_name_bytes",
        "maximum_token_limit",
    ):
        value = limits.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CatalogError(f"{provider_id}.limits.{field} is invalid")
    return policy, raw


def load_curated(
    curated_dir: Path, provider_id: str, limits: dict[str, Any]
) -> tuple[dict[str, Any], bytes]:
    value, raw = read_json(curated_dir / f"{provider_id}.json", max_bytes=2 * 1024 * 1024)
    curated = require_object(value, f"curated/{provider_id}.json")
    expected_fields = {
        "schema_version",
        "provider_id",
        "reviewed_at",
        "membership_policy",
        "default_model",
        "aliases",
        "models",
        "additions",
        "pricing",
    }
    if set(curated) != expected_fields:
        raise CatalogError(f"curated/{provider_id}.json has an invalid shape")
    if curated["schema_version"] != 1 or curated["provider_id"] != provider_id:
        raise CatalogError(f"curated metadata identity mismatch for {provider_id}")
    validate_timestamp(curated["reviewed_at"], f"{provider_id}.reviewed_at")
    if curated["membership_policy"] not in {"all_observed", "reviewed_only", "curated_only"}:
        raise CatalogError(f"{provider_id}.membership_policy is invalid")
    maximum_model_id_bytes = int(limits["maximum_model_id_bytes"])
    validate_model_id(
        curated["default_model"], maximum_model_id_bytes, f"{provider_id}.default_model"
    )
    aliases = require_array(curated["aliases"], f"{provider_id}.aliases")
    validated_aliases = [
        validate_model_id(alias, maximum_model_id_bytes, f"{provider_id}.aliases[{index}]")
        for index, alias in enumerate(aliases)
    ]
    if len(validated_aliases) != len(set(validated_aliases)):
        raise CatalogError(f"{provider_id}.aliases is invalid")
    curated["aliases"] = validated_aliases

    for collection in ("models", "additions"):
        records = require_array(curated[collection], f"{provider_id}.{collection}")
        validated = [
            validate_model(model, limits, f"{provider_id}.{collection}[{index}]")
            for index, model in enumerate(records)
        ]
        ids = [model["id"] for model in validated]
        if len(ids) != len(set(ids)):
            raise CatalogError(f"{provider_id}.{collection} contains duplicate model ids")
        if any("cost" in model for model in validated):
            raise CatalogError(f"{provider_id}.{collection} pricing belongs in the pricing map")
        curated[collection] = validated
    pricing = require_object(curated["pricing"], f"{provider_id}.pricing")
    validated_pricing = {}
    for model_id, cost in pricing.items():
        model_id = validate_model_id(
            model_id,
            maximum_model_id_bytes,
            f"{provider_id}.pricing model id",
        )
        validated_pricing[model_id] = validate_cost(
            cost,
            f"{provider_id}.pricing[{model_id}]",
        )
    curated["pricing"] = validated_pricing
    overlap = {model["id"] for model in curated["models"]} & {
        model["id"] for model in curated["additions"]
    }
    if overlap:
        raise CatalogError(f"{provider_id} models and additions overlap")
    model_ids = {
        model["id"] for collection in ("models", "additions") for model in curated[collection]
    }
    alias_collisions = set(curated["aliases"]) & model_ids
    if alias_collisions:
        raise CatalogError(f"{provider_id} aliases duplicate model ids")
    return curated, raw
