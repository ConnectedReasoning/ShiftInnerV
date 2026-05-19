"""
ShiftInnerV — Trial Ledger Tests
Item 14 of the Council Roadmap.

Tests for trial_ledger.py — schema initialisation, verdict recording,
position closing with P&L computation, bulk close, query helpers,
position revalidation history, and ledger summary.

No network calls, no LLM required. Uses in-memory / tmp SQLite.

Usage:
    pytest tests/test_trial_ledger.py -v
    pytest tests/test_trial_ledger.py -v -k "close"
    pytest tests/test_trial_ledger.py -v --tb=short
"""

import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from trial_ledger import (
    close_trial,
    close_trials_from_audit,
    get_ledger_summary,
    init_trial_ledger,
    load_closed_trials,
    load_open_trials,
    load_revalidation_history,
    parse_gate_results,
    parse_statistical_snapshot,
    record_active_verdict,
    record_position_revalidation,
)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

MINIMAL_GATES = {
    "gate_1": "PASS", "gate_2": "PASS", "gate_3": "PASS",
    "gate_4": "PASS", "gate_6": "PASS", "gate_7": "PASS",
}


def insert_verdict(db_path: str, ticker1="LMT", ticker2="NOC", **kwargs) -> str:
    return record_active_verdict(
        db_path=db_path,
        ticker1=ticker1,
        ticker2=ticker2,
        label=f"{ticker1} vs {ticker2}",
        gate_results=MINIMAL_GATES,
        snr=kwargs.get("snr", 1.8),
        half_life=kwargs.get("half_life", 28.0),
        entry_z=kwargs.get("entry_z", 2.1),
        composition_label=kwargs.get("composition_label", "defense"),
        spread_mean=kwargs.get("spread_mean", 0.01),
        spread_std=kwargs.get("spread_std", 0.05),
        hedge_ratio=kwargs.get("hedge_ratio", 1.2),
    )


