"""
ShiftInnerV — Gate Evaluator Tests
Item 4 of the Council Roadmap.

Tests for shiftinner/sensors/gate_evaluator.py — the deterministic five-gate evaluator
that is the PRIMARY trading decision path.

Usage:
    pytest tests/test_gate_evaluator.py -v
    pytest tests/test_gate_evaluator.py -v -k "gate_1"
    pytest tests/test_gate_evaluator.py -v --tb=short

No LLM, no network, no data files required.
"""

import math
import sys
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shiftinnerv.sensors.gate_evaluator import (
    DeterministicGateEvaluator,
    EvaluatorOutput,
    GateResult,
    evaluate_gates,
)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def all_pass_kwargs(**overrides) -> dict:
    """Return a default set of inputs that should produce ACTIVE, with overrides."""
    base = dict(
        trace_stat=20.0,
        crit_val_95=15.0,
        crit_val_90=10.0,
        half_life=25.0,
        snr=1.5,
        episodes=3,
        factor_loading=None,
        net_pnl_bps=None,
    )
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# GATE 1 — COINTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestGate1:
    def test_pass_at_95(self):
        r = evaluate_gates(**all_pass_kwargs(trace_stat=20.0, crit_val_95=15.0))
        assert r.gates["gate_1"].status == "PASS"
        assert r.verdict == "ACTIVE"

    def test_pass_exactly_at_95(self):
        """Edge case: trace == crit_95 should be PASS."""
        r = evaluate_gates(**all_pass_kwargs(trace_stat=15.0, crit_val_95=15.0))
        assert r.gates["gate_1"].status == "PASS"

    def test_monitor_near_between_90_and_95(self):
        r = evaluate_gates(**all_pass_kwargs(trace_stat=12.0, crit_val_95=15.0, crit_val_90=10.0))
        assert r.gates["gate_1"].status == "MONITOR-NEAR"
        assert r.verdict == "MONITOR-NEAR"

    def test_monitor_near_exactly_at_90(self):
        """Edge case: trace == crit_90 should be MONITOR-NEAR."""
        r = evaluate_gates(**all_pass_kwargs(trace_stat=10.0, crit_val_95=15.0, crit_val_90=10.0))
        assert r.gates["gate_1"].status == "MONITOR-NEAR"

    def test_fail_below_90(self):
        r = evaluate_gates(**all_pass_kwargs(trace_stat=8.0, crit_val_95=15.0, crit_val_90=10.0))
        assert r.gates["gate_1"].status == "FAIL"
        assert r.verdict == "REJECT"

    def test_fail_when_trace_is_none(self):
        r = evaluate_gates(**all_pass_kwargs(trace_stat=None))
        assert r.gates["gate_1"].status == "FAIL"
        assert r.verdict == "REJECT"

    def test_fail_when_crit_95_is_none(self):
        r = evaluate_gates(**all_pass_kwargs(crit_val_95=None))
        assert r.gates["gate_1"].status == "FAIL"
        assert r.verdict == "REJECT"

    def test_fail_when_crit_90_is_none_and_below_95(self):
        """No 90% crit means only 95% check applies — below it should FAIL."""
        r = evaluate_gates(**all_pass_kwargs(trace_stat=12.0, crit_val_95=15.0, crit_val_90=None))
        assert r.gates["gate_1"].status == "FAIL"
        assert r.verdict == "REJECT"


# ══════════════════════════════════════════════════════════════════════════════
# GATE 2 — HALF-LIFE
# ══════════════════════════════════════════════════════════════════════════════

class TestGate2:
    def test_pass_within_120(self):
        r = evaluate_gates(**all_pass_kwargs(half_life=25.0))
        assert r.gates["gate_2"].status == "PASS"

    def test_pass_exactly_120(self):
        r = evaluate_gates(**all_pass_kwargs(half_life=120.0))
        assert r.gates["gate_2"].status == "PASS"

    def test_fail_above_120(self):
        r = evaluate_gates(**all_pass_kwargs(half_life=121.0))
        assert r.gates["gate_2"].status == "FAIL"
        assert r.verdict == "REJECT"

    def test_fail_on_none(self):
        r = evaluate_gates(**all_pass_kwargs(half_life=None))
        assert r.gates["gate_2"].status == "FAIL"
        assert r.verdict == "REJECT"

    def test_fail_on_nan(self):
        r = evaluate_gates(**all_pass_kwargs(half_life=float("nan")))
        assert r.gates["gate_2"].status == "FAIL"
        assert r.verdict == "REJECT"


# ══════════════════════════════════════════════════════════════════════════════
# GATE 3 — SNR
# ══════════════════════════════════════════════════════════════════════════════

