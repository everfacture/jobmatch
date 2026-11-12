"""Deduplicate high-fit jobs before notification.

Identity is the posting, not the title.

- Same canonical URL (application URL, else source URL) is one posting.
- Same title and company is one posting only when the body text matches.
  A later LinkedIn URL for the same title at the same employer, with a
  different description, is a new advert and stays active.
- No company: title-only collapse only when the body text matches. Telegram
  Bali house ads are all titled "Various Positions" with an empty company
  and a different employer in the body. Those must not eat each other.
- A row is never marked duplicate merely because a differently-sourced row
  shares its title.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from jobmatch.database import get_connection

log = logging.getLogger(__name__)

MIN_SCORE = 7
_EMPTY_URL = {"", "none", "null", "n/a", "na"}


def _canonical_url(application_url: object, url: object) -> str:
    """Real apply link, else source URL. Literal 'None' is not a URL."""
    def clean(value: object) -> str:
        raw = str(value or "").strip()
        if raw.lower() in _EMPTY_URL:
            return ""
        return raw.lower()

    return clean(application_url) or clean(url)


def _body_key(full_description: object, description: object) -> str:
    text = str(full_description or "").strip() or str(description or "").strip()
    return re.sub(r"\s+", " ", text).strip().lower()


def _keep_order(row: sqlite3.Row) -> tuple:
    return (-(row["fit_score"] or 0), row["scored_at"] or "", row["rowid"])


def _mark_losers(group: list[sqlite3.Row], reason: str, marked: dict[int, str]) -> None:
    if len(group) < 2:
        return
    ordered = sorted(group, key=_keep_order)
    for loser in ordered[1:]:
        marked.setdefault(int(loser["rowid"]), reason)


def run_dedup(conn: sqlite3.Connection | None = None) -> dict:
    """Mark duplicate scored jobs. Keep the highest score; oldest scored_at wins ties."""
    if conn is None:
        conn = get_connection()
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT rowid, url, application_url, title, company,
               description, full_description, fit_score, scored_at
        FROM jobs
        WHERE status = 'active' AND fit_score >= ?
        """,
        (MIN_SCORE,),
    ).fetchall()

    marked: dict[int, str] = {}

    by_url: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        key = _canonical_url(row["application_url"], row["url"])
        if key:
            by_url.setdefault(key, []).append(row)
    for group in by_url.values():
        _mark_losers(group, "duplicate url", marked)

    by_title_company: dict[tuple[str, str], list[sqlite3.Row]] = {}
    by_title: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if int(row["rowid"]) in marked:
            continue
        title = (row["title"] or "").strip().lower()
        company = (row["company"] or "").strip()
        if not title:
            continue
        if company:
            by_title_company.setdefault((title, company.lower()), []).append(row)
        else:
            by_title.setdefault(title, []).append(row)

    for group in by_title_company.values():
        clusters: dict[str, list[sqlite3.Row]] = {}
        for row in group:
            clusters.setdefault(_body_key(row["full_description"], row["description"]), []).append(row)
        for cluster in clusters.values():
            _mark_losers(cluster, "duplicate title+company", marked)

    for group in by_title.values():
        clusters = {}
        for row in group:
            body = _body_key(row["full_description"], row["description"])
            if not body:
                continue
            clusters.setdefault(body, []).append(row)
        for cluster in clusters.values():
            _mark_losers(cluster, "duplicate title", marked)

    for rowid, reason in marked.items():
        conn.execute(
            "UPDATE jobs SET status = 'duplicate', status_reason = ? WHERE rowid = ?",
            (reason, rowid),
        )
    conn.commit()

    by_url_n = sum(1 for reason in marked.values() if reason == "duplicate url")
    by_tc_n = sum(1 for reason in marked.values() if reason == "duplicate title+company")
    by_title_n = sum(1 for reason in marked.values() if reason == "duplicate title")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND fit_score >= ?",
        (MIN_SCORE,),
    ).fetchone()[0]

    if marked:
        log.info(
            "Dedup: marked %d duplicates (%d by url, %d by title+company, %d by title+body), %d active remaining",
            len(marked), by_url_n, by_tc_n, by_title_n, remaining,
        )

    return {
        "duplicates_removed": len(marked),
        "by_url": by_url_n,
        "by_title_company": by_tc_n,
        "by_title_only": by_title_n,
        "total_remaining": remaining,
    }
