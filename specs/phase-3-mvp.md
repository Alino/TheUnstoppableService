# Phase 3 MVP Spec

Phase 3 adds the first autonomous revenue layer.

## Objectives

1. Donation-first revenue flow exposed to users.
2. API key monetization for developer search usage.
3. Contextual ad fallback when runway is low.
4. Optional search backend scaling path via Elasticsearch.

## Implemented

- `search_api.py`
  - donation page: `/donate`
  - API key management endpoints
  - revenue config endpoints
  - ads preview endpoint
  - search response now includes billing and ads metadata
- `apikeys.py`
  - key creation, top-up, usage tracking, daily limits, per-query pricing
- `monetization.py`
  - ad activation policy and contextual ad matching
- `search_backend.py`
  - SQLite FTS backend (default)
  - Elasticsearch backend with SQLite fallback
  - index sync routine for ES
- `indexer.py`
  - `rebuild_all()` now updates local index and optional ES index

## Revenue Model in Product

- Donations: manual and `/donate` simulated flow
- API usage billing:
  - `free` plan: free, lower daily limits
  - `builder` and `pro`: charged per query with preloaded credits
- Ads:
  - Off by default
  - Auto-on if runway below threshold, or manually force-enabled

## Elasticsearch Path

- Controlled by:
  - `UNSTOPPABLE_SEARCH_BACKEND=elasticsearch`
  - `ELASTICSEARCH_URL=http://localhost:9200`
  - `ELASTICSEARCH_INDEX=unstoppable-pages`
- Indexer syncs page documents to ES via `_bulk`
- Search API queries ES first with fallback to SQLite

## Notes

- Billing and payments are still simulation-grade.
- Real wallet signing/on-chain settlement is Phase 4+ work.
