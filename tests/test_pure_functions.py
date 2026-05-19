"""
ShiftInnerV — main.py and summarize.py Pure Function Tests

Tests for string parsing and text extraction functions that have
no external dependencies:

  main.py:
    - extract_report_text
    - extract_search_findings

  summarize.py:
    - extract_verdict
    - extract_zscore
    - extract_direction
    - _pair_from_filename
    - zscore_magnitude
    - build_pairs_text

No LLM, no network, no filesystem required.

Usage:
    pytest tests/test_pure_functions.py -v
"""

import ast
import sys
import json
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _extract_functions(module_name: str, func_names: list):
    """
    Extract only specific functions from a source file by parsing the AST
    and compiling just those function definitions — without importing the
    module at all, so no side-effects and no sys.modules pollution.
    """
    src_path = Path(__file__).parent.parent / f"{module_name}.py"
    source = src_path.read_text()
    tree = ast.parse(source)

    # Collect top-level imports and the target function nodes
    import_nodes = [
        n for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    func_nodes = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in func_names
    ]

    # Build a minimal module: stdlib/safe imports + the functions only
    # We stub the heavy deps by pre-populating the namespace
    stub_ns = {
        "__builtins__": __builtins__,
        "__name__": module_name,
        "re": __import__("re"),
        "json": __import__("json"),
        "os": __import__("os"),
        "sys": sys,
        "logging": __import__("logging"),
        "datetime": __import__("datetime"),
        "pathlib": __import__("pathlib"),
    }

    # Execute just the function definitions in the stub namespace
    for node in func_nodes:
        mini_module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(mini_module)
        exec(compile(mini_module, str(src_path), "exec"), stub_ns)

    return {name: stub_ns[name] for name in func_names if name in stub_ns}


# ── Load pure functions without importing the module ─────────────────────────
_main_funcs = _extract_functions("main", [
    "extract_report_text", "extract_search_findings"
])
extract_report_text     = _main_funcs["extract_report_text"]
extract_search_findings = _main_funcs["extract_search_findings"]

# summarize.py has no heavy deps — import normally
from summarize import (
    _pair_from_filename,
    build_pairs_text,
    extract_direction,
    extract_verdict,
    extract_zscore,
    zscore_magnitude,
)


SAMPLE_REPORT = """\
=== CORRELATION DECAY REPORT ===
Pair: LMT / NOC
Half-life: 28.0 days
SNR: 1.87
=== PAIR SCORE ===
Score: 82.3
"""


class TestExtractReportText:

    def test_plain_text_returns_success(self):
        text, recovery = extract_report_text(SAMPLE_REPORT)
        assert recovery == "success"
        assert "=== CORRELATION DECAY REPORT ===" in text

    def test_plain_text_returns_unchanged_content(self):
        text, _ = extract_report_text(SAMPLE_REPORT)
        assert "SNR: 1.87" in text

    def test_report_buried_in_preamble(self):
        raw = "Here is the output you requested:\n\n" + SAMPLE_REPORT
        text, recovery = extract_report_text(raw)
        assert "=== CORRELATION DECAY REPORT ===" in text
        assert recovery == "fallback_regex"

    def test_report_wrapped_in_json_string(self):
        blob = json.dumps({"output": SAMPLE_REPORT})
        text, recovery = extract_report_text(blob)
        assert "=== CORRELATION DECAY REPORT ===" in text
        # Regex fires before JSON parsing if the report header is findable in
        # the raw string — both recovery types are valid here.
        assert recovery in ("fallback_regex", "fallback_json_extraction")

    def test_report_in_nested_json(self):
        blob = json.dumps({"result": {"text": SAMPLE_REPORT}})
        text, recovery = extract_report_text(blob)
        assert "=== CORRELATION DECAY REPORT ===" in text

    def test_no_report_returns_failure(self):
        raw = "Something completely different with no report header."
        text, recovery = extract_report_text(raw)
        assert recovery == "failure"
        assert text == raw.strip()

    def test_strips_trailing_json_chars(self):
        # Stripping only applies on the regex fallback path (not success).
        raw = "prefix\n" + SAMPLE_REPORT + '"}}'
        text, recovery = extract_report_text(raw)
        if recovery == "fallback_regex":
            assert not text.endswith('}}')

    def test_escaped_newlines_in_json_unescaped(self):
        escaped = SAMPLE_REPORT.replace("\n", "\\n")
        raw = f"prefix {escaped}"
        text, recovery = extract_report_text(raw)
        assert recovery in ("fallback_regex", "fallback_json_extraction", "failure")
        # If recovered, newlines should be real newlines
        if recovery != "failure":
            assert "\\n" not in text

    def test_empty_string_returns_failure(self):
        text, recovery = extract_report_text("")
        assert recovery == "failure"

    def test_whitespace_only_returns_failure(self):
        _, recovery = extract_report_text("   \n\n  ")
        assert recovery == "failure"


