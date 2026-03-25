import sqlite3

from unstoppable.search_backend import sync_to_elasticsearch


def rebuild_fts(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM page_fts")
    conn.execute(
        """
        INSERT INTO page_fts(url, title, content, last_crawled)
        SELECT url, COALESCE(title, ''), COALESCE(content, ''), last_crawled
        FROM pages
        """
    )
    conn.commit()
    row = conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()
    return int(row["c"])


def rebuild_all(conn: sqlite3.Connection) -> dict:
    pages = rebuild_fts(conn)
    external = sync_to_elasticsearch(conn)
    return {"indexed_pages": pages, "external": external}
