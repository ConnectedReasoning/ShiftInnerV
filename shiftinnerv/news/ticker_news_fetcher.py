"""
shiftinnerv/news/ticker_news_fetcher.py
Item 21 — Deterministic News & Macro Context Injection

Fetches recent news headlines for a ticker via the Tiingo news API.

Supports both TIINGO_API_KEY (Item 21 convention) and TIINGO_KEY
(existing dossier.py convention) — checks both, prefers TIINGO_KEY
if set to maintain backward compatibility with existing configuration.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger(__name__)

_TIMEOUT    = 10
_TIINGO_BASE = "https://api.tiingo.com"


def _get_tiingo_key() -> str:
    """
    Return the Tiingo API key, checking both environment variable spellings.
    TIINGO_KEY (existing convention) takes priority over TIINGO_API_KEY (Item 21).
    """
    return (
        os.getenv("TIINGO_KEY", "")
        or os.getenv("TIINGO_API_KEY", "")
    )


def fetch_ticker_headlines(ticker: str,
                            lookback_hours: int = 48,
                            max_headlines: int = 3) -> list[dict]:
    """
    Fetch recent news headlines for a ticker via Tiingo news API.

    Returns a list of dicts: {ticker, headline, source, published_utc}
    Returns [] if TIINGO_KEY / TIINGO_API_KEY not set or fetch fails.

    Endpoint: https://api.tiingo.com/tiingo/news
    Parameters: tickers={ticker}, startDate={lookback}, token={key}
    Sort by publishedDate desc, take top max_headlines.

    Never raises.
    """
    # FX pairs (Yahoo Finance =X suffix) are not served by Tiingo news
    if ticker.upper().endswith("=X"):
        log.debug(
            f"[ticker_news_fetcher] Skipping FX ticker {ticker}"
            " — Tiingo covers equities only"
        )
        return []

    api_key = _get_tiingo_key()
    if not api_key:
        log.warning(
            "[ticker_news_fetcher] TIINGO_KEY / TIINGO_API_KEY not set — "
            "skipping Tier 3 headlines"
        )
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    start_date = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    url = f"{_TIINGO_BASE}/tiingo/news"
    params = {
        "tickers":   ticker.upper(),
        "startDate": start_date,
        "token":     api_key,
        "sortBy":    "publishedDate",
        "limit":     max_headlines * 3,  # over-fetch then trim
    }

    try:
        r = requests.get(
            url,
            params=params,
            timeout=_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        items = r.json()
    except requests.RequestException as exc:
        log.warning(f"[ticker_news_fetcher] Tiingo fetch failed for {ticker}: {exc}")
        return []
    except (ValueError, TypeError) as exc:
        log.warning(f"[ticker_news_fetcher] Tiingo JSON parse error for {ticker}: {exc}")
        return []
    except Exception as exc:
        log.warning(f"[ticker_news_fetcher] Unexpected error for {ticker}: {exc}")
        return []

    if not isinstance(items, list):
        return []

    results: list[dict] = []
    for item in items:
        headline = (item.get("title") or item.get("description") or "").strip()
        if not headline:
            continue

        # Parse published date
        pub_raw = item.get("publishedDate") or item.get("pubDate") or ""
        if isinstance(pub_raw, str) and pub_raw:
            pub_utc = pub_raw[:10]  # YYYY-MM-DD
        else:
            pub_utc = ""

        # Source / publisher name
        source = ""
        src_field = item.get("source") or item.get("publisher") or ""
        if isinstance(src_field, str):
            source = src_field
        elif isinstance(src_field, dict):
            source = src_field.get("name", "") or src_field.get("displayName", "")

        results.append({
            "ticker":        ticker.upper(),
            "headline":      headline,
            "source":        source,
            "published_utc": pub_utc,
        })

        if len(results) >= max_headlines:
            break

    return results