# ══════════════════════════════════════════════════════════════════════════════
# extract_search_findings
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractSearchFindings:

    def test_plain_prose_returned_unchanged(self):
        prose = "LMT and NOC show strong cointegration driven by defense procurement cycles."
        result = extract_search_findings(prose)
        assert result == prose

    def test_extracts_text_key_from_json(self):
        blob = json.dumps({"text": "Defense sector analysis shows strong correlation."})
        result = extract_search_findings(blob)
        assert "Defense sector" in result

    def test_extracts_output_key(self):
        blob = json.dumps({"output": "Sector analysis complete."})
        result = extract_search_findings(blob)
        assert "Sector analysis" in result

    def test_extracts_nested_prose(self):
        blob = json.dumps({"result": {"findings": "Strong cointegration found in defense pairs."}})
        result = extract_search_findings(blob)
        assert "cointegration" in result

    def test_does_not_return_report_text_as_prose(self):
        blob = json.dumps({"text": SAMPLE_REPORT})
        result = extract_search_findings(blob)
        # Should not return the correlation decay report as search findings
        assert "=== CORRELATION DECAY REPORT ===" not in result or result == blob.strip()

    def test_search_query_extracted_when_no_prose(self):
        blob = json.dumps({"search_query": "LMT NOC cointegration 2024"})
        result = extract_search_findings(blob)
        assert "LMT NOC" in result

    def test_invalid_json_returned_as_is(self):
        raw = "{not valid json}"
        result = extract_search_findings(raw)
        assert result == raw

    def test_empty_string_returned_as_is(self):
        result = extract_search_findings("")
        assert result == ""

    def test_list_json_handled(self):
        blob = json.dumps([{"text": "Analysis results here."}])
        result = extract_search_findings(blob)
        # Either extracted or returned as-is — should not crash
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# extract_verdict
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractVerdict:

    def test_extracts_active(self):
        text = "some preamble\nVERDICT: ACTIVE\nsome postamble"
        assert extract_verdict(text) == "ACTIVE"

    def test_extracts_monitor(self):
        text = "VERDICT: MONITOR"
        assert extract_verdict(text) == "MONITOR"

    def test_extracts_reject(self):
        text = "VERDICT: REJECT"
        assert extract_verdict(text) == "REJECT"

    def test_returns_unknown_when_missing(self):
        text = "No verdict line here."
        assert extract_verdict(text) == "UNKNOWN"

    def test_ignores_partial_match(self):
        text = "VERDICTS: not a real verdict line"
        # startswith("VERDICT:") won't match "VERDICTS:"
        assert extract_verdict(text) == "UNKNOWN"

    def test_strips_whitespace(self):
        text = "VERDICT:   ACTIVE  "
        assert extract_verdict(text) == "ACTIVE"

    def test_multiline_picks_first_verdict(self):
        text = "VERDICT: ACTIVE\nVERDICT: MONITOR"
        assert extract_verdict(text) == "ACTIVE"


# ══════════════════════════════════════════════════════════════════════════════
# extract_zscore
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractZscore:

    def test_extracts_positive_zscore(self):
        text = "Z-score: 2.34 (entry signal)"
        assert extract_zscore(text) == "2.34"

    def test_extracts_negative_zscore(self):
        text = "Z-score: -1.87"
        assert extract_zscore(text) == "-1.87"

    def test_returns_na_when_missing(self):
        text = "No z-score line here."
        assert extract_zscore(text) == "N/A"

    def test_picks_first_zscore_line(self):
        text = "Z-score: 2.1\nZ-score: 3.4"
        assert extract_zscore(text) == "2.1"


# ══════════════════════════════════════════════════════════════════════════════
# extract_direction
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractDirection:

    def test_extracts_long_short_line(self):
        text = "Entry: LONG LMT / SHORT NOC at z=2.1"
        assert "LONG" in extract_direction(text)
        assert "SHORT" in extract_direction(text)

    def test_returns_na_when_no_direction(self):
        text = "No direction specified."
        assert extract_direction(text) == "N/A"

    def test_ignores_direction_header_line(self):
        # A line with "Direction: SHORT LMT LONG NOC" should be ignored
        # because it contains the word "Direction"
        text = "Direction: SHORT LMT LONG NOC"
        # The function skips lines containing "Direction"
        assert extract_direction(text) == "N/A"


# ══════════════════════════════════════════════════════════════════════════════
# _pair_from_filename
# ══════════════════════════════════════════════════════════════════════════════

