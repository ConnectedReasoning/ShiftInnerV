#!/usr/bin/env python3
"""
ShiftInnerV — Trial Performance Ledger
Item 14 of the Council Roadmap.

Provides:
  - init_trial_ledger(db_path)            — create schema if absent
  - record_active_verdict(...)            — insert on ACTIVE verdict
  - close_trial(...)                      — update on trade exit
  - parse_gate_results(verdict_text)      — extract gate labels from LLM output
  - parse_statistical_snapshot(verdict_text) — extract numeric values from LLM output

This module is deliberately free of LLM / CrewAI imports so it can be
called from monitor.py, main.py, and scripts without side-effects.
"""

import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trial_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Verdict metadata
    verdict_id          TEXT UNIQUE NOT NULL,
    verdict_timestamp   TEXT NOT NULL,
    composition_label   TEXT,

    -- Pair identification
    ticker1             TEXT,
    ticker2             TEXT,

    -- Statistical snapshot at verdict time
    entry_z_verdict     REAL,
    half_life           REAL,
    snr                 REAL,
    episodes            INTEGER,
    trace_stat          REAL,
    crit_95             REAL,

    -- Position parameters
    hedge_ratio         REAL,
    spread_mean         REAL,
    spread_std          REAL,

    -- Entry execution
    entry_timestamp     TEXT,
    entry_price_1       REAL,
    entry_price_2       REAL,
    entry_notional      REAL,
    entry_z_actual      REAL,

    -- Exit execution
    exit_timestamp      TEXT,
    exit_price_1        REAL,
    exit_price_2        REAL,
    exit_z              REAL,
    exit_reason         TEXT,

    -- P&L record
    hold_days           INTEGER,
    gross_pnl_dollars   REAL,
    gross_pnl_bps       REAL,
    estimated_costs_bps REAL,
    net_pnl_bps         REAL,
    net_pnl_pct         REAL,

    -- Status
    is_closed           INTEGER DEFAULT 0,
    is_profitable       INTEGER,

    -- Gate results
    gate_1_result       TEXT,
    gate_2_result       TEXT,
    gate_3_result       TEXT,
    gate_4_result       TEXT,
    gate_6_result       TEXT,
    gate_7_result       TEXT,

    -- Item 8: Regime state at verdict time
    regime_state            TEXT DEFAULT 'NORMAL',   -- NORMAL | ELEVATED | HIGH_STRESS | CRISIS
    position_size_multiplier REAL DEFAULT 1.0,        -- e.g. 0.5, 0.25 in stressed regimes

    notes               TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_verdict_timestamp ON trial_ledger(verdict_timestamp);
