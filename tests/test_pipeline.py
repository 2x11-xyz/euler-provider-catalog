from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog_pipeline.common import CatalogError, canonical_json_bytes, write_or_check
from catalog_pipeline.config import SUPPORTED_PROVIDERS, load_policy
from catalog_pipeline.generate import generate_artifacts
from catalog_pipeline.observation import sidecar_bytes
from catalog_pipeline.record_observation import main as record_observations


ROOT = Path(__file__).resolve().parents[1]


def generate(
    fixtures: Path = ROOT / "fixtures",
    sources: Path = ROOT / "sources",
    curated: Path = ROOT / "curated",
):
    return generate_artifacts(
        observations_dir=fixtures,
        sources_dir=sources,
        curated_dir=curated,
    )


def refresh_sidecar(fixtures: Path, provider_id: str) -> None:
    policy, _ = load_policy(ROOT / "sources", provider_id)
    sidecar = sidecar_bytes(fixtures / provider_id, policy, "2026-07-18T00:00:00Z")
    (fixtures / provider_id / "observation.json").write_bytes(sidecar)


class PipelineTests(unittest.TestCase):
    def test_checked_in_artifacts_are_byte_identical(self) -> None:
        artifacts = generate()
        for name, expected in artifacts.encoded.items():
            self.assertEqual((ROOT / "fixtures" / "expected" / name).read_bytes(), expected, name)

    def test_check_rejects_unexpected_output_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "expected"
            shutil.copytree(ROOT / "fixtures" / "expected", output)
            (output / "obsolete-v1.json").write_text("{}\n")
            with self.assertRaisesRegex(CatalogError, "unexpected entries"):
                write_or_check(output, generate().encoded, check=True)

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
            refresh_sidecar(fixtures, "openrouter")
            models = generate(fixtures).documents["catalog-v1.json"]["providers"]["openrouter"][
                "models"
            ]
            auto = [model for model in models if model["id"] == "openrouter/auto"]
            self.assertEqual(len(auto), 1)
            self.assertEqual(auto[0]["max_output_tokens"], 4096)

    def test_openai_requires_reviewed_official_metadata(self) -> None:
        artifacts = generate().documents
        models = {
            model["id"] for model in artifacts["catalog-v1.json"]["providers"]["openai"]["models"]
        }
        self.assertNotIn("text-embedding-example", models)
        provenance = artifacts["provenance-v1.json"]["providers"]["openai"]
        self.assertEqual(provenance["skipped"]["not_reviewed_for_euler"], 1)

    def test_generation_rejects_non_provider_owned_openai_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "openai" / "models.json"
            payload = json.loads(path.read_bytes())
            target = next(model for model in payload["data"] if model["id"] == "gpt-5.4")
            target["owned_by"] = "org-123"
            path.write_bytes(canonical_json_bytes(payload))
            refresh_sidecar(fixtures, "openai")
            with self.assertRaisesRegex(CatalogError, "public provider-owned"):
                generate(fixtures)

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

    def test_generation_rejects_non_provider_owned_xai_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "xai" / "language-models.json"
            payload = json.loads(path.read_bytes())
            payload["models"][0]["owned_by"] = "org-123"
            path.write_bytes(canonical_json_bytes(payload))
            refresh_sidecar(fixtures, "xai")
            with self.assertRaisesRegex(CatalogError, "public provider-owned"):
                generate(fixtures)

    def test_xai_primary_ids_take_precedence_over_earlier_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            details_path = fixtures / "xai" / "models.json"
            details = json.loads(details_path.read_bytes())
            details["data"].append(
                {
                    "id": "example-primary",
                    "aliases": [],
                    "context_length": 64000,
                    "object": "model",
                    "owned_by": "xai",
                }
            )
            details_path.write_bytes(canonical_json_bytes(details))
            language_path = fixtures / "xai" / "language-models.json"
            language = json.loads(language_path.read_bytes())
            language["models"][0]["aliases"].append("example-primary")
            language["models"].append(
                {
                    "id": "example-primary",
                    "aliases": [],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "object": "model",
                    "owned_by": "xai",
                }
            )
            language_path.write_bytes(canonical_json_bytes(language))
            refresh_sidecar(fixtures, "xai")

            artifacts = generate(fixtures).documents
            models = {
                model["id"]: model
                for model in artifacts["catalog-v1.json"]["providers"]["xai"]["models"]
            }
            self.assertEqual(models["example-primary"]["display_name"], "example-primary")
            provenance = artifacts["provenance-v1.json"]["providers"]["xai"]
            self.assertEqual(provenance["skipped"]["alias_collides_with_primary_id"], 1)

    def test_chatgpt_membership_is_curated_and_context_is_officially_observed(self) -> None:
        artifacts = generate().documents
        models = {
            model["id"]: model
            for model in artifacts["catalog-v1.json"]["providers"]["chatgpt"]["models"]
        }
        for model_id in ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"):
            self.assertEqual(models[model_id]["context_window_tokens"], 272000)
        self.assertEqual(models["gpt-5.3-codex-spark"]["context_window_tokens"], 128000)
        self.assertNotIn("gpt-5.2", models)
        provenance = artifacts["provenance-v1.json"]["providers"]["chatgpt"]
        self.assertEqual(provenance["discovery_kind"], "official_snapshot")
        self.assertEqual(provenance["observed_model_count"], 8)
        self.assertEqual(provenance["published_model_count"], 7)
        self.assertEqual(provenance["skipped"]["not_curated_for_euler"], 2)
        self.assertTrue(any("1 reviewed ChatGPT route" in item for item in provenance["warnings"]))

    def test_chatgpt_official_context_overrides_reviewed_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            curated = Path(temporary) / "curated"
            shutil.copytree(ROOT / "fixtures", fixtures)
            shutil.copytree(ROOT / "curated", curated)
            curated_path = curated / "chatgpt.json"
            reviewed = json.loads(curated_path.read_bytes())
            target = next(model for model in reviewed["models"] if model["id"] == "gpt-5.6-sol")
            target["context_window_tokens"] = 999999
            curated_path.write_bytes(canonical_json_bytes(reviewed))

            artifacts = generate(fixtures=fixtures, curated=curated).documents
            models = {
                model["id"]: model
                for model in artifacts["catalog-v1.json"]["providers"]["chatgpt"]["models"]
            }
            self.assertEqual(models["gpt-5.6-sol"]["context_window_tokens"], 272000)
            warnings = artifacts["provenance-v1.json"]["providers"]["chatgpt"]["warnings"]
            self.assertTrue(any("1 reviewed ChatGPT context value" in item for item in warnings))

    def test_chatgpt_invalid_context_for_reviewed_route_fails_closed(self) -> None:
        for context, maximum in ((0, 272000), (300000, 272000)):
            with self.subTest(context=context), tempfile.TemporaryDirectory() as temporary:
                fixtures = Path(temporary) / "fixtures"
                shutil.copytree(ROOT / "fixtures", fixtures)
                path = fixtures / "chatgpt" / "models.json"
                payload = json.loads(path.read_bytes())
                target = next(
                    model for model in payload["models"] if model["slug"] == "gpt-5.6-sol"
                )
                target["context_window"] = context
                target["max_context_window"] = maximum
                path.write_bytes(canonical_json_bytes(payload))
                refresh_sidecar(fixtures, "chatgpt")
                with self.assertRaisesRegex(CatalogError, "invalid official context metadata"):
                    generate(fixtures)

    def test_chatgpt_allowed_unobserved_ids_must_be_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources"
            shutil.copytree(ROOT / "sources", sources)
            path = sources / "chatgpt.json"
            policy = json.loads(path.read_bytes())
            policy["filters"]["allowed_unobserved_model_ids"].append("not-reviewed")
            path.write_bytes(canonical_json_bytes(policy))
            with self.assertRaisesRegex(CatalogError, "must be reviewed routes"):
                generate(sources=sources)

    def test_chatgpt_malformed_record_is_counted_without_becoming_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "chatgpt" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["models"].append(None)
            path.write_bytes(canonical_json_bytes(payload))
            refresh_sidecar(fixtures, "chatgpt")
            provenance = generate(fixtures).documents["provenance-v1.json"]["providers"]["chatgpt"]
            self.assertEqual(provenance["observed_model_count"], 9)
            self.assertEqual(provenance["skipped"]["malformed_record"], 1)

    def test_chatgpt_unexpected_missing_reviewed_route_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "chatgpt" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["models"] = [
                model for model in payload["models"] if model["slug"] != "gpt-5.6-sol"
            ]
            path.write_bytes(canonical_json_bytes(payload))
            refresh_sidecar(fixtures, "chatgpt")
            with self.assertRaisesRegex(CatalogError, "gpt-5.6-sol"):
                generate(fixtures)

    def test_chatgpt_duplicate_slug_fails_before_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "chatgpt" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["models"].extend(
                [
                    {
                        "slug": "future-duplicate",
                        "context_window": 0,
                        "max_context_window": 272000,
                    },
                    {
                        "slug": "future-duplicate",
                        "context_window": 272000,
                        "max_context_window": 272000,
                    },
                ]
            )
            path.write_bytes(canonical_json_bytes(payload))
            refresh_sidecar(fixtures, "chatgpt")
            with self.assertRaisesRegex(CatalogError, "repeats model id future-duplicate"):
                generate(fixtures)

    def test_anthropic_reasoning_matches_the_adaptive_effort_adapter(self) -> None:
        models = {
            model["id"]: model
            for model in generate().documents["catalog-v1.json"]["providers"]["anthropic"]["models"]
        }
        self.assertFalse(models["claude-haiku-4-5"]["supports_reasoning"])
        self.assertEqual(models["claude-haiku-4-5"]["reasoning_efforts"], [])
        self.assertTrue(models["claude-sonnet-5"]["supports_reasoning"])

    def test_empty_openrouter_reasoning_metadata_is_not_capability_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            path = fixtures / "openrouter" / "models.json"
            payload = json.loads(path.read_bytes())
            payload["data"].append(
                {
                    "id": "example/empty-reasoning",
                    "name": "Empty Reasoning Metadata",
                    "context_length": 32000,
                    "architecture": {"output_modalities": ["text"]},
                    "supported_parameters": ["tools"],
                    "reasoning": {},
                }
            )
            path.write_bytes(canonical_json_bytes(payload))
            refresh_sidecar(fixtures, "openrouter")
            models = {
                model["id"]: model
                for model in generate(fixtures).documents["catalog-v1.json"]["providers"][
                    "openrouter"
                ]["models"]
            }
            self.assertFalse(models["example/empty-reasoning"]["supports_reasoning"])
            self.assertEqual(models["example/empty-reasoning"]["reasoning_efforts"], [])

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
            refresh_sidecar(fixtures, "openai")
            with self.assertRaisesRegex(CatalogError, "default model is not active"):
                generate(fixtures)

    def test_paginated_anthropic_observation_fails_closed(self) -> None:
        for has_more in (True, "true"):
            with self.subTest(has_more=has_more), tempfile.TemporaryDirectory() as temporary:
                fixtures = Path(temporary) / "fixtures"
                shutil.copytree(ROOT / "fixtures", fixtures)
                path = fixtures / "anthropic" / "models.json"
                payload = json.loads(path.read_bytes())
                payload["has_more"] = has_more
                path.write_bytes(canonical_json_bytes(payload))
                refresh_sidecar(fixtures, "anthropic")
                with self.assertRaisesRegex(CatalogError, "paginated beyond"):
                    generate(fixtures)

    def test_curated_aliases_obey_model_id_invariants(self) -> None:
        for alias, message in (
            ("bad alias with spaces", "valid Euler model id"),
            ("gpt-5.5", "aliases duplicate model ids"),
        ):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as temporary:
                curated = Path(temporary) / "curated"
                shutil.copytree(ROOT / "curated", curated)
                path = curated / "openai.json"
                payload = json.loads(path.read_bytes())
                payload["aliases"] = [alias]
                path.write_bytes(canonical_json_bytes(payload))
                with self.assertRaisesRegex(CatalogError, message):
                    generate(curated=curated)

    def test_malformed_source_policy_fails_with_catalog_error(self) -> None:
        for missing_field in ("filters", "reasoning_effort_map", "default_reasoning_efforts"):
            with self.subTest(field=missing_field), tempfile.TemporaryDirectory() as temporary:
                sources = Path(temporary) / "sources"
                shutil.copytree(ROOT / "sources", sources)
                path = sources / "openrouter.json"
                payload = json.loads(path.read_bytes())
                del payload[missing_field]
                path.write_bytes(canonical_json_bytes(payload))
                with self.assertRaises(CatalogError):
                    load_policy(sources, "openrouter")

    def test_official_snapshot_discovery_is_chatgpt_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources"
            shutil.copytree(ROOT / "sources", sources)
            path = sources / "anthropic.json"
            policy = json.loads(path.read_bytes())
            policy["normalizer"] = "chatgpt"
            policy["discovery"]["kind"] = "official_snapshot"
            policy["filters"] = {"allowed_unobserved_model_ids": []}
            path.write_bytes(canonical_json_bytes(policy))
            with self.assertRaisesRegex(CatalogError, "reserved for the chatgpt provider"):
                load_policy(sources, "anthropic")

    def test_record_all_continues_after_one_provider_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(ROOT / "fixtures", fixtures)
            (fixtures / "anthropic" / "models.json").unlink()
            observed_at = "2030-01-02T03:04:05Z"
            arguments = [
                "record_observation",
                "--provider",
                "all",
                "--observations-dir",
                str(fixtures),
                "--sources-dir",
                str(ROOT / "sources"),
                "--observed-at",
                observed_at,
            ]
            with patch("sys.argv", arguments), patch("builtins.print"):
                self.assertEqual(record_observations(), 1)
            for provider_id in ("chatgpt", "openai", "openrouter", "xai"):
                sidecar = json.loads((fixtures / provider_id / "observation.json").read_bytes())
                self.assertEqual(sidecar["observed_at"], observed_at)

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
