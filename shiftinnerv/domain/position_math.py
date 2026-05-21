"""
shiftinnerv.domain.position_math — Pure math for open-position revalidation.

Extracted from shiftinnerv/sensors/position_monitor.py in step 3 of the
package reorganization.

Every function and class in this module is pure: no I/O, no database,
no filesystem, no network. The sensor shell (position_monitor.py) handles
data loading; this module handles what you do with the data once loaded.

Classes:
    PositionRevalidationResult — Result container for a single position check.

Functions:
    compute_snr_from_prices — SNR from two price series using OLS residuals.
    detect_mean_drift       — Vidyamurthy mean drift criterion.
"""

from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant


# ── Result container ──────────────────────────────────────────────────────────

class PositionRevalidationResult:
    """Result of revalidating a single open position."""

    def __init__(self):
        self.verdict_id: Optional[str] = None
        self.ticker1: Optional[str] = None
        self.ticker2: Optional[str] = None
        self.entry_timestamp: Optional[str] = None
        self.entry_snr: Optional[float] = None
        self.current_snr: Optional[float] = None
        self.snr_change_bps: Optional[float] = None   # basis points
        self.mean_drift_sigma: Optional[float] = None
        self.drift_detected: bool = False
        self.decision: Optional[str] = None           # HOLD | MONITOR | AUTO_CLOSE
        self.rationale: Optional[str] = None
        self.days_held: Optional[int] = None
        self.error: Optional[str] = None

    def __repr__(self):
        return (
            f"<PositionRevalidationResult "
            f"{self.ticker1}/{self.ticker2} "
            f"snr={self.entry_snr:.3f}→{self.current_snr:.3f} "
            f"decision={self.decision}>"
        ) if self.current_snr is not None else (
            f"<PositionRevalidationResult "
            f"{self.ticker1}/{self.ticker2} "
            f"error={self.error}>"
        )


# ── Core computation ──────────────────────────────────────────────────────────

def compute_snr_from_prices(
    ticker1_prices: pd.Series,
    ticker2_prices: pd.Series,
    window: int = 63,
) -> Optional[float]:
    """
    Compute SNR from price series using OLS residuals.

    Matches the exact formula in correlation.py:
        residuals       = OLS(log_p1 ~ log_p2).resid
        trend_component = log_p1 - residuals
        SNR             = var(residuals) / var(trend_component)

    Parameters
    ----------
    ticker1_prices, ticker2_prices : pd.Series
        Close prices (raw or log — function detects and converts)
    window : int
        Number of trailing days to use (default 63, matching dossier window)

    Returns
    -------
    float or None
        SNR value, or None if computation fails or insufficient data
    """
    try:
        # Detect raw vs log prices and convert to log
        p1 = np.log(ticker1_prices) if ticker1_prices.iloc[0] > 50 else ticker1_prices.copy()
        p2 = np.log(ticker2_prices) if ticker2_prices.iloc[0] > 50 else ticker2_prices.copy()

        # Align on common index then take last `window` rows
        common = p1.index.intersection(p2.index)
        p1 = p1.loc[common].tail(window)
        p2 = p2.loc[common].tail(window)

        if len(p1) < max(20, window // 3):
            return None

        ols = OLS(p1, add_constant(p2)).fit()
        residuals = pd.Series(ols.resid, index=p1.index)
        trend = p1 - residuals

        var_stat    = float(np.var(residuals, ddof=1))
        var_nonstat = float(np.var(trend, ddof=1))

        if var_nonstat < 1e-10:
            return None

        return var_stat / var_nonstat

    except Exception as exc:
        print(f"[position_math] SNR computation error: {exc}")
        return None


def detect_mean_drift(
    spread: pd.Series,
    entry_mean: float,
    entry_std: float,
    half_life_days: int,
    threshold_sigma: float = 2.0,
) -> tuple[float, bool]:
    """
    Detect if the spread's rolling mean has drifted significantly from
    the entry-time spread mean (Vidyamurthy mean drift criterion).

    Parameters
    ----------
    spread : pd.Series
        Current spread values
    entry_mean : float
        Spread mean recorded at entry (spread_mean column in trial_ledger)
    entry_std : float
        Spread std recorded at entry (spread_std column in trial_ledger)
    half_life_days : int
        Half-life used as rolling window; clamped to [10, 120]
    threshold_sigma : float
        Number of sigma units that constitutes "drift detected" (default 2.0)

    Returns
    -------
    (drift_sigma, drift_detected) : (float, bool)
    """
    try:
        window = max(10, min(120, int(half_life_days)))
        rolling_mean = spread.rolling(window=window, min_periods=window // 2).mean()

        current_mean = float(rolling_mean.dropna().iloc[-1])

        if entry_std < 1e-10:
            return 0.0, False

        drift_sigma = (current_mean - entry_mean) / entry_std
        drift_detected = abs(drift_sigma) > threshold_sigma

        return float(drift_sigma), drift_detected

    except Exception as exc:
        print(f"[position_math] Drift detection error: {exc}")
        return 0.0, False
