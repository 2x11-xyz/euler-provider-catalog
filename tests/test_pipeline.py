from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from catalog_pipeline.common import CatalogError, canonical_json_bytes
from catalog_pipeline.config import SUPPORTED_PROVIDERS, load_policy
from catalog_pipeline.generate import generate_artifacts
from catalog_pipeline.observation import sidecar_bytes


ROOT = Path(__file__).resolve().parents[1]


def generate(fixtures: Path = ROOT / "fixtures"):
    return generate_artifacts(
        observations_dir=fixtures,
        sources_dir=ROOT / "sources",
        curated_dir=ROOT / "curated",
    )


class PipelineTests(unittest.TestCase):
    def test_checked_in_artifacts_are_byte_identical(self) -> None:
        artifacts = generate()
        for name, expected in artifacts.encoded.items():
            self.assertEqual((ROOT / "generated" / name).read_bytes(), expected, name)

    def test_catalog_centralizes_every_euler_provider(self) -> None:
        catalog = generate().documents["catalog-v1.json"]
        self.assertEqual(tuple(catalog["providers"]), SUPPORTED_PROVIDERS)
        for provider in catalog["providers"].values():
            ids = [model["id"] for model in provider["models"]]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(len(ids), len(set(ids)))
            self.assertIn(provider["default_model"], ids)

    def test_openrouter_admits_kimi_and_filters_unsupported_records(self) -> None:
        artifacts = generate().documents
        models = {
            model["id"]: model
            for model in artifacts["catalog-v1.json"]["providers"]["openrouter"]["models"]
        }
        self.assertEqual(models["moonshotai/kimi-k3"]["reasoning_efforts"], ["max"])
        self.assertEqual(
            models["thinkingmachines/inkling"]["reasoning_efforts"],
            ["xsmall", "small", "medium", "large", "max"],
        )
        self.assertNotIn("example/no-tools", models)
        self.assertNotIn("example/image-only", models)
        provenance = artifacts["provenance-v1.json"]["providers"]["openrouter"]
        self.assertEqual(provenance["skipped"]["tools_not_supported"], 1)
        self.assertEqual(provenance["skipped"]["text_output_not_supported"], 1)

    def test_curated_router_routes_are_fallbacks_when_api_starts_listing_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "openrouter" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["data"].append(
                {
                    "id": "openrouter/auto",
                    "name": "Auto Router",
                    "context_length": 2000000,
                    "architecture": {"output_modalities": ["text"]},
                    "supported_parameters": ["reasoning", "tools"],
                    "top_provider": {"max_completion_tokens": None},
                }
            )
            path.write_bytes(canonical_json_bytes(payload))
            policy, _ = load_policy(ROOT / "sources", "openrouter")
            sidecar = sidecar_bytes(
                fixtures / "openrouter", policy, "2026-07-18T00:00:00Z"
            )
            (fixtures / "openrouter" / "observation.json").write_bytes(sidecar)
            models = generate(fixtures).documents["catalog-v1.json"]["providers"]["openrouter"][
                "models"
            ]
            auto = [model for model in models if model["id"] == "openrouter/auto"]
            self.assertEqual(len(auto), 1)
            self.assertEqual(auto[0]["max_output_tokens"], 4096)

    def test_openai_requires_reviewed_official_metadata(self) -> None:
        artifacts = generate().documents
        models = {
            model["id"]
            for model in artifacts["catalog-v1.json"]["providers"]["openai"]["models"]
        }
        self.assertNotIn("text-embedding-example", models)
        provenance = artifacts["provenance-v1.json"]["providers"]["openai"]
        self.assertEqual(provenance["skipped"]["not_reviewed_for_euler"], 1)

    def test_xai_lifecycle_and_reasoning_follow_official_docs(self) -> None:
        models = {
            model["id"]: model
            for model in generate().documents["catalog-v1.json"]["providers"]["xai"]["models"]
        }
        self.assertEqual(models["grok-3"]["status"], "deprecated")
        self.assertEqual(models["grok-code-fast-1"]["status"], "deprecated")
        self.assertEqual(
            models["grok-4.20-multi-agent-0309"]["reasoning_efforts"],
            ["small", "medium", "large", "xlarge"],
        )
        self.assertEqual(
            models["grok-4.3-latest"]["reasoning_efforts"],
            models["grok-4.3"]["reasoning_efforts"],
        )
        self.assertEqual(
            models["grok-latest"]["context_window_tokens"],
            models["grok-4.3"]["context_window_tokens"],
        )
        self.assertNotIn("max_output_tokens", models["grok-4.5"])

    def test_chatgpt_is_explicitly_curated(self) -> None:
        provenance = generate().documents["provenance-v1.json"]["providers"]["chatgpt"]
        self.assertEqual(provenance["discovery_kind"], "curated")
        self.assertEqual(provenance["observed_model_count"], 0)
        self.assertEqual(provenance["published_model_count"], 7)

    def test_anthropic_reasoning_matches_the_adaptive_effort_adapter(self) -> None:
        models = {
            model["id"]: model
            for model in generate().documents["catalog-v1.json"]["providers"]["anthropic"][
                "models"
            ]
        }
        self.assertFalse(models["claude-haiku-4-5"]["supports_reasoning"])
        self.assertEqual(models["claude-haiku-4-5"]["reasoning_efforts"], [])
        self.assertTrue(models["claude-sonnet-5"]["supports_reasoning"])

    def test_manifest_authenticates_exact_artifact_bytes(self) -> None:
        artifacts = generate()
        manifest = artifacts.documents["manifest-v1.json"]
        for name in ("catalog-v1.json", "provenance-v1.json"):
            expected = manifest["artifacts"][name]
            data = artifacts.encoded[name]
            self.assertEqual(expected["bytes"], len(data))
            self.assertEqual(expected["sha256"], hashlib.sha256(data).hexdigest())

    def test_identical_inputs_are_deterministic(self) -> None:
        first = generate().encoded
        second = generate().encoded
        self.assertEqual(first, second)

    def test_tampered_observation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "openrouter" / "models.json"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(CatalogError, "does not match its observation digest"):
                generate(fixtures)

    def test_missing_default_fails_even_with_a_valid_observation_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "openai" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["data"] = [model for model in payload["data"] if model["id"] != "gpt-5.5"]
            path.write_bytes(canonical_json_bytes(payload))
            policy, _ = load_policy(ROOT / "sources", "openai")
            sidecar = sidecar_bytes(
                fixtures / "openai", policy, "2026-07-18T00:00:00Z"
            )
            (fixtures / "openai" / "observation.json").write_bytes(sidecar)
            with self.assertRaisesRegex(CatalogError, "default model is not active"):
                generate(fixtures)

    def test_paginated_anthropic_observation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "anthropic" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["has_more"] = True
            path.write_bytes(canonical_json_bytes(payload))
            policy, _ = load_policy(ROOT / "sources", "anthropic")
            sidecar = sidecar_bytes(
                fixtures / "anthropic", policy, "2026-07-18T00:00:00Z"
            )
            (fixtures / "anthropic" / "observation.json").write_bytes(sidecar)
            with self.assertRaisesRegex(CatalogError, "paginated beyond"):
                generate(fixtures)

    def test_runtime_catalog_contains_no_source_or_transport_fields(self) -> None:
        catalog = generate().documents["catalog-v1.json"]
        forbidden = {
            "api_key",
            "auth",
            "base_url",
            "documentation_urls",
            "endpoint",
            "headers",
            "prompt",
            "source_url",
        }

        def visit(value):
            if isinstance(value, dict):
                self.assertTrue(forbidden.isdisjoint(value))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(catalog)

    def test_source_policy_uses_official_https_sources_only(self) -> None:
        for provider_id in SUPPORTED_PROVIDERS:
            policy, _ = load_policy(ROOT / "sources", provider_id)
            discovery = policy["discovery"]
            for url in discovery["documentation_urls"]:
                self.assertTrue(url.startswith("https://"))
            for endpoint in discovery["endpoints"]:
                self.assertTrue(endpoint["url"].startswith("https://"))
        governed_text = "\n".join(
            path.read_text().lower()
            for directory in (ROOT / "README.md", ROOT / "sources")
            for path in ([directory] if directory.is_file() else directory.glob("*"))
            if path.is_file()
        )
        self.assertNotIn("cloudflare", governed_text)


if __name__ == "__main__":
    unittest.main()
