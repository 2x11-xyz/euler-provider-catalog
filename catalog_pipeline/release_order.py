from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from .common import CatalogError
from .release import ReleaseArtifacts, load_release


ReleaseOrder = Literal["newer", "same", "stale"]


def compare_releases(candidate: ReleaseArtifacts, baseline: ReleaseArtifacts) -> ReleaseOrder:
    if candidate.manifest["release_id"] == baseline.manifest["release_id"]:
        return "same"
    candidate_at = datetime.fromisoformat(candidate.manifest["generated_at"][:-1] + "+00:00")
    baseline_at = datetime.fromisoformat(baseline.manifest["generated_at"][:-1] + "+00:00")
    return "newer" if candidate_at > baseline_at else "stale"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two stable catalog releases")
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        candidate = load_release(args.candidate_dir, stable=True)
        baseline = load_release(args.baseline_dir, stable=True)
        order = compare_releases(candidate, baseline)
    except CatalogError as error:
        print(f"release comparison failed: {error}", file=sys.stderr)
        return 1
    print(order)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
