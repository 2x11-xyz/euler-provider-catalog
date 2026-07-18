from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import CatalogError, atomic_write, canonical_json_bytes
from .config import SUPPORTED_PROVIDERS, load_policy
from .record_observation import record


AUTH_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _headers(provider_id: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "euler-provider-catalog/0.1",
    }
    env_name = AUTH_ENV[provider_id]
    secret = os.environ.get(env_name)
    if provider_id == "openrouter" and not secret:
        return headers
    if not secret:
        raise CatalogError(f"{env_name} is required to observe {provider_id}")
    if provider_id == "anthropic":
        headers["x-api-key"] = secret
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _fetch(url: str, headers: dict[str, str], max_bytes: int) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise CatalogError(f"official response exceeds {max_bytes} bytes")
                except ValueError as error:
                    raise CatalogError("official response has an invalid Content-Length") from error
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        raise CatalogError(f"official endpoint returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise CatalogError(f"official endpoint request failed: {error.reason}") from error
    if not raw or len(raw) > max_bytes:
        raise CatalogError(f"official response is empty or exceeds {max_bytes} bytes")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("official endpoint returned malformed JSON") from error
    if not isinstance(payload, dict):
        raise CatalogError("official endpoint JSON must be an object")
    return raw


def _public_projection(
    provider_id: str,
    endpoint_id: str,
    payload: dict[str, Any],
    raw: bytes,
    filters: dict[str, Any],
) -> bytes:
    if provider_id not in {"openai", "xai"}:
        return raw
    collection_name = "models" if endpoint_id == "language-models" else "data"
    records = payload.get(collection_name)
    if not isinstance(records, list):
        raise CatalogError(f"{provider_id} official response is missing {collection_name}")
    allowed_owners = set(filters["required_owned_by"])
    forbidden_prefixes = tuple(filters.get("forbidden_id_prefixes", []))
    retained = []
    for record in records:
        if not isinstance(record, dict) or record.get("owned_by") not in allowed_owners:
            continue
        model_id = record.get("id")
        if not isinstance(model_id, str) or model_id.startswith(forbidden_prefixes):
            continue
        retained.append(record)
    projection = dict(payload)
    projection[collection_name] = retained
    return canonical_json_bytes(projection)


def fetch_provider(
    *, provider_id: str, observations_dir: Path, sources_dir: Path, observed_at: str
) -> None:
    policy, _ = load_policy(sources_dir, provider_id)
    if policy["discovery"]["kind"] != "official_api":
        raise CatalogError(f"{provider_id} has no official API observation")
    provider_dir = observations_dir / provider_id
    provider_dir.mkdir(parents=True, exist_ok=True)
    headers = _headers(provider_id)
    max_bytes = int(policy["limits"]["max_response_bytes"])
    for endpoint in policy["discovery"]["endpoints"]:
        raw = _fetch(endpoint["url"], headers, max_bytes)
        payload = json.loads(raw)
        observation = _public_projection(
            provider_id, endpoint["id"], payload, raw, policy["filters"]
        )
        atomic_write(provider_dir / endpoint["file"], observation)
        print(f"observed {provider_id}/{endpoint['id']}: {len(observation)} bytes")
    record(
        provider_id=provider_id,
        observations_dir=observations_dir,
        sources_dir=sources_dir,
        observed_at=observed_at,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch bounded official provider model lists")
    parser.add_argument("--provider", required=True, choices=(*SUPPORTED_PROVIDERS, "all"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sources-dir", type=Path, default=Path("sources"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    providers = SUPPORTED_PROVIDERS if args.provider == "all" else (args.provider,)
    observed_at = _timestamp()
    observed = 0
    failures: list[str] = []
    for provider_id in providers:
        try:
            policy, _ = load_policy(args.sources_dir, provider_id)
            if policy["discovery"]["kind"] != "official_api":
                continue
            fetch_provider(
                provider_id=provider_id,
                observations_dir=args.output_dir,
                sources_dir=args.sources_dir,
                observed_at=observed_at,
            )
            observed += 1
        except CatalogError as error:
            failures.append(provider_id)
            print(f"{provider_id} observation failed: {error}")
    if failures:
        print(f"provider observation failed for: {', '.join(failures)}")
        return 1
    print(f"recorded {observed} official provider observations at {observed_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
