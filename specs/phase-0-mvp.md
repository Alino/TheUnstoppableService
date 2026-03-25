# Phase 0 MVP Spec

This document turns the whitepaper into a buildable Phase 0 target.

## Goal

Ship a single-container search MVP that runs on decentralized compute and proves four things:

1. We can crawl and index pages automatically.
2. We can serve organic search results from our own index.
3. We can expose cost/runway telemetry for future autonomy.
4. The service can be packaged for Akash deployment.

## Scope (Phase 0)

- Single Python container with four internal components:
  - crawler
  - indexer
  - query API
  - brain-lite loop (cost/runway mode calculator)
- Storage backend: local SQLite (swap to Storj-backed object storage in Phase 1/2).
- Ranking: SQLite FTS5 BM25 (organic only, no ads).
- Inputs:
  - seed URL file
  - local treasury state file (mock for now)
- Outputs:
  - search API JSON responses
  - runway/mode decisions from brain-lite

## Out of Scope (Phase 0)

- Multi-node crawler swarm.
- On-chain wallets and real payment execution.
- ENS/IPFS front-end publishing.
- Autonomous redeploy and lease renewals.

## Target Endpoints

- `GET /health`
- `GET /search?q=<query>&limit=<n>`
- `GET /stats`

## Data Model

- `pages` table
  - `id`
  - `url` (unique)
  - `title`
  - `content`
  - `last_crawled`
- `page_links` table
  - `source_url`
  - `target_url`
- `page_fts` virtual table (FTS5)
  - `title`
  - `content`
  - linked to `pages`

## Operational Flow

1. `crawl` command reads seeds and fetches pages.
2. Content and links are stored in SQLite.
3. `index` command rebuilds FTS index.
4. `serve` command exposes search API.
5. `brain-once` evaluates treasury runway and emits mode.

## Success Criteria

- Crawl at least 100 pages from seed list without crashing.
- Return relevant results for simple keyword queries.
- Produce deterministic runway mode from treasury inputs.
- Run locally and inside Docker.

## Next Step After Phase 0

- Replace local treasury with live wallet adapters.
- Replace local DB with distributed storage/index pipeline.
- Split components into separate deployable services.
- Add Akash lease/health management automation.
