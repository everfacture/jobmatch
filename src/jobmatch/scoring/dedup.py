"""Deduplicate high-fit jobs before notification.

Marks duplicate postings inactive — same title + company (case-insensitive) —
keeping only the highest-scored row. Ties broken by oldest scored_at.
"""

from __future__ import annotations

import logging
import sqlite3

from jobmatch.database import get_connection

log = logging.getLogger(__name__)

MIN_SCORE = 7


def run_dedup(conn: sqlite3.Connection | None = None) -> dict:
    """Mark duplicate scored jobs, keeping highest score per title+company."""
    if conn is None:
        conn = get_connection()

    base_filter = "status = 'active' AND fit_score >= ?"

    marked_with_company = conn.execute(f"""
        UPDATE jobs
        SET status = 'duplicate', status_reason = 'duplicate title+company'
        WHERE {base_filter}
          AND (company IS NOT NULL AND company != '')
          AND ROWID NOT IN (
              SELECT ROWID FROM (
                  SELECT ROWID,
                         ROW_NUMBER() OVER (
                             PARTITION BY LOWER(title), LOWER(company)
                             ORDER BY fit_score DESC, scored_at ASC
                         ) as rn
                  FROM jobs
                  WHERE {base_filter}
                    AND (company IS NOT NULL AND company != '')
              )
              WHERE rn = 1
          )
    """, (MIN_SCORE, MIN_SCORE)).rowcount

    marked_no_company = conn.execute(f"""
        UPDATE jobs
        SET status = 'duplicate', status_reason = 'duplicate title'
        WHERE {base_filter}
          AND (company IS NULL OR company = '')
          AND ROWID NOT IN (
              SELECT ROWID FROM (
                  SELECT ROWID,
                         ROW_NUMBER() OVER (
                             PARTITION BY LOWER(title)
                             ORDER BY fit_score DESC, scored_at ASC
                         ) as rn
                  FROM jobs
                  WHERE {base_filter}
                    AND (company IS NULL OR company = '')
              )
              WHERE rn = 1
          )
    """, (MIN_SCORE, MIN_SCORE)).rowcount

    conn.commit()

    total_marked = marked_with_company + marked_no_company
    remaining = conn.execute(
        f"SELECT COUNT(*) FROM jobs WHERE {base_filter}", (MIN_SCORE,)
    ).fetchone()[0]

    if total_marked > 0:
        log.info(
            "Dedup: marked %d duplicates (%d by title+company, %d by title), %d active remaining",
            total_marked, marked_with_company, marked_no_company, remaining,
        )

    return {
        "duplicates_removed": total_marked,
        "by_title_company": marked_with_company,
        "by_title_only": marked_no_company,
        "total_remaining": remaining,
    }
