---
status: current
---

# Fansly Downloader NG Documentation

Content scraping and archival tool for Fansly. Download photos, videos,
audio, and other media — in bulk or selectively — from timelines,
messages, walls, stories, collections, and individual posts. The v0.13
release adds a continuous monitoring daemon that watches creators in
near real time via Fansly's WebSocket and a calibrated polling fallback.

See [the README on GitHub](https://github.com/Jakan-Kink/fansly-scraper/blob/main/README.md)
for install + quickstart. The pages in this docs site cover architecture,
reference material, planning docs, and testing guidelines.

## What's where

- **[Guide](guide/manual-token-extraction.md)** — user-facing how-tos
  (setup fallbacks, troubleshooting)
- **[Reference](reference/architecture.md)** — canonical architecture
  docs, protocol breakdowns, and data-mapping tables. Start with
  [Architecture](reference/architecture.md) for the big picture and
  [Monitoring Daemon Cadence](reference/monitoring-cadence.md) for the
  polling / anti-detection rationale.
- **[Planning](planning/monitoring-daemon-architecture.md)** — design
  documents for in-progress or partially-complete initiatives (monitoring
  daemon architecture, Stash ORM migration phases)
- **[Testing](testing/TESTING_REQUIREMENTS.md)** — testing requirements,
  migration trackers, and patterns (mocking boundaries, RESPX rules,
  fixture conventions)
- **Archive** — historical planning docs retained for context (e.g., the
  hard-cutover PostgreSQL migration plan that shipped in v0.11.0)

## Project home

[Jakan-Kink/fansly-scraper](https://github.com/Jakan-Kink/fansly-scraper) on
GitHub. The original upstream ([prof79/fansly-downloader-ng](https://github.com/prof79/fansly-downloader-ng))
has been dormant since June 2024 and is retained only for git
archaeology.

## Related projects

- **[stash-graphql-client](https://github.com/Jakan-Kink/stash-graphql-client)**
  — maintained by the same author; provides the async GraphQL client,
  Pydantic types, and `StashEntityStore` used by this project's Stash
  integration. The `PostgresEntityStore` design in this codebase
  follows the same identity-map + dirty-tracking patterns, and the two
  projects cross-pollinate ideas over each release cycle.
