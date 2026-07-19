"""Stub scraper — Indeed is high anti-bot risk; implement per phased plan
(docs/implementation-plan.md Phase 5). Kept as a separate module so a broken/
unimplemented portal never blocks other portals in `jobhunt search`.
"""
from __future__ import annotations

from playwright.sync_api import Page

from jobhunt.db import JobListing

NAME = "indeed"
base_url = "https://au.indeed.com"


def search(page: Page, keyword: str, location: str, base_url: str = "https://au.indeed.com") -> list[JobListing]:
    raise NotImplementedError(
        "Indeed scraper not yet implemented — see docs/implementation-plan.md Phase 5."
    )


def fetch_job_description(page: Page, job_url: str) -> str:
    raise NotImplementedError("Indeed scraper not yet implemented.")
