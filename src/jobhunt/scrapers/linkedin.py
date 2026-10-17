"""LinkedIn (linkedin.com) job search scraper.

LinkedIn is the most anti-bot-hardened of the supported portals: it aggressively
detects automation (headless fingerprinting, rate limiting, login walls,
occasional "please verify" challenges) and its markup changes more often than
Seek/Jora. This scraper is built to degrade gracefully rather than crash the
whole pipeline:
  - Requires a persistent, already logged-in browser context (see `jobhunt
    login linkedin`, scrapers/base.py). Running headless without a valid
    session will just redirect to a login/verification page and return zero
    results (detected via _looks_logged_out below).
  - Uses LinkedIn's own `f_TPR` search parameter to filter by posting recency
    (mapped from config.search.date_range_days) rather than relying on
    unreliable in-DOM "posted X days ago" parsing.
  - Paginates through LinkedIn's results (25 listings/page) up to
    MAX_PAGES pages so a search isn't silently capped at whatever loaded on
    the first screen. Since f_TPR already restricts results server-side to
    the requested date range, pagination stops as soon as a page returns no
    new listings (either end of results, or LinkedIn starts throttling) -
    it does not need to separately re-check dates client-side.
  - Wrapped defensively: any single card that fails to parse is skipped, not
    fatal; if the whole page structure doesn't match, returns an empty list
    with a clear log message rather than throwing, so `jobhunt search` can
    continue with other portals/keywords (see cli.py `search` command).
  - Uses conservative pacing (small delays between page loads) to reduce the
    chance of triggering rate limiting/blocks on a personal account. This is
    still automation of a ToS-restricted site; the user has explicitly
    accepted that risk (see docs/architecture.md).
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse

from playwright.sync_api import Page

from jobhunt.db import JobListing

NAME = "linkedin"
base_url = "https://www.linkedin.com"

SEARCH_PATH = "/jobs/search/"
RESULTS_PER_PAGE = 25
MAX_PAGES = 10


def _f_tpr_for_days(date_range_days: int | None) -> str | None:
    """Map a day count to LinkedIn's f_TPR=r<seconds> recency filter."""
    if not date_range_days or date_range_days <= 0:
        return None
    seconds = date_range_days * 86400
    return f"r{seconds}"


def _build_search_url(
    base_url: str, keyword: str, location: str, date_range_days: int | None, start: int = 0
) -> str:
    params = {
        "keywords": keyword,
        "start": str(start),
    }
    if location and location.strip().lower() != "remote":
        params["location"] = location
    if location and location.strip().lower() == "remote":
        params["f_WT"] = "2"  # LinkedIn's "Remote" work-type filter
    f_tpr = _f_tpr_for_days(date_range_days)
    if f_tpr:
        params["f_TPR"] = f_tpr
    query = urllib.parse.urlencode(params)
    return f"{base_url}{SEARCH_PATH}?{query}"


def _looks_logged_out(page: Page) -> bool:
    url = page.url
    if "/login" in url or "/authwall" in url or "/checkpoint" in url:
        return True
    if page.query_selector("form.login__form, #login-form"):
        return True
    return False


def _extract_company(card) -> str:
    """Try several selectors/fallbacks since LinkedIn's markup for company name
    varies between the logged-in ("voyager") and public jobs-search layouts."""
    for selector in (
        "h4.base-search-card__subtitle",
        "span.job-card-container__primary-description",
        "div.job-card-container__company-name",
        "a.job-card-container__company-name",
        ".artdeco-entity-lockup__subtitle",
        "[data-test-job-card-company-name]",
    ):
        el = card.query_selector(selector)
        if el:
            text = el.inner_text().strip()
            if text:
                return text
    # Generic fallback: LinkedIn renames these classes fairly often between
    # layout versions, so also scan any element whose class name merely
    # *contains* "subtitle" or "company" (logged-in and guest layouts both
    # use one of these substrings for the company name element).
    generic = _find_by_class_substring(card, ("subtitle", "company"), exclude_substrings=("title",))
    if generic:
        return generic
    # Last resort: company logo images usually carry the company name in alt text.
    img = card.query_selector("img")
    if img:
        alt = (img.get_attribute("alt") or "").strip()
        # Strip generic suffixes some layouts append, e.g. "Acme Corp logo".
        alt = re.sub(r"\s*logo$", "", alt, flags=re.IGNORECASE).strip()
        if alt:
            return alt
    return ""


def _extract_location(card) -> str:
    for selector in (
        "span.job-search-card__location",
        "li.job-card-container__metadata-item",
        ".artdeco-entity-lockup__caption",
    ):
        el = card.query_selector(selector)
        if el:
            text = el.inner_text().strip()
            if text:
                return text
    return _find_by_class_substring(card, ("location", "metadata", "caption"))


