# Euler Provider Catalog

`euler-provider-catalog` is the public source and publication authority for
provider and model metadata consumed by [Euler](https://github.com/2x11-xyz/euler).
It observes official provider sources, normalizes them deterministically, and
publishes reviewable versioned artifacts through GitHub.

This repository owns metadata only. Provider transports, authentication,
headers, endpoints, compatibility behavior, and secret resolution stay in the
Euler binary.

## Provider coverage

The catalog is centralized across every external model route currently built
into Euler:

| Euler route | Automated membership | Metadata policy |
|---|---|---|
| `openrouter` | Official Models API | API fields plus reviewed router pseudo-routes |
| `anthropic` | Official Models API | API capability and limit fields |
| `openai` | Official Models API | API membership intersected with reviewed official-doc metadata |
| `xai` | Official Models and Language Models APIs | Joined API fields plus narrow reviewed overrides |
| `chatgpt` | No suitable public discovery API | Explicitly curated subscription routes |

This is an Euler-supported catalog, not a claim that every account sees every
provider model. Account-scoped observations are evidence of availability;
publication policy decides whether a candidate is promoted.

The repository layout keeps those decisions visible:

- `sources/` records official endpoints, documentation, field ownership, and
  provider-specific acceptance rules;
- `curated/` records defaults and the narrow metadata that APIs do not expose;
- `catalog_pipeline/` fetches bounded observations and generates artifacts;
- `schema/` defines the catalog, manifest, provenance, and observation formats;
- `fixtures/` contains deterministic multi-provider upstream evidence;
- `generated/` is the byte-for-byte expected centralized output for the
  checked-in fixtures.

Generate the checked-in fixture artifacts:

```console
python -m catalog_pipeline.generate \
  --observations-dir fixtures \
  --output-dir generated
```

Run the tests and verify that generated artifacts are current:

```console
python -m pip install jsonschema==4.26.0
python -m unittest discover -s tests -v
python -m catalog_pipeline.generate \
  --observations-dir fixtures \
  --output-dir generated \
  --check
```

Observe all discoverable providers on demand (API keys are required for the
providers whose official list endpoints require authentication):

```console
python -m catalog_pipeline.fetch --provider all --output-dir observations
python -m catalog_pipeline.generate \
  --observations-dir observations \
  --output-dir candidate
```

## Publication model

GitHub Actions observes all four official discovery APIs daily and on manual
dispatch, then combines those observations with the reviewed ChatGPT route
list. Phase one retains the bounded response observations and generated
candidate as a workflow artifact. Promotion pull requests and GitHub Release
publication are the next implementation phase; the workflow does not mutate
the stable catalog yet.

The OpenAI and xAI observations are publication-safe projections of their
official responses: only provider-owned model records are written to disk.
Private fine-tune and organization-owned IDs are discarded in memory and never
uploaded from this public repository's workflow.

Euler remains usable from its embedded last-known-good catalog when GitHub or
an upstream provider is unavailable.

## License

MIT
