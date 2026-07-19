from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


REASONING_EFFORTS = ("xsmall", "small", "medium", "large", "xlarge", "max")
MODEL_REQUIRED_FIELDS = {
    "id",
    "display_name",
    "status",
    "context_window_tokens",
    "supports_tools",
    "supports_reasoning",
    "reasoning_efforts",
}
MODEL_OPTIONAL_FIELDS = {"cost", "max_output_tokens"}
MODEL_METADATA_FIELDS = tuple(
    sorted((MODEL_REQUIRED_FIELDS | MODEL_OPTIONAL_FIELDS) - {"id", "status"})
)
PRICE_RATE_FIELDS = (
    "input",
    "output",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
)
PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
RELEASE_ID_PATTERN = re.compile(r"^catalog-v1-[0-9]{8}t[0-9]{6}z-[a-f0-9]{64}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class CatalogError(ValueError):
    """A source observation cannot safely produce a catalog."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog_release_id(
    *,
    generated_at: str,
    minimum_euler_version: str,
    artifacts: dict[str, Any],
) -> str:
    # Wire-format invariant shared with Euler's provider_catalog::release_id:
    # identity keys (including nested artifact keys) are lexicographically
    # sorted, JSON uses ensure_ascii=False and two-space indentation, and one
    # trailing LF is hashed. Changing these canonical_json_bytes settings is a
    # catalog protocol change that must be coordinated across both repositories.
    timestamp = datetime.fromisoformat(generated_at[:-1] + "+00:00")
    identity = {
        "artifacts": artifacts,
        "generated_at": generated_at,
        "minimum_euler_version": minimum_euler_version,
        "schema_version": 1,
    }
    digest = sha256_hex(canonical_json_bytes(identity))
    return f"catalog-v1-{timestamp.strftime('%Y%m%dt%H%M%Sz').lower()}-{digest}"


def read_json(path: Path, *, max_bytes: int = 16 * 1024 * 1024) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CatalogError(f"cannot read {path}: {error}") from error
    if not raw:
        raise CatalogError(f"{path} is empty")
    if len(raw) > max_bytes:
        raise CatalogError(f"{path} exceeds {max_bytes} bytes")
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(f"{path} is not valid JSON: {error}") from error


def require_object(value: Any, scope: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogError(f"{scope} must be an object")
    return value


def require_array(value: Any, scope: str) -> list[Any]:
    if not isinstance(value, list):
        raise CatalogError(f"{scope} must be an array")
    return value


def require_string(value: Any, scope: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{scope} must be a non-empty string")
    return value


def positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _price_decimal(value: Any, scope: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogError(f"{scope} must be a non-negative JSON number")
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as error:
        raise CatalogError(f"{scope} must be a non-negative JSON number") from error
    if not decimal.is_finite() or decimal < 0 or decimal > Decimal("1000000"):
        raise CatalogError(f"{scope} is out of bounds")
    if decimal != decimal.quantize(Decimal("0.000001")):
        raise CatalogError(f"{scope} must have at most six decimal places")
    return decimal


def _canonical_price_number(value: Any, scope: str) -> int | float:
    decimal = _price_decimal(value, scope)
    if decimal == decimal.to_integral():
        return int(decimal)
    number = float(decimal)
    if Decimal(str(number)) != decimal:
        raise CatalogError(f"{scope} cannot be represented canonically")
    return number


def validate_cost(value: Any, scope: str) -> dict[str, Any]:
    cost = require_object(value, scope)
    allowed = set(PRICE_RATE_FIELDS) | {"tiers"}
    unknown = set(cost) - allowed
    missing = {"input", "output"} - set(cost)
    if missing:
        raise CatalogError(f"{scope} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise CatalogError(f"{scope} has unknown fields: {', '.join(sorted(unknown))}")
    normalized = {
        field: _canonical_price_number(cost[field], f"{scope}.{field}")
        for field in PRICE_RATE_FIELDS
        if field in cost
    }
    if "tiers" not in cost:
        return normalized
    tiers = require_array(cost["tiers"], f"{scope}.tiers")
    if len(tiers) > 16:
        raise CatalogError(f"{scope}.tiers has too many entries")
    normalized_tiers = []
    previous = 0
    for index, value in enumerate(tiers):
        tier_scope = f"{scope}.tiers[{index}]"
        tier = require_object(value, tier_scope)
        allowed_tier = set(PRICE_RATE_FIELDS) | {"input_tokens_above"}
        unknown_tier = set(tier) - allowed_tier
        missing_tier = {"input_tokens_above", "input", "output"} - set(tier)
        if missing_tier:
            raise CatalogError(f"{tier_scope} is missing fields: {', '.join(sorted(missing_tier))}")
        if unknown_tier:
            raise CatalogError(
                f"{tier_scope} has unknown fields: {', '.join(sorted(unknown_tier))}"
            )
        threshold = positive_int(tier["input_tokens_above"])
        if threshold is None or threshold <= previous:
            raise CatalogError(f"{scope}.tiers thresholds must be positive and ascending")
        previous = threshold
        normalized_tier = {
            "input_tokens_above": threshold,
            **{
                field: _canonical_price_number(tier[field], f"{tier_scope}.{field}")
                for field in PRICE_RATE_FIELDS
                if field in tier
            },
        }
        normalized_tiers.append(normalized_tier)
    normalized["tiers"] = normalized_tiers
    return normalized


def validate_timestamp(value: Any, scope: str) -> str:
    timestamp = require_string(value, scope)
    if not timestamp.endswith("Z"):
        raise CatalogError(f"{scope} must be a UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as error:
        raise CatalogError(f"{scope} is not an RFC 3339 timestamp") from error
    return timestamp


def validate_version(value: Any, scope: str) -> str:
    version = require_string(value, scope)
    if not VERSION_PATTERN.fullmatch(version):
        raise CatalogError(f"{scope} is not a numeric major.minor.patch version")
    return version


def validate_model_id(value: Any, maximum_bytes: int, scope: str) -> str:
    model_id = require_string(value, scope)
    if any(character.isspace() for character in model_id) or "::" in model_id:
        raise CatalogError(f"{scope} is not a valid Euler model id")
    if len(model_id.encode()) > maximum_bytes:
        raise CatalogError(f"{scope} is too long")
    return model_id


def is_provider_owned_record(value: Any, filters: dict[str, Any]) -> bool:
    owners = filters.get("required_owned_by")
    prefixes = filters.get("forbidden_id_prefixes", [])
    valid_filters = (
        isinstance(owners, list)
        and bool(owners)
        and all(isinstance(owner, str) and owner for owner in owners)
        and isinstance(prefixes, list)
        and all(isinstance(prefix, str) and prefix for prefix in prefixes)
    )
    if not valid_filters:
        raise CatalogError("provider ownership filters are invalid")
    if not isinstance(value, dict) or value.get("owned_by") not in owners:
        return False
    model_id = value.get("id")
    return isinstance(model_id, str) and bool(model_id) and not model_id.startswith(tuple(prefixes))


def validate_model(model: Any, limits: dict[str, Any], scope: str) -> dict[str, Any]:
    record = require_object(model, scope)
    fields = set(record)
    missing = MODEL_REQUIRED_FIELDS - fields
    unknown = fields - MODEL_REQUIRED_FIELDS - MODEL_OPTIONAL_FIELDS
    if missing:
        raise CatalogError(f"{scope} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise CatalogError(f"{scope} has unknown fields: {', '.join(sorted(unknown))}")

    validate_model_id(record["id"], int(limits["maximum_model_id_bytes"]), f"{scope}.id")

    display_name = require_string(record["display_name"], f"{scope}.display_name")
    if len(display_name.encode()) > int(limits["maximum_display_name_bytes"]):
        raise CatalogError(f"{scope}.display_name is too long")
    if record["status"] not in {"active", "deprecated", "removed"}:
        raise CatalogError(f"{scope}.status is invalid")

    token_limit = int(limits["maximum_token_limit"])
    context = positive_int(record["context_window_tokens"])
    if context is None or context > token_limit:
        raise CatalogError(f"{scope}.context_window_tokens is invalid")
    if "max_output_tokens" in record:
        output = positive_int(record["max_output_tokens"])
        if output is None or output > token_limit:
            raise CatalogError(f"{scope}.max_output_tokens is invalid")
    if "cost" in record:
        record["cost"] = validate_cost(record["cost"], f"{scope}.cost")
    if record["supports_tools"] is not True:
        raise CatalogError(f"{scope}.supports_tools must be true")
    if not isinstance(record["supports_reasoning"], bool):
        raise CatalogError(f"{scope}.supports_reasoning must be boolean")

    efforts = require_array(record["reasoning_efforts"], f"{scope}.reasoning_efforts")
    if len(efforts) != len(set(efforts)) or any(item not in REASONING_EFFORTS for item in efforts):
        raise CatalogError(f"{scope}.reasoning_efforts contains invalid values")
    if efforts != sorted(efforts, key=REASONING_EFFORTS.index):
        raise CatalogError(f"{scope}.reasoning_efforts is not in canonical order")
    if record["supports_reasoning"] != bool(efforts):
        raise CatalogError(f"{scope}.supports_reasoning disagrees with reasoning_efforts")
    return dict(record)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_or_check(output_dir: Path, outputs: dict[str, bytes], *, check: bool) -> None:
    stale: list[str] = []
    for name, data in outputs.items():
        path = output_dir / name
        if check:
            try:
                current = path.read_bytes()
            except OSError:
                stale.append(name)
                continue
            if current != data:
                stale.append(name)
        else:
            atomic_write(path, data)
    if stale:
        raise CatalogError(f"generated artifacts are stale: {', '.join(sorted(stale))}")
    if check and output_dir.exists():
        try:
            unexpected = sorted(
                path.name for path in output_dir.iterdir() if path.name not in outputs
            )
        except OSError as error:
            raise CatalogError(f"cannot inspect generated artifact directory: {error}") from error
        if unexpected:
            raise CatalogError(
                "generated artifact directory contains unexpected entries: " + ", ".join(unexpected)
            )
