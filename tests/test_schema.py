from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from catalog_pipeline.common import MODEL_METADATA_FIELDS, OBSERVED_DISCOVERY_KINDS
from catalog_pipeline.promotion import classify_promotion, load_promotion_policy
from catalog_pipeline.promotion_contract import DECISIONS, PROVIDER_FIELDS, REASONS, STATUSES
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

    def test_stable_release_artifacts_conform_to_their_schemas(self) -> None:
        for stem in ("catalog-v1", "manifest-v1", "provenance-v1", "diff-v1"):
            with self.subTest(artifact=stem):
                document = json.loads((ROOT / "stable" / f"{stem}.json").read_bytes())
                self._validator(f"{stem}.schema.json").validate(document)

    def test_observation_sidecars_conform_to_their_schema(self) -> None:
        validator = self._validator("observation-v1.schema.json")
        for provider_id in ("anthropic", "chatgpt", "openai", "openrouter", "xai"):
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

    def test_diff_enums_match_the_runtime_contract(self) -> None:
        schema = json.loads((ROOT / "schema" / "diff-v1.schema.json").read_bytes())
        provider = schema["$defs"]["provider_diff"]["properties"]
        self.assertEqual(set(schema["properties"]["decision"]["enum"]), DECISIONS)
        self.assertEqual(set(schema["properties"]["reasons"]["items"]["enum"]), REASONS)
        self.assertEqual(set(provider["provider_fields_changed"]["items"]["enum"]), PROVIDER_FIELDS)
        self.assertEqual(set(schema["$defs"]["status"]["enum"]), STATUSES)

    def test_provenance_discovery_enums_match_the_runtime_contract(self) -> None:
        schema = json.loads((ROOT / "schema" / "provenance-v1.schema.json").read_bytes())
        provider_kinds = schema["$defs"]["provider_provenance"]["properties"]["discovery_kind"][
            "enum"
        ]
        input_kinds = schema["$defs"]["input"]["properties"]["kind"]["enum"]
        self.assertEqual(set(provider_kinds), OBSERVED_DISCOVERY_KINDS | {"curated", "bootstrap"})
        self.assertEqual(
            set(input_kinds),
            OBSERVED_DISCOVERY_KINDS | {"bootstrap", "curated", "source_policy"},
        )


if __name__ == "__main__":
    unittest.main()
