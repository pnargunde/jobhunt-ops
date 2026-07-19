"""Jora (jora.com) scraper — implement per phased plan (docs/implementation-plan.md
Phase 2/5). Jora is expected to be comparatively low anti-bot risk like Seek;
stubbed here so `jobhunt search --portal jora` fails clearly until implemented.
"""
from __future__ import annotations

from playwright.sync_api import Page

from jobhunt.db import JobListing

NAME = "jora"
base_url = "https://au.jora.com"


def search(page: Page, keyword: str, location: str, base_url: str = "https://au.jora.com") -> list[JobListing]:
    raise NotImplementedError(
        "Jora scraper not yet implemented — see docs/implementation-plan.md Phase 2/5."
    )


def fetch_job_description(page: Page, job_url: str) -> str:
    raise NotImplementedError("Jora scraper not yet implemented.")
