from __future__ import annotations

import json
import sqlite3

import requests

from unstoppable.config import ELASTICSEARCH_INDEX, ELASTICSEARCH_URL, SEARCH_BACKEND


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_fts_query(query: str) -> str:
    parts = [p for p in query.replace("-", " ").split() if p]
    if not parts:
        return '""'
    return " OR ".join(parts[:8])


def _sqlite_search(conn, query: str, limit: int) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT
                url,
                title,
                substr(content, 1, 280) AS snippet,
                bm25(page_fts) AS score,
                last_crawled
            FROM page_fts
            WHERE page_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (_to_fts_query(query), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        like_q = f"%{_escape_like(query)}%"
        rows = conn.execute(
            """
            SELECT
                url,
                title,
                substr(content, 1, 280) AS snippet,
                0.0 AS score,
                last_crawled
            FROM pages
            WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'
            ORDER BY last_crawled DESC
            LIMIT ?
            """,
            (like_q, like_q, limit),
        ).fetchall()

    return [
        {
            "url": r["url"],
            "title": r["title"] or r["url"],
            "snippet": r["snippet"],
            "score": float(r["score"]),
            "last_crawled": r["last_crawled"],
        }
        for r in rows
    ]


def _es_search(query: str, limit: int) -> list[dict]:
    if not ELASTICSEARCH_URL:
        raise RuntimeError("ELASTICSEARCH_URL is not set")

    body = {
        "size": limit,
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "content"],
                "type": "best_fields",
            }
        },
    }
    res = requests.post(
        f"{ELASTICSEARCH_URL.rstrip('/')}/{ELASTICSEARCH_INDEX}/_search",
        json=body,
        timeout=8,
    )
    res.raise_for_status()
    data = res.json()
    hits = data.get("hits", {}).get("hits", [])
    out = []
    for h in hits:
        src = h.get("_source", {})
        out.append(
            {
                "url": src.get("url", ""),
                "title": src.get("title") or src.get("url", ""),
                "snippet": (src.get("content", "") or "")[:280],
                "score": float(h.get("_score", 0.0)),
                "last_crawled": src.get("last_crawled"),
            }
        )
    return out


def search(conn, query: str, limit: int) -> tuple[list[dict], str]:
    backend = SEARCH_BACKEND
    if backend == "elasticsearch":
        try:
            return _es_search(query, limit), "elasticsearch"
        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return _sqlite_search(conn, query, limit), "sqlite-fallback"
    return _sqlite_search(conn, query, limit), "sqlite"


def sync_to_elasticsearch(conn) -> dict:
    if SEARCH_BACKEND != "elasticsearch":
        return {"enabled": False, "message": "sqlite backend active"}
    if not ELASTICSEARCH_URL:
        return {"enabled": True, "error": "ELASTICSEARCH_URL not set"}

    index_url = f"{ELASTICSEARCH_URL.rstrip('/')}/{ELASTICSEARCH_INDEX}"
    mapping = {
        "mappings": {
            "properties": {
                "url": {"type": "keyword"},
                "title": {"type": "text"},
                "content": {"type": "text"},
                "last_crawled": {"type": "date"},
            }
        }
    }
    requests.put(index_url, json=mapping, timeout=8)

    rows = conn.execute(
        "SELECT url, title, content, last_crawled FROM pages ORDER BY id"
    ).fetchall()
    if not rows:
        return {"enabled": True, "indexed": 0}

    lines: list[str] = []
    for r in rows:
        lines.append(
            json.dumps({"index": {"_index": ELASTICSEARCH_INDEX, "_id": r["url"]}})
        )
        lines.append(
            json.dumps(
                {
                    "url": r["url"],
                    "title": r["title"] or "",
                    "content": r["content"] or "",
                    "last_crawled": r["last_crawled"],
                }
            )
        )
    payload = "\n".join(lines) + "\n"
    bulk_res = requests.post(
        f"{ELASTICSEARCH_URL.rstrip('/')}/_bulk",
        data=payload,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=20,
    )
    bulk_res.raise_for_status()
    bulk_json = bulk_res.json()
    return {
        "enabled": True,
        "indexed": len(rows),
        "errors": bool(bulk_json.get("errors", False)),
    }
