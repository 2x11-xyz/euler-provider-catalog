from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog_pipeline.common import CatalogError
from catalog_pipeline.config import SUPPORTED_PROVIDERS
from catalog_pipeline.fetch import _headers, _public_projection, main as fetch_observations


class FetchPolicyTests(unittest.TestCase):
    def test_all_observation_enumerates_apis_and_official_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            arguments = [
                "fetch",
                "--provider",
                "all",
                "--output-dir",
                str(Path(temporary) / "observations"),
            ]
            with (
                patch("sys.argv", arguments),
                patch("catalog_pipeline.fetch.fetch_provider") as fetch_provider,
                patch("builtins.print"),
            ):
                self.assertEqual(fetch_observations(), 0)
        observed = tuple(
            call.kwargs["policy"]["provider_id"] for call in fetch_provider.call_args_list
        )
        self.assertEqual(len(observed), len(SUPPORTED_PROVIDERS))
        self.assertEqual(set(observed), set(SUPPORTED_PROVIDERS))

    def test_chatgpt_public_catalog_requires_no_authentication(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            headers = _headers("chatgpt")
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("x-api-key", headers)

    def test_openrouter_auth_is_optional(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("Authorization", _headers("openrouter"))

    def test_authenticated_sources_fail_clearly_when_secret_is_missing(self) -> None:
        for provider_id, variable in (
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
            ("xai", "XAI_API_KEY"),
        ):
            with self.subTest(provider=provider_id), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(CatalogError, variable):
                    _headers(provider_id)

    def test_secret_values_are_only_placed_in_request_headers(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}, clear=True):
            headers = _headers("openai")
        self.assertEqual(headers["Authorization"], "Bearer test-secret")
        self.assertNotIn("test-secret", " ".join(headers.keys()))

    def test_private_openai_model_ids_are_never_persisted(self) -> None:
        payload = {
            "object": "list",
            "data": [
                {"id": "gpt-public", "object": "model", "owned_by": "openai"},
                {"id": "gpt-system", "object": "model", "owned_by": "system"},
                {"id": "ft:gpt-private", "object": "model", "owned_by": "openai"},
                {"id": "org-private-model", "object": "model", "owned_by": "org-123"},
            ],
        }
        observation = json.loads(
            _public_projection(
                "openai",
                "models",
                payload,
                json.dumps(payload).encode(),
                {
                    "required_owned_by": ["openai", "system"],
                    "forbidden_id_prefixes": ["ft:"],
                },
            )
        )
        self.assertEqual(
            [model["id"] for model in observation["data"]],
            ["gpt-public", "gpt-system"],
        )

    def test_chatgpt_projection_discards_bundled_prompts_and_client_policy(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-example",
                    "display_name": "GPT Example",
                    "context_window": 272000,
                    "max_context_window": 272000,
                    "supported_in_api": True,
                    "visibility": "list",
                    "model_messages": {"instructions_template": "do not persist"},
                    "tools": [{"name": "do not persist"}],
                },
                {
                    "slug": "gpt-malformed",
                    "context_window": {"prompt": "do not persist"},
                    "max_context_window": ["do not persist"],
                },
                "malformed",
            ],
            "client_policy": {"secret-shaped-but-public": "do not persist"},
        }
        observation = json.loads(
            _public_projection(
                "chatgpt",
                "models",
                payload,
                json.dumps(payload).encode(),
                {"required_supported_in_api": True},
            )
        )
        self.assertEqual(
            observation,
            {
                "models": [
                    {
                        "slug": "gpt-example",
                        "context_window": 272000,
                        "max_context_window": 272000,
                    },
                    {"slug": "gpt-malformed"},
                    None,
                ]
            },
        )


if __name__ == "__main__":
    unittest.main()
