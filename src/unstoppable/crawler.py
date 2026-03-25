from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from unstoppable.queue import claim_urls, enqueue_urls, mark_done, mark_failed


USER_AGENT = "UnstoppableServiceBot/0.1 (+https://example.com/bot)"


@dataclass
class CrawlResult:
    claimed: int
    crawled: int
    discovered: int
    failed: int


def _extract_text_and_links(html: str, base_url: str) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    text = " ".join(soup.get_text(" ", strip=True).split())

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            links.append(href.split("#", 1)[0])

    return title, text, links


def crawl(
    conn, seed_urls: list[str], max_pages: int = 100, delay_seconds: float = 0.25
) -> CrawlResult:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    queue = deque(seed_urls)
    seen = set(seed_urls)

    claimed = 0
    crawled = 0
    discovered = 0
    failed = 0

    while queue and crawled < max_pages:
        url = queue.popleft()
        claimed += 1
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code != 200 or "text/html" not in resp.headers.get(
                "Content-Type", ""
            ):
                failed += 1
                continue

            title, text, links = _extract_text_and_links(resp.text, url)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO pages(url, title, content, last_crawled)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    last_crawled=excluded.last_crawled
                """,
                (url, title[:500], text[:20000], now),
            )

            conn.execute("DELETE FROM page_links WHERE source_url = ?", (url,))
            for link in links[:100]:
                conn.execute(
                    "INSERT INTO page_links(source_url, target_url) VALUES(?, ?)",
                    (url, link),
                )

                if link not in seen:
                    queue.append(link)
                    seen.add(link)
                    discovered += 1

            conn.commit()
            crawled += 1
            time.sleep(delay_seconds)
        except requests.RequestException:
            failed += 1

    return CrawlResult(
        claimed=claimed, crawled=crawled, discovered=discovered, failed=failed
    )


def crawl_queue_batch(
    conn,
    worker_id: str,
    batch_size: int = 10,
    delay_seconds: float = 0.25,
    max_discovered_per_page: int = 60,
) -> CrawlResult:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    urls = claim_urls(conn, limit=batch_size, worker_id=worker_id)
    claimed = len(urls)
    crawled = 0
    discovered = 0
    failed = 0

    for url in urls:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code != 200 or "text/html" not in resp.headers.get(
                "Content-Type", ""
            ):
                mark_failed(conn, url, f"status={resp.status_code}")
                failed += 1
                continue

            title, text, links = _extract_text_and_links(resp.text, url)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO pages(url, title, content, last_crawled)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    last_crawled=excluded.last_crawled
                """,
                (url, title[:500], text[:20000], now),
            )
            conn.execute("DELETE FROM page_links WHERE source_url = ?", (url,))

            link_slice = links[:max_discovered_per_page]
            for link in link_slice:
                conn.execute(
                    "INSERT INTO page_links(source_url, target_url) VALUES(?, ?)",
                    (url, link),
                )
            conn.commit()

            discovered += enqueue_urls(
                conn, link_slice, priority=80, discovered_from=url
            )
            mark_done(conn, url)
            crawled += 1
            time.sleep(delay_seconds)
        except requests.RequestException as exc:
            mark_failed(conn, url, str(exc))
            failed += 1

    return CrawlResult(
        claimed=claimed, crawled=crawled, discovered=discovered, failed=failed
    )
