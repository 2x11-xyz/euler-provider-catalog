from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common import CatalogError
from .release import load_release


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify catalog release artifacts")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--stable", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        release = load_release(args.directory, stable=args.stable)
    except CatalogError as error:
        print(f"release verification failed: {error}", file=sys.stderr)
        return 1
    print(f"verified catalog release {release.manifest['release_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
