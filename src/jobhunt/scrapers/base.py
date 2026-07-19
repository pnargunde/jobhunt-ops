"""Common interface for portal scrapers, backed by a persistent Playwright
browser context so the user's logged-in session (cookies) survives between runs.

Each portal module (seek.py, jora.py, linkedin.py, indeed.py) implements
`search(page, keyword, location, config) -> list[JobListing]`.

Scrapers are intentionally isolated per-portal: if one portal's page structure
changes and breaks its scraper, that should not prevent other portals (or the
rest of the pipeline) from running. Callers should wrap each portal call in a
try/except and log+continue (see cli.py `search` command).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from playwright.sync_api import BrowserContext, Page, sync_playwright

from jobhunt.db import JobListing

REPO_ROOT = Path(__file__).resolve().parents[3]
BROWSER_PROFILE_DIR = REPO_ROOT / "state" / "browser_profile"


class PortalScraper(Protocol):
    name: str

    def search(self, page: Page, keyword: str, location: str) -> list[JobListing]:
        ...


def browser_profile_path(portal: str) -> Path:
    path = BROWSER_PROFILE_DIR / portal
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_persistent_context(portal: str, headless: bool = True) -> BrowserContext:
    """Open (or create) a persistent Chromium context for a given portal.

    The first time this runs for a portal, log in manually with
    `jobhunt login <portal>` (headless=False) so cookies/session are saved
    under state/browser_profile/<portal>/. Subsequent runs reuse that session.
    """
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(browser_profile_path(portal)),
        headless=headless,
    )
    # Keep a reference so playwright isn't garbage-collected/stopped early.
    context._jobhunt_playwright = playwright  # type: ignore[attr-defined]
    return context


def close_persistent_context(context: BrowserContext) -> None:
    playwright = getattr(context, "_jobhunt_playwright", None)
    context.close()
    if playwright is not None:
        playwright.stop()
