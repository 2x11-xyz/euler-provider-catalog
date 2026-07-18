from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def _validator(self, schema_name: str) -> jsonschema.Draft202012Validator:
        schema = json.loads((ROOT / "schema" / schema_name).read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        return jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        )

    def test_published_artifacts_conform_to_their_schemas(self) -> None:
        for stem in ("catalog-v1", "manifest-v1", "provenance-v1"):
            with self.subTest(artifact=stem):
                document = json.loads(
                    (ROOT / "fixtures" / "expected" / f"{stem}.json").read_bytes()
                )
                self._validator(f"{stem}.schema.json").validate(document)

    def test_observation_sidecars_conform_to_their_schema(self) -> None:
        validator = self._validator("observation-v1.schema.json")
        for provider_id in ("anthropic", "openai", "openrouter", "xai"):
            with self.subTest(provider=provider_id):
                document = json.loads(
                    (ROOT / "fixtures" / provider_id / "observation.json").read_bytes()
                )
                validator.validate(document)


if __name__ == "__main__":
    unittest.main()
