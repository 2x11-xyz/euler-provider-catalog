from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import CatalogError, OBSERVED_DISCOVERY_KINDS, atomic_write
from .config import SUPPORTED_PROVIDERS, load_policy
from .observation import sidecar_bytes


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record(
    *,
    policy: dict[str, Any],
    observations_dir: Path,
    observed_at: str,
) -> None:
    provider_id = policy["provider_id"]
    if policy["discovery"]["kind"] not in OBSERVED_DISCOVERY_KINDS:
        raise CatalogError(f"{provider_id} has no official structured observation")
    provider_dir = observations_dir / provider_id
    data = sidecar_bytes(provider_dir, policy, observed_at)
    atomic_write(provider_dir / "observation.json", data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record digests for official provider source files"
    )
    parser.add_argument("--provider", required=True, choices=(*SUPPORTED_PROVIDERS, "all"))
    parser.add_argument("--observations-dir", type=Path, required=True)
    parser.add_argument("--sources-dir", type=Path, default=Path("sources"))
    parser.add_argument("--observed-at", default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    observed_at = args.observed_at or _now()
    providers = SUPPORTED_PROVIDERS if args.provider == "all" else (args.provider,)
    recorded = 0
    failures: list[str] = []
    for provider_id in providers:
        try:
            policy, _ = load_policy(args.sources_dir, provider_id)
            if policy["discovery"]["kind"] not in OBSERVED_DISCOVERY_KINDS:
                continue
            record(
                policy=policy,
                observations_dir=args.observations_dir,
                observed_at=observed_at,
            )
            recorded += 1
        except CatalogError as error:
            failures.append(provider_id)
            print(f"{provider_id} observation recording failed: {error}")
    if failures:
        print(f"observation recording failed for: {', '.join(failures)}")
        return 1
    print(f"recorded {recorded} official source observations at {observed_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