CREATE INDEX IF NOT EXISTS idx_is_closed         ON trial_ledger(is_closed);
CREATE INDEX IF NOT EXISTS idx_ticker_pair        ON trial_ledger(ticker1, ticker2);
CREATE INDEX IF NOT EXISTS idx_composition_label  ON trial_ledger(composition_label);
"""


def init_trial_ledger(db_path: str) -> None:
    """Create the trial_ledger table (and indexes) if not already present."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    # ── Item 8: migrate existing databases (ADD COLUMN is idempotent via try/except)
    for stmt in (
        "ALTER TABLE trial_ledger ADD COLUMN regime_state TEXT DEFAULT 'NORMAL'",
        "ALTER TABLE trial_ledger ADD COLUMN position_size_multiplier REAL DEFAULT 1.0",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()


# ── Gate parser ───────────────────────────────────────────────────────────────

# Maps gate number → regex that captures the result token (PASS/FAIL/etc.)
# Handles formats like:
#   "Gate 1 Cointegration: PASS — ..."
#   "Gate 1 — Cointegration: PASS"
#   "Gate 1: PASS"
_GATE_PATTERNS = {
    "gate_1": re.compile(
        r"Gate\s*1[\s\w–—\-]*:\s*([A-Z][A-Z\-_]+)", re.IGNORECASE
    ),
    "gate_2": re.compile(
        r"Gate\s*2[\s\w–—\-]*:\s*([A-Z][A-Z\-_]+)", re.IGNORECASE
    ),
    "gate_3": re.compile(
        r"Gate\s*3[\s\w–—\-]*:\s*([A-Z][A-Z\-_]+)", re.IGNORECASE
    ),
    "gate_4": re.compile(
        r"Gate\s*4[\s\w–—\-]*:\s*([A-Z][A-Z\-_]+)", re.IGNORECASE
    ),
    "gate_6": re.compile(
        r"Gate\s*6[\s\w–—\-]*:\s*([A-Z][A-Z\-_]+)", re.IGNORECASE
    ),
    "gate_7": re.compile(
        r"Gate\s*7[^:]*:\s*([A-Z][A-Z\-_]+)", re.IGNORECASE
    ),
}

# Normalise tokens that mean "pass" or "fail" to consistent labels
_PASS_TOKENS = {"PASS", "PASS-NEAR", "MONITOR-NEAR"}
_FAIL_TOKENS = {"FAIL", "REJECT", "FACTOR_CONTAMINATED", "UNPROFITABLE"}


def _normalise_gate_token(raw: str) -> str:
    token = raw.strip().upper().replace(" ", "_")
    if token in _PASS_TOKENS:
        return "PASS"
    if token in _FAIL_TOKENS:
        return token  # preserve granularity (FAIL vs REJECT vs FACTOR_CONTAMINATED)
    return token  # SKIPPED, N/A, MARGINAL, etc.


def parse_gate_results(verdict_text: str) -> dict:
    """
    Extract gate result labels from a Signal Mathematician verdict.

    Returns a dict: {"gate_1": "PASS", "gate_2": "FAIL", ...}
    Missing gates get an empty string.
    """
    results = {}
    for gate, pattern in _GATE_PATTERNS.items():
        m = pattern.search(verdict_text)
        results[gate] = _normalise_gate_token(m.group(1)) if m else ""
    return results


# ── Statistical snapshot parser ───────────────────────────────────────────────

def parse_statistical_snapshot(verdict_text: str) -> dict:
    """
    Extract numeric values embedded in the LLM verdict / dossier output.

    Returns a dict with keys matching trial_ledger columns.
    Values are float or None if not found.
    """

    def _find_float(patterns: list, text: str) -> float | None:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        return None

    half_life = _find_float([
        r"half.life[:\s=]+([0-9]+\.?[0-9]*)\s*d",
        r"Half.Life[:\s=]+([0-9]+\.?[0-9]*)",
        r"half.life\s+of\s+([0-9]+\.?[0-9]*)",
    ], verdict_text)

    snr = _find_float([
        r"SNR[:\s=]+([0-9]+\.?[0-9]*)",
        r"signal.to.noise[:\s=]+([0-9]+\.?[0-9]*)",
    ], verdict_text)

    trace_stat = _find_float([
        r"trace\s+stat(?:istic)?[:\s=]+([0-9]+\.?[0-9]*)",
        r"Trace\s*=\s*([0-9]+\.?[0-9]*)",
    ], verdict_text)

    crit_95 = _find_float([
        r"crit(?:ical)?\s*(?:value\s*)?95[%]?[:\s=]+([0-9]+\.?[0-9]*)",
        r"95%\s*CI[:\s=]+([0-9]+\.?[0-9]*)",
        r"c95[:\s=]+([0-9]+\.?[0-9]*)",
        r"crit_95\s*=\s*([0-9]+\.?[0-9]*)",
    ], verdict_text)

    episodes = _find_float([
        r"([0-9]+)\s+distinct\s+episodes",
        r"episodes[:\s=]+([0-9]+)",
    ], verdict_text)

    z_score = _find_float([
        r"z.score[:\s=]+([0-9\-]+\.?[0-9]*)",
        r"current\s+z[:\s=]+([0-9\-]+\.?[0-9]*)",
        r"entry\s+z[:\s=]+([0-9\-]+\.?[0-9]*)",
    ], verdict_text)

    hedge_ratio = _find_float([
        r"hedge\s*ratio[:\s=]+([0-9\-]+\.?[0-9]*)",
        r"beta[:\s=]+([0-9\-]+\.?[0-9]*)",
        r"OLS\s+beta[:\s=]+([0-9\-]+\.?[0-9]*)",
    ], verdict_text)

    return {
        "entry_z_verdict": z_score,
        "half_life":        half_life,
        "snr":              snr,
        "episodes":         int(episodes) if episodes is not None else None,
        "trace_stat":       trace_stat,
        "crit_95":          crit_95,
        "hedge_ratio":      hedge_ratio,
        "spread_mean":      None,  # not typically in verdict text
        "spread_std":       None,
    }


# ── Insert on ACTIVE verdict ──────────────────────────────────────────────────

def record_active_verdict(
    db_path: str,
    ticker1: str,
    ticker2: str,
    label: str,
    gate_results: dict,
    *,
    composition_label: str | None = None,
    entry_z: float | None = None,
    half_life: float | None = None,
    snr: float | None = None,
    episodes: int | None = None,
    trace_stat: float | None = None,
    crit_95: float | None = None,
    hedge_ratio: float | None = None,
    spread_mean: float | None = None,
    spread_std: float | None = None,
    notes: str | None = None,
    # Item 8: Regime context
    regime_state: str = "NORMAL",
    position_size_multiplier: float = 1.0,
) -> str | None:
    """
    Insert a new trial record when an ACTIVE verdict is issued.

    Parameters
    ----------
    label : str
        Human-readable pair label (e.g. "Defense: LMT vs NOC").
    composition_label : str | None
        Composition category the pair belongs to (e.g. "defense",
        "china_em"). Used for concentration-limit enforcement (Item 15).
        Pass ``None`` for standalone pairs.
    regime_state : str
        Market regime at verdict time (Item 8). One of NORMAL, ELEVATED,
        HIGH_STRESS, CRISIS.
    position_size_multiplier : float
        Position sizing multiplier applied due to regime (Item 8).
        1.0 = full size, 0.5 = halved, 0.25 = quarter, 0.0 = no entry.

    Returns the 8-char verdict_id for later reference (or None on failure).
    """
    verdict_id = str(uuid.uuid4())[:8]
    try:
        init_trial_ledger(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO trial_ledger (
                verdict_id, verdict_timestamp, composition_label,
                ticker1, ticker2,
                entry_z_verdict, half_life, snr, episodes,
                trace_stat, crit_95,
                hedge_ratio, spread_mean, spread_std,
                gate_1_result, gate_2_result, gate_3_result, gate_4_result,
                gate_6_result, gate_7_result,
                regime_state, position_size_multiplier,
                is_closed, notes
            ) VALUES (
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                0, ?
            )
            """,
            (
                verdict_id,
                datetime.now().isoformat(),
                composition_label,   # ← Item 15: real composition label, not pair label
                ticker1,
                ticker2,
                entry_z,
                half_life,
                snr,
                episodes,
                trace_stat,
                crit_95,
                hedge_ratio,
                spread_mean,
                spread_std,
                gate_results.get("gate_1", ""),
                gate_results.get("gate_2", ""),
                gate_results.get("gate_3", ""),
                gate_results.get("gate_4", ""),
                gate_results.get("gate_6", ""),
                gate_results.get("gate_7", ""),
                regime_state,
                position_size_multiplier,
                notes,
            ),
        )
        conn.commit()
        conn.close()
        return verdict_id
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not record verdict — {exc}")
        return None


# ── Update on trade close ─────────────────────────────────────────────────────

def close_trial(
    db_path: str,
    verdict_id: str,
    entry_timestamp: str,
    entry_price_1: float,
    entry_price_2: float,
    exit_timestamp: str,
    exit_price_1: float,
    exit_price_2: float,
    exit_z: float,
    exit_reason: str,
    hedge_ratio: float,
    estimated_costs_bps: float,
    entry_z_actual: float | None = None,
) -> bool:
    """
    Update a trial record when the position closes.
    Computes P&L and marks is_closed = 1.

    Returns True on success.
    """
    try:
        notional_1 = 10_000.0
        notional_2 = 10_000.0 * abs(hedge_ratio) if hedge_ratio else 10_000.0
        total_notional = notional_1 + notional_2

        shares_1 = notional_1 / entry_price_1
        shares_2 = notional_2 / entry_price_2

        # Assume long spread (long ticker1, short ticker2)
        pnl_leg1 = shares_1 * (exit_price_1 - entry_price_1)
        pnl_leg2 = shares_2 * (entry_price_2 - exit_price_2)
        gross_pnl_dollars = pnl_leg1 + pnl_leg2

        gross_pnl_bps = (
            gross_pnl_dollars / total_notional * 10_000
            if total_notional > 0 else 0.0
        )
        net_pnl_bps = gross_pnl_bps - estimated_costs_bps
        net_pnl_pct = net_pnl_bps / 10_000

        hold_days = (
            pd.to_datetime(exit_timestamp) - pd.to_datetime(entry_timestamp)
        ).days

        init_trial_ledger(db_path)
        conn = sqlite3.connect(db_path)
        rows_updated = conn.execute(
            """
            UPDATE trial_ledger
            SET entry_timestamp     = ?,
                entry_price_1       = ?,
                entry_price_2       = ?,
                entry_notional      = ?,
                entry_z_actual      = ?,
                exit_timestamp      = ?,
                exit_price_1        = ?,
                exit_price_2        = ?,
                exit_z              = ?,
                exit_reason         = ?,
                hold_days           = ?,
                gross_pnl_dollars   = ?,
                gross_pnl_bps       = ?,
                estimated_costs_bps = ?,
                net_pnl_bps         = ?,
                net_pnl_pct         = ?,
                is_closed           = 1,
                is_profitable       = ?
            WHERE verdict_id = ?
            """,
            (
                entry_timestamp, entry_price_1, entry_price_2,
                total_notional, entry_z_actual,
                exit_timestamp, exit_price_1, exit_price_2,
                exit_z, exit_reason, hold_days,
                gross_pnl_dollars, gross_pnl_bps,
                estimated_costs_bps, net_pnl_bps, net_pnl_pct,
                1 if net_pnl_bps > 0 else 0,
                verdict_id,
            ),
        ).rowcount
        conn.commit()
        conn.close()

        if rows_updated == 0:
            print(f"WARNING [trial_ledger]: verdict_id '{verdict_id}' not found")
            return False
        return True
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not close trial — {exc}")
        return False


# ── Convenience: bulk-close from audit results ────────────────────────────────

def close_trials_from_audit(db_path: str, audit_results: list) -> tuple[int, int]:
    """
    Bulk-close trials using the output list from audit_active_verdicts.py.

    Each audit_result dict must have:
        verdict_id, entry_date, entry_price_1, entry_price_2,
        exit_date, exit_price_1, exit_price_2, exit_z, exit_reason,
        hedge_ratio, total_cost_bps

    Returns (n_success, n_failed).
    """
    ok = failed = 0
    for r in audit_results:
        success = close_trial(
            db_path=db_path,
            verdict_id=r["verdict_id"],
            entry_timestamp=r["entry_date"],
            entry_price_1=r["entry_price_1"],
            entry_price_2=r["entry_price_2"],
            exit_timestamp=r["exit_date"],
            exit_price_1=r["exit_price_1"],
            exit_price_2=r["exit_price_2"],
            exit_z=r.get("exit_z", 0.0),
            exit_reason=r.get("exit_reason", "unknown"),
            hedge_ratio=r["hedge_ratio"],
            estimated_costs_bps=r.get("total_cost_bps", 0.0),
        )
        if success:
            ok += 1
        else:
            failed += 1
    return ok, failed


# ── Query helpers ─────────────────────────────────────────────────────────────

def load_closed_trials(db_path: str) -> pd.DataFrame | None:
    """Return all closed trials as a DataFrame, or None on error."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            """
            SELECT id, verdict_id, verdict_timestamp, ticker1, ticker2,
                   composition_label, half_life, snr, episodes,
                   hold_days, net_pnl_bps, gross_pnl_bps, estimated_costs_bps,
                   is_profitable, exit_reason,
                   gate_1_result, gate_2_result, gate_3_result,
                   gate_4_result, gate_6_result, gate_7_result
            FROM trial_ledger
            WHERE is_closed = 1
            ORDER BY verdict_timestamp ASC
            """,
            conn,
        )
        conn.close()
        return df
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not read ledger — {exc}")
        return None


def load_open_trials(db_path: str) -> pd.DataFrame | None:
    """Return all open (not yet closed) trials."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            """
            SELECT id, verdict_id, verdict_timestamp, ticker1, ticker2,
                   composition_label, entry_z_verdict, half_life, snr
            FROM trial_ledger
            WHERE is_closed = 0
            ORDER BY verdict_timestamp DESC
            """,
            conn,
        )
        conn.close()
        return df
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not read open trials — {exc}")
        return None


# ── Item 13: Position Revalidation History ───────────────────────────────────

REVALIDATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS position_revalidations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict_id          TEXT NOT NULL,
    check_timestamp     TEXT NOT NULL,
    snr_entry           REAL,
    snr_current         REAL,
    snr_change_bps      REAL,
    mean_drift_sigma    REAL,
    drift_detected      INTEGER,
    decision            TEXT,   -- HOLD | MONITOR | AUTO_CLOSE
    rationale           TEXT,
    days_held           INTEGER,
    FOREIGN KEY(verdict_id) REFERENCES trial_ledger(verdict_id)
);

CREATE INDEX IF NOT EXISTS idx_reval_verdict_id ON position_revalidations(verdict_id);
CREATE INDEX IF NOT EXISTS idx_reval_timestamp  ON position_revalidations(check_timestamp);
CREATE INDEX IF NOT EXISTS idx_reval_decision   ON position_revalidations(decision);
"""


def init_position_revalidations_table(db_path: str) -> bool:
    """Create position_revalidations table and indexes if not already present."""
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(REVALIDATION_SCHEMA_SQL)
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not create position_revalidations table — {exc}")
        return False


def record_position_revalidation(
    db_path: str,
    verdict_id: str,
    snr_entry: Optional[float],
    snr_current: Optional[float],
    snr_change_bps: Optional[float],
    mean_drift_sigma: Optional[float],
    drift_detected: bool,
    decision: str,
    rationale: str,
    days_held: Optional[int],
) -> bool:
    """
    Record a single position revalidation result to position_revalidations table.

    Called by sentinel.py after each revalidation run.
    Returns True on success.
    """
    try:
        init_position_revalidations_table(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO position_revalidations (
                verdict_id, check_timestamp,
                snr_entry, snr_current, snr_change_bps,
                mean_drift_sigma, drift_detected,
                decision, rationale, days_held
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verdict_id,
                datetime.now().isoformat(),
                snr_entry,
                snr_current,
                snr_change_bps,
                mean_drift_sigma,
                1 if drift_detected else 0,
                decision,
                rationale,
                days_held,
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not record revalidation — {exc}")
        return False


def load_revalidation_history(
    db_path: str,
    verdict_id: Optional[str] = None,
    decision_filter: Optional[str] = None,
) -> "pd.DataFrame | None":
    """
    Return revalidation history as a DataFrame.

    Parameters
    ----------
    verdict_id : str, optional
        Filter to a single position
    decision_filter : str, optional
        Filter by decision (e.g. 'AUTO_CLOSE')
    """
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        clauses = []
        params = []
        if verdict_id:
            clauses.append("verdict_id = ?")
            params.append(verdict_id)
        if decision_filter:
            clauses.append("decision = ?")
            params.append(decision_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        df = pd.read_sql_query(
            f"""
            SELECT * FROM position_revalidations
            {where}
            ORDER BY check_timestamp DESC
            """,
            conn,
            params=params,
        )
        conn.close()
        return df
    except Exception as exc:
        print(f"ERROR [trial_ledger]: could not read revalidation history — {exc}")
        return None


def get_ledger_summary(db_path: str) -> dict:
    """Return a quick-read summary dict for reporting."""
    if not os.path.exists(db_path):
        return {"exists": False}
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            SELECT
                COUNT(*)                          AS total,
                SUM(is_closed)                    AS closed,
                SUM(CASE WHEN is_closed=0 THEN 1 END) AS open,
                SUM(is_profitable)                AS profitable,
                AVG(net_pnl_bps)                  AS avg_net_pnl_bps,
                MIN(verdict_timestamp)            AS first_verdict,
                MAX(verdict_timestamp)            AS last_verdict
            FROM trial_ledger
            """
        ).fetchone()
        conn.close()
        keys = ["total", "closed", "open", "profitable",
                "avg_net_pnl_bps", "first_verdict", "last_verdict"]
        return {"exists": True, **dict(zip(keys, row))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}