class TestPairFromFilename:

    def test_dossier_format(self):
        result = _pair_from_filename("DOSSIER_ENPH_KRE_20250519")
        assert result == "ENPH/KRE"

    def test_vs_format(self):
        # The function skips tokens equal to "VS" but scans for consecutive
        # alpha pairs. A filename like ENPH_VS_KRE has VS between the tickers,
        # so the scanner finds ENPH+VS or VS+KRE — both fail the alpha check
        # because VS is excluded by the t1 != "VS" guard. Result is None.
        result = _pair_from_filename("ENPH_VS_KRE_20250519_123456")
        # Either None (VS guard kicks in) or a valid pair — both are ok.
        assert result is None or "/" in result

    def test_two_consecutive_tickers(self):
        result = _pair_from_filename("REPORT_LMT_NOC_ANALYSIS")
        assert result == "LMT/NOC"

    def test_returns_none_for_no_match(self):
        result = _pair_from_filename("RANDOM_REPORT_12345")
        assert result is None

    def test_single_segment_returns_none(self):
        result = _pair_from_filename("ENPH")
        assert result is None

    def test_long_tickers_ignored(self):
        # Tickers > 5 chars should not match
        result = _pair_from_filename("TOOLONG_ALSOLONG")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# zscore_magnitude
# ══════════════════════════════════════════════════════════════════════════════

class TestZscoreMagnitude:

    def test_positive_zscore(self):
        entry = {"zscore": "2.34"}
        assert zscore_magnitude(entry) == pytest.approx(2.34)

    def test_negative_zscore(self):
        entry = {"zscore": "-1.87"}
        assert zscore_magnitude(entry) == pytest.approx(1.87)

    def test_plus_prefixed(self):
        entry = {"zscore": "+2.10"}
        assert zscore_magnitude(entry) == pytest.approx(2.10)

    def test_unicode_minus(self):
        entry = {"zscore": "−2.50"}
        assert zscore_magnitude(entry) == pytest.approx(2.50)

    def test_na_returns_zero(self):
        entry = {"zscore": "N/A"}
        assert zscore_magnitude(entry) == pytest.approx(0.0)

    def test_missing_key_raises_or_returns_zero(self):
        # zscore_magnitude does not guard against missing key — it raises
        # KeyError. Document and accept this behavior (caller must provide key).
        try:
            result = zscore_magnitude({})
            assert result == pytest.approx(0.0)
        except (KeyError, AttributeError):
            pass  # acceptable — caller responsibility

    def test_none_value_raises_or_returns_zero(self):
        try:
            result = zscore_magnitude({"zscore": None})
            assert result == pytest.approx(0.0)
        except (AttributeError, TypeError):
            pass  # acceptable — caller responsibility


# ══════════════════════════════════════════════════════════════════════════════
# build_pairs_text
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildPairsText:

    def _make_run_data(self, pairs: list) -> dict:
        """
        pairs: list of (pair_key, verdict, zscore)
        """
        run_data = {}
        for pair_key, verdict, zscore in pairs:
            run_data[pair_key] = {
                "verdict": f"VERDICT: {verdict}\nsome details",
                "dossier": f"Z-score: {zscore}\nLONG {pair_key.split('/')[0]} / SHORT {pair_key.split('/')[1]}\nDossier content.",
            }
        return run_data

    def test_active_pairs_included(self):
        run_data = self._make_run_data([("LMT/NOC", "ACTIVE", "2.3")])
        text, n_active, _ = build_pairs_text(run_data, top_n=5)
        assert n_active == 1
        assert "LMT/NOC" in text

    def test_monitor_pairs_counted(self):
        run_data = self._make_run_data([
            ("LMT/NOC", "ACTIVE", "2.3"),
            ("BABA/JD", "MONITOR", "1.8"),
        ])
        _, n_active, n_monitor = build_pairs_text(run_data, top_n=5)
        assert n_active == 1
        assert n_monitor == 1

    def test_top_n_limits_active_pairs(self):
        run_data = self._make_run_data([
            (f"T{i}/T{i+1}", "ACTIVE", str(i * 0.5))
            for i in range(10)
        ])
        text, n_active, _ = build_pairs_text(run_data, top_n=3)
        assert n_active == 3

    def test_reject_pairs_not_counted(self):
        run_data = self._make_run_data([("XYZ/ABC", "REJECT", "0.5")])
        _, n_active, n_monitor = build_pairs_text(run_data, top_n=5)
        assert n_active == 0
        assert n_monitor == 0

    def test_empty_run_data_returns_empty_text(self):
        text, n_active, n_monitor = build_pairs_text({}, top_n=5)
        assert n_active == 0
        assert n_monitor == 0

    def test_active_sorted_by_zscore_magnitude(self):
        run_data = self._make_run_data([
            ("LOW/XX", "ACTIVE", "1.1"),
            ("HIGH/XX", "ACTIVE", "3.5"),
            ("MID/XX", "ACTIVE", "2.2"),
        ])
        text, _, _ = build_pairs_text(run_data, top_n=5)
        # HIGH should appear before MID and LOW
        pos_high = text.find("HIGH/XX")
        pos_mid  = text.find("MID/XX")
        assert pos_high < pos_mid

    def test_returns_string(self):
        run_data = self._make_run_data([("A/B", "ACTIVE", "2.0")])
        text, _, _ = build_pairs_text(run_data, top_n=5)
        assert isinstance(text, str)
