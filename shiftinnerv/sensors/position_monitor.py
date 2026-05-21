#!/usr/bin/env python3
"""
ShiftInnerV — Position Monitor & SNR Revalidation
Item 13 of the Council Roadmap.

On every sentinel run, revalidate all open positions:
  - Recompute SNR from current price data (last 63 days)
  - Detect mean drift against entry-time spread statistics
  - Flag for review (MONITOR) or auto-close (AUTO_CLOSE) if SNR deteriorates

Decision logic:
  SNR >= 1.0                          → HOLD
  0.7 <= SNR < 1.0                    → MONITOR
  SNR < 0.7 AND drift_sigma > 2.0     → AUTO_CLOSE
  SNR < 0.7 AND no significant drift  → MONITOR

Usage:
    from shiftinnerv.sensors.position_monitor import revalidate_open_positions

    results = revalidate_open_positions(
        db_path="trial_ledger.db",
        data_dir="/path/to/price/data",
        logger=logger,
    )
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

# ── Pure math and result type (canonical home: domain/position_math.py) ───────
from shiftinnerv.domain.position_math import (
    PositionRevalidationResult,
    compute_snr_from_prices,
    detect_mean_drift,
)

# Re-exported for callers that import these names from this module
__all__ = [
    "PositionRevalidationResult",
    "compute_snr_from_prices",
    "detect_mean_drift",
    "load_price_series",
    "revalidate_open_positions",
]


# ── Data loading ──────────────────────────────────────────────────────────────

def load_price_series(
    data_dir: str,
    ticker: str,
) -> Optional[pd.Series]:
    """
    Load daily close prices from CSV.

    Expected filename: {ticker_lower}_daily.csv
    Expected format:   date-indexed, 'Close' column present
    """
    try:
        path = os.path.join(data_dir, f"{ticker.lower()}_daily.csv")
        if not os.path.exists(path):
            return None
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if "Close" not in df.columns:
            return None
        return df["Close"].dropna().sort_index()
    except Exception as exc:
        print(f"[position_monitor] Error loading {ticker}: {exc}")
        return None


# ── Main revalidation loop ────────────────────────────────────────────────────

def revalidate_open_positions(
    db_path: str,
    data_dir: str,
    logger=None,
    snr_threshold_hold: float = 1.0,
    snr_threshold_monitor: float = 0.7,
) -> list[PositionRevalidationResult]:
    """
    Revalidate all open positions in the trial ledger.

    For each open position:
      1. Load current price data
      2. Recompute rolling SNR (63-day window)
      3. Detect mean drift against entry-time statistics
      4. Apply decision logic (HOLD / MONITOR / AUTO_CLOSE)

    Parameters
    ----------
    db_path : str
        Path to trial_ledger.db
    data_dir : str
        Directory containing {ticker}_daily.csv files
    logger : logging.Logger, optional
        Logger for structured output
    snr_threshold_hold : float
        SNR at or above this → HOLD (default 1.0)
    snr_threshold_monitor : float
        SNR below this + drift → AUTO_CLOSE (default 0.7)

    Returns
    -------
    list of PositionRevalidationResult
    """
    results: list[PositionRevalidationResult] = []

    if not os.path.exists(db_path):
        if logger:
            logger.error(f"[position_monitor] Trial ledger not found: {db_path}")
        return results

    # ── Load open positions ───────────────────────────────────────────────────
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, verdict_id, ticker1, ticker2, verdict_timestamp,
                   entry_z_verdict, half_life, snr, spread_mean, spread_std,
                   hedge_ratio
            FROM trial_ledger
            WHERE is_closed = 0
            ORDER BY verdict_timestamp DESC
        """)
        open_positions = cursor.fetchall()
        conn.close()
    except Exception as exc:
        if logger:
            logger.error(f"[position_monitor] Error loading open positions: {exc}")
        return results

    if not open_positions:
        if logger:
            logger.info("[position_monitor] No open positions to revalidate.")
        return results

    # ── Revalidate each position ──────────────────────────────────────────────
    for pos in open_positions:
        result = PositionRevalidationResult()
        result.verdict_id      = pos["verdict_id"]
        result.ticker1         = pos["ticker1"]
        result.ticker2         = pos["ticker2"]
        result.entry_timestamp = pos["verdict_timestamp"]
        result.entry_snr       = float(pos["snr"]) if pos["snr"] is not None else None

        try:
            # Load prices
            p1 = load_price_series(data_dir, pos["ticker1"])
            p2 = load_price_series(data_dir, pos["ticker2"])

            if p1 is None or p2 is None:
                result.error = (
                    f"Price data unavailable for "
                    f"{'ticker1' if p1 is None else 'ticker2'} "
                    f"({pos['ticker1'] if p1 is None else pos['ticker2']})"
                )
                results.append(result)
                if logger:
                    logger.warning(
                        f"[position_monitor] SKIP {result.ticker1}/{result.ticker2} — {result.error}"
                    )
                continue

            # Recompute SNR
            result.current_snr = compute_snr_from_prices(p1, p2, window=63)

            if result.current_snr is None:
                result.error = "SNR computation failed (insufficient aligned data)"
                results.append(result)
                if logger:
                    logger.warning(
                        f"[position_monitor] SKIP {result.ticker1}/{result.ticker2} — {result.error}"
                    )
                continue

            # SNR change in basis points
            if result.entry_snr is not None:
                result.snr_change_bps = (result.current_snr - result.entry_snr) * 10_000

            # Days held
            try:
                entry_dt = pd.to_datetime(result.entry_timestamp)
                result.days_held = (datetime.now() - entry_dt).days
            except Exception:
                result.days_held = None

            # Mean drift detection
            spread_mean_entry = pos["spread_mean"]
            spread_std_entry  = pos["spread_std"]
            half_life         = pos["half_life"]
            hedge_ratio       = pos["hedge_ratio"] if pos["hedge_ratio"] is not None else 1.0

            if (
                spread_mean_entry is not None
                and spread_std_entry is not None
                and half_life is not None
            ):
                log_p1 = np.log(p1) if p1.iloc[0] > 50 else p1
                log_p2 = np.log(p2) if p2.iloc[0] > 50 else p2
                common = log_p1.index.intersection(log_p2.index)
                spread = (log_p1.loc[common] - hedge_ratio * log_p2.loc[common]).tail(126)

                result.mean_drift_sigma, result.drift_detected = detect_mean_drift(
                    spread,
                    entry_mean=float(spread_mean_entry),
                    entry_std=float(spread_std_entry),
                    half_life_days=int(half_life),
                )
            else:
                result.mean_drift_sigma = None
                result.drift_detected = False

            # ── Decision logic ────────────────────────────────────────────────
            snr = result.current_snr

            if snr >= snr_threshold_hold:
                result.decision  = "HOLD"
                result.rationale = (
                    f"SNR {snr:.3f} >= {snr_threshold_hold:.1f}. "
                    f"Signal still dominant. Hold position."
                )
            elif snr >= snr_threshold_monitor:
                result.decision  = "MONITOR"
                result.rationale = (
                    f"SNR {snr:.3f} in caution range "
                    f"[{snr_threshold_monitor:.1f}, {snr_threshold_hold:.1f}). "
                    f"Review position for potential exit."
                )
            else:
                if result.drift_detected:
                    result.decision  = "AUTO_CLOSE"
                    result.rationale = (
                        f"SNR {snr:.3f} < {snr_threshold_monitor:.1f} "
                        f"AND mean drift {result.mean_drift_sigma:+.2f}σ detected. "
                        f"Triggering time-based stop (Vidyamurthy criterion)."
                    )
                else:
                    drift_note = (
                        f"drift {result.mean_drift_sigma:+.2f}σ (within threshold)"
                        if result.mean_drift_sigma is not None
                        else "drift unknown (no baseline stats)"
                    )
                    result.decision  = "MONITOR"
                    result.rationale = (
                        f"SNR {snr:.3f} < {snr_threshold_monitor:.1f} "
                        f"but {drift_note}. Monitor closely."
                    )

        except Exception as exc:
            result.error = f"Revalidation error: {str(exc)}"

        results.append(result)

        # ── Log ───────────────────────────────────────────────────────────────
        if logger:
            if result.error:
                logger.warning(
                    f"[position_monitor] ERROR {result.ticker1}/{result.ticker2} — {result.error}"
                )
            else:
                icon = (
                    "⚠️ " if result.decision == "AUTO_CLOSE" else
                    "👀" if result.decision == "MONITOR"    else
                    "✓ "
                )
                bps_str = (
                    f"({result.snr_change_bps:+.0f} bps)"
                    if result.snr_change_bps is not None else ""
                )
                logger.info(
                    f"{icon} {result.ticker1}/{result.ticker2} | "
                    f"SNR {result.entry_snr:.3f} → {result.current_snr:.3f} {bps_str}| "
                    f"Decision: {result.decision}"
                )

    return results