def _find_by_class_substring(
    card, include_substrings: tuple[str, ...], exclude_substrings: tuple[str, ...] = ()
) -> str:
    """Scan descendant elements for a class name containing one of
    `include_substrings` (and none of `exclude_substrings`), returning the
    first non-empty inner text found. Used as a resilience net when
    LinkedIn's exact class names drift between layout/markup versions.

    Note: matching is done on whole class-name tokens (split on non-alphanumeric
    characters), not raw substrings - otherwise excluding "title" would also
    wrongly reject a legitimate "subtitle" class (which contains "title" as a
    plain substring).
    """
    try:
        elements = card.query_selector_all("[class]")
    except Exception:
        return ""
    for el in elements:
        cls = (el.get_attribute("class") or "").lower()
        tokens = set(re.split(r"[^a-z0-9]+", cls))
        if any(s in tokens for s in include_substrings) and not any(s in tokens for s in exclude_substrings):
            try:
                text = el.inner_text().strip()
            except Exception:
                continue
            if text:
                return text
    return ""


def _clean_title(raw: str) -> str:
    """LinkedIn card titles are frequently duplicated in the DOM (a visible
    span plus an aria-hidden/visually-hidden duplicate for screen readers),
    and sometimes have badge text appended (e.g. "with verification"). This
    collapses that down to a single clean title line."""
    if not raw:
        return ""
    # Split on newlines, strip, drop empties, and dedupe consecutive duplicates.
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    deduped: list[str] = []
    for line in lines:
        if not deduped or line.lower() != deduped[-1].lower():
            deduped.append(line)
    title = deduped[0] if deduped else raw.strip()
    # Strip known badge suffixes LinkedIn appends to the accessible name.
    title = re.sub(
        r"\s+with (verification|reactivate offer)\s*$", "", title, flags=re.IGNORECASE
    ).strip()
    return title


def _extract_listings_from_page(page: Page, base_url: str) -> list[JobListing]:
    listings: list[JobListing] = []
    cards = page.query_selector_all("ul.jobs-search__results-list li, div.job-card-container")
    for card in cards:
        try:
            title_el = card.query_selector(
                "a.job-card-list__title, a.job-card-container__link, h3.base-search-card__title"
            )
            link_el = card.query_selector(
                "a.job-card-list__title, a.job-card-container__link, a.base-card__full-link"
            )
            if not link_el:
                continue
            href = link_el.get_attribute("href") or ""
            if not href:
                continue
            job_url = urllib.parse.urljoin(base_url, href.split("?")[0])

            raw_title = title_el.inner_text() if title_el else link_el.inner_text()
            title = _clean_title(raw_title)
            company = _extract_company(card)
            job_location = _extract_location(card)

            listings.append(
                JobListing(
                    url=job_url,
                    portal=NAME,
                    title=title,
                    company=company,
                    location=job_location,
                    salary="",  # LinkedIn rarely surfaces salary on the search-results card
                )
            )
        except Exception:
            # Skip malformed/unexpected cards rather than failing the whole search.
            continue
    return listings


def _debug(msg: str) -> None:
    if os.environ.get("JOBHUNT_DEBUG_LINKEDIN"):
        print(f"[linkedin-debug] {msg}")


def _read_results_count(page: Page) -> str:
    """Best-effort read of LinkedIn's own "X results" header text, purely for
    debug/diagnostic logging - lets us tell whether a low card count is
    because LinkedIn genuinely has few results for this filter, or because
    we're under-scraping a page that has more."""
    for selector in (
        "small.jobs-search-results-list__text",
        "span.results-context-header__job-count",
        "h1.jobs-search-results-list__title",
        "[class*='results-context-header']",
    ):
        el = page.query_selector(selector)
        if el:
            try:
                text = el.inner_text().strip()
            except Exception:
                continue
            if text:
                return text
    return "(unknown)"


def _scroll_to_load_all_cards(page: Page, max_attempts: int = 10) -> None:
    """LinkedIn's authenticated job search results list is virtualized/lazy
    loaded: only cards currently scrolled into view exist in the DOM, even
    though the page conceptually has up to 25 results per `start` offset.
    Scrolling the *last currently-rendered card* into view is far more
    reliable than guessing the scroll-container CSS selector or blindly
    mouse-wheeling the window (which may not be the actual scroll parent in
    LinkedIn's split-pane layout) - Playwright's scroll_into_view_if_needed()
    walks up to whichever ancestor is actually scrollable."""
    last_count = -1
    for attempt in range(max_attempts):
        cards = page.query_selector_all("ul.jobs-search__results-list li, div.job-card-container")
        count = len(cards)
        _debug(f"scroll attempt {attempt}: {count} cards in DOM")
        if count == last_count:
            break
        last_count = count
        if cards:
            try:
                cards[-1].scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
        # Fallback attempts in case the card-level scroll didn't move anything
        # (e.g. selector mismatch) - harmless no-ops otherwise.
        try:
            page.mouse.wheel(0, 2500)
        except Exception:
            pass
        try:
            page.evaluate(
                """() => {
                    const el = document.querySelector(
                        'div.jobs-search-results-list, div.scaffold-layout__list, div.scaffold-layout__list-container'
                    );
                    if (el) { el.scrollTop = el.scrollHeight; }
                    window.scrollTo(0, document.body.scrollHeight);
                }"""
            )
        except Exception:
            pass
        time.sleep(1.0)