def close_verdict(db_path: str, verdict_id: str,
                  entry_p1=100.0, entry_p2=80.0,
                  exit_p1=102.0, exit_p2=79.0) -> bool:
    return close_trial(
        db_path=db_path,
        verdict_id=verdict_id,
        entry_timestamp="2025-01-10T09:30:00",
        entry_price_1=entry_p1,
        entry_price_2=entry_p2,
        exit_timestamp="2025-02-07T09:30:00",
        exit_price_1=exit_p1,
        exit_price_2=exit_p2,
        exit_z=0.1,
        exit_reason="convergence",
        hedge_ratio=1.2,
        estimated_costs_bps=15.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA INITIALISATION
# ══════════════════════════════════════════════════════════════════════════════

class TestInitTrialLedger:

    def test_creates_db_file(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        init_trial_ledger(db)
        assert os.path.exists(db)

    def test_creates_trial_ledger_table(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        init_trial_ledger(db)
        conn = sqlite3.connect(db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "trial_ledger" in tables

    def test_idempotent_second_call(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        init_trial_ledger(db)
        init_trial_ledger(db)  # should not raise
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM trial_ledger").fetchone()[0]
        conn.close()
        assert count == 0

    def test_creates_parent_dirs(self, tmp_path):
        db = str(tmp_path / "nested" / "deep" / "ledger.db")
        init_trial_ledger(db)
        assert os.path.exists(db)

    def test_regime_columns_present(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        init_trial_ledger(db)
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trial_ledger)").fetchall()]
        conn.close()
        assert "regime_state" in cols
        assert "position_size_multiplier" in cols


# ══════════════════════════════════════════════════════════════════════════════
# RECORD ACTIVE VERDICT
# ══════════════════════════════════════════════════════════════════════════════

class TestRecordActiveVerdict:

    def test_returns_8char_verdict_id(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        assert vid is not None
        assert len(vid) == 8

    def test_inserts_one_row(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        insert_verdict(db)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM trial_ledger").fetchone()[0]
        conn.close()
        assert count == 1

    def test_is_closed_zero_on_insert(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT is_closed FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == 0

    def test_tickers_stored_correctly(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db, ticker1="BABA", ticker2="JD")
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT ticker1, ticker2 FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == "BABA"
        assert row[1] == "JD"

    def test_gate_results_stored(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        gates = dict(MINIMAL_GATES)
        gates["gate_1"] = "FAIL"
        vid = record_active_verdict(
            db_path=db, ticker1="A", ticker2="B",
            label="A vs B", gate_results=gates,
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT gate_1_result FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == "FAIL"

    def test_snr_and_half_life_stored(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db, snr=2.3, half_life=42.0)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT snr, half_life FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == pytest.approx(2.3)
        assert row[1] == pytest.approx(42.0)

    def test_composition_label_stored(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db, composition_label="china_em")
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT composition_label FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == "china_em"

    def test_regime_fields_default_normal(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT regime_state, position_size_multiplier FROM trial_ledger WHERE verdict_id=?",
            (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == "NORMAL"
        assert row[1] == pytest.approx(1.0)

    def test_regime_fields_custom(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = record_active_verdict(
            db_path=db, ticker1="A", ticker2="B",
            label="A vs B", gate_results=MINIMAL_GATES,
            regime_state="HIGH_STRESS", position_size_multiplier=0.25,
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT regime_state, position_size_multiplier FROM trial_ledger WHERE verdict_id=?",
            (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == "HIGH_STRESS"
        assert row[1] == pytest.approx(0.25)

    def test_multiple_verdicts_unique_ids(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        ids = [insert_verdict(db) for _ in range(5)]
        assert len(set(ids)) == 5


# ══════════════════════════════════════════════════════════════════════════════
# CLOSE TRIAL & P&L COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

class TestCloseTrial:

    def test_returns_true_on_success(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        result = close_verdict(db, vid)
        assert result is True

    def test_is_closed_set_to_one(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_verdict(db, vid)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT is_closed FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == 1

    def test_hold_days_computed(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_verdict(db, vid)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT hold_days FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == 28  # 2025-01-10 → 2025-02-07

    def test_profitable_trade_marked_is_profitable(self, tmp_path):
        """Entry 100/80 → Exit 110/75: long leg gains, short leg gains."""
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_trial(
            db_path=db, verdict_id=vid,
            entry_timestamp="2025-01-10T09:30:00",
            entry_price_1=100.0, entry_price_2=80.0,
            exit_timestamp="2025-02-07T09:30:00",
            exit_price_1=110.0, exit_price_2=75.0,
            exit_z=0.1, exit_reason="convergence",
            hedge_ratio=1.0, estimated_costs_bps=5.0,
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT is_profitable, net_pnl_bps FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] > 0

    def test_losing_trade_marked_not_profitable(self, tmp_path):
        """Entry 100/80 → Exit 95/83: both legs move against the spread."""
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_trial(
            db_path=db, verdict_id=vid,
            entry_timestamp="2025-01-10T09:30:00",
            entry_price_1=100.0, entry_price_2=80.0,
            exit_timestamp="2025-02-07T09:30:00",
            exit_price_1=95.0, exit_price_2=83.0,
            exit_z=3.5, exit_reason="stop_loss",
            hedge_ratio=1.0, estimated_costs_bps=5.0,
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT is_profitable FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == 0

    def test_net_pnl_equals_gross_minus_costs(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_verdict(db, vid)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT gross_pnl_bps, estimated_costs_bps, net_pnl_bps FROM trial_ledger WHERE verdict_id=?",
            (vid,)
        ).fetchone()
        conn.close()
        gross, costs, net = row
        assert net == pytest.approx(gross - costs, abs=0.01)

    def test_exit_reason_stored(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_trial(
            db_path=db, verdict_id=vid,
            entry_timestamp="2025-01-10T09:30:00",
            entry_price_1=100.0, entry_price_2=80.0,
            exit_timestamp="2025-02-07T09:30:00",
            exit_price_1=102.0, exit_price_2=79.0,
            exit_z=0.1, exit_reason="stop_loss",
            hedge_ratio=1.0, estimated_costs_bps=10.0,
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT exit_reason FROM trial_ledger WHERE verdict_id=?", (vid,)
        ).fetchone()
        conn.close()
        assert row[0] == "stop_loss"

    def test_returns_false_for_unknown_verdict_id(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        init_trial_ledger(db)
        result = close_verdict(db, "badid00")
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# BULK CLOSE FROM AUDIT
# ══════════════════════════════════════════════════════════════════════════════

class TestCloseTrialsFromAudit:

    def _make_audit_result(self, vid: str) -> dict:
        return {
            "verdict_id": vid,
            "entry_date": "2025-01-10T09:30:00",
            "entry_price_1": 100.0,
            "entry_price_2": 80.0,
            "exit_date": "2025-02-07T09:30:00",
            "exit_price_1": 102.0,
            "exit_price_2": 79.0,
            "exit_z": 0.1,
            "exit_reason": "convergence",
            "hedge_ratio": 1.0,
            "total_cost_bps": 12.0,
        }

    def test_closes_multiple_trials(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vids = [insert_verdict(db) for _ in range(3)]
        audit = [self._make_audit_result(v) for v in vids]
        ok, failed = close_trials_from_audit(db, audit)
        assert ok == 3
        assert failed == 0

    def test_failed_count_for_bad_ids(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        audit = [self._make_audit_result("badid00")]
        ok, failed = close_trials_from_audit(db, audit)
        assert failed == 1

    def test_empty_list_returns_zero_zero(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        init_trial_ledger(db)
        ok, failed = close_trials_from_audit(db, [])
        assert ok == 0
        assert failed == 0


# ══════════════════════════════════════════════════════════════════════════════
# QUERY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadClosedTrials:

    def test_returns_none_when_no_db(self, tmp_path):
        result = load_closed_trials(str(tmp_path / "missing.db"))
        assert result is None

    def test_returns_empty_df_when_no_closed(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        insert_verdict(db)  # open only
        df = load_closed_trials(db)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_returns_closed_rows(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_verdict(db, vid)
        df = load_closed_trials(db)
        assert len(df) == 1
        assert df.iloc[0]["verdict_id"] == vid

    def test_does_not_include_open(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid_open = insert_verdict(db, ticker1="AA", ticker2="BB")
        vid_closed = insert_verdict(db, ticker1="CC", ticker2="DD")
        close_verdict(db, vid_closed)
        df = load_closed_trials(db)
        assert len(df) == 1
        assert vid_open not in df["verdict_id"].values


class TestLoadOpenTrials:

    def test_returns_none_when_no_db(self, tmp_path):
        result = load_open_trials(str(tmp_path / "missing.db"))
        assert result is None

    def test_returns_open_rows(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        insert_verdict(db)
        df = load_open_trials(db)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1

    def test_does_not_include_closed(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_verdict(db, vid)
        df = load_open_trials(db)
        assert len(df) == 0


# ══════════════════════════════════════════════════════════════════════════════
# POSITION REVALIDATION HISTORY
# ══════════════════════════════════════════════════════════════════════════════

class TestRecordPositionRevalidation:

    def test_records_and_loads_back(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        ok = record_position_revalidation(
            db_path=db, verdict_id=vid,
            snr_entry=1.5, snr_current=1.2,
            snr_change_bps=-3000.0,
            mean_drift_sigma=0.5, drift_detected=False,
            decision="MONITOR",
            rationale="SNR in caution range",
            days_held=14,
        )
        assert ok is True
        df = load_revalidation_history(db)
        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["verdict_id"] == vid

    def test_decision_stored(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        record_position_revalidation(
            db_path=db, verdict_id=vid,
            snr_entry=1.5, snr_current=0.6,
            snr_change_bps=-9000.0,
            mean_drift_sigma=2.5, drift_detected=True,
            decision="AUTO_CLOSE",
            rationale="SNR < 0.7 and drift detected",
            days_held=30,
        )
        df = load_revalidation_history(db, decision_filter="AUTO_CLOSE")
        assert len(df) == 1
        assert df.iloc[0]["decision"] == "AUTO_CLOSE"

    def test_filter_by_verdict_id(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        v1 = insert_verdict(db, ticker1="AA", ticker2="BB")
        v2 = insert_verdict(db, ticker1="CC", ticker2="DD")
        for vid in (v1, v2):
            record_position_revalidation(
                db_path=db, verdict_id=vid,
                snr_entry=1.5, snr_current=1.3,
                snr_change_bps=-2000.0,
                mean_drift_sigma=0.2, drift_detected=False,
                decision="HOLD", rationale="SNR ok", days_held=5,
            )
        df = load_revalidation_history(db, verdict_id=v1)
        assert len(df) == 1
        assert df.iloc[0]["verdict_id"] == v1

    def test_none_when_no_db(self, tmp_path):
        result = load_revalidation_history(str(tmp_path / "missing.db"))
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# LEDGER SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

class TestGetLedgerSummary:

    def test_returns_not_exists_when_missing(self, tmp_path):
        result = get_ledger_summary(str(tmp_path / "missing.db"))
        assert result["exists"] is False

    def test_counts_total_open_closed(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        v1 = insert_verdict(db, ticker1="AA", ticker2="BB")
        v2 = insert_verdict(db, ticker1="CC", ticker2="DD")
        close_verdict(db, v1)
        summary = get_ledger_summary(db)
        assert summary["total"] == 2
        assert summary["closed"] == 1
        assert summary["open"] == 1

    def test_profitable_count(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        # Profitable close
        close_trial(
            db_path=db, verdict_id=vid,
            entry_timestamp="2025-01-10T09:30:00",
            entry_price_1=100.0, entry_price_2=80.0,
            exit_timestamp="2025-02-07T09:30:00",
            exit_price_1=110.0, exit_price_2=75.0,
            exit_z=0.1, exit_reason="convergence",
            hedge_ratio=1.0, estimated_costs_bps=5.0,
        )
        summary = get_ledger_summary(db)
        assert summary["profitable"] == 1

    def test_avg_net_pnl_bps_computed(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        vid = insert_verdict(db)
        close_verdict(db, vid)
        summary = get_ledger_summary(db)
        assert summary["avg_net_pnl_bps"] is not None

    def test_timestamps_present(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        insert_verdict(db)
        summary = get_ledger_summary(db)
        assert summary["first_verdict"] is not None
        assert summary["last_verdict"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# GATE & STATISTICAL SNAPSHOT PARSERS
# ══════════════════════════════════════════════════════════════════════════════

class TestParseGateResults:

    def test_parses_all_pass(self):
        text = """
        Gate 1 Cointegration: PASS — trace 20.1 >= 15.0
        Gate 2 Half-life: PASS — 28 days
        Gate 3 SNR: PASS — 1.8
        Gate 4 Episodes: PASS — 3
        Gate 6 Factor: PASS — loading 0.1
        Gate 7 Net P&L: PASS — 45 bps
        """
        results = parse_gate_results(text)
        assert results["gate_1"] == "PASS"
        assert results["gate_2"] == "PASS"
        assert results["gate_3"] == "PASS"
        assert results["gate_4"] == "PASS"
        assert results["gate_6"] == "PASS"
        assert results["gate_7"] == "PASS"

    def test_parses_fail_token(self):
        text = "Gate 1 Cointegration: FAIL — trace too low"
        results = parse_gate_results(text)
        assert results["gate_1"] == "FAIL"

    def test_missing_gate_returns_empty_string(self):
        text = "Gate 1 Cointegration: PASS"
        results = parse_gate_results(text)
        assert results["gate_2"] == ""

    def test_normalises_reject_token(self):
        text = "Gate 2 Half-life: REJECT — too slow"
        results = parse_gate_results(text)
        assert results["gate_2"] == "REJECT"


class TestParseStatisticalSnapshot:

    def test_parses_half_life(self):
        text = "half-life: 28.5 days"
        snap = parse_statistical_snapshot(text)
        assert snap["half_life"] == pytest.approx(28.5)

    def test_parses_snr(self):
        text = "SNR: 1.87"
        snap = parse_statistical_snapshot(text)
        assert snap["snr"] == pytest.approx(1.87)

    def test_parses_episodes(self):
        text = "3 distinct episodes detected"
        snap = parse_statistical_snapshot(text)
        assert snap["episodes"] == 3

    def test_parses_trace_stat(self):
        text = "trace stat: 21.3"
        snap = parse_statistical_snapshot(text)
        assert snap["trace_stat"] == pytest.approx(21.3)

    def test_parses_hedge_ratio(self):
        text = "hedge ratio: 1.23"
        snap = parse_statistical_snapshot(text)
        assert snap["hedge_ratio"] == pytest.approx(1.23)

    def test_missing_values_are_none(self):
        snap = parse_statistical_snapshot("no useful numbers here")
        assert snap["half_life"] is None
        assert snap["snr"] is None
        assert snap["episodes"] is None

    def test_spread_mean_std_always_none(self):
        """spread_mean and spread_std are not in verdict text."""
        snap = parse_statistical_snapshot("SNR: 1.5 half-life: 30d")
        assert snap["spread_mean"] is None
        assert snap["spread_std"] is None
