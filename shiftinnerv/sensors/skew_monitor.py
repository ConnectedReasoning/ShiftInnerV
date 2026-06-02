"""
ShiftInnerV — Put Skew Monitor
Council Roadmap: Dow Capital Structure Universe

Captures daily put implied volatility skew for a universe of tickers as a
proxy for company-specific credit stress. Skew is defined as:

    raw_skew  = IV(put at ~80% moneyness) / IV(put at ~100% moneyness)
    norm_skew = raw_skew(ticker) / raw_skew(SPY)

Normalization against SPY strips out market-wide fear (VIX effect), leaving
the idiosyncratic credit stress signal for each name.

Data source: yfinance option chains (snapshot of current chain — no historical
options API required). Run daily via Sentinel to build a rolling time series.

Storage: skew_ledger table in the existing trial_ledger.db (silent ALTER TABLE
migration for backward compatibility).

Usage:
    from shiftinnerv.sensors.skew_monitor import SkewMonitor

    monitor = SkewMonitor(db_path="path/to/trial_ledger.db", logger=logger)
    results = monitor.snapshot(tickers=["AAPL", "JPM", "BA"])
    # Returns list of SkewRecord dataclasses
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

import numpy as np
import pandas as pd

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


# ── Schema ────────────────────────────────────────────────────────────────────

SKEW_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS skew_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date       TEXT    NOT NULL,
    ticker              TEXT    NOT NULL,

    -- Raw skew: IV(OTM put ~80% moneyness) / IV(ATM put ~100% moneyness)
    atm_iv              REAL,
    otm_iv              REAL,
    raw_skew            REAL,

    -- SPY normalisation
    spy_raw_skew        REAL,
    norm_skew           REAL,   -- raw_skew / spy_raw_skew; NULL if SPY failed

    -- Nearest expiry used (days to expiration)
    expiry_used         TEXT,
    dte                 INTEGER,

    -- Moneyness of strikes actually selected
    atm_strike          REAL,
    otm_strike          REAL,
    spot_price          REAL,
    atm_moneyness       REAL,   -- atm_strike / spot_price
    otm_moneyness       REAL,   -- otm_strike / spot_price

    -- Quality flags
    atm_volume          INTEGER,
    otm_volume          INTEGER,
    low_liquidity       INTEGER DEFAULT 0,  -- 1 if volume < MIN_VOLUME threshold
    fetch_error         TEXT,   -- error message if fetch failed

    created_at          TEXT    DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_skew_date   ON skew_ledger(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_skew_ticker ON skew_ledger(ticker);
"""

# ── Constants ─────────────────────────────────────────────────────────────────

SPY_BENCHMARK       = "SPY"
OTM_MONEYNESS_TARGET = 0.80   # target ~20% OTM put
ATM_MONEYNESS_TARGET = 1.00   # at-the-money
MONEYNESS_TOLERANCE  = 0.05   # accept strikes within ±5% of target
MIN_DTE              = 7      # skip expiries closer than this
MAX_DTE              = 60     # prefer near-term but not too short
MIN_VOLUME           = 10     # flag low-liquidity if below this


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SkewRecord:
    snapshot_date:   str
    ticker:          str
    atm_iv:          Optional[float] = None
    otm_iv:          Optional[float] = None
    raw_skew:        Optional[float] = None
    spy_raw_skew:    Optional[float] = None
    norm_skew:       Optional[float] = None
    expiry_used:     Optional[str]   = None
    dte:             Optional[int]   = None
    atm_strike:      Optional[float] = None
    otm_strike:      Optional[float] = None
    spot_price:      Optional[float] = None
    atm_moneyness:   Optional[float] = None
    otm_moneyness:   Optional[float] = None
    atm_volume:      Optional[int]   = None
    otm_volume:      Optional[int]   = None
    low_liquidity:   int             = 0
    fetch_error:     Optional[str]   = None


# ── Core computation ──────────────────────────────────────────────────────────

def _select_expiry(expirations: tuple, min_dte: int = MIN_DTE,
                   max_dte: int = MAX_DTE) -> Optional[str]:
    """
    Pick the nearest expiry within [min_dte, max_dte] days.
    Falls back to the nearest expiry beyond max_dte if none found in window.
    """
    today = date.today()
    candidates = []
    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if dte >= min_dte:
                candidates.append((dte, exp_str))
        except ValueError:
            continue

    if not candidates:
        return None

    # Prefer within window; fall back to nearest outside
    in_window = [(dte, e) for dte, e in candidates if dte <= max_dte]
    if in_window:
        return min(in_window, key=lambda x: x[0])[1]
    return min(candidates, key=lambda x: x[0])[1]


def _find_strike(puts, spot: float, target_moneyness: float,
                 tolerance: float = MONEYNESS_TOLERANCE):
    """
    Find the put row closest to target_moneyness = strike / spot.
    Returns the row or None if no strike within tolerance.
    """
    if puts is None or puts.empty:
        return None

    puts = puts.copy()
    puts["moneyness"] = puts["strike"] / spot
    puts["dist"] = (puts["moneyness"] - target_moneyness).abs()

    # Filter within tolerance
    candidates = puts[puts["dist"] <= tolerance]
    if candidates.empty:
        return None

    return candidates.loc[candidates["dist"].idxmin()]


