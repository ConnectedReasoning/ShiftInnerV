"""
ShiftInnerV — Deterministic Gate Evaluator
Item 4 of the Council Roadmap.

Pure Python evaluation of the five-gate framework. Takes numerical inputs
from the Scout report and outputs a structured verdict. No LLM involved.
This is the PRIMARY trading decision path.

Thresholds (from Items 1–3):
  Gate 1 — Johansen trace vs 95%/90% CI
  Gate 2 — Half-life <= 120 days
  Gate 3 — SNR >= 1.0
  Gate 4 — Episodes >= 2
  Gate 6 — Factor loading <= 0.3
  Gate 7 — Net P&L > 25 bps (0–25 = MARGINAL, <0 = UNPROFITABLE)
"""

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GateResult:
    """Result of a single gate evaluation."""
    status: str          # PASS | FAIL | N/A | MONITOR-NEAR | FACTOR_CONTAMINATED | SKIPPED | MARGINAL | UNPROFITABLE
    reason: str          # human-readable explanation
    value: Optional[float] = None  # the numeric value that triggered the gate


@dataclass
class EvaluatorOutput:
    """Complete verdict output."""
    verdict: str         # ACTIVE | MONITOR | MONITOR-NEAR | REJECT
    gates: dict = field(default_factory=dict)   # {"gate_1": GateResult, ...}
    rationale: str = ""  # concise explanation of verdict


