from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from catalog_pipeline.common import MODEL_METADATA_FIELDS
from catalog_pipeline.promotion import classify_promotion, load_promotion_policy
from catalog_pipeline.release import load_release


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

    def test_promotion_policy_conforms_to_its_schema(self) -> None:
        document = json.loads((ROOT / "promotion-policy.json").read_bytes())
        self._validator("promotion-policy-v1.schema.json").validate(document)

    def test_bootstrap_diff_conforms_to_its_schema(self) -> None:
        release = load_release(ROOT / "fixtures" / "expected")
        policy = load_promotion_policy(ROOT / "promotion-policy.json")
        diff = classify_promotion(None, release, policy)
        self._validator("diff-v1.schema.json").validate(diff)

    def test_diff_metadata_fields_match_the_runtime_model_contract(self) -> None:
        schema = json.loads((ROOT / "schema" / "diff-v1.schema.json").read_bytes())
        fields = schema["$defs"]["provider_diff"]["properties"]["metadata_changes"]["items"][
            "properties"
        ]["fields"]["items"]["enum"]
        self.assertEqual(fields, list(MODEL_METADATA_FIELDS))


if __name__ == "__main__":
    unittest.main()
