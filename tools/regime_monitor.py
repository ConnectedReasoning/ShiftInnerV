"""
ShiftInnerV — Market Regime Detection Sensor
Item 8 of the Council Roadmap.

Detects market stress via:
  1. VIX level (volatility proxy)
  2. Rolling pair-SPY correlation (systematic risk)

On each screening cycle, determines regime state and applies position sizing
modulations. Halts new entries if VIX >= 40 (CRISIS).

Usage:
    from tools.regime_monitor import (
        RegimeDetector, RegimeState, get_position_size_multiplier
    )

    detector = RegimeDetector(data_dir=data_dir, logger=logger)
    regime = detector.detect_regime(
        open_positions=[(ticker1, ticker2), ...],
        logger=logger,
    )

    print(f"Current regime: {regime.state}")
    print(f"Position size multiplier: {regime.position_size_multiplier}x")

    if regime.state == RegimeState.CRISIS:
        print("HALT: New entries forbidden in CRISIS regime")
        sys.exit(1)
"""

import os
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

import numpy as np
import pandas as pd
import yfinance as yf


class RegimeState(str, Enum):
    """Market regime classification."""
    NORMAL     = "NORMAL"
    ELEVATED   = "ELEVATED"
    HIGH_STRESS = "HIGH_STRESS"
    CRISIS     = "CRISIS"


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
    vix_unavailable:          bool = False  # True if VIX fetch failed and default was used