class DeterministicGateEvaluator:
    """
    Evaluate all gates from numerical inputs only.

    Trading decision path — deterministic and auditable.
    Replaces LLM verdict matching for the primary signal.
    """

    def evaluate(
        self,
        trace_stat: Optional[float],
        crit_val_95: Optional[float],
        crit_val_90: Optional[float],
        half_life: Optional[float],
        snr: Optional[float],
        episodes: Optional[int],
        factor_loading: Optional[float] = None,
        net_pnl_bps: Optional[float] = None,
    ) -> EvaluatorOutput:
        """
        Evaluate all gates and return a structured verdict.

        Parameters
        ----------
        trace_stat      : Johansen trace statistic
        crit_val_95     : Critical value at 95% CI
        crit_val_90     : Critical value at 90% CI
        half_life       : Mean-reversion half-life in days
        snr             : Signal-to-noise ratio
        episodes        : Number of decoupling episodes
        factor_loading  : Normalised factor loading (None → SKIPPED)
        net_pnl_bps     : Expected net P&L after costs in basis points (None → SKIPPED)

        Returns
        -------
        EvaluatorOutput with verdict, per-gate results, and rationale
        """
        gates: dict[str, GateResult] = {}

        gates["gate_1"] = self._eval_gate_1(trace_stat, crit_val_95, crit_val_90)
        gates["gate_2"] = self._eval_gate_2(half_life)
        gates["gate_3"] = self._eval_gate_3(snr)
        gates["gate_4"] = self._eval_gate_4(episodes)
        gates["gate_6"] = self._eval_gate_6(factor_loading)
        gates["gate_7"] = self._eval_gate_7(net_pnl_bps)

        verdict, rationale = self._compute_verdict(
            gates["gate_1"], gates["gate_2"], gates["gate_3"],
            gates["gate_4"], gates["gate_6"], gates["gate_7"],
        )

        return EvaluatorOutput(verdict=verdict, gates=gates, rationale=rationale)

    # ── Individual gate evaluators ────────────────────────────────────────────

    def _eval_gate_1(
        self,
        trace_stat: Optional[float],
        crit_95: Optional[float],
        crit_90: Optional[float],
    ) -> GateResult:
        """Gate 1: Johansen cointegration at 95% CI, fallback MONITOR-NEAR at 90%."""
        if trace_stat is None or crit_95 is None:
            return GateResult(
                status="FAIL",
                reason="Missing trace statistic or 95% critical value.",
                value=trace_stat,
            )
        if trace_stat >= crit_95:
            return GateResult(
                status="PASS",
                reason=f"Trace {trace_stat:.2f} >= 95% crit {crit_95:.2f}.",
                value=trace_stat,
            )
        if crit_90 is not None and trace_stat >= crit_90:
            return GateResult(
                status="MONITOR-NEAR",
                reason=(
                    f"Trace {trace_stat:.2f} fails 95% CI ({crit_95:.2f}) "
                    f"but passes 90% CI ({crit_90:.2f})."
                ),
                value=trace_stat,
            )
        crit_90_display = f"{crit_90:.2f}" if crit_90 is not None else "N/A"
        return GateResult(
            status="FAIL",
            reason=(
                f"Trace {trace_stat:.2f} < 90% crit {crit_90_display}. "
                f"No cointegration found."
            ),
            value=trace_stat,
        )

    def _eval_gate_2(self, half_life: Optional[float]) -> GateResult:
        """Gate 2: Half-life must be <= 120 days."""
        if half_life is None or (isinstance(half_life, float) and math.isnan(half_life)):
            return GateResult(
                status="FAIL",
                reason="Half-life is None or NaN. Spread is non-mean-reverting.",
                value=None,
            )
        if half_life > 120:
            return GateResult(
                status="FAIL",
                reason=(
                    f"Half-life {half_life:.1f}d exceeds 120d ceiling. "
                    f"Mean reversion too slow to trade."
                ),
                value=half_life,
            )
        return GateResult(
            status="PASS",
            reason=f"Half-life {half_life:.1f}d is within tradeable horizon.",
            value=half_life,
        )

    def _eval_gate_3(self, snr: Optional[float]) -> GateResult:
        """
        Gate 3: SNR must be >= 2.0.

        SNR = var(spread level) / var(spread daily changes).
        Higher means cleaner mean reversion relative to daily noise.
        Threshold raised from 1.0 to 2.0 to match the new definition —
        under the corrected formula, SNR < 2 means daily noise dominates.
        """
        if snr is None or (isinstance(snr, float) and math.isnan(snr)):
            return GateResult(
                status="FAIL",
                reason="SNR is None or NaN.",
                value=None,
            )
        if snr < 2.0:
            return GateResult(
                status="FAIL",
                reason=f"SNR {snr:.2f} < 2.0. Daily noise dominates the mean-reversion signal.",
                value=snr,
            )
        return GateResult(
            status="PASS",
            reason=f"SNR {snr:.2f} >= 2.0. Mean-reversion signal exceeds daily noise.",
            value=snr,
        )

    def _eval_gate_4(self, episodes: Optional[int]) -> GateResult:
        """Gate 4: At least 2 decoupling episodes required."""
        if episodes is None:
            return GateResult(
                status="N/A",
                reason="Episodes unknown (prior gate may have failed).",
                value=None,
            )
        if episodes < 2:
            return GateResult(
                status="FAIL",
                reason=f"Only {episodes} episode(s) detected. Insufficient persistence.",
                value=float(episodes),
            )
        return GateResult(
            status="PASS",
            reason=f"{episodes} episodes detected. Reversion is persistent.",
            value=float(episodes),
        )

    def _eval_gate_6(self, factor_loading: Optional[float]) -> GateResult:
        """Gate 6: Factor loading must be <= 0.3 (idiosyncratic cointegration)."""
        if factor_loading is None:
            return GateResult(
                status="SKIPPED",
                reason="Factor proxy not available or computation skipped.",
                value=None,
            )
        if factor_loading > 0.3:
            return GateResult(
                status="FACTOR_CONTAMINATED",
                reason=(
                    f"Factor loading {factor_loading:.4f} > 0.3. "
                    f"Cointegration may be sector-driven, not idiosyncratic."
                ),
                value=factor_loading,
            )
        return GateResult(
            status="PASS",
            reason=(
                f"Factor loading {factor_loading:.4f} <= 0.3. "
                f"Cointegration appears idiosyncratic."
            ),
            value=factor_loading,
        )

    def _eval_gate_7(self, net_pnl_bps: Optional[float]) -> GateResult:
        """Gate 7: Net P&L after costs must be > 25 bps (< 0 = UNPROFITABLE, 0–25 = MARGINAL)."""
        if net_pnl_bps is None:
            return GateResult(
                status="SKIPPED",
                reason="Net P&L not computed.",
                value=None,
            )
        if net_pnl_bps < 0:
            return GateResult(
                status="UNPROFITABLE",
                reason=(
                    f"Net P&L {net_pnl_bps:.0f} bps < 0. "
                    f"Costs exceed expected return."
                ),
                value=net_pnl_bps,
            )
        if net_pnl_bps < 25:
            return GateResult(
                status="MARGINAL",
                reason=(
                    f"Net P&L {net_pnl_bps:.0f} bps (0–25 range). "
                    f"Edge is thin; execution risk high."
                ),
                value=net_pnl_bps,
            )
        return GateResult(
            status="PASS",
            reason=f"Net P&L {net_pnl_bps:.0f} bps. Edge is robust after costs.",
            value=net_pnl_bps,
        )

    # ── Verdict aggregation ───────────────────────────────────────────────────

    def _compute_verdict(
        self,
        g1: GateResult,
        g2: GateResult,
        g3: GateResult,
        g4: GateResult,
        g6: GateResult,
        g7: GateResult,
    ) -> tuple[str, str]:
        """
        Apply verdict logic.

        REJECT  — Gate 1/2/3 FAIL, or Gate 7 UNPROFITABLE
        MONITOR — Gate 4 FAIL, Gate 6 FACTOR_CONTAMINATED, Gate 7 MARGINAL
        MONITOR-NEAR — Gate 1 MONITOR-NEAR (all other gates OK)
        ACTIVE  — all gates passed (or skipped non-blocking)
        """
        # Hard rejects — stop early
        if g1.status == "FAIL":
            return "REJECT", f"Gate 1: {g1.reason}"
        if g2.status == "FAIL":
            return "REJECT", f"Gate 2: {g2.reason}"
        if g3.status == "FAIL":
            return "REJECT", f"Gate 3: {g3.reason}"
        if g7.status == "UNPROFITABLE":
            return "REJECT", f"Gate 7: {g7.reason}"

        # Soft downgrades — accumulate
        downgrades: list[str] = []
        if g4.status == "FAIL":
            downgrades.append(f"Gate 4: {g4.reason}")
        if g6.status == "FACTOR_CONTAMINATED":
            downgrades.append(f"Gate 6: {g6.reason}")
        if g7.status == "MARGINAL":
            downgrades.append(f"Gate 7: {g7.reason}")

        if downgrades:
            return "MONITOR", "; ".join(downgrades)

        # Near-miss on cointegration
        if g1.status == "MONITOR-NEAR":
            return "MONITOR-NEAR", f"Gate 1: {g1.reason}"

        # All clear
        return "ACTIVE", "All gates passed."


