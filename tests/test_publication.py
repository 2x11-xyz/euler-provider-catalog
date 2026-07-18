from __future__ import annotations

import json
import copy
import tempfile
import unittest
from pathlib import Path

from catalog_pipeline.bootstrap import generate_bootstrap_artifacts
from catalog_pipeline.common import (
    CatalogError,
    canonical_json_bytes,
    catalog_release_id,
    sha256_hex,
)
from catalog_pipeline.prepare_stable import NO_CHANGE_EXIT, prepare_stable
from catalog_pipeline.release import RUNTIME_ARTIFACTS, load_release
from catalog_pipeline.release_order import compare_releases


ROOT = Path(__file__).resolve().parents[1]


def write_artifacts(directory: Path, artifacts: dict[str, bytes]) -> None:
    directory.mkdir()
    for name, data in artifacts.items():
        (directory / name).write_bytes(data)


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bootstrap = generate_bootstrap_artifacts(
            bootstrap_dir=ROOT / "bootstrap",
        )

    def test_bootstrap_is_a_complete_reviewed_euler_snapshot(self) -> None:
        providers = self.bootstrap.documents["catalog-v1.json"]["providers"]
        self.assertEqual(tuple(providers), ("anthropic", "chatgpt", "openai", "openrouter", "xai"))
        self.assertIn(
            "moonshotai/kimi-k3",
            {model["id"] for model in providers["openrouter"]["models"]},
        )
        provenance = self.bootstrap.documents["provenance-v1.json"]
        for provider in provenance["providers"].values():
            self.assertEqual(provider["discovery_kind"], "bootstrap")
            self.assertEqual(provider["observed_model_count"], 0)
            self.assertEqual(provider["curated_model_count"], 0)
            self.assertTrue(
                any(
                    "no provider API observation is claimed" in item
                    for item in provider["warnings"]
                )
            )

    def test_bootstrap_prepares_a_strict_stable_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            stable = root / "stable"
            write_artifacts(candidate, self.bootstrap.encoded)
            outputs, diff = prepare_stable(
                candidate_dir=candidate,
                previous_dir=None,
                policy_path=ROOT / "promotion-policy.json",
            )
            self.assertEqual(diff["decision"], "review_required")
            write_artifacts(stable, outputs)
            release = load_release(stable, stable=True)
            self.assertEqual(release.manifest, self.bootstrap.documents["manifest-v1.json"])

    def test_unchanged_candidate_is_not_materialized_as_a_new_stable_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / "previous"
            candidate = root / "candidate"
            write_artifacts(
                previous, {path.name: path.read_bytes() for path in (ROOT / "stable").iterdir()}
            )
            write_artifacts(
                candidate,
                {name: (ROOT / "stable" / name).read_bytes() for name in RUNTIME_ARTIFACTS},
            )
            _, diff = prepare_stable(
                candidate_dir=candidate,
                previous_dir=previous,
                policy_path=ROOT / "promotion-policy.json",
            )
            self.assertEqual(diff["decision"], "no_change")
            self.assertNotEqual(NO_CHANGE_EXIT, 2)

    def test_pending_release_order_never_regresses_the_bot_branch(self) -> None:
        stable = load_release(ROOT / "stable", stable=True)
        older = load_release(ROOT / "fixtures" / "expected")
        self.assertEqual(compare_releases(stable, stable), "same")
        self.assertEqual(compare_releases(stable, older), "newer")
        self.assertEqual(compare_releases(older, stable), "stale")

    def test_blocked_candidate_cannot_be_prepared_as_stable(self) -> None:
        with self.assertRaisesRegex(CatalogError, "blocked candidate"):
            prepare_stable(
                candidate_dir=ROOT / "fixtures" / "expected",
                previous_dir=ROOT / "stable",
                policy_path=ROOT / "promotion-policy.json",
            )

    def test_bootstrap_provenance_cannot_claim_official_observations(self) -> None:
        documents = copy.deepcopy(self.bootstrap.documents)
        provenance = documents["provenance-v1.json"]
        provenance["providers"]["anthropic"]["inputs"].append(
            {
                "bytes": 1,
                "kind": "official_api",
                "path": "observations/anthropic/models.json",
                "sha256": "a" * 64,
                "source_url": "https://api.anthropic.com/v1/models",
            }
        )
        provenance["providers"]["anthropic"]["inputs"].sort(key=lambda item: item["path"])
        provenance_bytes = canonical_json_bytes(provenance)
        manifest = documents["manifest-v1.json"]
        manifest["artifacts"]["provenance-v1.json"] = {
            "bytes": len(provenance_bytes),
            "sha256": sha256_hex(provenance_bytes),
        }
        manifest["release_id"] = catalog_release_id(
            generated_at=manifest["generated_at"],
            minimum_euler_version=manifest["minimum_euler_version"],
            artifacts=manifest["artifacts"],
        )
        encoded = {
            "catalog-v1.json": self.bootstrap.encoded["catalog-v1.json"],
            "manifest-v1.json": canonical_json_bytes(manifest),
            "provenance-v1.json": provenance_bytes,
        }
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            write_artifacts(candidate, encoded)
            with self.assertRaisesRegex(CatalogError, "discovery inputs are inconsistent"):
                load_release(candidate)

    def test_bootstrap_metadata_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap = Path(temporary) / "bootstrap"
            bootstrap.mkdir()
            for name in ("catalog-v1.json", "metadata-v1.json"):
                (bootstrap / name).write_bytes((ROOT / "bootstrap" / name).read_bytes())
            metadata_path = bootstrap / "metadata-v1.json"
            metadata = json.loads(metadata_path.read_bytes())
            metadata["source_repository"] = "https://example.invalid/euler"
            metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(CatalogError, "source repository"):
                generate_bootstrap_artifacts(
                    bootstrap_dir=bootstrap,
                )


if __name__ == "__main__":
    unittest.main()
