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
- `bootstrap/` is the reviewed import of Euler's pre-catalog built-ins;
- `stable/` is the sole release input and changes only through pull requests;
- `promotion-policy.json` owns the reviewed catalog-shrink threshold;
- `schema/` defines the runtime, evidence, and promotion formats;
- `fixtures/` contains deterministic multi-provider upstream evidence;
- `fixtures/expected/` is the byte-for-byte expected centralized output for
  the checked-in fixtures. It is test data, never a release source.

Optional token-price schedules follow the same ownership rules. OpenRouter
rates come from each official Models API record. Providers whose list APIs do
not publish prices use the reviewed `pricing` map in their curated input,
backed by the official pricing documentation named by the source policy.
Published rates are exact USD-per-million-token decimals; missing rates remain
absent instead of being inferred as zero. A price-bearing catalog requires an
Euler version that understands the added strict model field. Such a candidate
must not be promoted into `stable/` until that minimum Euler version has been
released; generated fixtures exercise the future protocol but are never a
publication source.

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

The historical bootstrap release can be reproduced without a network call:

```console
python -m catalog_pipeline.bootstrap --output-dir /tmp/bootstrap-candidate
python -m catalog_pipeline.verify_release \
  --directory /tmp/bootstrap-candidate
```

That self-contained baseline is an exact, reviewed import of the model
membership embedded in Euler at the source revision named by
`bootstrap/metadata-v1.json`. Its provenance explicitly claims no live
provider observation. It exists so a fresh or offline Euler install has a real
last-known-good catalog while daily official-source observations take over
future updates. `stable/` initially matches it and then advances independently
through reviewed promotion pull requests.

## Publication model

GitHub Actions observes all four official discovery APIs daily and on manual
dispatch, then combines those observations with the reviewed ChatGPT route
list. A successful run retains the bounded evidence and candidate as a
short-lived workflow artifact. A separate `workflow_run` job has no provider
credentials: it validates that artifact, applies the promotion policy, and
updates one monotonic bot-owned branch under `stable/`. A no-change observation
does nothing, and a blocked or incomplete candidate cannot update the branch.

The organization policy intentionally prevents `GITHUB_TOKEN` from creating or
approving pull requests. The promotion job therefore dispatches CI against the
exact bot commit and opens or updates one tracking issue with GitHub's compare
link. A maintainer creates and merges the PR from that link; publication closes
the notice. There is no long-lived bot token and no API-to-release path that
bypasses the stable-state diff.

The retention step also runs after an observation or generation failure so
maintainers can inspect partial, already-projected evidence. Partial artifacts
are debugging evidence only and are never eligible for promotion.

Only a merged `stable/` change on `main` starts publication. The workflow
revalidates the repository, creates a draft GitHub Release named by the
content-authenticated `manifest.release_id`, uploads the three runtime
artifacts, downloads and verifies them, and only then publishes the release as
`latest`. A matching interrupted draft is safely resumed; published tags and
releases are never overwritten. Repository release immutability must remain
enabled.

Euler resolves the small latest manifest from
`releases/latest/download/manifest-v1.json`, then fetches the catalog from the
manifest's release-specific URL. The release-specific hop makes a moving
`latest` pointer harmless: the bytes still have to match the selected
manifest. Euler packages the current stable snapshot and treats network
refresh as a best-effort update, never as a startup dependency.

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