def compute_raw_skew(ticker: str,
                     logger: Optional[logging.Logger] = None) -> SkewRecord:
    """
    Fetch the options chain for ticker and compute put skew.

    Returns a SkewRecord. On failure, fetch_error is populated and
    raw_skew is None (caller decides how to handle).
    """
    today_str = date.today().isoformat()
    rec = SkewRecord(snapshot_date=today_str, ticker=ticker)

    if not _YF_AVAILABLE:
        rec.fetch_error = "yfinance not installed"
        return rec

    log = logger or logging.getLogger("skew_monitor")

    try:
        t = yf.Ticker(ticker)

        # Spot price
        hist = t.history(period="2d")
        if hist.empty:
            rec.fetch_error = "no price history"
            return rec
        spot = float(hist["Close"].iloc[-1])
        rec.spot_price = spot

        # Expiry selection
        expirations = t.options
        if not expirations:
            rec.fetch_error = "no options expirations available"
            return rec

        expiry = _select_expiry(expirations)
        if expiry is None:
            rec.fetch_error = "no suitable expiry found"
            return rec

        rec.expiry_used = expiry
        rec.dte = (datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days

        # Fetch puts for selected expiry
        chain = t.option_chain(expiry)
        puts = chain.puts

        if puts is None or puts.empty:
            rec.fetch_error = "no puts in chain"
            return rec

        # Filter out zero/NaN IV rows
        puts = puts[puts["impliedVolatility"].notna() & (puts["impliedVolatility"] > 0)]
        if puts.empty:
            rec.fetch_error = "all puts have zero/null IV"
            return rec

        # Select ATM and OTM strikes
        atm_row = _find_strike(puts, spot, ATM_MONEYNESS_TARGET)
        otm_row = _find_strike(puts, spot, OTM_MONEYNESS_TARGET)

        if atm_row is None:
            rec.fetch_error = f"no ATM put found near moneyness {ATM_MONEYNESS_TARGET}"
            return rec
        if otm_row is None:
            rec.fetch_error = f"no OTM put found near moneyness {OTM_MONEYNESS_TARGET}"
            return rec

        # Extract values
        rec.atm_strike    = float(atm_row["strike"])
        rec.otm_strike    = float(otm_row["strike"])
        rec.atm_iv        = float(atm_row["impliedVolatility"])
        rec.otm_iv        = float(otm_row["impliedVolatility"])
        rec.atm_moneyness = rec.atm_strike / spot
        rec.otm_moneyness = rec.otm_strike / spot
        rec.atm_volume    = int(atm_row["volume"]) if pd.notna(atm_row.get("volume")) else 0
        rec.otm_volume    = int(otm_row["volume"]) if pd.notna(otm_row.get("volume")) else 0

        # Liquidity flag
        if rec.atm_volume < MIN_VOLUME or rec.otm_volume < MIN_VOLUME:
            rec.low_liquidity = 1

        # Raw skew
        if rec.atm_iv > 0:
            rec.raw_skew = rec.otm_iv / rec.atm_iv
        else:
            rec.fetch_error = "ATM IV is zero — cannot compute skew"

        log.debug(
            f"[skew] {ticker}: spot={spot:.2f} expiry={expiry} dte={rec.dte} "
            f"atm_strike={rec.atm_strike} atm_iv={rec.atm_iv:.3f} "
            f"otm_strike={rec.otm_strike} otm_iv={rec.otm_iv:.3f} "
            f"raw_skew={f'{rec.raw_skew:.3f}' if rec.raw_skew is not None else 'N/A'}"
        )

    except Exception as e:
        rec.fetch_error = str(e)
        log.warning(f"[skew] {ticker}: exception — {e}")

    return rec


# ── SkewMonitor ───────────────────────────────────────────────────────────────

class SkewMonitor:
    """
    Daily put skew snapshot for a universe of tickers.

    Computes raw skew per ticker, normalises against SPY, and persists
    to the skew_ledger table in the existing trial_ledger.db.

    Parameters
    ----------
    db_path : str
        Path to trial_ledger.db (will be created if absent).
    logger  : logging.Logger, optional
    """

    def __init__(self, db_path: str, logger: Optional[logging.Logger] = None):
        self.db_path = db_path
        self.logger  = logger or logging.getLogger("skew_monitor")
        self._init_db()

    def _init_db(self):
        """Create skew_ledger table if absent (silent on existing DB)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SKEW_LEDGER_DDL)
            conn.commit()
        finally:
            conn.close()

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(
        self,
        tickers: List[str],
        sleep_between: float = 1.0,
        max_retries: int = 2,
    ) -> List[SkewRecord]:
        """
        Fetch put skew for each ticker and persist to DB.

        SPY is always fetched as the normalisation benchmark (even if not in
        the tickers list). Results include SPY's own record.

        Parameters
        ----------
        tickers        : list of ticker symbols
        sleep_between  : seconds to wait between fetches (default 1.0)
                         At 500 tickers this adds ~8 minutes — acceptable for
                         a daily morning run.
        max_retries    : retry attempts on transient errors (default 2)
                         with exponential backoff.

        Returns list of SkewRecord (including SPY).
        """
        import time

        all_tickers = list(dict.fromkeys([SPY_BENCHMARK] + tickers))  # SPY first, deduped
        total       = len(all_tickers)
        raw_records: dict[str, SkewRecord] = {}

        for i, ticker in enumerate(all_tickers, 1):
            self.logger.info(f"[skew] Fetching {ticker} ({i}/{total})...")

            # Retry loop with exponential backoff
            rec = None
            for attempt in range(1, max_retries + 1):
                rec = compute_raw_skew(ticker, logger=self.logger)
                if rec.fetch_error is None:
                    break
                if attempt < max_retries:
                    backoff = sleep_between * (2 ** attempt)
                    self.logger.warning(
                        f"[skew] {ticker}: attempt {attempt} failed "
                        f"({rec.fetch_error}) — retrying in {backoff:.1f}s"
                    )
                    time.sleep(backoff)

            raw_records[ticker] = rec

            # Rate limit pause between tickers (skip after last)
            if i < total:
                time.sleep(sleep_between)

        # Normalise against SPY
        spy_rec = raw_records.get(SPY_BENCHMARK)
        spy_skew = spy_rec.raw_skew if (spy_rec and spy_rec.raw_skew is not None) else None

        if spy_skew is None:
            self.logger.warning(
                f"[skew] SPY skew unavailable "
                f"({spy_rec.fetch_error if spy_rec else 'no record'}) "
                f"— norm_skew will be NULL for all tickers"
            )

        results = []
        for ticker, rec in raw_records.items():
            rec.spy_raw_skew = spy_skew
            if rec.raw_skew is not None and spy_skew is not None and spy_skew > 0:
                rec.norm_skew = rec.raw_skew / spy_skew
            self._upsert(rec)
            results.append(rec)

        return results

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_rolling_skew(self, ticker: str, days: int = 10):
        """
        Return a DataFrame of the last N days of skew for a ticker.
        Columns: snapshot_date, raw_skew, norm_skew, low_liquidity, fetch_error
        """
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                """
                SELECT snapshot_date, raw_skew, norm_skew,
                       low_liquidity, fetch_error, spot_price,
                       atm_iv, otm_iv, dte
                FROM   skew_ledger
                WHERE  ticker = ?
                ORDER  BY snapshot_date DESC
                LIMIT  ?
                """,
                conn,
                params=(ticker, days),
            )
        finally:
            conn.close()
        return df

    def get_latest_snapshot(self, date_str: Optional[str] = None):
        """
        Return all ticker rows for a given date (default: today).
        """
        date_str = date_str or date.today().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                """
                SELECT ticker, raw_skew, norm_skew, low_liquidity,
                       fetch_error, spot_price, atm_iv, otm_iv, dte
                FROM   skew_ledger
                WHERE  snapshot_date = ?
                ORDER  BY norm_skew DESC NULLS LAST
                """,
                conn,
                params=(date_str,),
            )
        finally:
            conn.close()
        return df

    # ── Persistence ───────────────────────────────────────────────────────────

    def _upsert(self, rec: SkewRecord):
        """Insert or replace skew record for (snapshot_date, ticker)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO skew_ledger (
                    snapshot_date, ticker,
                    atm_iv, otm_iv, raw_skew,
                    spy_raw_skew, norm_skew,
                    expiry_used, dte,
                    atm_strike, otm_strike, spot_price,
                    atm_moneyness, otm_moneyness,
                    atm_volume, otm_volume,
                    low_liquidity, fetch_error
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(snapshot_date, ticker) DO UPDATE SET
                    atm_iv        = excluded.atm_iv,
                    otm_iv        = excluded.otm_iv,
                    raw_skew      = excluded.raw_skew,
                    spy_raw_skew  = excluded.spy_raw_skew,
                    norm_skew     = excluded.norm_skew,
                    expiry_used   = excluded.expiry_used,
                    dte           = excluded.dte,
                    atm_strike    = excluded.atm_strike,
                    otm_strike    = excluded.otm_strike,
                    spot_price    = excluded.spot_price,
                    atm_moneyness = excluded.atm_moneyness,
                    otm_moneyness = excluded.otm_moneyness,
                    atm_volume    = excluded.atm_volume,
                    otm_volume    = excluded.otm_volume,
                    low_liquidity = excluded.low_liquidity,
                    fetch_error   = excluded.fetch_error
                """,
                (
                    rec.snapshot_date, rec.ticker,
                    rec.atm_iv, rec.otm_iv, rec.raw_skew,
                    rec.spy_raw_skew, rec.norm_skew,
                    rec.expiry_used, rec.dte,
                    rec.atm_strike, rec.otm_strike, rec.spot_price,
                    rec.atm_moneyness, rec.otm_moneyness,
                    rec.atm_volume, rec.otm_volume,
                    rec.low_liquidity, rec.fetch_error,
                ),
            )
            conn.commit()
        finally:
            conn.close()
