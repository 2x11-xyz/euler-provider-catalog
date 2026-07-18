from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common import CatalogError, canonical_json_bytes, write_or_check
from .promotion import classify_promotion
from .promotion_contract import load_promotion_policy
from .release import load_release


NO_CHANGE_EXIT = 3


def prepare_stable(
    *, candidate_dir: Path, previous_dir: Path | None, policy_path: Path
) -> tuple[dict[str, bytes], dict[str, object]]:
    candidate = load_release(candidate_dir)
    previous = load_release(previous_dir, stable=True) if previous_dir else None
    policy = load_promotion_policy(policy_path)
    diff = classify_promotion(previous, candidate, policy)
    if diff["decision"] == "blocked":
        raise CatalogError("blocked candidate cannot become stable state")
    outputs = dict(candidate.encoded)
    outputs["diff-v1.json"] = canonical_json_bytes(diff)
    return outputs, diff


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare reviewed stable catalog state")
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--previous-dir", type=Path)
    parser.add_argument("--policy", type=Path, default=Path("promotion-policy.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        outputs, diff = prepare_stable(
            candidate_dir=args.candidate_dir,
            previous_dir=args.previous_dir,
            policy_path=args.policy,
        )
        if diff["decision"] == "no_change":
            print("promotion decision: no_change")
            return NO_CHANGE_EXIT
        write_or_check(args.output_dir, outputs, check=args.check)
        load_release(args.output_dir, stable=True)
    except CatalogError as error:
        print(f"stable preparation failed: {error}", file=sys.stderr)
        return 1
    action = "verified" if args.check else "prepared"
    print(f"{action} stable catalog: {diff['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
