import os
import re
import json
import yaml
import argparse
from dotenv import load_dotenv
from datetime import date
from crewai import Crew, Process

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

from shiftinnerv.pipelines.agents import make_crew
from shiftinnerv.pipelines.tasks import build_tasks
from shiftinnerv.services.data_manager import ensure_data, tickers_from_pairs, check_data_staleness
from shiftinnerv.pipelines.dossier import render_dossier
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


def extract_report_text(raw: str) -> tuple[str, str]:
    """
    Recover the actual === CORRELATION DECAY REPORT === text from the Scout's
    output regardless of whether llama3.1 returned it as plain text or wrapped
    it in a JSON blob. Strips trailing JSON closing characters ("}} etc).

    Returns
    -------
    (extracted_text, recovery_type)
    recovery_type: "success" | "fallback_regex" | "fallback_json_extraction" | "failure"
    """
    # Already plain text — no fallback needed
    if raw.strip().startswith("=== CORRELATION DECAY REPORT ==="):
        return raw.strip(), "success"

    # Fallback 1: Regex search for report header anywhere in string
    match = re.search(r"(=== CORRELATION DECAY REPORT ===.*)", raw, re.DOTALL)
    if match:
        text = match.group(1).strip()
        text = text.replace('\\n', '\n')
        text = re.sub(r'[\"\'\}\]]+\s*$', '', text).strip()
        return text, "fallback_regex"

    # Fallback 2: JSON parsing — walk all string values
    try:
        blob = json.loads(raw)

        def find_report(obj):
            if isinstance(obj, str) and "=== CORRELATION DECAY REPORT ===" in obj:
                idx = obj.index("=== CORRELATION DECAY REPORT ===")
                return obj[idx:].strip()
            if isinstance(obj, dict):
                for v in obj.values():
                    r = find_report(v)
                    if r:
                        return r
            if isinstance(obj, list):
                for item in obj:
                    r = find_report(item)
                    if r:
                        return r
            return None

        found = find_report(blob)
        if found:
            return found, "fallback_json_extraction"
    except Exception:
        pass

    # Total failure — return raw so downstream still has something
    return raw.strip(), "failure"


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


def _setup_llm_logger() -> logging.Logger:
    """Configure the LLM-call outcome logger (separate file for measurement)."""
    os.makedirs(data_dir, exist_ok=True)
    llm_logger = logging.getLogger("shiftinnerv.llm_calls")
    llm_logger.setLevel(logging.INFO)
    if not llm_logger.handlers:  # avoid duplicate handlers on re-import
        handler = logging.FileHandler(os.path.join(data_dir, "llm_calls.log"))
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
        ))
        llm_logger.addHandler(handler)
    return llm_logger


def log_llm_outcome(
    llm_logger: logging.Logger,
    agent_name: str,
    pair: str,
    raw_output: str,
    recovery_type: str,
    success: bool,
) -> None:
    """
    Log a single LLM call outcome to llm_calls.log.

    recovery_type: "success" | "fallback_regex" | "fallback_json_extraction" | "failure"
    success:       True if usable output was recovered
    """
    status = "OK" if success else "FAIL"
    truncated = raw_output[:200].replace("\n", " ")
    llm_logger.info(
        f"[{status}] {agent_name} {pair} | recovery={recovery_type} | "
        f"output={truncated}"
    )

# ── Run the crew for each pair ────────────────────────────────────────────────
if __name__ == "__main__":
    from datetime import datetime
    log, log_path = _setup_log()
    llm_log = _setup_llm_logger()

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

        lookback_years = pair.get("lookback_years", 5)
        n_pairs = len(pairs)
        quant_scout, signal_mathematician = make_crew(
            ticker1, ticker2,
            lookback_years=lookback_years,
            n_pairs_in_composition=n_pairs,
            pair_label=pair.get("label", ""),
            factor_proxy_ticker=pair.get("factor_proxy", ""),
        )
        correlation_audit, quant_assessment = build_tasks(
            pair=pair,
            agents=(quant_scout, signal_mathematician)
        )
        crew = Crew(
            agents=[quant_scout, signal_mathematician],
            tasks=[correlation_audit, quant_assessment],
            process=Process.sequential,
            verbose=False
        )

        crew_error = None
        result = None
        try:
            result = crew.kickoff()
        except Exception as e:
            crew_error = str(e)
            log.error(f"Crew failed for {label}: {e}")

        # ── Extract Scout report text (Part 1: instrumentation) ───────────────
        scout_raw = ""
        scout_report_text = ""
        for task in [correlation_audit, quant_assessment]:
            if (hasattr(task, "output") and task.output
                    and hasattr(task, "agent")
                    and task.agent.role == "Lead Quantitative Scout"):
                scout_raw = task.output.raw or ""
                break

        if scout_raw:
            scout_report_text, scout_recovery = extract_report_text(scout_raw)
            scout_success = scout_recovery != "failure"
            log_llm_outcome(
                llm_logger=llm_log,
                agent_name="Lead Quantitative Scout",
                pair=f"{ticker1}/{ticker2}",
                raw_output=scout_raw,
                recovery_type=scout_recovery,
                success=scout_success,
            )
            if not scout_success:
                print(f"\r  ⚠️  Scout output recovery FAILED for {ticker1}/{ticker2}")
                log.warning(f"Scout recovery FAILED for {ticker1}/{ticker2}")
            elif scout_recovery != "success":
                log.warning(
                    f"Scout output required fallback ({scout_recovery}) "
                    f"for {ticker1}/{ticker2}"
                )

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

        # ── STEP 3: Log Signal Mathematician LLM output (narrative only) ──────
        verdict_text = str(result.raw) if result and not crew_error else ""
        if verdict_text:
            sm_recovered, sm_recovery = verdict_text, "success"
            # Signal Math output doesn't use the REPORT header — just measure it
            sm_success = bool(verdict_text.strip())
            log_llm_outcome(
                llm_logger=llm_log,
                agent_name="Signal Mathematician",
                pair=f"{ticker1}/{ticker2}",
                raw_output=verdict_text,
                recovery_type=sm_recovery,
                success=sm_success,
            )
            log.debug(f"FULL SIGNAL MATH OUTPUT:\n{verdict_text}")

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
        for task in [correlation_audit, quant_assessment]:
            if hasattr(task, "output") and task.output:
                raw  = task.output.raw or ""
                role = task.agent.role
                if role == "Lead Quantitative Scout":
                    cleaned = scout_report_text if scout_report_text else raw
                else:
                    cleaned = raw
                appendix_lines.append(f"### {role}\n")
                appendix_lines.append(f"```\n{cleaned}\n```\n")
                log.debug(f"TASK OUTPUT [{role}]:\n{cleaned}")

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
                f.write("\n### Signal Mathematician Narrative\n\n")
                if verdict_text:
                    f.write(verdict_text)
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
