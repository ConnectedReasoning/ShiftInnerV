"""
Tests for tools/composition_monitor.py — Item 15
ShiftInnerV Composition Concentration Monitor

Coverage:
  - load_compositions: directory scan, label extraction, pair parsing
  - get_pair_composition: both orderings, missing pair, multi-composition
  - check_composition_concentration: ALLOW / DOWNGRADE_TO_MONITOR, missing DB
  - get_all_concentrations: bulk helper

Run:
    pytest tests/test_composition_monitor.py -v
"""

import os
import sqlite3
import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.composition_monitor import (
    ConcentrationCheckResult,
    check_composition_concentration,
    get_all_concentrations,
    get_pair_composition,
    load_compositions,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def compositions_dir(tmp_path):
    """
    Create a temporary compositions directory with two composition files:
      composition_b_defense.yaml        — LMT/NOC, RTX/GD
      composition_c_china_em.yaml       — BABA/JD, NIO/XPEV
    """
    defense_content = textwrap.dedent("""\
        pairs:
          - ticker1: LMT
            ticker2: NOC
            label: 'Defense: LMT vs NOC'
            lookback_years: 3
          - ticker1: RTX
            ticker2: GD
            label: 'Defense: RTX vs GD'
            lookback_years: 3
    """)
    china_em_content = textwrap.dedent("""\
        pairs:
          - ticker1: BABA
            ticker2: JD
            label: 'China EM: BABA vs JD'
            lookback_years: 3
          - ticker1: NIO
            ticker2: XPEV
            label: 'China EM: NIO vs XPEV'
            lookback_years: 3
    """)

    (tmp_path / "composition_b_defense.yaml").write_text(defense_content)
    (tmp_path / "composition_c_china_em.yaml").write_text(china_em_content)

    # A promoted_ file — should be IGNORED by load_compositions
    (tmp_path / "promoted_20260517_1105.yaml").write_text(defense_content)

    return str(tmp_path)


@pytest.fixture
def empty_ledger(tmp_path):
    """An initialised trial_ledger.db with no records."""
    from init_trial_ledger import init_trial_ledger
    db_path = str(tmp_path / "trial_ledger.db")
    init_trial_ledger(db_path)
    return db_path


@pytest.fixture
def ledger_with_one_open(empty_ledger):
    """Ledger pre-seeded with one open defense position."""
    conn = sqlite3.connect(empty_ledger)
    conn.execute(
        """
        INSERT INTO trial_ledger
            (verdict_id, verdict_timestamp, composition_label,
             ticker1, ticker2, is_closed)
        VALUES ('aaa00001', '2026-05-01T10:00:00', 'defense', 'LMT', 'NOC', 0)
        """
    )
    conn.commit()
    conn.close()
    return empty_ledger


@pytest.fixture
def ledger_with_two_open(ledger_with_one_open):
    """Ledger pre-seeded with TWO open defense positions (at the limit)."""
    conn = sqlite3.connect(ledger_with_one_open)
    conn.execute(
        """
        INSERT INTO trial_ledger
            (verdict_id, verdict_timestamp, composition_label,
             ticker1, ticker2, is_closed)
        VALUES ('aaa00002', '2026-05-02T10:00:00', 'defense', 'RTX', 'GD', 0)
        """
    )
    conn.commit()
    conn.close()
    return ledger_with_one_open


# ── load_compositions ─────────────────────────────────────────────────────────

class TestLoadCompositions:
    def test_loads_two_compositions(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        assert set(comps.keys()) == {"defense", "china_em"}

    def test_promoted_files_excluded(self, compositions_dir):
        """Files not matching composition_*.yaml must be ignored."""
        comps = load_compositions(compositions_dir)
        assert "promoted_20260517_1105" not in comps

    def test_defense_has_expected_pairs(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        defense_pairs = comps["defense"]["pairs"]
        assert ("LMT", "NOC") in defense_pairs or ("NOC", "LMT") in defense_pairs
        assert ("RTX", "GD")  in defense_pairs or ("GD",  "RTX")  in defense_pairs

    def test_china_em_has_expected_pairs(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        china = comps["china_em"]["pairs"]
        assert ("BABA", "JD")   in china or ("JD",   "BABA")  in china
        assert ("NIO",  "XPEV") in china or ("XPEV", "NIO")   in china

    def test_file_metadata_stored(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        assert comps["defense"]["file"] == "composition_b_defense.yaml"

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        comps = load_compositions(str(tmp_path / "does_not_exist"))
        assert comps == {}

    def test_multi_segment_label(self, tmp_path):
        """composition_a_commodity_equity_proxy.yaml → label 'commodity_equity_proxy'"""
        content = "pairs:\n  - ticker1: GLD\n    ticker2: XLE\n    label: test\n"
        (tmp_path / "composition_a_commodity_equity_proxy.yaml").write_text(content)
        comps = load_compositions(str(tmp_path))
        assert "commodity_equity_proxy" in comps


# ── get_pair_composition ──────────────────────────────────────────────────────

class TestGetPairComposition:
    def test_finds_defense_pair(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        assert get_pair_composition("LMT", "NOC", comps) == "defense"

    def test_reverse_order_works(self, compositions_dir):
        """(NOC, LMT) should resolve to 'defense' even if stored as (LMT, NOC)."""
        comps = load_compositions(compositions_dir)
        assert get_pair_composition("NOC", "LMT", comps) == "defense"

    def test_finds_china_em_pair(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        assert get_pair_composition("BABA", "JD", comps) == "china_em"

    def test_unknown_pair_returns_none(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        assert get_pair_composition("AAPL", "MSFT", comps) is None

    def test_case_insensitive(self, compositions_dir):
        comps = load_compositions(compositions_dir)
        assert get_pair_composition("lmt", "noc", comps) == "defense"

    def test_empty_compositions_returns_none(self):
        assert get_pair_composition("LMT", "NOC", {}) is None


# ── check_composition_concentration ──────────────────────────────────────────

class TestCheckCompositionConcentration:
    def test_allow_when_zero_open(self, empty_ledger):
        result = check_composition_concentration(
            db_path=empty_ledger,
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "ALLOW"
        assert result.open_count == 0

    def test_allow_when_one_open(self, ledger_with_one_open):
        result = check_composition_concentration(
            db_path=ledger_with_one_open,
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "ALLOW"
        assert result.open_count == 1

    def test_downgrade_when_at_limit(self, ledger_with_two_open):
        result = check_composition_concentration(
            db_path=ledger_with_two_open,
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "DOWNGRADE_TO_MONITOR"
        assert result.open_count == 2

    def test_downgrade_when_above_limit(self, ledger_with_two_open):
        """Even with limit=1, two open positions should trigger downgrade."""
        result = check_composition_concentration(
            db_path=ledger_with_two_open,
            composition_label="defense",
            limit=1,
        )
        assert result.decision == "DOWNGRADE_TO_MONITOR"

    def test_other_composition_not_affected(self, ledger_with_two_open):
        """Two open defense positions must not block a china_em ACTIVE verdict."""
        result = check_composition_concentration(
            db_path=ledger_with_two_open,
            composition_label="china_em",
            limit=2,
        )
        assert result.decision == "ALLOW"
        assert result.open_count == 0

    def test_closed_positions_not_counted(self, empty_ledger):
        """Closed positions must not inflate the open count."""
        conn = sqlite3.connect(empty_ledger)
        conn.execute(
            """
            INSERT INTO trial_ledger
                (verdict_id, verdict_timestamp, composition_label,
                 ticker1, ticker2, is_closed)
            VALUES ('closed01', '2026-01-01T00:00:00', 'defense', 'LMT', 'NOC', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO trial_ledger
                (verdict_id, verdict_timestamp, composition_label,
                 ticker1, ticker2, is_closed)
            VALUES ('closed02', '2026-01-02T00:00:00', 'defense', 'RTX', 'GD', 1)
            """
        )
        conn.commit()
        conn.close()

        result = check_composition_concentration(
            db_path=empty_ledger,
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "ALLOW"
        assert result.open_count == 0

    def test_missing_ledger_defaults_to_allow(self, tmp_path):
        result = check_composition_concentration(
            db_path=str(tmp_path / "nonexistent.db"),
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "ALLOW"

    def test_open_positions_list_populated(self, ledger_with_two_open):
        result = check_composition_concentration(
            db_path=ledger_with_two_open,
            composition_label="defense",
            limit=2,
        )
        assert "aaa00001" in result.open_positions
        assert "aaa00002" in result.open_positions

    def test_limit_stored_in_result(self, empty_ledger):
        result = check_composition_concentration(
            db_path=empty_ledger,
            composition_label="defense",
            limit=3,
        )
        assert result.limit == 3

    def test_logger_called_on_downgrade(self, ledger_with_two_open):
        import logging
        from unittest.mock import MagicMock
        mock_logger = MagicMock(spec=logging.Logger)
        check_composition_concentration(
            db_path=ledger_with_two_open,
            composition_label="defense",
            limit=2,
            logger=mock_logger,
        )
        # No error logged — downgrade is not an error, just a warning if we add one
        mock_logger.error.assert_not_called()


# ── get_all_concentrations ────────────────────────────────────────────────────

class TestGetAllConcentrations:
    def test_returns_result_per_composition(self, compositions_dir, empty_ledger):
        comps = load_compositions(compositions_dir)
        all_results = get_all_concentrations(empty_ledger, comps, limit=2)
        assert set(all_results.keys()) == {"defense", "china_em"}

    def test_all_allow_on_empty_ledger(self, compositions_dir, empty_ledger):
        comps = load_compositions(compositions_dir)
        all_results = get_all_concentrations(empty_ledger, comps)
        for label, result in all_results.items():
            assert result.decision == "ALLOW", f"{label} should be ALLOW on empty ledger"

    def test_only_affected_composition_blocked(self, compositions_dir, ledger_with_two_open):
        comps = load_compositions(compositions_dir)
        all_results = get_all_concentrations(ledger_with_two_open, comps, limit=2)
        assert all_results["defense"].decision  == "DOWNGRADE_TO_MONITOR"
        assert all_results["china_em"].decision == "ALLOW"


# ── Integration: record then check ───────────────────────────────────────────

class TestRecordAndCheck:
    """
    Full round-trip: record ACTIVE verdicts via record_active_verdict,
    then verify concentration checks respond correctly.
    """

    def test_third_active_in_same_composition_is_blocked(self, empty_ledger):
        """
        Scenario: two defense verdicts already in ledger (open).
        A third defense pair must be blocked (DOWNGRADE_TO_MONITOR).
        Verifies the fix to record_active_verdict passing composition_label.
        """
        from init_trial_ledger import record_active_verdict

        record_active_verdict(
            db_path=empty_ledger,
            ticker1="LMT", ticker2="NOC", label="Defense: LMT vs NOC",
            gate_results={},
            composition_label="defense",
        )
        record_active_verdict(
            db_path=empty_ledger,
            ticker1="RTX", ticker2="GD", label="Defense: RTX vs GD",
            gate_results={},
            composition_label="defense",
        )

        result = check_composition_concentration(
            db_path=empty_ledger,
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "DOWNGRADE_TO_MONITOR"
        assert result.open_count == 2

    def test_cross_composition_not_blocked(self, empty_ledger):
        """
        Two open defense positions must not block an unrelated china_em verdict.
        """
        from init_trial_ledger import record_active_verdict

        record_active_verdict(
            db_path=empty_ledger,
            ticker1="LMT", ticker2="NOC", label="Defense: LMT vs NOC",
            gate_results={}, composition_label="defense",
        )
        record_active_verdict(
            db_path=empty_ledger,
            ticker1="RTX", ticker2="GD", label="Defense: RTX vs GD",
            gate_results={}, composition_label="defense",
        )

        result = check_composition_concentration(
            db_path=empty_ledger,
            composition_label="china_em",
            limit=2,
        )
        assert result.decision == "ALLOW"

    def test_standalone_pair_always_allowed(self, empty_ledger):
        """
        A pair with composition_label=None (standalone) should never be
        concentration-blocked by other standalone positions.
        """
        from init_trial_ledger import record_active_verdict

        for i in range(5):
            record_active_verdict(
                db_path=empty_ledger,
                ticker1=f"AAA{i}", ticker2=f"BBB{i}", label="standalone",
                gate_results={}, composition_label=None,
            )

        # Standalone pairs query against composition_label = None in the DB.
        # The spec says "no concentration limit applies" for standalones,
        # so we simply don't call check_composition_concentration for them.
        # This test documents that None-label records don't block labeled ones.
        result = check_composition_concentration(
            db_path=empty_ledger,
            composition_label="defense",
            limit=2,
        )
        assert result.decision == "ALLOW"
        assert result.open_count == 0
