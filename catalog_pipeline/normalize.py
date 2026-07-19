from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .common import (
    CatalogError,
    REASONING_EFFORTS,
    is_provider_owned_record,
    positive_int,
    require_array,
    require_object,
    validate_model,
)


@dataclass(frozen=True)
class NormalizationResult:
    models: list[dict[str, Any]]
    observed_model_count: int
    curated_model_count: int
    skipped: dict[str, int]
    warnings: list[str]


def _increment(skipped: dict[str, int], reason: str) -> None:
    skipped[reason] = skipped.get(reason, 0) + 1


def _canonical_efforts(values: list[str]) -> list[str]:
    unique = set(values)
    return [effort for effort in REASONING_EFFORTS if effort in unique]


def _require_provider_owned(value: dict[str, Any], policy: dict[str, Any], scope: str) -> None:
    if not is_provider_owned_record(value, policy["filters"]):
        raise CatalogError(f"{scope} is not a public provider-owned model record")


def _finish(
    policy: dict[str, Any],
    curated: dict[str, Any],
    models: list[dict[str, Any]],
    *,
    observed_count: int,
    curated_count: int,
    skipped: dict[str, int],
    warnings: list[str],
) -> NormalizationResult:
    provider_id = policy["provider_id"]
    limits = policy["limits"]
    if observed_count < int(limits["minimum_observed_models"]):
        raise CatalogError(
            f"{provider_id} observed {observed_count} models; "
            f"minimum is {limits['minimum_observed_models']}"
        )

    base_ids = [model.get("id") for model in models]
    if len(base_ids) != len(set(base_ids)):
        raise CatalogError(f"{provider_id} produced duplicate model ids")
    by_id = {model["id"]: model for model in models}
    curated_conflicts = 0
    for addition in curated["additions"]:
        observed = by_id.get(addition["id"])
        if observed is None:
            models.append(addition)
            by_id[addition["id"]] = addition
            continue
        conflict = False
        for field, value in addition.items():
            if field not in observed:
                observed[field] = value
            elif observed[field] != value:
                conflict = True
        curated_conflicts += int(conflict)
    curated_count += len(curated["additions"])
    if curated_conflicts:
        warnings.append(
            f"{curated_conflicts} curated fallback records disagreed with official fields; "
            "official values won"
        )
    validated = [
        validate_model(model, limits, f"{provider_id}.models[{index}]")
        for index, model in enumerate(models)
    ]
    validated.sort(key=lambda model: model["id"])

    minimum = int(limits["minimum_published_models"])
    maximum = int(limits["maximum_published_models"])
    if not minimum <= len(validated) <= maximum:
        raise CatalogError(
            f"{provider_id} produced {len(validated)} models; expected {minimum}..{maximum}"
        )
    return NormalizationResult(
        models=validated,
        observed_model_count=observed_count,
        curated_model_count=curated_count,
        skipped=dict(sorted(skipped.items())),
        warnings=sorted(set(warnings)),
    )


