import os
import re
import json
import yaml
import argparse
from dotenv import load_dotenv
from datetime import date
from crewai import Crew, Process

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

from shiftinnerv.services.data_manager import ensure_data, tickers_from_pairs, check_data_staleness
from shiftinnerv.pipelines.dossier import render_dossier
# Item 21: News & Macro Context
from shiftinnerv.news.news_brief_builder import build_news_context_with_flags
from shiftinnerv.pipelines.agents import make_analyst
from shiftinnerv.pipelines.tasks import build_analyst_task
from shiftinnerv.sensors.correlation import CorrelationDecayTool
from promote import run as promote_run
from shiftinnerv.services.trial_ledger import (
    record_active_verdict,
    parse_gate_results,
    parse_statistical_snapshot,
    init_trial_ledger,
)
from shiftinnerv.domain.gate_evaluator import evaluate_gates
from shiftinnerv.sensors.composition_monitor import (
    load_compositions,
    get_pair_composition,
    check_composition_concentration,
)

data_dir   = os.path.expanduser(os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data"))
report_dir = os.path.expanduser(os.getenv("REPORT_DIR", "~/Projects/ShiftInnerV_Data/reports"))
os.makedirs(report_dir, exist_ok=True)

# ── Item 5: Staleness threshold ───────────────────────────────────────────────
# 26 h = 1 trading day (close → next open) + overnight buffer.
# Override via env for testing: export PRICE_DATA_STALENESS_HOURS=999
PRICE_DATA_STALENESS_THRESHOLD_HOURS = int(
    os.getenv("PRICE_DATA_STALENESS_HOURS", "26")
)

# ── Item 15: Composition concentration limits (gate override) ─────────────────
# Default: 2 simultaneous open positions per composition.
# Override per-composition below for finer control.
COMPOSITION_CONCENTRATION_LIMIT = 2

COMPOSITION_LIMITS: dict = {
    # More conservative for commodity pairs — higher cross-asset correlation
    "commodity_equity_proxy": 1,
    # Standard limit for all others (overrides default when key present)
}


# ── Parse arguments ───────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="ShiftInnerV Shadow Audit")
parser.add_argument(
    "--pairs",
    type=str,
    default=None,
    help="Path to a pairs yaml file (default: pairs.yaml in project root)"
)
parser.add_argument(
    "--staleness-hours",
    type=int,
    default=None,
    help=(
        "Override price data staleness threshold in hours "
        "(default: PRICE_DATA_STALENESS_HOURS env var, fallback 26). "
        "Use 999 to skip the check during development."
    ),
)
args = parser.parse_args()

# ── Load the composition ──────────────────────────────────────────────────────
if args.pairs:
    pairs_path = os.path.expanduser(args.pairs)
else:
    pairs_path = os.path.join(os.path.dirname(__file__), "pairs.yaml")
with open(pairs_path, "r") as f:
    composition = yaml.safe_load(f)

pairs = composition["pairs"]


def extract_search_findings(raw: str) -> str:
    """
    The Researcher agent often wraps its final answer in JSON instead of
    returning plain prose. This function attempts to recover meaningful text:
    - If the raw output is already prose, return it
    - If it's JSON, extract any 'text', 'output', or 'result' string values
    - If Serper results are embedded, summarise the titles/snippets found
    Falls back to raw if nothing useful is found.
    """
    stripped = raw.strip()

    # Already plain prose — no JSON brace at start
    if not stripped.startswith("{") and not stripped.startswith("["):
        return stripped

    try:
        blob = json.loads(stripped)

        # Look for prose text in common parameter keys
        prose_keys = {"text", "output", "result", "findings", "summary", "answer"}

        def find_prose(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in prose_keys and isinstance(v, str) and len(v) > 40:
                        return v.strip()
                for v in obj.values():
                    r = find_prose(v)
                    if r:
                        return r
            if isinstance(obj, list):
                for item in obj:
                    r = find_prose(item)
                    if r:
                        return r
            return None

        prose = find_prose(blob)
        if prose and "=== CORRELATION DECAY REPORT ===" not in prose:
            return prose

        # If the JSON contains a search_query, note what was searched
        def find_query(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if "query" in k.lower() and isinstance(v, str):
                        return v
                for v in obj.values():
                    r = find_query(v)
                    if r:
                        return r
            return None

        query = find_query(blob)
        if query:
            return f"[Researcher described search for: {query} — no results extracted]"

    except Exception:
        pass

    return stripped


# ── Logging setup ────────────────────────────────────────────────────────────
import logging
import sys as _sys

def _setup_log():
    log_path = os.path.join(data_dir, "main.log")
    os.makedirs(data_dir, exist_ok=True)
    logger = logging.getLogger("main")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger, log_path


def _build_analyst_brief(scout_report: str, det_verdict, news_context: str) -> str:
    """
    Assemble the full brief that The Analyst receives.
    Combines the deterministic tool output, gate verdicts, and news context.
    """
    verdict_block = (
        f"=== DETERMINISTIC VERDICT ===\n"
        f"Verdict:   {det_verdict.verdict}\n"
        f"Rationale: {det_verdict.rationale}\n"
        f"\nGATE SUMMARY:\n"
    )
    for gate_name, gate in det_verdict.gates.items():
        verdict_block += f"  {gate_name}: {gate.status}\n"
    verdict_block += "============================="

    sections = [scout_report, verdict_block]
    if news_context:
        sections.append(news_context)
    return "\n\n".join(sections)


# ── Run the crew for each pair ────────────────────────────────────────────────
if __name__ == "__main__":
    from datetime import datetime
    log, log_path = _setup_log()

    n      = len(pairs)
    source = os.path.basename(pairs_path)
    print(f"ShiftInnerV  {date.today()}  |  {n} pair(s) from {source}")
    print(f"Log → {log_path}")
    print()
    log.info(f"=== RUN START  {source}  ({n} pairs) ===")

    # ── Initialise trial ledger ───────────────────────────────────────────────
    ledger_db_path = os.path.join(data_dir, "trial_ledger.db")
    init_trial_ledger(ledger_db_path)
    log.info(f"Trial ledger: {ledger_db_path}")

    # ── Item 15: Load compositions once (used for concentration checks) ───────
    _compositions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compositions")
    compositions = load_compositions(_compositions_dir)
    log.info(f"Loaded {len(compositions)} composition(s) for concentration tracking: "
             f"{', '.join(sorted(compositions.keys())) or '(none)'}")

    # ── Item 5: Resolve effective staleness threshold ─────────────────────────
    # CLI flag beats env var; env var beats compiled default.
    effective_staleness_hours = (
        args.staleness_hours
        if args.staleness_hours is not None
        else PRICE_DATA_STALENESS_THRESHOLD_HOURS
    )

    # ── Ensure data ───────────────────────────────────────────────────────────
    print(f"\n── Data Management (staleness threshold: {effective_staleness_hours}h) ─────────")
    tickers     = tickers_from_pairs(pairs)
    data_status = ensure_data(tickers, data_dir)
    failed      = [t for t, s in data_status.items() if s == "failed"]

    for t, s in data_status.items():
        log.info(f"  data  {t}: {s}")

    if failed:
        print(f"  ⚠️  Data fetch failed: {', '.join(failed)}")
        log.warning(f"Data fetch failed: {', '.join(failed)}")

    # ── Item 5: Staleness check — hard abort if any data is too old ───────────
    print(f"\n── Data Staleness Check ───────────────────────────────────────")
    staleness_results = check_data_staleness(
        tickers,
        data_dir,
        staleness_hours=effective_staleness_hours,
        logger=log,
    )

    stale_tickers   = [t for t, s in staleness_results.items() if s == "stale"]
    missing_tickers = [t for t, s in staleness_results.items() if s == "missing"]
    fresh_count     = len([t for t, s in staleness_results.items() if s == "fresh"])

    print(f"  Fresh: {fresh_count}")
    if stale_tickers:
        print(f"  ⚠️  STALE (too old): {', '.join(stale_tickers)}")
    if missing_tickers:
        print(f"  ✗ MISSING: {', '.join(missing_tickers)}")

    if stale_tickers or missing_tickers:
        problem_tickers = stale_tickers + missing_tickers
        problem_desc = (
            f"stale: {', '.join(stale_tickers)}" if stale_tickers else ""
        )
        if missing_tickers:
            problem_desc += (
                (" | " if problem_desc else "")
                + f"missing: {', '.join(missing_tickers)}"
            )

        print(f"\n❌ ABORTING RUN: Price data is stale or missing")
        print(f"   Problem tickers: {', '.join(problem_tickers)}")
        print(f"   Threshold: {effective_staleness_hours} hours")
        print(f"   To override (dev only): export PRICE_DATA_STALENESS_HOURS=999")

        log.critical(
            f"STALE_DATA ABORT: Cannot proceed with verdicts. "
            f"{problem_desc}. "
            f"Last update > {effective_staleness_hours} hours ago (or file missing). "
            f"Check data source (Tiingo/yfinance) and re-run after data is fresh."
        )

        _sys.exit(1)

    print(f"  ✓ All data is fresh. Proceeding with verdicts.")
    log.info("Data staleness check PASSED. Proceeding with verdict generation.")

    # Item 21: Warn (never abort) if news API keys are missing
    import os as _os_news
    if not (_os_news.getenv("FRED_API_KEY", "") or ""):
        print("  ⚠️  FRED_API_KEY not set — Tier 1/2 macro context will be limited")
        log.warning("[Item 21] FRED_API_KEY not set — macro calendar fetch degraded")
    if not _os_news.getenv("ALPHA_VANTAGE_KEY", ""):
        print("  ⚠️  ALPHA_VANTAGE_KEY not set — Tier 3 ticker headlines will be skipped")
        log.warning("[Item 21] ALPHA_VANTAGE_KEY not set — Tier 3 headlines skipped")

    # ── Run crew for each pair ────────────────────────────────────────────────
    verdicts = []

    for i, pair in enumerate(pairs, 1):
        ticker1 = pair["ticker1"]
        ticker2 = pair["ticker2"]
        label   = pair["label"]

        if ticker1 in failed or ticker2 in failed:
            print(f"  [{i:>3}/{n}]  SKIP   {ticker1}/{ticker2}  — missing data")
            log.warning(f"SKIP {ticker1}/{ticker2} — missing data")
            continue

        print(f"  [{i:>3}/{n}]  ...    {ticker1}/{ticker2}  {label}", end="", flush=True)
        log.info(f"START {ticker1}/{ticker2}  ({label})")

        lookback_years = pair.get("lookback_years", 3)
        n_pairs = len(pairs)
        lookback_years = pair.get("lookback_years", 3)
        n_pairs = len(pairs)

        # ── Item 21: fetch news & macro context (deterministic) ───────────────
        _news_context, _cb_decision_recent, _macro_surprise = \
            build_news_context_with_flags(ticker1, ticker2)
        log.info(
            f"[news_context] {ticker1}/{ticker2} "
            f"{'populated' if _news_context else 'empty'} | "
            f"cb_recent={_cb_decision_recent} macro_surprise={_macro_surprise}"
        )

        # ── Deterministic tool call — no LLM involved ─────────────────────────
        crew_error = None
        result = None
        scout_report_text = ""
        try:
            _tool = CorrelationDecayTool(
                expected_ticker1=ticker1,
                expected_ticker2=ticker2,
                lookback_years=lookback_years,
                n_pairs_in_composition=n_pairs,
                pair_label=pair.get("label", ""),
                factor_proxy_ticker=pair.get("factor_proxy", ""),
            )
            _tool_output = _tool._run(ticker1=ticker1, ticker2=ticker2)
            if _tool_output.startswith("Tool error:"):
                crew_error = _tool_output
                log.error(f"Tool failed for {label}: {_tool_output}")
            elif "=== CORRELATION DECAY REPORT ===" not in _tool_output:
                crew_error = "Tool returned unexpected output (no report header)"
                log.error(f"Tool bad output for {label}: {_tool_output[:120]}")
            else:
                scout_report_text = _tool_output
                log.info(f"[tool] {ticker1}/{ticker2} report OK ({len(scout_report_text)} chars)")
        except Exception as e:
            crew_error = str(e)
            log.error(f"Tool exception for {label}: {e}")


        # ── STEP 2: Deterministic gate evaluation (PRIMARY trading decision) ──
        verdict_tag = "REJECT "
        trading_rationale = "No Scout report available."
        det_verdict = None
        gate_results = {}
        composition_label = None   # Item 15: set during gate block if applicable

        if not crew_error and scout_report_text:
            snapshot = parse_statistical_snapshot(scout_report_text)
            det_verdict = evaluate_gates(
                trace_stat=snapshot.get("trace_stat"),
                crit_val_95=snapshot.get("crit_95"),
                crit_val_90=snapshot.get("crit_90"),
                half_life=snapshot.get("half_life"),
                snr=snapshot.get("snr"),
                episodes=snapshot.get("episodes"),
                factor_loading=snapshot.get("factor_loading"),
                net_pnl_bps=snapshot.get("net_pnl_bps"),
            )
            trading_rationale = det_verdict.rationale

            gate_results = {
                gk: det_verdict.gates[gk].status
                for gk in ["gate_1", "gate_2", "gate_3", "gate_4", "gate_6", "gate_7"]
                if gk in det_verdict.gates
            }

            v = det_verdict.verdict
            if v == "ACTIVE":
                verdict_tag = "ACTIVE ✅"
            elif v == "MONITOR-NEAR":
                verdict_tag = "MONITOR-NEAR 👀"
            elif v == "MONITOR":
                verdict_tag = "MONITOR 👀"
            else:
                verdict_tag = "REJECT "

            # ── Item 15: Composition concentration gate override ──────────────
            # Identify which composition this pair belongs to.
            composition_label = get_pair_composition(ticker1, ticker2, compositions)

            # Only ACTIVE verdicts can be downgraded; MONITOR/REJECT are unaffected.
            if composition_label and det_verdict.verdict == "ACTIVE":
                conc_limit = COMPOSITION_LIMITS.get(
                    composition_label, COMPOSITION_CONCENTRATION_LIMIT
                )
                conc_check = check_composition_concentration(
                    db_path=ledger_db_path,
                    composition_label=composition_label,
                    limit=conc_limit,
                    logger=log,
                )
                if conc_check.decision == "DOWNGRADE_TO_MONITOR":
                    log.warning(
                        f"CONCENTRATION OVERRIDE: {ticker1}/{ticker2} "
                        f"({composition_label}) downgraded from ACTIVE to MONITOR. "
                        f"{conc_check.rationale}"
                    )
                    det_verdict.verdict  = "MONITOR"
                    det_verdict.rationale = (
                        f"[CONCENTRATION OVERRIDE] {conc_check.rationale} "
                        f"Original gates passed. Downgraded to MONITOR until an "
                        f"existing {composition_label} position closes."
                    )
                    trading_rationale = det_verdict.rationale
                    verdict_tag = "MONITOR 👀"

            # ── Item 8: Regime filter — applied after all other downgrades ──────
            # Only fires on verdicts still ACTIVE at this point.
            if det_verdict is not None and det_verdict.verdict == "ACTIVE":
                regime_state      = os.getenv("CURRENT_REGIME_STATE", "NORMAL")
                regime_multiplier = float(os.getenv("POSITION_SIZE_MULTIPLIER", "1.0"))
                pair_snr          = snapshot.get("snr") if 'snapshot' in dir() else None

                # HIGH_STRESS: require SNR >= 2.0 for new entries
                if regime_state == "HIGH_STRESS":
                    snr_threshold = 2.0
                    if pair_snr is None or pair_snr < snr_threshold:
                        snr_display = f"{pair_snr:.2f}" if pair_snr is not None else "N/A"
                        log.warning(
                            f"REGIME FILTER [HIGH_STRESS]: {ticker1}/{ticker2} downgraded "
                            f"from ACTIVE to MONITOR. SNR {snr_display} < {snr_threshold} required."
                        )
                        det_verdict.verdict  = "MONITOR"
                        det_verdict.rationale = (
                            f"[STRESS_REGIME] Downgraded from ACTIVE. "
                            f"HIGH_STRESS requires SNR ≥ {snr_threshold}; "
                            f"this pair has SNR {snr_display}. "
                            f"Original gates passed."
                        )
                        trading_rationale = det_verdict.rationale
                        verdict_tag = "MONITOR 👀"
                        print(
                            f"         ↳ STRESS_REGIME: SNR {snr_display} < {snr_threshold} "
                            f"(HIGH_STRESS) — downgraded to MONITOR"
                        )

        elif crew_error:
            verdict_tag = "ERROR  "

        print(f"\r  [{i:>3}/{n}]  {verdict_tag:<16}  {ticker1}/{ticker2}  {label}")
        # Item 15: surface concentration override in console
        if (det_verdict is not None
                and "[CONCENTRATION OVERRIDE]" in (det_verdict.rationale or "")):
            print(
                f"         ↳ CONCENTRATION OVERRIDE: {composition_label} "
                f"at limit — downgraded from ACTIVE to MONITOR"
            )
        log.info(f"VERDICT (deterministic) {ticker1}/{ticker2}: {verdict_tag.strip()}")
        log.info(f"RATIONALE: {trading_rationale}")

        # Log individual gate statuses for audit trail
        for gk, gs in gate_results.items():
            log.debug(f"  {gk}: {gs}")

        verdicts.append((ticker1, ticker2, verdict_tag))

        # ── STEP 3: Run The Analyst (single LLM call) ────────────────────────
        analyst_text = ""
        if not crew_error and scout_report_text and det_verdict:
            try:
                _analyst = make_analyst()
                _brief = _build_analyst_brief(
                    scout_report_text, det_verdict, _news_context
                )
                _task = build_analyst_task(
                    analyst=_analyst,
                    brief=_brief,
                    ticker1=ticker1,
                    ticker2=ticker2,
                    label=label,
                    verdict=det_verdict.verdict,
                )
                _crew = Crew(
                    agents=[_analyst],
                    tasks=[_task],
                    process=Process.sequential,
                    verbose=False,
                )
                _result = _crew.kickoff()
                analyst_text = str(_result.raw) if _result else ""
                log.info(f"[analyst] {ticker1}/{ticker2} OK ({len(analyst_text)} chars)")
            except Exception as e:
                log.warning(f"[analyst] {ticker1}/{ticker2} failed: {e}")
                analyst_text = ""


        # ── STEP 4: Record ACTIVE verdicts to trial ledger ────────────────────
        is_active = (det_verdict is not None and det_verdict.verdict == "ACTIVE")
        is_monitor_near = (det_verdict is not None and det_verdict.verdict == "MONITOR-NEAR")

        if is_active:
            snapshot = parse_statistical_snapshot(scout_report_text)
            # Item 8: attach regime context to every ACTIVE ledger entry
            _regime_state      = os.getenv("CURRENT_REGIME_STATE", "NORMAL")
            _regime_multiplier = os.getenv("POSITION_SIZE_MULTIPLIER", "1.0")
            _regime_note       = f"Regime: {_regime_state} | PosSizeMultiplier: {_regime_multiplier}x"
            try:
                verdict_id = record_active_verdict(
                    db_path=ledger_db_path,
                    ticker1=ticker1,
                    ticker2=ticker2,
                    label=label,
                    gate_results=gate_results,
                    composition_label=composition_label,   # Item 15
                    entry_z=snapshot.get("entry_z_verdict"),
                    half_life=snapshot.get("half_life"),
                    snr=snapshot.get("snr"),
                    episodes=snapshot.get("episodes"),
                    trace_stat=snapshot.get("trace_stat"),
                    crit_95=snapshot.get("crit_95"),
                    hedge_ratio=snapshot.get("hedge_ratio"),
                    spread_mean=snapshot.get("spread_mean"),
                    spread_std=snapshot.get("spread_std"),
                    regime_state=_regime_state,                     # Item 8
                    position_size_multiplier=float(_regime_multiplier),  # Item 8
                    notes=f"[{_regime_note}] Deterministic verdict: {trading_rationale}",
                )
                if verdict_id:
                    print(f"         ↳ ledger  → trial {verdict_id}")
                    log.info(f"LEDGER  → trial {verdict_id}  ({ticker1}/{ticker2})")
                else:
                    log.warning(f"LEDGER insert failed for {ticker1}/{ticker2}")
            except Exception as e:
                log.warning(f"LEDGER insert error for {label}: {e}")

        # ── Build appendix ────────────────────────────────────────────────────
        appendix_lines = []
        if scout_report_text:
            appendix_lines.append("### Correlation Decay Report (deterministic)\n")
            appendix_lines.append(f"```\n{scout_report_text}\n```\n")
            log.debug(f"TOOL OUTPUT [{ticker1}/{ticker2}]:\n{scout_report_text}")


        # ── Write report ──────────────────────────────────────────────────────
        safe_label = label.lower()
        safe_label = "".join(c if c.isalnum() or c in "-_ " else "" for c in safe_label)
        safe_label = safe_label.strip().replace(" ", "_")
        timestamp  = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename   = f"{safe_label}_{ticker1}_{ticker2}_{timestamp}.md"
        report_path = os.path.join(report_dir, filename)

        with open(report_path, "w") as f:
            f.write(f"# ShiftInnerV Divergence Report\n")
            f.write(f"**Pair:** {label}\n")
            f.write(f"**Date:** {date.today()}\n\n")
            f.write("---\n\n")
            f.write("## Final Verdict\n\n")
            if crew_error:
                f.write(f"**ERROR:** Crew failed to complete — {crew_error}\n\n")
                f.write("Partial tool outputs may be available in the appendix below.\n")
            else:
                f.write(f"**Deterministic Verdict:** {verdict_tag.strip()}\n\n")
                f.write(f"**Rationale:** {trading_rationale}\n\n")
                if gate_results:
                    f.write("**Gate Summary:**\n")
                    for gk, gs in sorted(gate_results.items()):
                        f.write(f"- {gk}: {gs}\n")
                f.write("\n### Analyst Interpretation\n\n")
                if analyst_text:
                    f.write(analyst_text)
            f.write("\n\n---\n\n")
            f.write("## Appendix: Tool Execution Log\n\n")
            f.write("\n".join(appendix_lines))

        log.info(f"REPORT  → {report_path}")

        # ── Dossier on actionable verdicts ────────────────────────────────────
        is_actionable = not crew_error and (is_active or is_monitor_near)

        if is_actionable:
            try:
                lookback_days = pair.get("lookback_years", 5) * 252 // 12
                lookback_days = min(max(lookback_days, 60), 180)
                dossier_text  = render_dossier(ticker1, ticker2, lookback_days)
                dossier_path  = os.path.join(report_dir, f"dossier_{ticker1}_{ticker2}_{timestamp}.md")
                with open(dossier_path, "w") as f:
                    f.write(dossier_text)
                print(f"         ↳ dossier → {dossier_path}")
                log.info(f"DOSSIER → {dossier_path}")
            except Exception as e:
                log.warning(f"Dossier failed for {label}: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    actives  = [v for v in verdicts if "ACTIVE"  in v[2]]
    monitors = [v for v in verdicts if "MONITOR" in v[2]]
    rejects  = [v for v in verdicts if "REJECT"  in v[2]]
    print()
    print(f"  Done  {len(verdicts)} pair(s) — "
          f"ACTIVE: {len(actives)}  MONITOR: {len(monitors)}  REJECT: {len(rejects)}")
    log.info(f"=== RUN END  ACTIVE:{len(actives)} MONITOR:{len(monitors)} REJECT:{len(rejects)} ===")

    # ── Auto-promote ──────────────────────────────────────────────────────────
    if len(pairs) > 1:
        try:
            promoted_path = promote_run(quiet=True)
            if promoted_path:
                print(f"  Promoted → {promoted_path}")
                log.info(f"PROMOTED → {promoted_path}")
        except Exception as e:
            log.warning(f"promote.py failed: {e}")
