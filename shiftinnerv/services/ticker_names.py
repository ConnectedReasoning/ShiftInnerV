"""
ShiftInnerV — Ticker Name Lookup with Persistent Cache

Lazily fetches long company names from yfinance and caches them in
the trial_ledger database. Each ticker is fetched at most once,
then served from cache forever.

Usage:
    from shiftinnerv.services.ticker_names import get_ticker_name

    name = get_ticker_name("AAPL", db_path="path/to/trial_ledger.db")
    # → "Apple Inc."

    # Bulk variant — single connection
    names = get_ticker_names(["AAPL", "MSFT"], db_path="...")
    # → {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation"}
"""

import logging
import sqlite3
from typing import Dict, List, Optional


# ── Schema ────────────────────────────────────────────────────────────────────

TICKER_NAMES_DDL = """
CREATE TABLE IF NOT EXISTS ticker_names (
    ticker     TEXT PRIMARY KEY,
    long_name  TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(TICKER_NAMES_DDL)
    conn.commit()


def _fetch_from_yfinance(ticker: str, logger: Optional[logging.Logger] = None) -> Optional[str]:
    """
    Fetch long name from yfinance. Returns None on any error.
    Falls back gracefully — failures here should never block briefing.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        name = info.get("longName") or info.get("shortName")
        if name:
            return str(name).strip()
    except Exception as e:
        if logger:
            logger.debug(f"[ticker_names] yfinance lookup failed for {ticker}: {e}")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_ticker_name(
    ticker: str,
    db_path: str,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Return the long company name for a ticker.
    Falls back to the ticker symbol itself if the lookup fails.
    """
    return get_ticker_names([ticker], db_path, logger).get(ticker, ticker)


def get_ticker_names(
    tickers: List[str],
    db_path: str,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, str]:
    """
    Bulk-load names for multiple tickers using a single DB connection.
    Cached lookups are instant; uncached ones hit yfinance one-by-one.
    Always returns a dict with every input ticker as a key — fallback
    value is the ticker symbol itself.
    """
    result: Dict[str, str] = {}

    if not tickers:
        return result

    conn = sqlite3.connect(db_path)
    try:
        _ensure_schema(conn)

        # Cache hit pass
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, long_name FROM ticker_names WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        cached = {t: n for t, n in rows if n}

        # Cache miss pass — fetch and persist
        for ticker in tickers:
            if ticker in cached:
                result[ticker] = cached[ticker]
                continue

            name = _fetch_from_yfinance(ticker, logger)

            if name:
                conn.execute(
                    "INSERT OR REPLACE INTO ticker_names (ticker, long_name) VALUES (?, ?)",
                    (ticker, name),
                )
                result[ticker] = name
            else:
                # Store NULL to mark as "tried" — avoids retry storm on bad tickers
                conn.execute(
                    "INSERT OR IGNORE INTO ticker_names (ticker, long_name) VALUES (?, NULL)",
                    (ticker,),
                )
                result[ticker] = ticker   # fallback to ticker symbol

        conn.commit()
    finally:
        conn.close()

    return result
