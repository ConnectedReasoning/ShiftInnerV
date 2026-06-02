"""
ShiftInnerV — Skew Signal Generator
Council Roadmap: Options Skew vs Equity Price Strategy

Reads the skew_ledger built daily by SkewMonitor and computes a rolling
z-score of norm_skew for each ticker. When the z-score crosses a threshold,
the options market is pricing in stress (or calm) that the equity price
hasn't yet acknowledged — that divergence is the trade signal.

Signal logic:
    z_score = (today_norm_skew - rolling_mean) / rolling_std
              computed over LOOKBACK_DAYS of norm_skew history

    z_score > +ENTRY_THRESHOLD  → SHORT  (options fear not in equity yet)
    z_score < -ENTRY_THRESHOLD  → LONG   (options calm, equity oversold)
    otherwise                   → HOLD

Parameters (tunable):
    LOOKBACK_DAYS   = 10   (rolling window for z-score baseline)
    ENTRY_THRESHOLD = 1.0  (z-score magnitude to trigger signal)
    MIN_HISTORY     = 5    (minimum rows needed before signalling)

Storage: skew_signals table in trial_ledger.db (silent migration).

Usage:
    from shiftinnerv.sensors.skew_signal import SkewSignalGenerator

    gen = SkewSignalGenerator(db_path="path/to/trial_ledger.db", logger=logger)
    signals = gen.generate(tickers=["AAPL", "JPM", "BA"])
    # Returns list of SkewSignal dataclasses
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

import pandas as pd


# ── Parameters ────────────────────────────────────────────────────────────────

LOOKBACK_DAYS    = 10    # rolling window for z-score baseline
ENTRY_THRESHOLD  = 1.0   # z-score magnitude to trigger SHORT or LONG
EXIT_THRESHOLD   = 0.0   # z-score magnitude to close (revert to mean)
MAX_HOLD_DAYS    = 5     # time stop — close after this many days regardless
MIN_HISTORY      = 5     # minimum rows before generating a signal


# ── Schema ────────────────────────────────────────────────────────────────────

SKEW_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS skew_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date     TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,

    norm_skew       REAL,           -- today's norm_skew from skew_ledger
    rolling_mean    REAL,           -- mean of norm_skew over LOOKBACK_DAYS
    rolling_std     REAL,           -- std of norm_skew over LOOKBACK_DAYS
    z_score         REAL,           -- (norm_skew - mean) / std

    signal          TEXT    NOT NULL,   -- SHORT / LONG / HOLD / INSUFFICIENT_DATA
    history_days    INTEGER,            -- how many days of history were available

    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(signal_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_skewsig_date   ON skew_signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_skewsig_ticker ON skew_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_skewsig_signal ON skew_signals(signal);
"""


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SkewSignal:
    signal_date:   str
    ticker:        str
    norm_skew:     Optional[float]
    rolling_mean:  Optional[float]
    rolling_std:   Optional[float]
    z_score:       Optional[float]
    signal:        str              # SHORT / LONG / HOLD / INSUFFICIENT_DATA
    history_days:  int


# ── Generator ─────────────────────────────────────────────────────────────────

