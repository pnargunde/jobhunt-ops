"""Seek (seek.com.au) scraper.

Uses the persistent, logged-in Playwright context from scrapers/base.py.
Selectors are based on Seek's public search-results page structure as of
2024/2025 and WILL need occasional adjustment if Seek changes their markup —
this is expected and documented as a known risk in docs/architecture.md.
"""
from __future__ import annotations

import re
import urllib.parse

from playwright.sync_api import Page

from jobhunt.db import JobListing

NAME = "seek"
base_url = "https://www.seek.com.au"


def _build_search_url(base_url: str, keyword: str, location: str) -> str:
    query = urllib.parse.quote_plus(keyword)
    where = urllib.parse.quote_plus(location) if location and location.lower() != "remote" else ""
    url = f"{base_url}/{query}-jobs"
    if where:
        url += f"/in-{where}"
    return url


def search(page: Page, keyword: str, location: str, base_url: str = "https://www.seek.com.au") -> list[JobListing]:
    url = _build_search_url(base_url, keyword, location)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("article", timeout=15000)

    listings: list[JobListing] = []
    cards = page.query_selector_all("article")
    for card in cards:
        try:
            title_el = card.query_selector("a[data-automation='jobTitle']")
            if not title_el:
                continue
            title = title_el.inner_text().strip()
            href = title_el.get_attribute("href") or ""
            job_url = urllib.parse.urljoin(base_url, href.split("?")[0])

            company_el = card.query_selector("a[data-automation='jobCompany'], span[data-automation='jobCompany']")
            company = company_el.inner_text().strip() if company_el else ""

            location_el = card.query_selector("a[data-automation='jobLocation'], span[data-automation='jobLocation']")
            job_location = location_el.inner_text().strip() if location_el else ""

            salary_el = card.query_selector("span[data-automation='jobSalary']")
            salary = salary_el.inner_text().strip() if salary_el else ""

            listings.append(
                JobListing(
                    url=job_url,
                    portal=NAME,
                    title=title,
                    company=company,
                    location=job_location,
                    salary=salary,
                )
            )
        except Exception:
            # Skip malformed/unexpected cards rather than failing the whole search.
            continue

    return listings


def fetch_job_description(page: Page, job_url: str) -> str:
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_selector("[data-automation='jobAdDetails']", timeout=15000)
    el = page.query_selector("[data-automation='jobAdDetails']")
    text = el.inner_text() if el else ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()
