"""Stub scraper — LinkedIn is high anti-bot risk; implement per phased plan
(docs/implementation-plan.md Phase 5). Kept as a separate module so a broken/
unimplemented portal never blocks other portals in `jobhunt search`.
"""
from __future__ import annotations

from playwright.sync_api import Page

from jobhunt.db import JobListing

NAME = "linkedin"
base_url = "https://www.linkedin.com"


def search(page: Page, keyword: str, location: str, base_url: str = "https://www.linkedin.com") -> list[JobListing]:
    raise NotImplementedError(
        "LinkedIn scraper not yet implemented — see docs/implementation-plan.md Phase 5."
    )


def fetch_job_description(page: Page, job_url: str) -> str:
    raise NotImplementedError("LinkedIn scraper not yet implemented.")
