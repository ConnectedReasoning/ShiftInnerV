"""
shiftinnerv/news/news_brief_builder.py
Item 21 — Deterministic News & Macro Context Injection

Assembles the three tiers of macro/news context into a formatted block
suitable for injection into the statistical brief that flows into the
narrative agent.

Architecture principle:
  - This module is the handoff point between the deterministic fetch layer
    and the LLM interpretation layer.
  - It fetches, formats, and returns a string.
  - It never interprets. It never raises.
  - All failures degrade gracefully — if all tiers are empty, returns "".

The assembled block is injected between the COMPOSITE SCORE section and
the closing delimiter of the statistical brief (see integration in tasks.py).
"""

import logging
import os
from datetime import datetime, timezone

from shiftinnerv.news.currency_registry import get_currencies_for_pair
from shiftinnerv.news.macro_fetcher import fetch_calendar_context
from shiftinnerv.news.cb_statement_fetcher import fetch_cb_statement
from shiftinnerv.news.ticker_news_fetcher import fetch_ticker_headlines

log = logging.getLogger(__name__)

# Flags returned alongside the context string so the router can
# adjust complexity scores without re-parsing the text.
_CB_DECISION_KEYWORDS = (
    "rate decision", "monetary policy", "policy decision",
    "basis points", "unchanged", "raised", "lowered",
    "press conference", "meeting minutes", "fomc", "mpc",
)


def _has_cb_text(text: str) -> bool:
    """Heuristic: does the assembled context contain CB statement prose?"""
    lower = text.lower()
    return any(kw in lower for kw in _CB_DECISION_KEYWORDS)


def _has_macro_surprise(text: str) -> bool:
    """True if any BEAT or MISS appears in the calendar section."""
    return "BEAT" in text or "MISS" in text


# ── Tier 1 formatting ─────────────────────────────────────────────────────────

def _format_calendar_event(evt: dict) -> str:
    """
    Format one calendar event dict into a single display line.

    Example output:
      [USD] CPI: actual 3.8 vs prev 3.5 — BEAT — 2026-05-23
    """
    currency  = evt.get("currency", "???")
    event     = evt.get("event", "")
    actual    = evt.get("actual")
    previous  = evt.get("previous")
    timestamp = evt.get("timestamp", "")
    beat_miss = evt.get("beat_miss") or ""

    actual_str   = f"{actual:.4g}"   if actual   is not None else "N/A"
    previous_str = f"{previous:.4g}" if previous is not None else "N/A"

    beat_tag = f" — {beat_miss}" if beat_miss else ""
    return f"  [{currency}] {event}: actual {actual_str} vs prev {previous_str}{beat_tag} — {timestamp}"


def _build_tier1(currencies: list[str]) -> str:
    """Fetch and format Tier 1 calendar data for all currencies."""
    all_events: list[dict] = []
    for ccy in currencies:
        try:
            events = fetch_calendar_context(ccy)
            all_events.extend(events)
        except Exception as exc:
            log.warning(f"[news_brief_builder] Tier 1 failed for {ccy}: {exc}")

    if not all_events:
        return ""

    lines = [_format_calendar_event(e) for e in all_events]
    return "ECONOMIC RELEASES (last 48h / next 24h):\n" + "\n".join(lines)


# ── Tier 2 formatting ─────────────────────────────────────────────────────────

def _build_tier2(currencies: list[str]) -> str:
    """Fetch and format Tier 2 CB statement text for all currencies."""
    from shiftinnerv.news.currency_registry import CURRENCY_DATA_SOURCES
    today = datetime.now(timezone.utc).date()

    sections: list[str] = []
    for ccy in currencies:
        try:
            text = fetch_cb_statement(ccy, max_age_days=7, max_tokens=1500)
        except Exception as exc:
            log.warning(f"[news_brief_builder] Tier 2 failed for {ccy}: {exc}")
            text = None

        cb_name = CURRENCY_DATA_SOURCES.get(ccy, {}).get("cb_name", ccy)

        if text:
            sections.append(
                f"CENTRAL BANK ({cb_name}):\n"
                f"  {text}"
            )
        else:
            # Omit silently if no recent statement — per spec
            log.debug(f"[news_brief_builder] No recent CB statement for {ccy}")

    return "\n\n".join(sections)


# ── Tier 3 formatting ─────────────────────────────────────────────────────────

def _build_tier3(ticker1: str, ticker2: str) -> str:
    """Fetch and format Tier 3 headlines for both tickers."""
    tickers = [t for t in [ticker1, ticker2] if t]
    all_lines: list[str] = []

    for ticker in tickers:
        try:
            headlines = fetch_ticker_headlines(ticker, lookback_hours=48, max_headlines=3)
        except Exception as exc:
            log.warning(f"[news_brief_builder] Tier 3 failed for {ticker}: {exc}")
            headlines = []

        for h in headlines:
            source  = h.get("source", "")
            pub     = h.get("published_utc", "")
            headline = h.get("headline", "")
            source_tag = f" — {source}" if source else ""
            pub_tag    = f", {pub}"      if pub    else ""
            all_lines.append(f"  [{ticker.upper()}] {headline}{source_tag}{pub_tag}")

    if not all_lines:
        return ""

    return "TICKER HEADLINES:\n" + "\n".join(all_lines)


# ── Public API ────────────────────────────────────────────────────────────────

def build_news_context(ticker1: str, ticker2: str,
                        lookback_hours: int = 48) -> str:
    """
    Assemble all three tiers into a formatted NEWS & MACRO CONTEXT block
    for injection into the statistical brief.

    Returns an empty string if all tiers produce no data — the brief
    degrades gracefully rather than showing an empty section.

    All fetch calls are wrapped in try/except. Any individual failure
    is logged and skipped — the function always returns a string.

    Returns a plain string. Callers that need the boolean flags for
    complexity routing should call build_news_context_with_flags() instead.
    """
    context, _, _ = build_news_context_with_flags(ticker1, ticker2, lookback_hours)
    return context


def build_news_context_with_flags(ticker1: str, ticker2: str,
                                   lookback_hours: int = 48
                                   ) -> tuple[str, bool, bool]:
    """
    Like build_news_context() but also returns:
      cb_decision_recent  : bool — a CB statement was found within 7 days
      macro_surprise      : bool — at least one BEAT or MISS in calendar data

    Used by narrative_router to adjust complexity scores.
    """
    currencies = get_currencies_for_pair(ticker1, ticker2)
    now_utc    = datetime.now(timezone.utc)
    today_str  = now_utc.strftime("%Y-%m-%d")
    time_str   = now_utc.strftime("%H:%M")

    tier1 = _build_tier1(currencies) if currencies else ""
    tier2 = _build_tier2(currencies) if currencies else ""
    tier3 = _build_tier3(ticker1, ticker2)

    sections = [s for s in [tier1, tier2, tier3] if s.strip()]

    if not sections:
        return "", False, False

    body = "\n\n".join(sections)
    context = (
        f"=== NEWS & MACRO CONTEXT ===\n"
        f"Retrieved: {today_str} {time_str} UTC\n\n"
        f"{body}\n"
        f"============================"
    )

    cb_decision_recent = _has_cb_text(tier2)
    macro_surprise     = _has_macro_surprise(tier1)

    return context, cb_decision_recent, macro_surprise
