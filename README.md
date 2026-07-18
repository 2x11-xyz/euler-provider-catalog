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
- `promotion-policy.json` owns the reviewed catalog-shrink threshold;
- `schema/` defines the runtime, evidence, and promotion formats;
- `fixtures/` contains deterministic multi-provider upstream evidence;
- `fixtures/expected/` is the byte-for-byte expected centralized output for
  the checked-in fixtures. It is test data, never a release source.

Generate the checked-in fixture artifacts:

```console
python -m catalog_pipeline.generate \
  --observations-dir fixtures \
  --output-dir fixtures/expected
```

Run the tests and verify that generated artifacts are current:

```console
python -m pip install jsonschema==4.26.0 ruff==0.15.19
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
python -m catalog_pipeline.generate \
  --observations-dir fixtures \
  --output-dir fixtures/expected \
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

Those keys are catalog-operator discovery credentials, not Euler user
credentials and not catalog data. They are injected into the observation
process, used only in request headers for official read-only model-list calls,
and never written to observations, provenance, candidates, releases, or the
Euler client. Providers without a suitable unauthenticated official endpoint
must otherwise remain explicitly curated from official documentation; the
pipeline does not fall back to scraping or secondary indexes.

Classify a complete candidate against the current stable release:

```console
python -m catalog_pipeline.promotion \
  --candidate-dir candidate \
  --previous-dir stable \
  --output-dir promotion-review
```

The classifier validates the internal integrity of both releases, emits a deterministic
`diff-v1.json`, and returns a nonzero status for a blocked candidate. Omit
`--previous-dir` only when preparing the first stable baseline; bootstrap is
always classified as requiring human review. The classifier never writes to
`stable/`.

## Publication model

GitHub Actions observes all four official discovery APIs daily and on manual
dispatch, then combines those observations with the reviewed ChatGPT route
list. Phase one retains the bounded response observations and generated
candidate as a workflow artifact. Promotion pull requests and GitHub Release
publication are the next implementation phase; the workflow does not mutate
the stable catalog yet.

The retention step also runs after an observation or generation failure so
maintainers can inspect partial, already-projected evidence. Partial artifacts
are debugging evidence only and are never eligible for promotion.

The OpenAI and xAI observations are publication-safe projections of their
official responses: only provider-owned model records are written to disk.
Private fine-tune and organization-owned IDs are discarded in memory and never
uploaded from this public repository's workflow.

Euler remains usable from its embedded last-known-good catalog when GitHub or
an upstream provider is unavailable.

Artifact hashes and release IDs detect inconsistent or corrupted release
content. They do not create a trust root independent of the official GitHub
repository; repository controls and reviewed publication remain the authority.

## License

MIT
