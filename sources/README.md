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
- ChatGPT subscription routing has no suitable unattended public list API and
  is therefore review-only.

Human-readable pages back structured review but are never scraped to mutate a
stable catalog. An unknown or incomplete API record is skipped with provenance,
not filled by an undocumented guess.

Because this is a public repository, account-scoped APIs are projected before
their response is written. For OpenAI and xAI, only records owned by the
provider are retained; private fine-tunes and organization-owned model IDs
never enter an observation artifact.
