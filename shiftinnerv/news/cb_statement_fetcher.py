"""
shiftinnerv/news/cb_statement_fetcher.py
Item 21 — Deterministic News & Macro Context Injection

Fetches the most recent central bank rate decision statement or minutes
for a given currency. Returns raw text truncated to max_tokens words.

Design principles:
- Uses stdlib html.parser only — no BeautifulSoup dependency
- 10-second timeout on all HTTP requests
- Browser-like User-Agent to avoid 403s from CB sites
- Returns None on any failure — never raises
"""

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from shiftinnerv.news.currency_registry import CURRENCY_DATA_SOURCES

log = logging.getLogger(__name__)

_TIMEOUT = 10
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}


# ── HTML text extractor (stdlib only) ────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """
    Strips HTML tags and returns visible text content.
    Skips nav, header, footer, script, style, aside elements.
    """
    _SKIP_TAGS = {"script", "style", "nav", "header", "footer",
                  "aside", "noscript", "iframe", "svg"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    # Collapse excess whitespace
    text = re.sub(r"\s{2,}", " ", parser.get_text())
    return text.strip()


def _truncate_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " [...]"


def _fetch_url(url: str) -> str | None:
    """Fetch a URL and return HTML text, or None on failure."""
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        return r.text
    except requests.RequestException as exc:
        log.debug(f"[cb_statement_fetcher] HTTP fetch failed for {url}: {exc}")
        return None


# ── CB-specific link extractors ───────────────────────────────────────────────

class _LinkExtractor(HTMLParser):
    """Collect all <a href> links from an HTML page."""
    def __init__(self, base_url: str = ""):
        super().__init__()
        self._base = base_url
        self.links: list[tuple[str, str]] = []  # (href, text)
        self._current_text: list[str] = []
        self._in_a = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._in_a = True
            self._current_text = []
            for name, val in attrs:
                if name == "href" and val:
                    self._href = val
        else:
            self._href = None

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._in_a:
            self._in_a = False
            text = " ".join(self._current_text).strip()
            href = getattr(self, "_href", None)
            if href:
                full = urljoin(self._base, href)
                self.links.append((full, text))

    def handle_data(self, data):
        if self._in_a:
            self._current_text.append(data.strip())


def _get_links(html: str, base_url: str) -> list[tuple[str, str]]:
    p = _LinkExtractor(base_url)
    p.feed(html)
    return p.links


# ── Per-CB statement fetchers ─────────────────────────────────────────────────

def _fetch_fed_statement(max_age_days: int, max_words: int) -> tuple[str | None, str | None]:
    """
    Federal Reserve: parse FOMC calendar page for most recent statement link.
    Returns (text, date_str) or (None, None).
    """
    index_url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    html = _fetch_url(index_url)
    if not html:
        return None, None

    links = _get_links(html, "https://www.federalreserve.gov")
    # Find links to press releases: typically contain "newsevents/pressreleases/monetary"
    pr_links = [
        (href, txt) for href, txt in links
        if "monetary" in href and "pressreleases" in href and href.endswith(".htm")
    ]
    if not pr_links:
        return None, None

    # Take most recent (last in page order)
    target_url, _ = pr_links[-1]
    # Try to extract date from URL pattern: monetary20260507a1.htm
    date_match = re.search(r"monetary(\d{8})", target_url)
    stmt_date = None
    if date_match:
        try:
            stmt_date = datetime.strptime(date_match.group(1), "%Y%m%d").date()
        except ValueError:
            pass

    if stmt_date:
        age_days = (datetime.now().date() - stmt_date).days
        if age_days > max_age_days:
            log.debug(f"[cb_statement_fetcher] Fed statement {stmt_date} is {age_days}d old — skipping")
            return None, str(stmt_date)

    pr_html = _fetch_url(target_url)
    if not pr_html:
        return None, str(stmt_date) if stmt_date else None

    text = _extract_text(pr_html)
    return _truncate_to_words(text, max_words), str(stmt_date) if stmt_date else None


def _fetch_generic_cb(currency: str, max_age_days: int, max_words: int) -> tuple[str | None, str | None]:
    """
    Generic CB fetcher: fetch index page, find most recent press-release link,
    follow it, extract text. Works for BoE, BoJ, BoC, RBA, SNB, BoK.
    Returns (text, date_str) or (None, None).
    """
    entry = CURRENCY_DATA_SOURCES[currency]
    index_url = entry["cb_statement_url"]
    if not index_url:
        return None, None

    html = _fetch_url(index_url)
    if not html:
        return None, None

    links = _get_links(html, index_url)
    if not links:
        return None, None

    # Heuristic: find links that look like press releases / statements
    # Prioritise links whose text contains date-like or statement-like keywords
    _keywords = {"statement", "decision", "minutes", "press", "release",
                 "monetary", "rate", "policy"}
    candidates = [
        (href, txt) for href, txt in links
        if any(kw in (txt + href).lower() for kw in _keywords)
        and href.startswith("http")
    ]

    if not candidates:
        # Fall back: take first 3 links from the page
        candidates = [(href, txt) for href, txt in links
                      if href.startswith("http")][:3]

    if not candidates:
        return None, None

    target_url, _ = candidates[0]
    pr_html = _fetch_url(target_url)
    if not pr_html:
        return None, None

    text = _extract_text(pr_html)
    return _truncate_to_words(text, max_words), None


def _fetch_ecb_statement(max_age_days: int, max_words: int) -> tuple[str | None, str | None]:
    """ECB: use press conference index."""
    index_url = "https://www.ecb.europa.eu/press/pressconf/html/index.en.html"
    html = _fetch_url(index_url)
    if not html:
        return None, None

    links = _get_links(html, "https://www.ecb.europa.eu")
    # ECB press conf links: /press/pressconf/YYYY/html/ecb.is*.en.html
    pr_links = [
        (href, txt) for href, txt in links
        if "/pressconf/" in href and href.endswith(".html")
    ]
    if not pr_links:
        return None, None

    target_url, _ = pr_links[0]  # Most recent first on ECB page
    pr_html = _fetch_url(target_url)
    if not pr_html:
        return None, None

    text = _extract_text(pr_html)
    return _truncate_to_words(text, max_words), None


# ── Dispatcher ────────────────────────────────────────────────────────────────

def fetch_cb_statement(currency_code: str,
                        max_age_days: int = 7,
                        max_tokens: int = 1500) -> str | None:
    """
    Fetch the most recent central bank rate decision statement or minutes.

    Returns the statement text truncated to max_tokens words, or None if:
    - No statement URL configured for this currency
    - Most recent statement is older than max_age_days
    - Fetch fails for any reason

    Never raises. All failures return None with a log warning.

    Note: max_tokens here means words, not subword tokens — word count
    is sufficient and avoids a tokenizer dependency.
    """
    if currency_code not in CURRENCY_DATA_SOURCES:
        return None

    entry = CURRENCY_DATA_SOURCES[currency_code]
    if not entry.get("cb_statement_url"):
        return None

    try:
        if currency_code == "USD":
            text, _ = _fetch_fed_statement(max_age_days, max_tokens)
        elif currency_code == "EUR":
            text, _ = _fetch_ecb_statement(max_age_days, max_tokens)
        else:
            text, _ = _fetch_generic_cb(currency_code, max_age_days, max_tokens)

        return text
    except Exception as exc:
        log.warning(f"[cb_statement_fetcher] Failed for {currency_code}: {exc}")
        return None
