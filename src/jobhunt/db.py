"""SQLite state store for scraped/tracked jobs.

Schema keyed by URL so re-running `search` daily just upserts: new listings
are inserted with status='new'; existing listings get last_seen refreshed
but keep first_seen and any manually-set status untouched.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "state" / "jobhunt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    url TEXT PRIMARY KEY,
    portal TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    salary TEXT,
    jd_text TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    match_score INTEGER,
    match_rationale TEXT,
    resume_path TEXT,
    cover_letter_path TEXT
);
"""


@dataclass
class JobListing:
    """A single job posting as extracted by a scraper, before it's in the DB."""

    url: str
    portal: str
    title: str
    company: str
    location: str = ""
    salary: str = ""
    jd_text: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


def upsert_job(conn: sqlite3.Connection, listing: JobListing) -> None:
    """Insert a new job or refresh last_seen/jd_text on an existing one.

    Never overwrites `status` or `first_seen` on existing rows, so manual
    status changes (applied/interviewing/rejected/ignored) survive re-runs.
    """
    now = _now()
    conn.execute(
        """
        INSERT INTO jobs (url, portal, title, company, location, salary, jd_text, first_seen, last_seen, status)
        VALUES (:url, :portal, :title, :company, :location, :salary, :jd_text, :now, :now, 'new')
        ON CONFLICT(url) DO UPDATE SET
            title = excluded.title,
            company = excluded.company,
            location = excluded.location,
            salary = excluded.salary,
            jd_text = excluded.jd_text,
            last_seen = excluded.last_seen
        """,
        {**asdict(listing), "now": now},
    )
    conn.commit()


def set_match_score(conn: sqlite3.Connection, url: str, score: int, rationale: str) -> None:
    conn.execute(
        "UPDATE jobs SET match_score = ?, match_rationale = ? WHERE url = ?",
        (score, rationale, url),
    )
    conn.commit()


def set_generated_docs(conn: sqlite3.Connection, url: str, resume_path: str, cover_letter_path: str) -> None:
    conn.execute(
        "UPDATE jobs SET resume_path = ?, cover_letter_path = ? WHERE url = ?",
        (resume_path, cover_letter_path, url),
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, url: str, status: str) -> None:
    conn.execute("UPDATE jobs SET status = ? WHERE url = ?", (status, url))
    conn.commit()


def get_jobs_needing_score(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE match_score IS NULL").fetchall()


def get_jobs_for_docgen(conn: sqlite3.Connection, min_score: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE match_score >= ? AND resume_path IS NULL",
        (min_score,),
    ).fetchall()


def get_all_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs ORDER BY match_score DESC NULLS LAST, first_seen DESC").fetchall()


def get_job(conn: sqlite3.Connection, url: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