class RegimeDetector:
    """
    Detect market regime and apply position sizing rules.

    Runs before each screening cycle to determine if conditions have changed.
    VIX is cached for 1 hour to avoid repeated network calls within a single run.
    """

    # VIX thresholds
    VIX_ELEVATED   = 20.0
    VIX_HIGH_STRESS = 30.0
    VIX_CRISIS     = 40.0

    # SPY correlation threshold — above this a pair is considered SYSTEMATIC
    SPY_CORR_THRESHOLD = 0.7

    # Fraction of open positions that must be SYSTEMATIC to trigger CORRELATION_REGIME
    CORRELATION_REGIME_FRACTION = 0.5

    # VIX default when data is unavailable — conservative: assume ELEVATED
    VIX_DEFAULT_UNAVAILABLE = 20.0

    def __init__(self, data_dir: str, logger: logging.Logger = None):
        self.data_dir  = data_dir
        self.logger    = logger
        self._last_vix       = None
        self._last_vix_fetch = None

    # ── VIX ───────────────────────────────────────────────────────────────────

    def fetch_vix(self, use_cache: bool = True) -> float | None:
        """
        Fetch current VIX level via yfinance.

        Parameters
        ----------
        use_cache : bool
            If True and VIX was fetched < 1 hour ago, return cached value.

        Returns
        -------
        float | None
            Current VIX level, or None if unavailable (caller handles fallback).
        """
        if (use_cache
                and self._last_vix is not None
                and self._last_vix_fetch is not None):
            age = datetime.now() - self._last_vix_fetch
            if age < timedelta(hours=1):
                if self.logger:
                    self.logger.debug(
                        f"[regime] VIX cache hit — {self._last_vix:.1f} "
                        f"(fetched {int(age.total_seconds())}s ago)"
                    )
                return self._last_vix

        try:
            vix_data = yf.download(
                "^VIX",
                period="2d",      # 2d to ensure we always get at least one row
                progress=False,
                auto_adjust=True,
            )
            if vix_data.empty:
                if self.logger:
                    self.logger.warning("[regime] VIX download returned empty DataFrame.")
                return self._last_vix  # return cached if available, else None

            vix_close = float(vix_data["Close"].iloc[-1])
            self._last_vix       = vix_close
            self._last_vix_fetch = datetime.now()
            return vix_close

        except Exception as exc:
            if self.logger:
                self.logger.warning(f"[regime] VIX fetch failed: {exc}")
            return self._last_vix  # cached or None

    # ── Pair-SPY correlation ──────────────────────────────────────────────────

    def compute_pair_spy_correlation(
        self,
        ticker1: str,
        ticker2: str,
        window: int = 20,
    ) -> float | None:
        """
        Compute rolling correlation of pair log-spread to SPY daily returns.

        Uses price CSVs from data_dir (same format as the rest of the pipeline).
        Falls back to yfinance download when CSVs are missing.

        Parameters
        ----------
        ticker1, ticker2 : str
        window : int
            Look-back in trading days (default 20).

        Returns
        -------
        float | None
        """
        try:
            p1  = self._load_prices(ticker1, window=window + 5)
            p2  = self._load_prices(ticker2, window=window + 5)
            spy = self._load_prices("SPY",    window=window + 5)

            if p1 is None or p2 is None or spy is None:
                return None

            combined = pd.concat(
                {"p1": p1, "p2": p2, "spy": spy}, axis=1
            ).dropna()

            if len(combined) < max(window // 2, 5):
                return None

            spread      = np.log(combined["p1"]) - np.log(combined["p2"])
            spy_returns = combined["spy"].pct_change().dropna()

            common = spread.index.intersection(spy_returns.index)
            if len(common) < max(window // 2, 5):
                return None

            corr = spread[common].corr(spy_returns[common])
            return float(corr) if not np.isnan(corr) else None

        except Exception as exc:
            if self.logger:
                self.logger.debug(
                    f"[regime] Correlation failed for {ticker1}/{ticker2}: {exc}"
                )
            return None

    def _load_prices(self, ticker: str, window: int = 25) -> pd.Series | None:
        """
        Load closing prices for *ticker*.

        Tries the pipeline CSV first (data_dir/{ticker}_daily.csv or
        data_dir/{ticker.lower()}_daily.csv), then falls back to a live
        yfinance download for SPY and any ticker that isn't in the local store.
        """
        for name in (ticker, ticker.lower(), ticker.upper()):
            csv_path = os.path.join(self.data_dir, f"{name}_daily.csv")
            if os.path.exists(csv_path):
                try:
                    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                    if "Close" in df.columns:
                        return df["Close"].tail(window)
                    if "close" in df.columns:
                        return df["close"].tail(window)
                except Exception:
                    pass

        # Fallback: live yfinance fetch (network call — only for missing CSVs)
        try:
            raw = yf.download(ticker, period="3mo", progress=False, auto_adjust=True)
            if not raw.empty and "Close" in raw.columns:
                return raw["Close"].tail(window)
        except Exception:
            pass

        return None

    # ── Main detection ────────────────────────────────────────────────────────

    def detect_regime(
        self,
        open_positions: list | None = None,
        logger: logging.Logger = None,
    ) -> RegimeSnapshot:
        """
        Determine current market regime.

        Parameters
        ----------
        open_positions : list of (ticker1, ticker2) tuples
            Open position pairs to check for SPY correlation.
        logger : logging.Logger
            Optional override logger (falls back to self.logger).

        Returns
        -------
        RegimeSnapshot
        """
        log = logger or self.logger

        # ── 1. VIX ────────────────────────────────────────────────────────────
        vix = self.fetch_vix()
        vix_unavailable = False
        if vix is None:
            vix = self.VIX_DEFAULT_UNAVAILABLE
            vix_unavailable = True
            if log:
                log.warning(
                    f"[regime] VIX unavailable; defaulting to {vix:.1f} (ELEVATED boundary). "
                    f"Treat as conservative estimate."
                )

        # ── 2. Pair-SPY correlation ───────────────────────────────────────────
        correlated_pairs: list[tuple[str, str, float]] = []
        if open_positions:
            for t1, t2 in open_positions:
                corr = self.compute_pair_spy_correlation(t1, t2)
                if corr is not None and abs(corr) > self.SPY_CORR_THRESHOLD:
                    correlated_pairs.append((t1, t2, corr))
                    if log:
                        log.info(
                            f"[regime] {t1}/{t2} SPY-corr={corr:.3f} — SYSTEMATIC "
                            f"(|corr| > {self.SPY_CORR_THRESHOLD})"
                        )

        n_open = len(open_positions) if open_positions else 0
        correlation_regime = (
            n_open > 0
            and len(correlated_pairs) > n_open * self.CORRELATION_REGIME_FRACTION
        )

        # ── 3. Regime state & base multiplier ────────────────────────────────
        if vix >= self.VIX_CRISIS:
            state      = RegimeState.CRISIS
            multiplier = 0.0
            rationale  = f"CRISIS: VIX {vix:.1f} ≥ {self.VIX_CRISIS:.0f}. All new entries halted."
        elif vix >= self.VIX_HIGH_STRESS:
            state      = RegimeState.HIGH_STRESS
            multiplier = 0.25
            rationale  = (
                f"HIGH_STRESS: VIX {vix:.1f} in [{self.VIX_HIGH_STRESS:.0f}, "
                f"{self.VIX_CRISIS:.0f}). Only SNR ≥ 2.0 pairs accepted. "
                f"Position size 0.25x."
            )
        elif vix >= self.VIX_ELEVATED:
            state      = RegimeState.ELEVATED
            multiplier = 0.5
            rationale  = (
                f"ELEVATED: VIX {vix:.1f} in [{self.VIX_ELEVATED:.0f}, "
                f"{self.VIX_HIGH_STRESS:.0f}). Position size 0.5x."
            )
        else:
            state      = RegimeState.NORMAL
            multiplier = 1.0
            rationale  = f"NORMAL: VIX {vix:.1f} < {self.VIX_ELEVATED:.0f}. Position size 1.0x."

        # ── 4. Correlation regime stacks on top ───────────────────────────────
        if correlation_regime and state != RegimeState.CRISIS:
            multiplier *= 0.5
            rationale += (
                f" CORRELATION_REGIME: {len(correlated_pairs)}/{n_open} pair(s) "
                f"have |SPY corr| > {self.SPY_CORR_THRESHOLD}. "
                f"Additional 0.5x reduction → final {multiplier:.4g}x."
            )

        if vix_unavailable:
            rationale = f"[VIX UNAVAILABLE — used default {self.VIX_DEFAULT_UNAVAILABLE}] " + rationale

        snapshot = RegimeSnapshot(
            state=state,
            timestamp=datetime.now(),
            vix_level=vix,
            correlation_regime=correlation_regime,
            correlated_pairs=correlated_pairs,
            position_size_multiplier=multiplier,
            rationale=rationale,
            vix_unavailable=vix_unavailable,
        )

        if log:
            log.info(
                f"[regime] State={snapshot.state.value} | VIX={snapshot.vix_level:.1f} | "
                f"Multiplier={snapshot.position_size_multiplier}x | "
                f"OpenPos={n_open} | Correlated={len(correlated_pairs)}"
            )

        return snapshot


def get_position_size_multiplier(regime: RegimeSnapshot) -> float:
    """Helper to extract position size multiplier from a RegimeSnapshot."""
    return regime.position_size_multiplier
