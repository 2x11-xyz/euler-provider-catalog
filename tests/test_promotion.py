from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from catalog_pipeline.common import (
    CatalogError,
    catalog_release_id,
    canonical_json_bytes,
    sha256_hex,
)
from catalog_pipeline.promotion import (
    PromotionPolicy,
    classify_promotion,
    load_promotion_policy,
    main as promotion_main,
)
from catalog_pipeline.promotion_contract import validate_promotion_diff
from catalog_pipeline.release import ReleaseArtifacts, load_release


ROOT = Path(__file__).resolve().parents[1]


def changed_release(
    base: ReleaseArtifacts,
    *,
    catalog_change: Callable[[dict[str, Any]], None] | None = None,
    provenance_change: Callable[[dict[str, Any]], None] | None = None,
) -> ReleaseArtifacts:
    catalog = copy.deepcopy(base.catalog)
    provenance = copy.deepcopy(base.provenance)
    manifest = copy.deepcopy(base.manifest)
    if catalog_change:
        catalog_change(catalog)
    if provenance_change:
        provenance_change(provenance)
    catalog_bytes = canonical_json_bytes(catalog)
    provenance_bytes = canonical_json_bytes(provenance)
    manifest["artifacts"] = {
        "catalog-v1.json": {
            "bytes": len(catalog_bytes),
            "sha256": sha256_hex(catalog_bytes),
        },
        "provenance-v1.json": {
            "bytes": len(provenance_bytes),
            "sha256": sha256_hex(provenance_bytes),
        },
    }
    manifest["release_id"] = catalog_release_id(
        generated_at=manifest["generated_at"],
        minimum_euler_version=manifest["minimum_euler_version"],
        artifacts=manifest["artifacts"],
    )
    return ReleaseArtifacts(
        manifest=manifest,
        catalog=catalog,
        provenance=provenance,
        encoded={
            "catalog-v1.json": catalog_bytes,
            "manifest-v1.json": canonical_json_bytes(manifest),
            "provenance-v1.json": provenance_bytes,
        },
    )


class PromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release = load_release(ROOT / "fixtures" / "expected")
        cls.policy = load_promotion_policy(ROOT / "promotion-policy.json")

    def test_identical_catalog_requires_no_promotion(self) -> None:
        candidate = changed_release(self.release)
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "no_change")
        self.assertEqual(diff["reasons"], [])

    def test_addition_only_candidate_is_separately_classified(self) -> None:
        def add_model(catalog: dict[str, Any]) -> None:
            provider = catalog["providers"]["openrouter"]
            model = copy.deepcopy(provider["models"][0])
            model["id"] = "example/new-model"
            model["display_name"] = "Example New Model"
            provider["models"].append(model)
            provider["models"].sort(key=lambda item: item["id"])

        candidate = changed_release(self.release, catalog_change=add_model)
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "addition_only")
        self.assertEqual(diff["reasons"], ["model_addition"])
        self.assertEqual(diff["providers"]["openrouter"]["models_added"], ["example/new-model"])

    def test_metadata_and_lifecycle_changes_require_review(self) -> None:
        def change_models(catalog: dict[str, Any]) -> None:
            models = catalog["providers"]["openrouter"]["models"]
            kimi = next(model for model in models if model["id"] == "moonshotai/kimi-k3")
            kimi["context_window_tokens"] += 1
            kimi["status"] = "deprecated"

        candidate = changed_release(self.release, catalog_change=change_models)
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "review_required")
        self.assertIn("model_lifecycle_changed", diff["reasons"])
        self.assertIn("model_metadata_changed", diff["reasons"])
        provider = diff["providers"]["openrouter"]
        self.assertEqual(provider["metadata_changes"][0]["fields"], ["context_window_tokens"])

    def test_small_removal_requires_review(self) -> None:
        def remove_model(catalog: dict[str, Any]) -> None:
            provider = catalog["providers"]["xai"]
            provider["models"] = [
                model for model in provider["models"] if model["id"] != "grok-build-0.1"
            ]

        candidate = changed_release(self.release, catalog_change=remove_model)
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "review_required")
        self.assertEqual(diff["providers"]["xai"]["shrink_basis_points"], 910)
        self.assertEqual(diff["providers"]["xai"]["models_removed"], ["grok-build-0.1"])

    def test_excessive_provider_shrink_is_blocked(self) -> None:
        def remove_model(catalog: dict[str, Any]) -> None:
            provider = catalog["providers"]["anthropic"]
            provider["models"] = [
                model for model in provider["models"] if model["id"] != "claude-haiku-4-5"
            ]

        candidate = changed_release(self.release, catalog_change=remove_model)
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "blocked")
        self.assertIn("excessive_shrink", diff["reasons"])
        self.assertEqual(diff["providers"]["anthropic"]["shrink_basis_points"], 3334)

    def test_governed_input_change_prevents_addition_only_classification(self) -> None:
        def add_model(catalog: dict[str, Any]) -> None:
            provider = catalog["providers"]["openrouter"]
            model = copy.deepcopy(provider["models"][0])
            model["id"] = "example/new-model"
            provider["models"].append(model)
            provider["models"].sort(key=lambda item: item["id"])

        def change_source(provenance: dict[str, Any]) -> None:
            inputs = provenance["providers"]["openrouter"]["inputs"]
            source = next(item for item in inputs if item["kind"] == "source_policy")
            source["sha256"] = "f" * 64

        candidate = changed_release(
            self.release,
            catalog_change=add_model,
            provenance_change=change_source,
        )
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "review_required")
        self.assertIn("source_policy_changed", diff["reasons"])

    def test_governed_input_change_requires_review_when_catalog_is_unchanged(self) -> None:
        def change_source(provenance: dict[str, Any]) -> None:
            inputs = provenance["providers"]["openrouter"]["inputs"]
            source = next(item for item in inputs if item["kind"] == "source_policy")
            source["sha256"] = "f" * 64

        candidate = changed_release(self.release, provenance_change=change_source)
        diff = classify_promotion(self.release, candidate, self.policy)
        self.assertEqual(diff["decision"], "review_required")
        self.assertEqual(diff["reasons"], ["source_policy_changed"])

    def test_bootstrap_always_requires_review(self) -> None:
        diff = classify_promotion(None, self.release, self.policy)
        self.assertEqual(diff["decision"], "review_required")
        self.assertIn("bootstrap", diff["reasons"])
        self.assertIsNone(diff["from_release_id"])

    def test_release_loader_rejects_tampering_and_extra_files(self) -> None:
        for mutation, message in (
            ("tamper", "canonical JSON"),
            ("extra", "exactly"),
            ("release_id", "does not authenticate"),
            ("minimum_version", "does not authenticate"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                candidate = Path(temporary) / "candidate"
                shutil.copytree(ROOT / "fixtures" / "expected", candidate)
                if mutation == "tamper":
                    path = candidate / "catalog-v1.json"
                    path.write_bytes(path.read_bytes() + b"\n")
                else:
                    if mutation == "extra":
                        (candidate / "unexpected.json").write_text("{}\n")
                    else:
                        path = candidate / "manifest-v1.json"
                        manifest = json.loads(path.read_bytes())
                        if mutation == "release_id":
                            manifest["release_id"] = "catalog-v1-20300102t030405z-" + "d" * 64
                        else:
                            manifest["minimum_euler_version"] = "9.9.9"
                        path.write_bytes(canonical_json_bytes(manifest))
                with self.assertRaisesRegex(CatalogError, message):
                    load_release(candidate)

    def test_release_loader_rejects_malformed_provenance_cleanly(self) -> None:
        def corrupt_provenance(provenance: dict[str, Any]) -> None:
            provenance["providers"]["openrouter"]["inputs"].append("not-an-input")

        release = changed_release(self.release, provenance_change=corrupt_provenance)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            candidate.mkdir()
            for name, data in release.encoded.items():
                (candidate / name).write_bytes(data)
            with self.assertRaisesRegex(CatalogError, "must be an object"):
                load_release(candidate)

    def test_stable_loader_requires_the_matching_review_diff(self) -> None:
        diff = classify_promotion(None, self.release, self.policy)
        with tempfile.TemporaryDirectory() as temporary:
            stable = Path(temporary) / "stable"
            stable.mkdir()
            for name, data in self.release.encoded.items():
                (stable / name).write_bytes(data)
            (stable / "diff-v1.json").write_bytes(canonical_json_bytes(diff))
            loaded = load_release(stable, stable=True)
            self.assertEqual(loaded.manifest["release_id"], self.release.manifest["release_id"])

            diff["to_release_id"] = "catalog-v1-20300102t030405z-" + "d" * 64
            (stable / "diff-v1.json").write_bytes(canonical_json_bytes(diff))
            with self.assertRaisesRegex(CatalogError, "does not identify"):
                load_release(stable, stable=True)

            diff = classify_promotion(None, self.release, self.policy)
            del diff["reasons"]
            (stable / "diff-v1.json").write_bytes(canonical_json_bytes(diff))
            with self.assertRaisesRegex(CatalogError, "invalid shape"):
                load_release(stable, stable=True)

    def test_diff_validator_rejects_semantic_tampering(self) -> None:
        baseline = classify_promotion(None, self.release, self.policy)
        for mutation, message in (
            ("decision", "decision disagrees"),
            ("reasons", "reasons disagree"),
            ("counts", "model counts disagree"),
        ):
            with self.subTest(mutation=mutation):
                diff = copy.deepcopy(baseline)
                if mutation == "decision":
                    diff["decision"] = "no_change"
                elif mutation == "reasons":
                    diff["reasons"] = []
                else:
                    diff["providers"]["openrouter"]["after_model_count"] += 1
                with self.assertRaisesRegex(CatalogError, message):
                    validate_promotion_diff(diff)

    def test_release_loader_rejects_unsafe_provenance_paths(self) -> None:
        for unsafe_path in ("../../secrets.json", "observations//openrouter/models.json"):
            with self.subTest(path=unsafe_path):

                def corrupt_path(provenance: dict[str, Any]) -> None:
                    inputs = provenance["providers"]["openrouter"]["inputs"]
                    observation = next(item for item in inputs if item["kind"] == "official_api")
                    observation["path"] = unsafe_path

                release = changed_release(self.release, provenance_change=corrupt_path)
                with tempfile.TemporaryDirectory() as temporary:
                    candidate = Path(temporary) / "candidate"
                    candidate.mkdir()
                    for name, data in release.encoded.items():
                        (candidate / name).write_bytes(data)
                    with self.assertRaisesRegex(CatalogError, "provenance input is invalid"):
                        load_release(candidate)

    def test_blocked_cli_returns_nonzero_and_retains_the_diff(self) -> None:
        def remove_model(catalog: dict[str, Any]) -> None:
            provider = catalog["providers"]["anthropic"]
            provider["models"] = [
                model for model in provider["models"] if model["id"] != "claude-haiku-4-5"
            ]

        def update_count(provenance: dict[str, Any]) -> None:
            provenance["providers"]["anthropic"]["published_model_count"] -= 1

        candidate_release = changed_release(
            self.release,
            catalog_change=remove_model,
            provenance_change=update_count,
        )
        baseline_diff = classify_promotion(None, self.release, self.policy)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stable = root / "stable"
            candidate = root / "candidate"
            output = root / "review"
            stable.mkdir()
            candidate.mkdir()
            for name, data in self.release.encoded.items():
                (stable / name).write_bytes(data)
            (stable / "diff-v1.json").write_bytes(canonical_json_bytes(baseline_diff))
            for name, data in candidate_release.encoded.items():
                (candidate / name).write_bytes(data)
            arguments = [
                "promotion",
                "--candidate-dir",
                str(candidate),
                "--previous-dir",
                str(stable),
                "--policy",
                str(ROOT / "promotion-policy.json"),
                "--output-dir",
                str(output),
            ]
            with patch("sys.argv", arguments), patch("builtins.print"):
                self.assertEqual(promotion_main(), 1)
            diff = json.loads((output / "diff-v1.json").read_bytes())
            self.assertEqual(diff["decision"], "blocked")

    def test_invalid_promotion_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "promotion-policy.json"
            path.write_text(
                json.dumps(
                    {"schema_version": 1, "maximum_shrink_basis_points": 10001},
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            with self.assertRaisesRegex(CatalogError, "shrink limit"):
                load_promotion_policy(path)

    def test_classification_is_deterministic(self) -> None:
        first = classify_promotion(None, self.release, self.policy)
        second = classify_promotion(None, self.release, self.policy)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))

    def test_policy_value_is_carried_into_the_diff(self) -> None:
        policy = PromotionPolicy(maximum_shrink_basis_points=500, sha256="a" * 64)
        diff = classify_promotion(None, self.release, policy)
        self.assertEqual(diff["promotion_policy"]["maximum_shrink_basis_points"], 500)
        self.assertEqual(diff["promotion_policy"]["sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
