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
| `chatgpt` | Explicitly curated subscription routes | Matching context limits from OpenAI's bundled Codex model snapshot |

This is an Euler-supported catalog, not a claim that every account sees every
provider model. Account-scoped observations are evidence of availability;
publication policy decides whether a candidate is promoted.

The repository layout keeps those decisions visible:

- `sources/` records official sources, documentation, field ownership, and
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

## Authority and route semantics

The bootstrap is a historical seed, not a continuing upstream. After the first
stable release, the authority chain is one-way: official provider evidence and
reviewed curation produce `stable/`; `stable/` produces an immutable GitHub
Release; and Euler consumes those exact release bytes. Syncing a release into
Euler never makes Euler an input to a later catalog release.

A catalog model ID is a provider-callable route string. Official aliases are
therefore intentionally represented as selectable model entries, even when an
upstream list endpoint returns only the canonical snapshot. A curated addition
may retain a route documented by the provider but omitted from its structured
list; it remains governed by the curated-input digest and human-reviewed
promotion diff. `deprecated` identifies a retained compatibility or redirect
route and does not claim that the upstream still serves the original model.
For OpenAI, reviewed metadata alone never creates membership: publication is
always the intersection of reviewed records and the current provider-owned API
observation.

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
Euler client. Providers without a suitable official structured endpoint must
otherwise remain explicitly curated from official documentation; the pipeline
does not fall back to scraping or secondary indexes.

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

GitHub Actions observes four provider model APIs plus the model snapshot
bundled in OpenAI's official Codex repository daily and on manual dispatch.
ChatGPT membership remains reviewed; the snapshot supplies context limits only
for matching routes. A
successful run retains the bounded evidence and candidate as a short-lived
workflow artifact. A separate `workflow_run` job has no provider
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

The OpenAI and xAI observations are publication-safe projections of official
API responses and retain only provider-owned model records. The ChatGPT
observation is a projection of an official repository snapshot; it retains
only model slugs and context bounds and drops bundled prompts and client
instructions. Private fine-tune and organization-owned IDs are discarded in
memory and never uploaded from this public repository's workflow.

Euler remains usable from its embedded last-known-good catalog when GitHub or
an upstream provider is unavailable.

Artifact hashes and release IDs detect inconsistent or corrupted release
content. They do not create a trust root independent of the official GitHub
repository; repository controls and reviewed publication remain the authority.

## How Euler consumes the catalog

Every Euler release binary embeds a verified catalog snapshot, so a fresh
installation can list providers and models offline:

```console
euler models
```

The full-screen TUI performs a non-blocking GitHub check when refresh is due.
Users and automation can request the same check without opening a session:

```console
euler models refresh
```

Euler validates the release manifest, content identity, digest, schema,
compatibility, and monotonic release time before writing a managed local cache.
If refresh fails, it retains the embedded or last-known-good catalog. The
refresh client downloads only public release files and does not use provider
API keys. The user's optional `~/.euler/models.json` additions and same-ID
metadata overrides are applied afterward.

Before a new Euler version is tagged, maintainers synchronize the latest
catalog release into the Euler source tree. Euler's release workflow refuses to
build or publish a release from a new tag with a stale embedded snapshot, so
prebuilt release binaries and source builds from that tag start from the same
catalog. Existing binaries can consume newer compatible catalog releases at
runtime; they do not need to be rebuilt for metadata-only changes.

See Euler's
[Provider catalog and model updates](https://github.com/2x11-xyz/euler/blob/main/docs/guides/provider-catalog.md)
guide for the complete installation, runtime, cache, failure, and release-build
lifecycle.

## License

MIT