# ── Module-level singleton ────────────────────────────────────────────────────

_evaluator = DeterministicGateEvaluator()


def evaluate_gates(
    trace_stat: Optional[float],
    crit_val_95: Optional[float],
    crit_val_90: Optional[float],
    half_life: Optional[float],
    snr: Optional[float],
    episodes: Optional[int],
    factor_loading: Optional[float] = None,
    net_pnl_bps: Optional[float] = None,
) -> EvaluatorOutput:
    """
    Evaluate all gates deterministically.

    This is the PRIMARY verdict path. Use instead of LLM for trading decisions.
    The LLM path (Signal Mathematician) should only be used for narrative/dossier.

    Parameters
    ----------
    trace_stat      : Johansen trace statistic
    crit_val_95     : 95% CI critical value
    crit_val_90     : 90% CI critical value
    half_life       : Mean-reversion half-life in days
    snr             : Signal-to-noise ratio
    episodes        : Number of decoupling episodes
    factor_loading  : Normalised factor loading (optional)
    net_pnl_bps     : Expected net P&L after costs in bps (optional)

    Returns
    -------
    EvaluatorOutput
        .verdict   — "ACTIVE" | "MONITOR" | "MONITOR-NEAR" | "REJECT"
        .gates     — per-gate GateResult objects
        .rationale — human-readable rationale string
    """
    return _evaluator.evaluate(
        trace_stat=trace_stat,
        crit_val_95=crit_val_95,
        crit_val_90=crit_val_90,
        half_life=half_life,
        snr=snr,
        episodes=episodes,
        factor_loading=factor_loading,
        net_pnl_bps=net_pnl_bps,
    )
