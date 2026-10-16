"""Seek (seek.com.au) scraper.

Seek is a public site (no login/session required) with standard
server-rendered, paginated search results and stable `data-automation="..."`
attributes for its key fields - a much more reliable scraping target than
LinkedIn's obfuscated/frequently-changing class names and virtualized,
login-gated results list. Selectors are based on Seek's public search-results
page structure as of 2024/2025 and WILL need occasional adjustment if Seek
changes their markup - this is expected and documented as a known risk in
docs/architecture.md.

Seek's canonical domain is au.seek.com (seek.com.au redirects there), and its
search URLs use lowercase, dash-separated slugs, e.g.:
    https://au.seek.com/technical-lead-jobs/in-All-Sydney-NSW
The "All-" prefix on the location segment matters - Seek uses it to mean "the
whole named region" rather than a specific suburb; omitting it can silently
change/narrow the match and was the cause of an earlier "0 listings" bug.
"""
from __future__ import annotations

import re
import urllib.parse

from playwright.sync_api import Page

from jobhunt.db import JobListing

NAME = "seek"
base_url = "https://au.seek.com"

MAX_PAGES = 10

# Seek's `daterange` query param accepts a day count; it snaps internally to
# the nearest option Seek's UI offers (1, 3, 7, 14, 31), but passing the raw
# value from config still works for filtering purposes.


def _slugify(value: str) -> str:
    """Lowercase, dash-separated slug matching Seek's URL path segment style
    (e.g. "Technical Lead" -> "technical-lead").

    Seek keeps "+" characters (URL-percent-encoded as %2B) rather than
    stripping them - e.g. "C++" -> "c%2B%2B-jobs". Naively stripping all
    non-alphanumeric characters previously collapsed "C++" down to just "c",
    silently turning it into a near-meaningless one-letter search that
    matched unrelated jobs (diamond drillers, security officers, etc.)."""
    slug = value.strip().lower()
    slug = re.sub(r"[^a-z0-9+]+", "-", slug)
    slug = slug.strip("-")
    return slug.replace("+", "%2B")


def _location_slug(location: str) -> str:
    """Seek location path segments are dash-separated but keep their original
    capitalisation (e.g. "Sydney NSW" -> "Sydney-NSW"), prefixed with "All-"
    to mean the whole named region rather than a specific suburb."""
    parts = location.strip().split()
    return "All-" + "-".join(parts)


def _build_search_url(base_url: str, keyword: str, location: str, date_range_days: int | None, page_num: int = 1) -> str:
    keyword_slug = _slugify(keyword)
    url = f"{base_url}/{keyword_slug}-jobs"
    if location and location.strip().lower() != "remote":
        url += f"/in-{_location_slug(location)}"
    params = {}
    if location and location.strip().lower() == "remote":
        params["workarrangement"] = "1"  # Seek's "Remote" work-arrangement filter
    if date_range_days and date_range_days > 0:
        params["daterange"] = str(date_range_days)
    if page_num > 1:
        params["page"] = str(page_num)
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    return url


def _extract_listings_from_page(page: Page, base_url: str) -> list[JobListing]:
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


def search(
    page: Page,
    keyword: str,
    location: str,
    base_url: str = "https://au.seek.com",
    date_range_days: int | None = None,
) -> list[JobListing]:
    all_listings: list[JobListing] = []
    seen_urls: set[str] = set()

    for page_num in range(1, MAX_PAGES + 1):
        url = _build_search_url(base_url, keyword, location, date_range_days, page_num=page_num)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector("article", timeout=15000)
        except Exception:
            # No results on this page (e.g. ran past the last page) - stop paginating.
            break

        page_listings = _extract_listings_from_page(page, base_url)
        new_listings = [listing for listing in page_listings if listing.url not in seen_urls]

        if not new_listings:
            # No new (deduped) listings - either the last page repeated, or truly
            # no more results. Either way, stop rather than looping needlessly.
            break

        for listing in new_listings:
            seen_urls.add(listing.url)
        all_listings.extend(new_listings)

        # Seek shows a "No matching search results" state or a disabled/absent
        # "Next" pagination control on the last page - checking for the Next
        # link is a cheap, reliable stop signal in addition to the dedup check.
        next_link = page.query_selector("a[data-automation='page-next']")
        if not next_link:
            break

    return all_listings


def fetch_job_description(page: Page, job_url: str) -> str:
    page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("[data-automation='jobAdDetails']", timeout=15000)
    el = page.query_selector("[data-automation='jobAdDetails']")
    text = el.inner_text() if el else ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()
