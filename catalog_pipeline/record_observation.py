from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .common import CatalogError, atomic_write
from .config import SUPPORTED_PROVIDERS, load_policy
from .observation import sidecar_bytes


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record(
    *,
    provider_id: str,
    observations_dir: Path,
    sources_dir: Path,
    observed_at: str,
) -> None:
    policy, _ = load_policy(sources_dir, provider_id)
    if policy["discovery"]["kind"] != "official_api":
        raise CatalogError(f"{provider_id} has no official API observation")
    provider_dir = observations_dir / provider_id
    data = sidecar_bytes(provider_dir, policy, observed_at)
    atomic_write(provider_dir / "observation.json", data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record digests for provider API response files")
    parser.add_argument("--provider", required=True, choices=(*SUPPORTED_PROVIDERS, "all"))
    parser.add_argument("--observations-dir", type=Path, required=True)
    parser.add_argument("--sources-dir", type=Path, default=Path("sources"))
    parser.add_argument("--observed-at", default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    observed_at = args.observed_at or _now()
    providers = SUPPORTED_PROVIDERS if args.provider == "all" else (args.provider,)
    try:
        recorded = 0
        for provider_id in providers:
            policy, _ = load_policy(args.sources_dir, provider_id)
            if policy["discovery"]["kind"] != "official_api":
                continue
            record(
                provider_id=provider_id,
                observations_dir=args.observations_dir,
                sources_dir=args.sources_dir,
                observed_at=observed_at,
            )
            recorded += 1
    except CatalogError as error:
        print(f"observation recording failed: {error}")
        return 1
    print(f"recorded {recorded} provider observations at {observed_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