class SkewSignalGenerator:
    """
    Reads skew_ledger history per ticker and emits trade signals based on
    the rolling z-score of norm_skew.

    Parameters
    ----------
    db_path  : str   — path to trial_ledger.db
    logger   : logging.Logger, optional
    lookback : int   — rolling window in trading days (default: LOOKBACK_DAYS)
    threshold: float — z-score entry threshold (default: ENTRY_THRESHOLD)
    """

    def __init__(
        self,
        db_path:   str,
        logger:    Optional[logging.Logger] = None,
        lookback:  int   = LOOKBACK_DAYS,
        threshold: float = ENTRY_THRESHOLD,
    ):
        self.db_path   = db_path
        self.logger    = logger or logging.getLogger("skew_signal")
        self.lookback  = lookback
        self.threshold = threshold
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SKEW_SIGNALS_DDL)
            conn.commit()
        finally:
            conn.close()

    # ── Main entry point ──────────────────────────────────────────────────────

    def generate(self, tickers: List[str]) -> List[SkewSignal]:
        """
        Generate signals for all tickers and persist to skew_signals table.
        Returns list of SkewSignal (all tickers, including HOLD and
        INSUFFICIENT_DATA — caller filters as needed).
        """
        today_str = date.today().isoformat()
        results   = []

        for ticker in tickers:
            sig = self._compute_signal(ticker, today_str)
            self._upsert(sig)
            results.append(sig)
            self.logger.debug(
                f"[skew_signal] {ticker}: z={f'{sig.z_score:.2f}' if sig.z_score is not None else 'N/A'}"
                f"  signal={sig.signal}  history={sig.history_days}d"
            )

        actionable = [s for s in results if s.signal in ("SHORT", "LONG")]
        self.logger.info(
            f"[skew_signal] {len(actionable)} actionable signals "
            f"({sum(1 for s in actionable if s.signal == 'SHORT')} SHORT, "
            f"{sum(1 for s in actionable if s.signal == 'LONG')} LONG) "
            f"out of {len(results)} tickers"
        )

        return results

    # ── Signal computation ────────────────────────────────────────────────────

    def _compute_signal(self, ticker: str, today_str: str) -> SkewSignal:
        """
        Load rolling norm_skew history for ticker and compute z-score signal.
        """
        history = self._load_history(ticker, self.lookback)

        base = SkewSignal(
            signal_date  = today_str,
            ticker       = ticker,
            norm_skew    = None,
            rolling_mean = None,
            rolling_std  = None,
            z_score      = None,
            signal       = "INSUFFICIENT_DATA",
            history_days = len(history),
        )

        if len(history) < MIN_HISTORY:
            return base

        # Today's value is the most recent row
        today_val = history["norm_skew"].iloc[-1]
        if pd.isna(today_val):
            return base

        mean = history["norm_skew"].mean()
        std  = history["norm_skew"].std()

        if std is None or std == 0:
            # No variance — no signal
            base.norm_skew    = today_val
            base.rolling_mean = mean
            base.rolling_std  = 0.0
            base.z_score      = 0.0
            base.signal       = "HOLD"
            return base

        z = (today_val - mean) / std

        if z > self.threshold:
            signal = "SHORT"
        elif z < -self.threshold:
            signal = "LONG"
        else:
            signal = "HOLD"

        return SkewSignal(
            signal_date  = today_str,
            ticker       = ticker,
            norm_skew    = round(today_val, 4),
            rolling_mean = round(mean, 4),
            rolling_std  = round(std, 4),
            z_score      = round(z, 4),
            signal       = signal,
            history_days = len(history),
        )

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _load_history(self, ticker: str, days: int) -> pd.DataFrame:
        """
        Load last N days of norm_skew from skew_ledger for ticker.
        Only rows where norm_skew IS NOT NULL are included.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                """
                SELECT snapshot_date, norm_skew
                FROM   skew_ledger
                WHERE  ticker    = ?
                  AND  norm_skew IS NOT NULL
                ORDER  BY snapshot_date DESC
                LIMIT  ?
                """,
                conn,
                params=(ticker, days),
            )
        finally:
            conn.close()

        # Return chronological order (oldest first) for rolling stats
        return df.iloc[::-1].reset_index(drop=True)

    def _upsert(self, sig: SkewSignal):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO skew_signals (
                    signal_date, ticker,
                    norm_skew, rolling_mean, rolling_std, z_score,
                    signal, history_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_date, ticker) DO UPDATE SET
                    norm_skew    = excluded.norm_skew,
                    rolling_mean = excluded.rolling_mean,
                    rolling_std  = excluded.rolling_std,
                    z_score      = excluded.z_score,
                    signal       = excluded.signal,
                    history_days = excluded.history_days
                """,
                (
                    sig.signal_date, sig.ticker,
                    sig.norm_skew, sig.rolling_mean, sig.rolling_std,
                    sig.z_score, sig.signal, sig.history_days,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_latest_signals(self, date_str: Optional[str] = None) -> pd.DataFrame:
        """
        Return all signals for a given date (default: today).
        Ordered by abs(z_score) descending — strongest signals first.
        """
        date_str = date_str or date.today().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                """
                SELECT ticker, signal, z_score, norm_skew,
                       rolling_mean, rolling_std, history_days
                FROM   skew_signals
                WHERE  signal_date = ?
                ORDER  BY ABS(z_score) DESC NULLS LAST
                """,
                conn,
                params=(date_str,),
            )
        finally:
            conn.close()
        return df