class TestGate3:
    def test_pass_above_1(self):
        r = evaluate_gates(**all_pass_kwargs(snr=1.5))
        assert r.gates["gate_3"].status == "PASS"

    def test_pass_exactly_1(self):
        r = evaluate_gates(**all_pass_kwargs(snr=1.0))
        assert r.gates["gate_3"].status == "PASS"

    def test_fail_below_1(self):
        r = evaluate_gates(**all_pass_kwargs(snr=0.99))
        assert r.gates["gate_3"].status == "FAIL"
        assert r.verdict == "REJECT"

    def test_fail_on_none(self):
        r = evaluate_gates(**all_pass_kwargs(snr=None))
        assert r.gates["gate_3"].status == "FAIL"

    def test_fail_on_nan(self):
        r = evaluate_gates(**all_pass_kwargs(snr=float("nan")))
        assert r.gates["gate_3"].status == "FAIL"


# ══════════════════════════════════════════════════════════════════════════════
# GATE 4 — EPISODES
# ══════════════════════════════════════════════════════════════════════════════

class TestGate4:
    def test_pass_two_episodes(self):
        r = evaluate_gates(**all_pass_kwargs(episodes=2))
        assert r.gates["gate_4"].status == "PASS"

    def test_fail_one_episode(self):
        r = evaluate_gates(**all_pass_kwargs(episodes=1))
        assert r.gates["gate_4"].status == "FAIL"
        assert r.verdict == "MONITOR"  # soft downgrade, not REJECT

    def test_fail_zero_episodes(self):
        r = evaluate_gates(**all_pass_kwargs(episodes=0))
        assert r.gates["gate_4"].status == "FAIL"
        assert r.verdict == "MONITOR"

    def test_na_on_none(self):
        r = evaluate_gates(**all_pass_kwargs(episodes=None))
        assert r.gates["gate_4"].status == "N/A"
        # N/A is not a downgrade — should still be ACTIVE
        assert r.verdict == "ACTIVE"


# ══════════════════════════════════════════════════════════════════════════════
# GATE 6 — FACTOR LOADING
# ══════════════════════════════════════════════════════════════════════════════

class TestGate6:
    def test_pass_below_0_3(self):
        r = evaluate_gates(**all_pass_kwargs(factor_loading=0.15))
        assert r.gates["gate_6"].status == "PASS"

    def test_pass_exactly_0_3(self):
        r = evaluate_gates(**all_pass_kwargs(factor_loading=0.3))
        assert r.gates["gate_6"].status == "PASS"

    def test_contaminated_above_0_3(self):
        r = evaluate_gates(**all_pass_kwargs(factor_loading=0.31))
        assert r.gates["gate_6"].status == "FACTOR_CONTAMINATED"
        assert r.verdict == "MONITOR"

    def test_skipped_when_none(self):
        r = evaluate_gates(**all_pass_kwargs(factor_loading=None))
        assert r.gates["gate_6"].status == "SKIPPED"
        assert r.verdict == "ACTIVE"  # skipped is non-blocking


# ══════════════════════════════════════════════════════════════════════════════
# GATE 7 — NET P&L
# ══════════════════════════════════════════════════════════════════════════════

class TestGate7:
    def test_pass_above_25(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=100.0))
        assert r.gates["gate_7"].status == "PASS"

    def test_pass_exactly_25(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=25.0))
        assert r.gates["gate_7"].status == "PASS"

    def test_marginal_between_0_and_25(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=10.0))
        assert r.gates["gate_7"].status == "MARGINAL"
        assert r.verdict == "MONITOR"

    def test_marginal_exactly_0(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=0.0))
        assert r.gates["gate_7"].status == "MARGINAL"
        assert r.verdict == "MONITOR"

    def test_unprofitable_negative(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=-10.0))
        assert r.gates["gate_7"].status == "UNPROFITABLE"
        assert r.verdict == "REJECT"

    def test_skipped_when_none(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=None))
        assert r.gates["gate_7"].status == "SKIPPED"
        assert r.verdict == "ACTIVE"


# ══════════════════════════════════════════════════════════════════════════════
# VERDICT AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════

