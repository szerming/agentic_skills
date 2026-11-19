"""LinkedIn browser automation.

Ported from agentic_workflow's linkedin_browser.py, with three changes that
matter for skill use:

1. Every policy that was hardcoded inside the class is now a BrowserConfig
   field: geo id, seniority, workplace types, page cap, headless, delay.
2. One browser instance serves a whole run. The reference launched a fresh
   Chromium inside every tool call, which forced an asyncio.Semaphore(1) to
   serialise them.
3. A failed description fetch returns an error instead of caching the error
   text as if it were the posting.

Playwright is imported lazily so that schema, cache and validation commands work
without it installed.
"""

from __future__ import annotations

import asyncio
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_models import JobDescription, JobEntry

SEARCH_URL = "https://www.linkedin.com/jobs/search/?"
RECOMMENDED_URL = "https://www.linkedin.com/jobs/collections/recommended/"
LOGIN_URL = "https://www.linkedin.com/login"
SESSION_COOKIE = "li_at"
DEFAULT_GEO_ID = "102057244"  # Cambridgeshire; LinkedIn's geo id, not a city id
PAGE_SIZE = 25

# Selectors are the fragile part of this skill. They live in one place so a DOM
# change is a one-file fix, and `describe --save-html` dumps the page when they
# break.
#
# LinkedIn replaced the old `.scaffold-layout__list` markup in 2026 with a
# virtualised React list. Cards are no longer anchors: each one is a role=button
# keyed by job id, so the posting URL is rebuilt from the key.
SEL_RESULTS_LIST = '[role="button"][componentkey^="job-card-component-ref-"]'
SEL_CARD = '[role="button"][componentkey^="job-card-component-ref-"]'
SEL_CARD_KEY_PREFIX = "job-card-component-ref-"
SEL_NEXT_PAGE = 'button[aria-label="Next"]'
SEL_DESCRIPTION = (
    'xpath=//h2[normalize-space(.)="About the job"]'
    '/following::span[@data-testid="expandable-text-box"][1]'
)
DESCRIPTION_HEADING = "About the job"
NO_JOBS_TEXT = "No matching jobs found."

# LinkedIn virtualises the results list: only the cards near the viewport are
# hydrated, the rest are empty placeholders. Each card has to be scrolled into
# view and given a moment to render before it can be read.
CARD_HYDRATE_DELAY = 0.3


class LinkedInError(RuntimeError):
    """Anything the browser layer refuses to do."""


class SessionExpired(LinkedInError):
    """The persistent profile has no LinkedIn session."""


@dataclass(frozen=True)
class BrowserConfig:
    session_dir: Path
    headless: bool = False
    max_pages: int = 5
    geo_id: str = DEFAULT_GEO_ID
    seniority_levels: tuple[int, ...] = (4, 5, 6)  # Mid-Senior, Director, Executive
    workplace_types: tuple[int, ...] = (1, 2, 3)  # On-site, Remote, Hybrid
    delay: float = 1.0
    timeout_ms: int = 15000
    # Google refuses OAuth ("browser or app may not be secure") from Playwright's
    # automation-flagged Chromium. Point these at a stock Chrome to sign in.
    channel: str | None = None
    executable_path: str | None = None


def build_search_url(
    keywords: str,
    *,
    past_days: int = 7,
    geo_id: str = DEFAULT_GEO_ID,
    seniority_levels: tuple[int, ...] = (4, 5, 6),
    workplace_types: tuple[int, ...] = (1, 2, 3),
    sort_by_recent: bool = True,
) -> str:
    """Build a LinkedIn job search URL. Pure, so it is cheap to test."""
    days = max(1, int(past_days))
    params: dict[str, str] = {
        "keywords": keywords,
        "geoId": geo_id,
        "f_TPR": f"r{days * 24 * 60 * 60}",
    }
    if sort_by_recent:
        params["sortBy"] = "DD"
    if seniority_levels:
        params["f_E"] = ",".join(str(level) for level in seniority_levels)
    if workplace_types:
        params["f_WT"] = ",".join(str(kind) for kind in workplace_types)
    # safe="()," keeps boolean expressions readable; LinkedIn accepts either form.
    return SEARCH_URL + urllib.parse.urlencode(params, safe="(),")


def with_page(url: str, page: int) -> str:
    """Append LinkedIn's `start` offset for page N (0-based)."""
    if url.endswith(("?", "&")):
        separator = ""
    elif "?" in url:
        separator = "&"
    else:
        separator = "?"
    return f"{url}{separator}start={page * PAGE_SIZE}"


