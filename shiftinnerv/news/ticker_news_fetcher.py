"""
shiftinnerv/news/ticker_news_fetcher.py
Item 21 — Deterministic News & Macro Context Injection

Fetches recent news headlines and sentiment for a ticker via the
Alpha Vantage News Sentiment API.

Alpha Vantage free tier:
  - 25 requests/day (sufficient for daily 4-pair bond/equity runs)
  - Covers equities AND ETFs (TLT, IEF, TIP, GOVT etc.)
  - Returns per-article sentiment scores (Bullish/Bearish/Neutral)
  - No separate ETF restriction

Environment variable:
  ALPHA_VANTAGE_KEY   — Alpha Vantage API key (get free at alphavantage.co)

Falls back to empty list if key not set or fetch fails. Never raises.

Return format (same interface as previous Tiingo implementation):
  [{ticker, headline, source, published_utc, sentiment, sentiment_score}]

The extra sentiment fields are optional — downstream consumers that only
expect {ticker, headline, source, published_utc} will work unchanged.
"""

import logging
import os
import time
import threading
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger(__name__)

_TIMEOUT  = 10
_AV_BASE  = "https://www.alphavantage.co/query"

# ── Rate limiter ─────────────────────────────────────────────────────────────
# Alpha Vantage free tier: ~5 requests/minute.
# Enforce 1.2s between calls to stay well under that limit.
# AV_MIN_INTERVAL_SECS env var overrides for paid tiers.
_av_lock           = threading.Lock()
_av_last_call_time = 0.0


def _av_rate_check() -> bool:
    """
    Enforce minimum inter-call spacing.
    Sleeps as needed. Always returns True.
    """
    global _av_last_call_time

    min_interval = float(os.getenv("AV_MIN_INTERVAL_SECS", "1.2"))

    with _av_lock:
        elapsed = time.monotonic() - _av_last_call_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _av_last_call_time = time.monotonic()
        return True


def _get_av_key() -> str:
    return os.getenv("ALPHA_VANTAGE_KEY", "").strip()


def fetch_ticker_headlines(ticker: str,
                           lookback_hours: int = 48,
                           max_headlines: int = 3) -> list[dict]:
    """
    Fetch recent news headlines and sentiment for a ticker via
    Alpha Vantage News Sentiment API.

    Returns a list of dicts:
        {ticker, headline, source, published_utc, sentiment, sentiment_score}

    sentiment        : "Bullish" | "Somewhat-Bullish" | "Neutral" |
                       "Somewhat-Bearish" | "Bearish"
    sentiment_score  : float in [-1, 1]  (negative = bearish)

    Returns [] if ALPHA_VANTAGE_KEY not set or fetch fails.
    Never raises.
    """
    # FX pairs (Yahoo Finance =X suffix) — Alpha Vantage uses different
    # forex endpoint; skip here to avoid noisy failures
    if ticker.upper().endswith("=X"):
        log.debug(
            f"[ticker_news_fetcher] Skipping FX ticker {ticker}"
            " — use forex-specific news source for FX pairs"
        )
        return []

    api_key = _get_av_key()
    if not api_key:
        log.debug(
            "[ticker_news_fetcher] ALPHA_VANTAGE_KEY not set — "
            "skipping news headlines"
        )
        return []

    # Alpha Vantage time_from format: YYYYMMDDTHHMM
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    time_from = cutoff.strftime("%Y%m%dT%H%M")

    params = {
        "function":   "NEWS_SENTIMENT",
        "tickers":    ticker.upper(),
        "time_from":  time_from,
        "sort":       "LATEST",
        "limit":      min(max_headlines * 3, 50),  # over-fetch, then trim
        "apikey":     api_key,
    }

    if not _av_rate_check():
        return []

    try:
        r = requests.get(_AV_BASE, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        log.warning(f"[ticker_news_fetcher] Alpha Vantage fetch failed for {ticker}: {exc}")
        return []
    except (ValueError, TypeError) as exc:
        log.warning(f"[ticker_news_fetcher] Alpha Vantage JSON parse error for {ticker}: {exc}")
        return []
    except Exception as exc:
        log.warning(f"[ticker_news_fetcher] Unexpected error for {ticker}: {exc}")
        return []

    # Alpha Vantage returns {"feed": [...], "items": "N", ...}
    # or {"Information": "..."} on rate limit
    if "Information" in data:
        log.warning(
            f"[ticker_news_fetcher] Alpha Vantage rate limit hit for {ticker}: "
            f"{data['Information'][:120]}"
        )
        return []

    if "Note" in data:
        log.warning(
            f"[ticker_news_fetcher] Alpha Vantage note for {ticker}: "
            f"{data['Note'][:120]}"
        )
        return []

    feed = data.get("feed", [])
    if not isinstance(feed, list):
        return []

    results: list[dict] = []

    for article in feed:
        headline = (article.get("title") or "").strip()
        if not headline:
            continue

        # Published date: "20260529T143000" → "2026-05-29"
        pub_raw = article.get("time_published", "")
        pub_utc = ""
        if isinstance(pub_raw, str) and len(pub_raw) >= 8:
            pub_utc = f"{pub_raw[:4]}-{pub_raw[4:6]}-{pub_raw[6:8]}"

        source = article.get("source", "")

        # Per-ticker sentiment within this article
        # ticker_sentiment_scores is a list of {ticker, relevance_score,
        # ticker_sentiment_score, ticker_sentiment_label}
        sentiment       = "Neutral"
        sentiment_score = 0.0
        for ts in article.get("ticker_sentiment", []):
            if ts.get("ticker", "").upper() == ticker.upper():
                sentiment       = ts.get("ticker_sentiment_label", "Neutral")
                try:
                    sentiment_score = float(ts.get("ticker_sentiment_score", 0.0))
                except (ValueError, TypeError):
                    sentiment_score = 0.0
                break

        results.append({
            "ticker":          ticker.upper(),
            "headline":        headline,
            "source":          source,
            "published_utc":   pub_utc,
            "sentiment":       sentiment,
            "sentiment_score": sentiment_score,
        })

        if len(results) >= max_headlines:
            break

    return results
