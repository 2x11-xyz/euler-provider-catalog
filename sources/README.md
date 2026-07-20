# Provider source policy

Every provider has one machine-readable policy. The `discovery` block names
only documented official sources. `field_ownership` explains which source is
authoritative for each published field, while `limits` bounds untrusted input
before it reaches a candidate catalog.

The normalizers are intentionally provider-specific. Similar-looking model
APIs do not expose equivalent contracts:

- OpenRouter and Anthropic expose useful capability data directly.
- OpenAI exposes broad, account-scoped model identity; only IDs with reviewed
  official-documentation metadata are admitted.
- xAI exposes model limits and language-model membership through separate
  official endpoints, so the normalizer joins them by canonical ID.
- ChatGPT subscription membership remains review-only, while matching context
  limits are observed from OpenAI's public bundled Codex model catalog.

Discovery is classified per provider:

- `official_api` means a live provider model API; `official_snapshot` means a
  provider-owned repository snapshot; `curated` means there is no unattended
  structured observation.
- OpenRouter uses an official unauthenticated API.
- Anthropic, OpenAI, and xAI use official authenticated APIs. Authentication is
  supplied only to the observation job as a dedicated read-only discovery
  credential; it is not an Euler user secret and never becomes artifact data.
- ChatGPT reads OpenAI's official public Codex catalog JSON without
  authentication. That checked-in file is the bundled snapshot of Codex's
  `/models` metadata. Euler's `chatgpt` adapter uses the Codex subscription
  responses route, which makes that client metadata relevant, but the snapshot
  is not represented as a live backend response. The fetcher retains only
  model slugs and context bounds before it writes an observation; prompts and
  client instructions are discarded.

The Codex snapshot owns context metadata for matching reviewed ChatGPT routes;
it does not own Euler membership, lifecycle, or account availability. A
reviewed route missing from the snapshot fails observation unless its ID is an
explicit source-policy exception. The current exception is Codex Spark, whose
Euler route evidence exists outside the bundled picker snapshot. OpenAI's
[context-window refresh](https://github.com/openai/codex/pull/33972) documents
the source's intended client-metadata role.

The source URL intentionally follows the OpenAI repository's `main` branch so
daily observation detects current client metadata. Each observation records
the exact projected bytes and digest, and a metadata change still requires the
existing reviewed promotion pull request before it can enter `stable/`.

Avoiding discovery credentials for every provider would require either brittle
documentation scraping or a secondary source. Both are deliberately excluded;
without an authenticated discovery credential, that provider cannot produce a
complete automated candidate.

Human-readable pages back structured review but are never scraped to mutate a
stable catalog. An unknown or incomplete structured record is skipped with
provenance, not filled by an undocumented guess.

Provider-callable aliases are membership, not display-only labels. Anthropic's
list endpoint currently returns canonical snapshots while its official model-ID
documentation also defines short Claude API aliases; those reviewed aliases
are explicit curated additions. xAI's language-model response supplies both
canonical IDs and aliases directly. Officially retired xAI slugs that still
redirect may remain explicit `deprecated` additions until a later reviewed
removal. For OpenAI, carrying reviewed metadata forward from the last stable
catalog never bypasses current API membership: an unobserved ID is not
published.

Because this is a public repository, upstream responses are projected before
they are written when they may contain private or non-metadata fields. For
OpenAI and xAI, only records owned by the provider are retained; private
fine-tunes and organization-owned model IDs never enter an observation
artifact. For ChatGPT, only model slugs and context bounds are retained.
