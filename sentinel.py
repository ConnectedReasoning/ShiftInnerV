#!/usr/bin/env python3
"""
ShiftInnerV — Sentinel

Single-run orchestrator. Designed to be called by launchd on a schedule.
Runs once, does its work, exits. launchd handles the schedule and restarts.

What it does each run:
  1. Runs monitor.py — scans all compositions, writes new anomaly yamls
  2. Processes any new anomaly yamls through main.py (agents + dossier)
  3. If --promoted flag: refreshes promote.py and runs main.py on best candidates
  4. Exits

launchd calls this at 07:00 (with --promoted) and 19:00 (anomalies only).
See launchd/ directory for plist files.

Usage:
    python sentinel.py                  # monitor + process new anomalies
    python sentinel.py --promoted       # also run promoted composition
    python sentinel.py --dry-run        # print config and exit

Lock file:
    Writes DATA_DIR/sentinel.lock on start, removes on exit.
    If lock exists on start, a previous run is still in progress — exits immediately.
    This prevents launchd overlap if a run takes longer than the schedule interval.

Env (from ~/.shiftinnerv_env):
    DATA_STORAGE_PATH   base data dir (default ~/Projects/ShiftInnerV_Data)
    TIINGO_KEY          Tiingo API key
    REPORT_DIR          report output dir
"""

import os
import sys
import logging
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Item 8 imports (lazy-imported inside main() to avoid startup cost when --dry-run)
# from shiftinnerv.sensors.regime_monitor import RegimeDetector, RegimeState
# from trial_ledger import load_open_trials

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.expanduser(
    os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data")
)
COMPOSITIONS_DIR = os.path.join(PROJECT_DIR, "compositions")
ANOMALY_DIR      = os.path.join(COMPOSITIONS_DIR, "anomalies")
LOG_PATH         = os.path.join(DATA_DIR, "sentinel.log")
LOCK_PATH        = os.path.join(DATA_DIR, "sentinel.lock")

MONITOR_PY = os.path.join(PROJECT_DIR, "monitor.py")
MAIN_PY    = os.path.join(PROJECT_DIR, "main.py")
PROMOTE_PY = os.path.join(PROJECT_DIR, "promote.py")

SEEN_PATH       = os.path.join(DATA_DIR, "sentinel_seen.txt")
LEDGER_DB_PATH  = os.path.join(DATA_DIR, "trial_ledger.db")


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    os.makedirs(DATA_DIR, exist_ok=True)
    logger = logging.getLogger("sentinel")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# ── Lock ──────────────────────────────────────────────────────────────────────

def acquire_lock(log: logging.Logger) -> bool:
    """Return True if lock acquired, False if another run is in progress."""
    if os.path.exists(LOCK_PATH):
        # Check if the PID in lock file is still running
        try:
            pid = int(open(LOCK_PATH).read().strip())
            os.kill(pid, 0)   # signal 0 = check existence only
            log.warning(f"Sentinel already running (PID {pid}) — exiting.")
            return False
        except (ValueError, OSError):
            # Stale lock — previous run crashed without cleanup
            log.warning("Stale lock file found — removing and continuing.")
            os.remove(LOCK_PATH)

    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    if os.path.exists(LOCK_PATH):
        os.remove(LOCK_PATH)


# ── Seen-file tracker ─────────────────────────────────────────────────────────

def load_seen() -> set:
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH) as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_seen(seen: set):
    with open(SEEN_PATH, "w") as f:
        f.write("\n".join(sorted(seen)))


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run_subprocess(cmd: list, label: str, log: logging.Logger) -> bool:
    """
    Run subprocess. All output goes to log file at DEBUG level.
    Progress lines from main.py are also shown on console at INFO level.
    Monitor pair-by-pair detail is suppressed from console.
    """
    CONSOLE_TOKENS = (
        "[",           # pair progress:  [  3/25]  ACTIVE ...
        "Done ",       # run summary line
        "\u21b3 dossier",  # dossier path
        "Promoted \u2192",  # promote path
        "Log \u2192",       # log path
        "WARNING",
        "ERROR",
    )
    SUPPRESS_TOKENS = (
        "OK      ",
        "NOISE   ",
        "WATCH   ",
        "SOLID   ",
        "STRONG  ",
        "PRIME   ",
        "WEAK    ",
        "ANOMALY ",
        "Flagged:",
        "Anomaly yaml:",
        "Monitor pass",
        "composition file",
    )

    log.info(f"START  {label}")
    start = datetime.now()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            log.debug(f"  |  {line}")
            suppressed = any(t in line for t in SUPPRESS_TOKENS)
            promoted   = any(t in line for t in CONSOLE_TOKENS)
            if promoted and not suppressed:
                log.info(f"  |  {line}")
        proc.wait()
        elapsed = (datetime.now() - start).seconds
        ok      = proc.returncode == 0
        status  = "OK" if ok else f"EXIT {proc.returncode}"
        log.info(f"END    {label} — {status} ({elapsed}s)")
        return ok
    except Exception as e:
        log.error(f"ERROR  {label} — {e}")
        return False


