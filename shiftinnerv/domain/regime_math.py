"""
shiftinnerv.domain.regime_math — Pure types and logic for regime classification.

Extracted from shiftinnerv/sensors/regime_monitor.py in step 3 of the
package reorganization.

Every class and function here is pure: no network calls, no filesystem,
no yfinance, no cache state. The sensor shell (regime_monitor.py) handles
data acquisition (VIX fetch, CSV loading, caching); this module handles
what you do with the numbers once you have them.

Classes:
    RegimeState    — Enum of market stress levels.
    RegimeSnapshot — Immutable snapshot of a single regime evaluation.

Functions:
    classify_regime             — Given VIX + correlation info, return state
                                  and position size multiplier.
    get_position_size_multiplier — Extract multiplier from a RegimeSnapshot.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ── Enums and data shapes ─────────────────────────────────────────────────────

class RegimeState(str, Enum):
    """Market regime classification."""
    NORMAL      = "NORMAL"
    ELEVATED    = "ELEVATED"
    HIGH_STRESS = "HIGH_STRESS"
    CRISIS      = "CRISIS"


@dataclass
class RegimeSnapshot:
    """Current market regime state."""
    state:                    RegimeState
    timestamp:                datetime
    vix_level:                float
    correlation_regime:       bool
    correlated_pairs:         list          # [(ticker1, ticker2, corr), ...]
    position_size_multiplier: float         # 1.0, 0.5, 0.25, or 0.0
    rationale:                str
    vix_unavailable:          bool = False  # True if VIX fetch failed


# ── Pure classification ───────────────────────────────────────────────────────

# VIX thresholds — duplicated here so domain code can use them without
# importing the sensor class. Keep in sync with RegimeDetector constants.
VIX_ELEVATED    = 20.0
VIX_HIGH_STRESS = 30.0
VIX_CRISIS      = 40.0
SPY_CORR_THRESHOLD         = 0.7
CORRELATION_REGIME_FRACTION = 0.5
VIX_DEFAULT_UNAVAILABLE    = 20.0


def classify_regime(
    vix: float,
    correlated_pairs: list,
    n_open_positions: int,
    vix_unavailable: bool = False,
) -> tuple[RegimeState, float, str]:
    """
    Classify market regime given VIX level and correlation info.

    This is the pure heart of RegimeDetector.detect_regime() — the
    threshold logic extracted from the data-acquisition shell.

    Parameters
    ----------
    vix : float
        Current VIX level (or the default when unavailable).
    correlated_pairs : list
        List of (ticker1, ticker2, corr) tuples where |corr| > threshold.
    n_open_positions : int
        Total number of open positions (used for correlation regime fraction).
    vix_unavailable : bool
        If True, the VIX value is a fallback default, not a live reading.
        Prepended to rationale string.

    Returns
    -------
    (state, multiplier, rationale) : (RegimeState, float, str)
    """
    # ── VIX-driven base state ─────────────────────────────────────────────────
    if vix >= VIX_CRISIS:
        state      = RegimeState.CRISIS
        multiplier = 0.0
        rationale  = f"CRISIS: VIX {vix:.1f} ≥ {VIX_CRISIS:.0f}. All new entries halted."
    elif vix >= VIX_HIGH_STRESS:
        state      = RegimeState.HIGH_STRESS
        multiplier = 0.25
        rationale  = (
            f"HIGH_STRESS: VIX {vix:.1f} in [{VIX_HIGH_STRESS:.0f}, "
            f"{VIX_CRISIS:.0f}). Only SNR ≥ 2.0 pairs accepted. "
            f"Position size 0.25x."
        )
    elif vix >= VIX_ELEVATED:
        state      = RegimeState.ELEVATED
        multiplier = 0.5
        rationale  = (
            f"ELEVATED: VIX {vix:.1f} in [{VIX_ELEVATED:.0f}, "
            f"{VIX_HIGH_STRESS:.0f}). Position size 0.5x."
        )
    else:
        state      = RegimeState.NORMAL
        multiplier = 1.0
        rationale  = f"NORMAL: VIX {vix:.1f} < {VIX_ELEVATED:.0f}. Position size 1.0x."

    # ── Correlation regime stacks on top ──────────────────────────────────────
    correlation_regime = (
        n_open_positions > 0
        and len(correlated_pairs) > n_open_positions * CORRELATION_REGIME_FRACTION
    )
    if correlation_regime and state != RegimeState.CRISIS:
        multiplier *= 0.5
        rationale += (
            f" CORRELATION_REGIME: {len(correlated_pairs)}/{n_open_positions} pair(s) "
            f"have |SPY corr| > {SPY_CORR_THRESHOLD}. "
            f"Additional 0.5x reduction → final {multiplier:.4g}x."
        )

    if vix_unavailable:
        rationale = f"[VIX UNAVAILABLE — used default {VIX_DEFAULT_UNAVAILABLE}] " + rationale

    return state, multiplier, rationale


def get_position_size_multiplier(regime: RegimeSnapshot) -> float:
    """Helper to extract position size multiplier from a RegimeSnapshot."""
    return regime.position_size_multiplier
