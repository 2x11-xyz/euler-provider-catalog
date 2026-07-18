from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from catalog_pipeline.common import CatalogError
from catalog_pipeline.fetch import _headers, _public_projection


class FetchPolicyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