# ── Latest promoted yaml ──────────────────────────────────────────────────────

def latest_promoted() -> str | None:
    files = sorted(Path(COMPOSITIONS_DIR).glob("promoted_*.yaml"), reverse=True)
    return str(files[0]) if files else None


# ── Position revalidation (Item 13) ──────────────────────────────────────────

def run_position_revalidation(log: logging.Logger) -> None:
    """
    Revalidate all open positions for SNR deterioration and mean drift.
    Auto-closes positions that fail both the SNR and drift criteria.
    Records all results to position_revalidations table.
    """
    from shiftinnerv.sensors.position_monitor import revalidate_open_positions
    from trial_ledger import record_position_revalidation

    print("\n── Position Revalidation ────────────────────────────────────────")

    if not os.path.exists(LEDGER_DB_PATH):
        log.warning(f"[position_revalidation] Trial ledger not found: {LEDGER_DB_PATH}")
        print("  Trial ledger not found — skipping revalidation.")
        return

    results = revalidate_open_positions(
        db_path=LEDGER_DB_PATH,
        data_dir=DATA_DIR,
        logger=log,
    )

    if not results:
        print("  No open positions to revalidate.")
        return

    auto_close_count = sum(1 for r in results if r.decision == "AUTO_CLOSE")
    monitor_count    = sum(1 for r in results if r.decision == "MONITOR")
    hold_count       = sum(1 for r in results if r.decision == "HOLD")
    error_count      = sum(1 for r in results if r.error is not None)

    print(f"  Revalidated {len(results)} open position(s)")
    if hold_count       > 0: print(f"  ✓  {hold_count} position(s) to HOLD")
    if monitor_count    > 0: print(f"  👀 {monitor_count} position(s) flagged for MONITOR")
    if auto_close_count > 0: print(f"  ⚠️  {auto_close_count} position(s) triggered AUTO_CLOSE")
    if error_count      > 0: print(f"  ✗  {error_count} position(s) skipped (data/error)")

    log.info(
        f"[position_revalidation] {len(results)} checked — "
        f"{hold_count} HOLD | {monitor_count} MONITOR | "
        f"{auto_close_count} AUTO_CLOSE | {error_count} errors"
    )

    # Persist results to revalidation history table
    for result in results:
        if result.error is not None:
            continue
        record_position_revalidation(
            db_path=LEDGER_DB_PATH,
            verdict_id=result.verdict_id,
            snr_entry=result.entry_snr,
            snr_current=result.current_snr,
            snr_change_bps=result.snr_change_bps,
            mean_drift_sigma=result.mean_drift_sigma,
            drift_detected=result.drift_detected,
            decision=result.decision,
            rationale=result.rationale,
            days_held=result.days_held,
        )



# ── Item 8: Regime Detection ──────────────────────────────────────────────────