def _openrouter(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    if curated["membership_policy"] != "all_observed" or curated["models"]:
        raise CatalogError("openrouter supports API membership plus explicit additions only")
    payload = require_object(payloads["models"], "openrouter.models response")
    records = require_array(payload.get("data"), "openrouter.models.data")
    filters = policy["filters"]
    required_parameters = set(filters["required_supported_parameters"])
    required_modalities = set(filters["required_output_modalities"])
    effort_map = policy["reasoning_effort_map"]
    defaults = policy["default_reasoning_efforts"]
    skipped: dict[str, int] = {}
    warnings: list[str] = []
    models: list[dict[str, Any]] = []
    seen: set[str] = set()

    for value in records:
        if not isinstance(value, dict):
            _increment(skipped, "malformed_record")
            continue
        model_id = value.get("id")
        name = value.get("name")
        context = positive_int(value.get("context_length"))
        parameters = value.get("supported_parameters")
        architecture = value.get("architecture")
        modalities = (
            architecture.get("output_modalities") if isinstance(architecture, dict) else None
        )
        invalid_identity = (
            not isinstance(model_id, str)
            or not model_id
            or not isinstance(name, str)
            or not name
            or context is None
        )
        if invalid_identity:
            _increment(skipped, "malformed_record")
            continue
        if model_id in seen:
            _increment(skipped, "duplicate_id")
            continue
        seen.add(model_id)
        if not isinstance(parameters, list) or not required_parameters.issubset(set(parameters)):
            _increment(skipped, "tools_not_supported")
            continue
        if not isinstance(modalities, list) or not required_modalities.issubset(set(modalities)):
            _increment(skipped, "text_output_not_supported")
            continue

        reasoning = value.get("reasoning")
        reasoning_parameters = {"reasoning", "reasoning_effort", "include_reasoning"}
        supports_reasoning = bool(reasoning_parameters & set(parameters)) or (
            isinstance(reasoning, dict) and bool(reasoning)
        )
        mapped: list[str] = []
        if isinstance(reasoning, dict) and isinstance(reasoning.get("supported_efforts"), list):
            mapped = [
                effort_map[item]
                for item in reasoning["supported_efforts"]
                if isinstance(item, str) and item in effort_map
            ]
        efforts = _canonical_efforts(mapped or (defaults if supports_reasoning else []))
        model: dict[str, Any] = {
            "id": model_id,
            "display_name": name,
            "status": "active",
            "context_window_tokens": context,
            "supports_tools": True,
            "supports_reasoning": supports_reasoning,
            "reasoning_efforts": efforts,
        }
        top_provider = value.get("top_provider")
        output = (
            positive_int(top_provider.get("max_completion_tokens"))
            if isinstance(top_provider, dict)
            else None
        )
        if output is not None:
            model["max_output_tokens"] = output
        models.append(model)

    return _finish(
        policy,
        curated,
        models,
        observed_count=len(records),
        curated_count=0,
        skipped=skipped,
        warnings=warnings,
    )


def _anthropic(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    if curated["membership_policy"] != "all_observed" or curated["models"]:
        raise CatalogError("anthropic supports API-owned records plus explicit additions only")
    payload = require_object(payloads["models"], "anthropic.models response")
    has_more = payload.get("has_more")
    if has_more is not False and has_more is not None:
        raise CatalogError("anthropic models response is paginated beyond the bounded observation")
    records = require_array(payload.get("data"), "anthropic.models.data")
    effort_map = policy["reasoning_effort_map"]
    defaults = policy["default_reasoning_efforts"]
    skipped: dict[str, int] = {}
    models: list[dict[str, Any]] = []
    seen: set[str] = set()

    for value in records:
        if not isinstance(value, dict):
            _increment(skipped, "malformed_record")
            continue
        if value.get("type") != policy["filters"]["required_object_type"]:
            _increment(skipped, "unsupported_object_type")
            continue
        model_id = value.get("id")
        name = value.get("display_name")
        context = positive_int(value.get("max_input_tokens"))
        output = positive_int(value.get("max_tokens"))
        invalid_identity = (
            not isinstance(model_id, str)
            or not model_id
            or not isinstance(name, str)
            or not name
            or context is None
        )
        if invalid_identity:
            _increment(skipped, "incomplete_metadata")
            continue
        if model_id in seen:
            _increment(skipped, "duplicate_id")
            continue
        seen.add(model_id)

        capabilities = value.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, dict) else {}
        thinking = capabilities.get("thinking")
        effort_capability = capabilities.get("effort")
        thinking_types = thinking.get("types") if isinstance(thinking, dict) else None
        adaptive = thinking_types.get("adaptive") if isinstance(thinking_types, dict) else None
        supports_reasoning = (
            isinstance(adaptive, dict)
            and adaptive.get("supported") is True
            and isinstance(effort_capability, dict)
            and effort_capability.get("supported") is True
        )
        mapped: list[str] = []
        if isinstance(effort_capability, dict):
            for upstream, canonical in effort_map.items():
                support = effort_capability.get(upstream)
                if isinstance(support, dict) and support.get("supported") is True:
                    mapped.append(canonical)
        efforts = _canonical_efforts((mapped or defaults) if supports_reasoning else [])
        model: dict[str, Any] = {
            "id": model_id,
            "display_name": name,
            "status": "active",
            "context_window_tokens": context,
            "supports_tools": True,
            "supports_reasoning": supports_reasoning,
            "reasoning_efforts": efforts,
        }
        if output is not None:
            model["max_output_tokens"] = output
        models.append(model)

    return _finish(
        policy,
        curated,
        models,
        observed_count=len(records),
        curated_count=0,
        skipped=skipped,
        warnings=[],
    )


def _openai(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    if curated["membership_policy"] != "reviewed_only" or curated["additions"]:
        raise CatalogError("openai requires reviewed-only API membership")
    payload = require_object(payloads["models"], "openai.models response")
    records = require_array(payload.get("data"), "openai.models.data")
    observed_ids: set[str] = set()
    skipped: dict[str, int] = {}
    for value in records:
        if not isinstance(value, dict):
            _increment(skipped, "malformed_or_unsupported_record")
            continue
        model_id = value.get("id")
        if not isinstance(model_id, str) or not model_id:
            _increment(skipped, "malformed_or_unsupported_record")
            continue
        _require_provider_owned(value, policy, "openai.models record")
        if value.get("object") != policy["filters"]["required_object_type"]:
            _increment(skipped, "malformed_or_unsupported_record")
            continue
        if model_id in observed_ids:
            _increment(skipped, "duplicate_id")
            continue
        observed_ids.add(model_id)

    reviewed = {model["id"]: model for model in curated["models"]}
    admitted_ids = sorted(observed_ids & set(reviewed))
    unreviewed_count = len(observed_ids - set(reviewed))
    unavailable_count = len(set(reviewed) - observed_ids)
    if unreviewed_count:
        skipped["not_reviewed_for_euler"] = unreviewed_count
    warnings: list[str] = []
    if unreviewed_count:
        warnings.append(
            f"{unreviewed_count} observed OpenAI model ids lack reviewed Euler metadata"
        )
    if unavailable_count:
        warnings.append(
            f"{unavailable_count} reviewed OpenAI model ids were absent from this account "
            "observation"
        )
    return _finish(
        policy,
        curated,
        [reviewed[model_id] for model_id in admitted_ids],
        observed_count=len(records),
        curated_count=len(admitted_ids),
        skipped=skipped,
        warnings=warnings,
    )


def _xai(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    if curated["membership_policy"] != "all_observed":
        raise CatalogError("xai requires API-owned membership")
    raw_models = require_object(payloads["models"], "xai.models response")
    details = require_array(raw_models.get("data"), "xai.models.data")
    raw_language = require_object(payloads["language-models"], "xai.language-models response")
    language_models = require_array(raw_language.get("models"), "xai.language-models.models")

    contexts: dict[str, int] = {}
    for value in details:
        if not isinstance(value, dict):
            continue
        model_id = value.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        _require_provider_owned(value, policy, "xai.models record")
        context = positive_int(value.get("context_length"))
        if context is None:
            continue
        contexts[model_id] = context
        aliases = value.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias:
                    contexts.setdefault(alias, context)

    reviewed = {model["id"]: model for model in curated["models"]}
    used_reviewed: set[str] = set()
    required_modalities = set(policy["filters"]["required_output_modalities"])
    skipped: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    primary_ids: set[str] = set()
    for value in language_models:
        if not isinstance(value, dict):
            _increment(skipped, "malformed_or_unsupported_record")
            continue
        primary_id = value.get("id")
        if not isinstance(primary_id, str) or not primary_id:
            _increment(skipped, "malformed_or_unsupported_record")
            continue
        _require_provider_owned(value, policy, "xai.language-models record")
        if value.get("object") != policy["filters"]["required_object_type"]:
            _increment(skipped, "malformed_or_unsupported_record")
            continue
        modalities = value.get("output_modalities")
        if not isinstance(modalities, list) or not required_modalities.issubset(set(modalities)):
            _increment(skipped, "text_output_not_supported")
            continue
        if primary_id in primary_ids:
            _increment(skipped, "duplicate_id")
            continue
        primary_ids.add(primary_id)
        records.append(value)

    models_by_id: dict[str, dict[str, Any]] = {}

    def add_model(model_id: str, primary_id: str) -> None:
        reviewed_model = reviewed.get(model_id)
        inherited_alias = reviewed_model is None and model_id != primary_id
        if inherited_alias:
            reviewed_model = reviewed.get(primary_id)
        context = contexts.get(model_id) or contexts.get(primary_id)
        if reviewed_model is not None:
            model = dict(reviewed_model)
            used_reviewed.add(reviewed_model["id"])
            if inherited_alias:
                model["id"] = model_id
                model["display_name"] = f"{reviewed_model['display_name']} ({model_id})"
            if context is not None:
                model["context_window_tokens"] = context
        else:
            if context is None:
                _increment(skipped, "missing_context_limit")
                return
            model = {
                "id": model_id,
                "display_name": model_id,
                "status": "active",
                "context_window_tokens": context,
                "supports_tools": True,
                "supports_reasoning": False,
                "reasoning_efforts": [],
            }
        models_by_id[model_id] = model

    for value in records:
        add_model(value["id"], value["id"])

    for value in records:
        primary_id = value["id"]
        aliases = value.get("aliases")
        if isinstance(aliases, list):
            candidate_ids = [alias for alias in aliases if isinstance(alias, str) and alias]
        else:
            candidate_ids = []
        for model_id in candidate_ids:
            if model_id == primary_id:
                continue
            if model_id in primary_ids:
                _increment(skipped, "alias_collides_with_primary_id")
                continue
            if model_id in models_by_id:
                _increment(skipped, "duplicate_alias")
                continue
            add_model(model_id, primary_id)

    unavailable = len(set(reviewed) - used_reviewed)
    warnings = []
    if unavailable:
        warnings.append(
            f"{unavailable} reviewed xAI model ids were absent from this account observation"
        )
    return _finish(
        policy,
        curated,
        list(models_by_id.values()),
        observed_count=len(language_models),
        curated_count=len(used_reviewed),
        skipped=skipped,
        warnings=warnings,
    )


def _chatgpt(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    if curated["membership_policy"] != "curated_only" or curated["additions"]:
        raise CatalogError("chatgpt requires curated membership plus official metadata")
    payload = require_object(payloads["models"], "chatgpt.models response")
    records = require_array(payload.get("models"), "chatgpt.models")
    reviewed = {model["id"]: model for model in curated["models"]}
    observed_contexts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    seen: set[str] = set()

    for value in records:
        if not isinstance(value, dict):
            _increment(skipped, "malformed_record")
            continue
        model_id = value.get("slug")
        if not isinstance(model_id, str) or not model_id:
            _increment(skipped, "malformed_record")
            continue
        if model_id in seen:
            raise CatalogError(f"chatgpt official catalog repeats model id {model_id}")
        seen.add(model_id)
        context = positive_int(value.get("context_window"))
        maximum = positive_int(value.get("max_context_window"))
        if context is None or maximum is None or context > maximum:
            if model_id in reviewed:
                raise CatalogError(
                    f"reviewed ChatGPT route {model_id} has invalid official context metadata"
                )
            _increment(skipped, "malformed_record")
            continue
        observed_contexts[model_id] = context

    models: list[dict[str, Any]] = []
    matched: set[str] = set()
    overridden_contexts = 0
    for model_id, reviewed_model in reviewed.items():
        model = dict(reviewed_model)
        official_context = observed_contexts.get(model_id)
        if official_context is not None:
            overridden_contexts += int(model["context_window_tokens"] != official_context)
            model["context_window_tokens"] = official_context
            matched.add(model_id)
        models.append(model)

    reviewed_ids = set(reviewed)
    allowed_unobserved = set(policy["filters"].get("allowed_unobserved_model_ids", []))
    if not allowed_unobserved <= reviewed_ids:
        raise CatalogError("chatgpt allowed unobserved ids must be reviewed routes")
    unreviewed = len(set(observed_contexts) - reviewed_ids)
    unavailable_ids = reviewed_ids - matched
    unexpected_unavailable = unavailable_ids - allowed_unobserved
    if unexpected_unavailable:
        raise CatalogError(
            "reviewed ChatGPT routes are absent from the official Codex catalog: "
            + ", ".join(sorted(unexpected_unavailable))
        )
    unavailable = len(unavailable_ids)
    if unreviewed:
        skipped["not_curated_for_euler"] = unreviewed
    warnings: list[str] = []
    if overridden_contexts:
        value_noun = "value" if overridden_contexts == 1 else "values"
        warnings.append(
            f"{overridden_contexts} reviewed ChatGPT context {value_noun} differed from the "
            f"official snapshot; official {value_noun} won"
        )
    if unreviewed:
        route_noun = "route" if unreviewed == 1 else "routes"
        verb = "lacks" if unreviewed == 1 else "lack"
        warnings.append(
            f"{unreviewed} official ChatGPT {route_noun} {verb} reviewed Euler membership"
        )
    if unavailable:
        route_noun = "route" if unavailable == 1 else "routes"
        verb = "was" if unavailable == 1 else "were"
        warnings.append(
            f"{unavailable} reviewed ChatGPT {route_noun} {verb} absent from the "
            "official Codex catalog; "
            "reviewed fallback metadata was retained"
        )
    return _finish(
        policy,
        curated,
        models,
        observed_count=len(records),
        curated_count=len(models),
        skipped=skipped,
        warnings=warnings,
    )


def _curated(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    if payloads or curated["membership_policy"] != "curated_only" or curated["additions"]:
        raise CatalogError("curated provider has invalid membership inputs")
    return _finish(
        policy,
        curated,
        list(curated["models"]),
        observed_count=0,
        curated_count=len(curated["models"]),
        skipped={},
        warnings=["no suitable unattended public discovery API; route membership is reviewed"],
    )


NORMALIZERS: dict[
    str,
    Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], NormalizationResult],
] = {
    "anthropic": _anthropic,
    "chatgpt": _chatgpt,
    "curated": _curated,
    "openai": _openai,
    "openrouter": _openrouter,
    "xai": _xai,
}


def normalize_provider(
    policy: dict[str, Any], curated: dict[str, Any], payloads: dict[str, Any]
) -> NormalizationResult:
    normalizer = NORMALIZERS.get(policy["normalizer"])
    if normalizer is None:
        raise CatalogError(f"unknown normalizer: {policy['normalizer']}")
    return normalizer(policy, curated, payloads)