def search(
    page: Page,
    keyword: str,
    location: str,
    base_url: str = "https://www.linkedin.com",
    date_range_days: int | None = None,
) -> list[JobListing]:
    all_listings: list[JobListing] = []
    seen_urls: set[str] = set()

    for page_index in range(MAX_PAGES):
        start = page_index * RESULTS_PER_PAGE
        url = _build_search_url(base_url, keyword, location, date_range_days, start=start)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1.5)  # brief settle before checking auth state / scraping, reduces bot-detection footprint
        _debug(f"page {page_index} (start={start}): loaded {page.url}")

        if _looks_logged_out(page):
            raise RuntimeError(
                "LinkedIn session appears logged out or blocked (redirected to login/checkpoint). "
                "Run `jobhunt login linkedin` to refresh your session."
            )

        try:
            page.wait_for_selector("ul.jobs-search__results-list li, div.job-card-container", timeout=15000)
        except Exception:
            _debug(f"page {page_index}: no result-card selector found, stopping pagination")
            if os.environ.get("JOBHUNT_DEBUG_LINKEDIN"):
                _dump_debug_html(page, f"{keyword}_{location}_p{page_index}", "no_cards")
            # Page structure not matched (LinkedIn changed markup, or no results on this page) - stop paginating.
            break

        _debug(f"page {page_index}: LinkedIn reports results header: {_read_results_count(page)}")
        _scroll_to_load_all_cards(page)

        page_listings = _extract_listings_from_page(page, base_url)
        new_listings = [listing for listing in page_listings if listing.url not in seen_urls]
        _debug(
            f"page {page_index}: {len(page_listings)} cards extracted, "
            f"{len(new_listings)} new after dedup"
        )

        if not new_listings:
            # Either we've reached the end of results for this f_TPR window, or LinkedIn
            # is repeating/throttling. Either way, stop rather than looping needlessly.
            break

        for listing in new_listings:
            seen_urls.add(listing.url)
        all_listings.extend(new_listings)

        if len(page_listings) < RESULTS_PER_PAGE // 2:
            # A significantly short page (well under half a page) after full
            # scrolling almost always means we've hit the end of results for
            # this filter, not just an under-rendered virtualized list.
            _debug(f"page {page_index}: short page ({len(page_listings)}) after scrolling, stopping")
            if os.environ.get("JOBHUNT_DEBUG_LINKEDIN"):
                _dump_debug_html(page, f"{keyword}_{location}_p{page_index}", "short_page")
            break

        time.sleep(1.0)  # pace between page loads

    return all_listings


def fetch_job_description(page: Page, job_url: str) -> str:
    page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.5)

    if _looks_logged_out(page):
        raise RuntimeError(
            "LinkedIn session appears logged out or blocked while fetching job description. "
            "Run `jobhunt login linkedin` to refresh your session."
        )

    # LinkedIn job pages sometimes hide the full description behind a "Show more" toggle.
    try:
        show_more = page.query_selector(
            "button.show-more-less-html__button, button[aria-label*='Click to see more description']"
        )
        if show_more:
            show_more.click()
            time.sleep(0.5)
    except Exception:
        pass

    el = page.query_selector(
        "div.show-more-less-html__markup, div.jobs-description__content, div.description__text, "
        "div.jobs-description-content__text, article.jobs-description__container"
    )
    text = el.inner_text() if el else ""

    if not text.strip():
        # Layout drift fallback: scan for any element whose class contains
        # "description" and take the longest text found (avoids grabbing an
        # unrelated short snippet like a "description" label).
        candidates = page.query_selector_all("[class*='description' i]")
        best = ""
        for candidate in candidates:
            try:
                candidate_text = candidate.inner_text().strip()
            except Exception:
                continue
            if len(candidate_text) > len(best):
                best = candidate_text
        text = best

    if not text.strip() and os.environ.get("JOBHUNT_DEBUG_LINKEDIN"):
        _dump_debug_html(page, job_url, "jd_empty")

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _dump_debug_html(page: Page, context: str, reason: str) -> None:
    """When JOBHUNT_DEBUG_LINKEDIN=1 is set, dump the full page HTML to
    state/debug/ so real markup can be inspected/diagnosed without needing
    interactive access to a live LinkedIn session."""
    try:
        from pathlib import Path

        debug_dir = Path("state/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", context)[:80]
        out_path = debug_dir / f"linkedin_{reason}_{safe_name}.html"
        out_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