def run_regime_detection(log: logging.Logger):
    """
    Detect current market regime and set env vars for downstream processes.

    - Fetches VIX level
    - Checks open position correlation to SPY
    - Determines RegimeState and position_size_multiplier
    - Sets CURRENT_REGIME_STATE and POSITION_SIZE_MULTIPLIER env vars
    - Halts new entries (sys.exit(0)) on CRISIS with monitoring-only run

    Returns
    -------
    RegimeSnapshot — always returned (even CRISIS, before the exit)
    """
    from shiftinnerv.sensors.regime_monitor import RegimeDetector, RegimeState
    from trial_ledger import load_open_trials

    print("\n── Market Regime Detection ─────────────────────────────────────")

    detector = RegimeDetector(data_dir=DATA_DIR, logger=log)

    # Load currently open positions for correlation check
    open_positions = []
    if os.path.exists(LEDGER_DB_PATH):
        open_df = load_open_trials(LEDGER_DB_PATH)
        if open_df is not None and len(open_df) > 0:
            open_positions = [
                (row["ticker1"], row["ticker2"])
                for _, row in open_df.iterrows()
                if row["ticker1"] and row["ticker2"]
            ]

    regime = detector.detect_regime(open_positions=open_positions, logger=log)

    # ── Console output ────────────────────────────────────────────────────────
    state_icons = {
        "NORMAL":      "✓",
        "ELEVATED":    "⚠️ ",
        "HIGH_STRESS": "⚠️ ",
        "CRISIS":      "❌",
    }
    icon = state_icons.get(regime.state.value, "")
    print(f"  Regime:   {regime.state.value}  {icon}")
    print(f"  VIX:      {regime.vix_level:.1f}"
          + ("  [UNAVAILABLE — using default]" if regime.vix_unavailable else ""))
    print(f"  Pos size: {regime.position_size_multiplier}x")

    if regime.state == "NORMAL":
        print("  ✓ Market conditions stable.")
    elif regime.state == RegimeState.ELEVATED:
        print("  ⚠️  ELEVATED: Reduce position sizes to 50%.")
    elif regime.state == RegimeState.HIGH_STRESS:
        print("  ⚠️  HIGH_STRESS: Only SNR ≥ 2.0 pairs accepted. Position size 25%.")

    if regime.correlation_regime:
        print(f"  ⚠️  CORRELATION_REGIME: {len(regime.correlated_pairs)} pair(s) "
              f"|SPY corr| > 0.7")
        for t1, t2, corr in regime.correlated_pairs:
            print(f"       {t1}/{t2}: {corr:.3f}")
        if regime.state != RegimeState.CRISIS:
            print(f"  ⚠️  Further reduction applied → final {regime.position_size_multiplier:.4g}x "
                  f"(VIX × 0.5 correlation)")

    log.info(
        f"[regime] State={regime.state.value} | VIX={regime.vix_level:.1f} | "
        f"Multiplier={regime.position_size_multiplier}x | "
        f"Open={len(open_positions)} | Correlated={len(regime.correlated_pairs)}"
    )

    # ── Propagate to downstream subprocesses via env vars ─────────────────────
    os.environ["CURRENT_REGIME_STATE"]    = regime.state.value
    os.environ["POSITION_SIZE_MULTIPLIER"] = str(regime.position_size_multiplier)

    # ── CRISIS: hard halt on new entries ─────────────────────────────────────
    if regime.state == RegimeState.CRISIS:
        print(f"\n❌ HALT: CRISIS regime detected (VIX {regime.vix_level:.1f} ≥ 40)")
        print(f"   No new verdicts will be generated.")
        print(f"   Existing open positions continue to be monitored.")
        print(f"   Operator must manually restart screening after regime normalises.")

        log.critical(
            f"CRISIS_REGIME: VIX {regime.vix_level:.1f} ≥ 40. "
            f"Halting new entries. Manual restart required."
        )

        print(f"\n   Running monitoring only (no new verdicts)...")
        run_subprocess([sys.executable, MONITOR_PY], "monitor.py (monitoring only)", log)
        release_lock()
        sys.exit(0)   # Clean exit — launchd will re-run on schedule

    return regime


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV Sentinel — single-run orchestrator"
    )
    parser.add_argument(
        "--promoted", action="store_true",
        help="Also refresh and run the promoted composition (use for morning run)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print configuration and exit without running"
    )
    args = parser.parse_args()

    if args.dry_run:
        print("ShiftInnerV Sentinel — configuration")
        print(f"  Project dir  : {PROJECT_DIR}")
        print(f"  Anomaly dir  : {ANOMALY_DIR}")
        print(f"  Data dir     : {DATA_DIR}")
        print(f"  Log          : {LOG_PATH}")
        print(f"  Lock         : {LOCK_PATH}")
        print(f"  Seen file    : {SEEN_PATH}")
        print(f"  monitor.py   : {'✅' if os.path.exists(MONITOR_PY) else '❌ NOT FOUND'}")
        print(f"  main.py      : {'✅' if os.path.exists(MAIN_PY) else '❌ NOT FOUND'}")
        print(f"  promote.py   : {'✅' if os.path.exists(PROMOTE_PY) else '❌ NOT FOUND'}")
        pos_monitor_path = os.path.join(PROJECT_DIR, "shiftinner", "sensors", "position_monitor.py")
        regime_monitor_path = os.path.join(PROJECT_DIR, "shiftinner", "sensors", "regime_monitor.py")
        print(f"  position_monitor.py : {'✅' if os.path.exists(pos_monitor_path) else '❌ NOT FOUND'}")
        print(f"  regime_monitor.py   : {'✅' if os.path.exists(regime_monitor_path) else '❌ NOT FOUND'}  (Item 8)")
        print(f"  trial_ledger : {'✅' if os.path.exists(LEDGER_DB_PATH) else '⚠️  not created yet'}")
        print(f"  --promoted   : {args.promoted}")
        return

    log = setup_logging()

    # ── Lock ──────────────────────────────────────────────────────────────────
    if not acquire_lock(log):
        sys.exit(0)

    try:
        log.info("=" * 55)
        log.info(f"Sentinel run — {datetime.now().strftime('%Y-%m-%d %H:%M')}  "
                 f"promoted={args.promoted}")
        log.info("=" * 55)

        # ── Step 0: Market Regime Detection (Item 8) ──────────────────────────
        regime = run_regime_detection(log)

        # ── Step 1: Monitor pass ──────────────────────────────────────────────
        run_subprocess([sys.executable, MONITOR_PY], "monitor.py", log)

        # ── Step 1b: Position Revalidation (Item 13) ──────────────────────────
        run_position_revalidation(log)

        # ── Step 2: Process new anomaly yamls ─────────────────────────────────
        seen     = load_seen()
        all_yaml = sorted(Path(ANOMALY_DIR).glob("anomaly_*.yaml"))

        # Dedup by pair+lookback key, not full path — prevents reprocessing
        # yesterday's yaml when today's hasn't been written yet.
        # Key format: "anomaly_TICKER1_TICKER2_Nyr"  (strip trailing _DATE.yaml)
        def _yaml_key(path: str) -> str:
            stem = Path(path).stem          # e.g. anomaly_BAC_MS_3yr_2026-05-19
            parts = stem.rsplit("_", 1)     # split off the date
            return parts[0]                 # e.g. anomaly_BAC_MS_3yr

        seen_keys = {_yaml_key(p) for p in seen}
        new_yaml  = [str(f) for f in all_yaml if _yaml_key(str(f)) not in seen_keys]

        if new_yaml:
            log.info(f"New anomaly files: {len(new_yaml)}")
            for path in new_yaml:
                label = os.path.basename(path)
                ok    = run_subprocess(
                    [sys.executable, MAIN_PY, "--pairs", path],
                    f"main.py [{label}]",
                    log,
                )
                seen.add(_yaml_key(path))
                save_seen(seen)
                if not ok:
                    log.warning(f"main.py non-zero for {label} — marked seen, continuing.")
        else:
            log.info("No new anomaly files.")

        # ── Step 3: Promoted composition run (morning only) ───────────────────
        if args.promoted:
            log.info("Promoted run requested — refreshing promote.py...")
            run_subprocess([sys.executable, PROMOTE_PY, "--quiet"], "promote.py", log)

            promoted = latest_promoted()
            if promoted:
                log.info(f"Running promoted: {os.path.basename(promoted)}")
                run_subprocess(
                    [sys.executable, MAIN_PY, "--pairs", promoted],
                    f"main.py [promoted]",
                    log,
                )

                # ── Step 4: AI summary after promoted run ─────────────────
                summarize_py = os.path.join(PROJECT_DIR, "summarize.py")
                if os.path.exists(summarize_py):
                    log.info("Generating AI run summary...")
                    run_subprocess(
                        [sys.executable, summarize_py],
                        "summarize.py",
                        log,
                    )
                else:
                    log.warning("summarize.py not found — skipping summary.")
            else:
                log.warning("No promoted yaml found — skipping.")

        log.info("Sentinel run complete.")

    finally:
        release_lock()


if __name__ == "__main__":
    main()
