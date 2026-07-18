# Provider catalog rollout

The catalog is intentionally split into observation, promotion, publication,
and consumption. A provider API changing must never directly rewrite the model
list on a user's machine.

## Phase 1: centralized candidate pipeline

Status: implemented by pull request 1.

- Observe the official OpenRouter, Anthropic, OpenAI, and xAI model APIs.
- Keep the ChatGPT subscription route list explicitly reviewed.
- Normalize all five Euler routes into one deterministic catalog.
- Retain bounded observations and a candidate as short-lived workflow
  artifacts.
- Validate digests, schemas, defaults, adapter constraints, and source limits.
- Make no stable-catalog mutation and publish no GitHub Release.

The files under `fixtures/expected/` prove deterministic generation from test
observations. They are not a production catalog and must never be released.

## Phase 2: guarded promotion and GitHub Releases

Status: promotion classification is implemented. Stable-state writes, bot pull
requests, removal-history enforcement, and release publication remain gated on
the repository prerequisites below and a reviewed first live baseline.

The classifier authenticates canonical candidate and stable artifacts, records
model/provider/governed-input changes in `diff-v1.json`, separates addition-only
updates from human-review changes, and blocks any candidate that drops more
than the configured fraction of a provider's prior model IDs. The initial
threshold in `promotion-policy.json` is 1,000 basis points (10%); the
calculation rounds upward so a change is never understated. Every removal at
or below that threshold still requires human review.

### Repository prerequisites

1. Protect `main` and require the catalog CI check.
2. Enable required code-owner review using the checked-in `CODEOWNERS` rules
   for `sources/`, `curated/`, schemas, promotion policy, stable state, and
   workflows.
3. Inject dedicated observation-only discovery credentials through GitHub
   Actions for official endpoints that require authentication:
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `XAI_API_KEY`.
   `OPENROUTER_API_KEY` remains optional.
4. Restrict each credential to model reads when the provider supports endpoint
   permissions. Otherwise use an isolated project with a minimal spend limit.
5. Install a narrowly scoped GitHub App for bot branches and pull requests.
   Pull requests created with the repository `GITHUB_TOKEN` do not reliably
   trigger the independent CI run required for protected promotion.

These are catalog-operator credentials, never Euler user credentials or
published catalog content. The repository stores only their GitHub secret
references. The scheduled workflow fails closed while any required discovery
credential is absent; credential readiness must be checked before enabling
promotion. A zero-credential deployment must accept curated official-doc
coverage for those providers rather than pretending to have complete daily API
discovery.

### Stable state

Add a `stable/` directory containing exactly:

- `catalog-v1.json`;
- `manifest-v1.json`;
- `provenance-v1.json`;
- `diff-v1.json`, a review artifact describing the change from the previous
  stable catalog.

Raw or projected observations remain bounded workflow artifacts. They do not
accumulate on `main`.

### Candidate classification

Compare each complete candidate with `stable/catalog-v1.json` and classify it
before creating or updating one bot pull request:

| Change | Initial policy |
|---|---|
| No byte change | Do nothing |
| Model addition | Bot PR; eligible for auto-merge after an operating soak period |
| Display name, limits, or capability change | Bot PR; human review initially |
| Model deprecation | Bot PR; human review |
| Model removal | Human review and either three consecutive observations or explicit override |
| Default, provider set, source policy, schema, or workflow change | Human review required |
| Missing provider, digest failure, excessive shrink, or count outside bounds | Fail closed; no PR |

The diff must report per-provider additions, removals, lifecycle changes,
metadata changes, old/new counts, and absolute and percentage shrink. Promotion
tests enforce the classification; prose labels alone are not a guard.

### Release publication

Only a merged change under `stable/` may publish. A separate workflow reruns
all validation, creates an immutable tag from `manifest.release_id`, and
uploads the three runtime artifacts as GitHub Release assets. It must refuse to
overwrite an existing tag or release and verify the downloaded assets before
marking the release successful.

## Phase 3: Euler consumption

Implement this in small Euler pull requests after the first stable release
exists:

1. Add strict Rust types and invariant validation for catalog v1 and manifest
   v1.
2. Embed the current stable catalog at build time so a fresh install and
   offline launch always work.
3. Change `euler models refresh` to fetch the latest GitHub Release manifest
   and catalog with bounded size, timeout, redirect, schema, and SHA-256 checks.
4. Atomically store the verified snapshot under a machine-owned catalog path;
   preserve `~/.euler/models.json` as the user-owned override.
5. Replace built-in model membership with the verified full snapshot, then
   apply user additions and same-ID overrides.
6. Keep headless commands offline. Add first-interactive-launch best-effort
   refresh only after the explicit refresh path has dogfood evidence.
7. Remove direct `models.dev` refresh ownership once the migration boundary is
   tested against existing generated and user-authored files.

The Euler consumer never reads provider credentials and never learns provider
transport from the catalog.

## Phase 4: adding providers safely

A new catalog provider is accepted only after Euler has a reviewed adapter. Its
change must add one source policy, one curated policy, a focused normalizer,
recorded fixtures, schema/invariant coverage, and an explicit default. Catalog
membership cannot create an executable provider by itself.