class TestVerdictAggregation:
    def test_all_pass_is_active(self):
        r = evaluate_gates(**all_pass_kwargs())
        assert r.verdict == "ACTIVE"
        assert "All gates passed" in r.rationale

    def test_gate1_fail_overrides_all_others(self):
        """Gate 1 failure → REJECT even if all other gates pass."""
        r = evaluate_gates(**all_pass_kwargs(trace_stat=5.0, crit_val_95=15.0, crit_val_90=10.0))
        assert r.verdict == "REJECT"
        assert "Gate 1" in r.rationale

    def test_gate2_fail_is_reject(self):
        r = evaluate_gates(**all_pass_kwargs(half_life=200.0))
        assert r.verdict == "REJECT"

    def test_gate3_fail_is_reject(self):
        r = evaluate_gates(**all_pass_kwargs(snr=0.5))
        assert r.verdict == "REJECT"

    def test_gate4_fail_is_monitor_not_reject(self):
        """Gate 4 failure downgrades to MONITOR, does not REJECT."""
        r = evaluate_gates(**all_pass_kwargs(episodes=1))
        assert r.verdict == "MONITOR"

    def test_gate1_monitor_near_with_other_gates_passing(self):
        r = evaluate_gates(**all_pass_kwargs(trace_stat=12.0, crit_val_95=15.0, crit_val_90=10.0))
        assert r.verdict == "MONITOR-NEAR"

    def test_multiple_soft_downgrades_all_captured_in_rationale(self):
        """Gate 4 fail + Gate 6 contaminated → MONITOR with both reasons."""
        r = evaluate_gates(**all_pass_kwargs(episodes=1, factor_loading=0.5))
        assert r.verdict == "MONITOR"
        assert "Gate 4" in r.rationale
        assert "Gate 6" in r.rationale

    def test_gate7_unprofitable_is_hard_reject(self):
        r = evaluate_gates(**all_pass_kwargs(net_pnl_bps=-50.0))
        assert r.verdict == "REJECT"

    def test_gate1_monitor_near_overridden_by_gate4_fail(self):
        """Gate 1 MONITOR-NEAR + Gate 4 FAIL → MONITOR (not MONITOR-NEAR)."""
        r = evaluate_gates(**all_pass_kwargs(
            trace_stat=12.0, crit_val_95=15.0, crit_val_90=10.0,
            episodes=1,
        ))
        assert r.verdict == "MONITOR"

    def test_output_structure(self):
        r = evaluate_gates(**all_pass_kwargs())
        assert isinstance(r, EvaluatorOutput)
        assert r.verdict in ("ACTIVE", "MONITOR", "MONITOR-NEAR", "REJECT")
        assert all(f"gate_{i}" in r.gates for i in [1, 2, 3, 4, 6, 7])
        assert isinstance(r.rationale, str)
        for g in r.gates.values():
            assert isinstance(g, GateResult)
            assert isinstance(g.status, str)
            assert isinstance(g.reason, str)


# ══════════════════════════════════════════════════════════════════════════════
# REAL-WORLD REGRESSION TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestRealWorldRegressions:
    def test_baba_jd_should_reject(self):
        """
        BABA/JD from dossier: trace=8.77, crit_95=13.43, crit_90=9.04
        trace < crit_90 → Gate 1 FAIL → REJECT
        The LLM previously labelled this ACTIVE — that was a hallucination.
        """
        r = evaluate_gates(
            trace_stat=8.77,
            crit_val_95=13.43,
            crit_val_90=9.04,
            half_life=3.8,
            snr=39.4,
            episodes=4,
            factor_loading=None,
            net_pnl_bps=780,
        )
        assert r.gates["gate_1"].status == "FAIL", (
            "BABA/JD trace=8.77 < crit_90=9.04: Gate 1 must FAIL"
        )
        assert r.verdict == "REJECT", (
            "BABA/JD must be REJECT (not ACTIVE as LLM hallucinated)"
        )

    def test_strong_cointegration_pair_is_active(self):
        """A textbook-quality pair should pass all gates and be ACTIVE."""
        r = evaluate_gates(
            trace_stat=25.5,
            crit_val_95=15.0,
            crit_val_90=10.0,
            half_life=22.0,
            snr=1.8,
            episodes=5,
            factor_loading=0.12,
            net_pnl_bps=187,
        )
        assert r.verdict == "ACTIVE"
        for gk in ["gate_1", "gate_2", "gate_3", "gate_4", "gate_6", "gate_7"]:
            assert r.gates[gk].status == "PASS", f"{gk} should be PASS"

    def test_slow_mean_reversion_is_reject(self):
        """Half-life 180 days is untradeable — must REJECT regardless of cointegration."""
        r = evaluate_gates(
            trace_stat=20.0,
            crit_val_95=15.0,
            crit_val_90=10.0,
            half_life=180.0,
            snr=1.5,
            episodes=3,
        )
        assert r.gates["gate_2"].status == "FAIL"
        assert r.verdict == "REJECT"


# ══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL FUNCTION TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestModuleLevelFunction:
    def test_evaluate_gates_returns_evaluator_output(self):
        r = evaluate_gates(
            trace_stat=20.0,
            crit_val_95=15.0,
            crit_val_90=10.0,
            half_life=25.0,
            snr=1.5,
            episodes=3,
        )
        assert isinstance(r, EvaluatorOutput)

    def test_evaluate_gates_is_deterministic(self):
        """Same inputs must always produce same output."""
        kwargs = all_pass_kwargs()
        r1 = evaluate_gates(**kwargs)
        r2 = evaluate_gates(**kwargs)
        assert r1.verdict == r2.verdict
        for gk in r1.gates:
            assert r1.gates[gk].status == r2.gates[gk].status