class LinkedInBrowser:
    """One persistent Chromium context, reused for a whole run."""

    def __init__(self, config: BrowserConfig):
        self.config = config
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._session_checked = False

    async def __aenter__(self) -> "LinkedInBrowser":
        from playwright.async_api import async_playwright

        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.config.session_dir),
            headless=self.config.headless,
            viewport={"width": 1440, "height": 900},
            channel=self.config.channel,
            executable_path=self.config.executable_path,
        )
        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )
        self._page.set_default_timeout(self.config.timeout_ms)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()

    # -- session ---------------------------------------------------------

    async def has_session(self) -> bool:
        """True when the persisted profile holds a LinkedIn auth cookie."""
        if self._context is None:
            return False
        cookies = await self._context.cookies("https://www.linkedin.com")
        return any(cookie.get("name") == SESSION_COOKIE for cookie in cookies)

    async def require_session(self) -> None:
        """Fail fast, before any navigation, if the profile is logged out."""
        if self._session_checked:
            return
        if not await self.has_session():
            raise SessionExpired(
                "No LinkedIn session in "
                f"{self.config.session_dir}. Run the login command first: "
                "jobsearch.py login"
            )
        self._session_checked = True

    async def login(self, timeout_s: int = 600) -> bool:
        """Open the login page and wait for the human to finish, detecting success by cookie."""
        try:
            await self._page.goto(LOGIN_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise LinkedInError(f"could not open {LOGIN_URL}: {exc}") from exc
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            try:
                if await self.has_session():
                    self._session_checked = True
                    return True
            except Exception as exc:  # noqa: BLE001
                raise LinkedInError("the browser window closed before sign-in completed") from exc
            await asyncio.sleep(2)
        return False

    def _guard_url(self, url: str) -> None:
        """A redirect here means the session died mid-run; say so instead of scraping nothing."""
        lowered = url.lower()
        if "/login" in lowered or "authwall" in lowered or "/checkpoint" in lowered:
            raise SessionExpired(
                f"LinkedIn redirected to {url}. The session expired; re-run: jobsearch.py login"
            )

    # -- listing pages ---------------------------------------------------

    async def fetch_entries(self, base_url: str) -> list[JobEntry]:
        """Paginate a search or listing URL up to max_pages, stopping at the last page."""
        results: list[JobEntry] = []
        for page_index in range(max(1, self.config.max_pages)):
            page_results, has_next = await self.fetch_page(base_url, page_index)
            results.extend(page_results)
            if not has_next or not page_results:
                break
            await asyncio.sleep(self.config.delay)
        return results

    async def fetch_page(self, base_url: str, page_index: int) -> tuple[list[JobEntry], bool]:
        """One results page. Returns the entries found and whether a next page exists.

        The list is virtualised, so scrolling to the footer and reading the
        cards once keeps only the handful that happen to be hydrated. Walk the
        cards in order and keep each one as it renders instead.
        """
        url = with_page(base_url, page_index)
        entries: list[JobEntry] = []
        seen: set[str] = set()

        try:
            await self._page.goto(url, wait_until="domcontentloaded")
            self._guard_url(self._page.url)

            if await self._page.get_by_text(NO_JOBS_TEXT).is_visible(timeout=1000):
                return entries, False

            await self._page.wait_for_selector(SEL_RESULTS_LIST)

            cards = self._page.locator(SEL_CARD)
            total = await self._settle_cards(cards)
            for index in range(total):
                entry = await self._hydrate_card(cards.nth(index))
                if entry is None or entry.url in seen:
                    continue
                seen.add(entry.url)
                entries.append(entry)

            next_button = self._page.locator(SEL_NEXT_PAGE).first
            has_next = await next_button.is_visible() and await next_button.is_enabled()

        except SessionExpired:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the run
            print(
                f"[jobsearch] page {page_index + 1} failed ({exc}); keeping "
                f"{len(entries)} entries gathered so far",
                file=sys.stderr,
            )
            return entries, False

        return entries, has_next

    async def _settle_cards(self, cards) -> int:
        """Wait for the virtualised list to finish hydrating.

        LinkedIn renders the first few cards, then appends the rest a beat
        later (7 to 25 in about a second in practice). Counting once keeps the
        first batch and silently drops the rest.
        """
        count = await cards.count()
        stable = 0
        for _ in range(20):  # at most ~10s
            await asyncio.sleep(0.5)
            current = await cards.count()
            if current == count:
                stable += 1
                if stable >= 2:
                    break
            else:
                count = current
                stable = 0
        return count

    async def _hydrate_card(self, card) -> JobEntry | None:
        """Scroll one virtualised card into view, then read it."""
        try:
            await card.scroll_into_view_if_needed(timeout=3000)
        except Exception:  # noqa: BLE001 - a card we cannot scroll is still worth a read
            pass
        await asyncio.sleep(CARD_HYDRATE_DELAY)
        for _ in range(8):
            entry = await self._read_card(card)
            if entry is not None:
                return entry
            await asyncio.sleep(CARD_HYDRATE_DELAY)
        return None

    async def _read_card(self, card) -> JobEntry | None:
        """Read one result card. Missing fields degrade instead of aborting the page."""
        try:
            key = await card.get_attribute("componentkey") or ""
            if not key.startswith(SEL_CARD_KEY_PREFIX):
                return None
            job_id = key[len(SEL_CARD_KEY_PREFIX) :]
            if not job_id.isdigit():
                return None

            paragraphs = card.locator("p")
            if not await paragraphs.count():
                return None  # still a placeholder; the caller retries

            # The title paragraph also holds screen-reader badges such as
            # "Selected, " and "(Verified job)"; the visible title is the last
            # non-empty line.
            title_lines = [
                line.strip()
                for line in (await paragraphs.nth(0).inner_text()).splitlines()
                if line.strip()
            ]
            title = title_lines[-1] if title_lines else ""
            if not title:
                return None

            company = (
                (await paragraphs.nth(1).inner_text()).strip()
                if await paragraphs.count() > 1
                else ""
            )
            location = (
                (await paragraphs.nth(2).inner_text()).strip()
                if await paragraphs.count() > 2
                else ""
            )

            return JobEntry(
                title=title,
                url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                company=company,
                location=location,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[jobsearch] skipped unreadable card: {exc}", file=sys.stderr)
            return None

    async def _inner_text(self, card, selector: str) -> str:
        locator = card.locator(selector)
        if not await locator.count():
            return ""
        return (await locator.first.inner_text()).strip()

    async def fetch_search(self, keywords: str, past_days: int = 7) -> list[JobEntry]:
        url = build_search_url(
            keywords,
            past_days=past_days,
            geo_id=self.config.geo_id,
            seniority_levels=self.config.seniority_levels,
            workplace_types=self.config.workplace_types,
        )
        return await self.fetch_entries(url)

    async def fetch_recommended(self) -> list[JobEntry]:
        return await self.fetch_entries(RECOMMENDED_URL)

    async def fetch_listing(self, url: str) -> list[JobEntry]:
        return await self.fetch_entries(url)

    # -- descriptions ----------------------------------------------------

    async def get_description(
        self, entry: JobEntry, save_html: Path | None = None
    ) -> tuple[JobDescription, str | None]:
        """Fetch one posting.

        Returns (description, error). On failure the description is empty and the
        error is set; the caller must not cache a failure.
        """
        try:
            await self._page.goto(entry.url, wait_until="domcontentloaded")
            self._guard_url(self._page.url)

            body = self._page.locator(SEL_DESCRIPTION).first
            await body.wait_for(state="attached", timeout=self.config.timeout_ms)

            # The panel attaches with a short preview and then keeps filling in,
            # so reading the first non-empty value silently truncates long posts.
            # Wait until the length stops changing instead.
            text = ""
            previous = -1
            stable = 0
            for _ in range(40):  # at most ~12s
                text = (await body.inner_text()).strip()
                if text and len(text) == previous:
                    stable += 1
                    if stable >= 3:
                        break
                else:
                    stable = 0
                previous = len(text)
                await asyncio.sleep(0.3)
            # The expander is a button inside the text span, not part of the
            # posting; inner_text renders it as a trailing "… more".
            text = re.sub(r"\s*(?:…|\.\.\.)\s*more\s*$", "", text).strip()
            if not text:
                raise LinkedInError("description container was empty")

            return JobDescription(description=text, **entry.model_dump()), None
        except SessionExpired:
            raise
        except Exception as exc:  # noqa: BLE001
            if save_html is not None:
                await self._dump_html(save_html)
            return (
                JobDescription(description="", **entry.model_dump()),
                f"{type(exc).__name__}: {exc}",
            )

    async def _dump_html(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(await self._page.content(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[jobsearch] could not save html to {path}: {exc}", file=sys.stderr)
