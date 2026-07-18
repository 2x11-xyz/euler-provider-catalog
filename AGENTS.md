# Catalog Agent Instructions

## Boundary

This repository owns provider and model metadata only. The observation client
may contain the minimum read-only HTTP mechanics needed to call documented
official list endpoints. Never put provider inference transport,
authentication, endpoint selection, headers, request compatibility, prompting,
executable content, or secret values in published catalog artifacts.

## Invariants

- Official provider APIs and documentation outrank secondary sources.
- Identical recorded inputs must produce byte-identical artifacts.
- Fetch and parse failures fail closed; they never become empty catalogs.
- Generated catalogs contain only models accepted by the provider policy.
- Defaults, aliases, and router pseudo-models are curated and review-visible.
- Secrets are never written to observations, logs, provenance, or artifacts.
- Actions use least privilege and immutable commit SHAs.

Prefer a small explicit pipeline over a general plugin framework. Provider
differences belong in named source policies and focused normalizers.
