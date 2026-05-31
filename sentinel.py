#!/usr/bin/env python3
"""
ShiftInnerV — Sentinel (Strategy Pattern Refactor)

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
import yaml
import sqlite3
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from dotenv import load_dotenv

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

MONITOR_PY = os.path.join(PROJECT_DIR, "shiftinnerv", "pipelines", "monitor.py")
MAIN_PY    = os.path.join(PROJECT_DIR, "main.py")
PROMOTE_PY = os.path.join(PROJECT_DIR, "promote.py")

SEEN_PATH       = os.path.join(DATA_DIR, "sentinel_seen.txt")
LEDGER_DB_PATH  = os.path.join(DATA_DIR, "trial_ledger.db")

# ── Startup path verification ─────────────────────────────────────────────────
_REQUIRED_PATHS = {
    "MONITOR_PY": MONITOR_PY,
    "MAIN_PY":    MAIN_PY,
    "PROMOTE_PY": PROMOTE_PY,
}
_missing = [f"{name} → {path}" for name, path in _REQUIRED_PATHS.items()
            if not os.path.exists(path)]
if _missing:
    raise FileNotFoundError(
        "sentinel.py: subprocess target(s) not found — check path constants:\n"
        + "\n".join(f"  {m}" for m in _missing)
    )


# ── SentinelContext: Single source of truth for run state ────────────────────

@dataclass
class SentinelContext:
    """
    Shared state flowing through all strategies.
    Each strategy reads from and writes to ctx.
    """
    # Run metadata
    run_timestamp: datetime = field(default_factory=datetime.now)
    promoted_flag: bool = False
    
    # Regime detection
    regime: object = None                # RegimeSnapshot from regime detector
    regime_state: str = "NORMAL"
    position_size_multiplier: float = 1.0
    
    # Pair sourcing
    sourced_composition_path: str | None = None
    universe_path: str | None = None   # set from --universe arg; None = default universe.yaml
    universe_name: str = "FX"          # human-readable name derived from universe filename
    
    # Monitor results
    monitor_success: bool = False
    
    # Position revalidation
    revalidation_results: list = field(default_factory=list)
    auto_close_count: int = 0
    monitor_count: int = 0
    hold_count: int = 0
    
    # Anomaly processing
    seen_anomalies: set = field(default_factory=set)
    new_anomalies: list = field(default_factory=list)
    processed_anomalies_count: int = 0
    
    # Promoted composition
    promoted_path: str | None = None
    promoted_executed: bool = False
    
    # Briefing data
    sourced_pairs: list = field(default_factory=list)
    verdicts: dict = field(default_factory=lambda: {'active': 0, 'monitor': 0, 'reject': 0})
    rejected_pairs: list = field(default_factory=list)
    open_positions_count: int = 0
    vix_level: float = 0.0
    
    # Error tracking
    crisis_halt: bool = False


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
        try:
            pid = int(open(LOCK_PATH).read().strip())
            os.kill(pid, 0)   # signal 0 = check existence only
            log.warning(f"Sentinel already running (PID {pid}) — exiting.")
            return False
        except (ValueError, OSError):
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


def latest_promoted() -> str | None:
    files = sorted(Path(COMPOSITIONS_DIR).glob("promoted_*.yaml"), reverse=True)
    return str(files[0]) if files else None


def latest_sourced() -> str | None:
    """Get the most recent sourced_YYYYMMDD.yaml file."""
    files = sorted(Path(COMPOSITIONS_DIR).glob("sourced_*.yaml"), reverse=True)
    return str(files[0]) if files else None


# ── STRATEGY: Base class ──────────────────────────────────────────────────────

class Strategy(ABC):
    """Base strategy class. Each strategy is independently testable."""
    
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name for logging."""
        pass
    
    @abstractmethod
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        """
        Execute the strategy. Return True to continue, False to abort the run.
        Strategy must read from and write to ctx.
        Non-fatal strategies should return True even on internal errors.
        """
        pass


# ── STRATEGY: Regime Detection ────────────────────────────────────────────────

class RegimeDetectionStrategy(Strategy):
    """
    Detect market regime. Sets ctx.regime, ctx.regime_state, ctx.position_size_multiplier.
    On CRISIS: halts new entries and exits cleanly (not aborted, just early termination).
    """
    
    def name(self) -> str:
        return "Market Regime Detection"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        from shiftinnerv.sensors.regime_monitor import RegimeDetector, RegimeState
        from shiftinnerv.services.trial_ledger import load_open_trials
        
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
        
        # Store in context
        ctx.regime = regime
        ctx.regime_state = regime.state.value
        ctx.position_size_multiplier = regime.position_size_multiplier
        ctx.vix_level = regime.vix_level
        
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
        
        # Propagate to downstream subprocesses via env vars
        os.environ["CURRENT_REGIME_STATE"]    = regime.state.value
        os.environ["POSITION_SIZE_MULTIPLIER"] = str(regime.position_size_multiplier)
        
        # ── CRISIS: hard halt on new entries ──────────────────────────────────────
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
            ctx.crisis_halt = True
            return False  # Abort execution chain, but main() will see crisis_halt flag
        
        return True


# ── STRATEGY: Skew Snapshot ──────────────────────────────────────────────────

class SkewSnapshotStrategy(Strategy):
    """
    Daily put skew snapshot for the Dow universe (or any skew-enabled universe).

    Runs after regime detection. Fetches yfinance options chains, computes
    put skew (OTM IV / ATM IV) for each ticker, normalises against SPY, and
    persists to the skew_ledger table in trial_ledger.db.

    Non-fatal: skew failure never blocks downstream strategies.
    Skipped silently if the universe file has no skew-relevant tickers.
    """

    def name(self) -> str:
        return "Skew Snapshot"

    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        try:
            from shiftinnerv.sensors.skew_monitor import SkewMonitor
            import yaml

            universe_path = ctx.universe_path or os.path.join(PROJECT_DIR, "universe.yaml")
            if not os.path.exists(universe_path):
                log.warning("[skew] Universe file not found — skipping skew snapshot.")
                print("  ⚠️  Universe file not found — skipping skew snapshot.")
                return True

            # Load tickers from universe yaml
            with open(universe_path) as f:
                udata = yaml.safe_load(f)

            universe_block = udata.get("universe", {})
            tickers = []
            for category_tickers in universe_block.values():
                if isinstance(category_tickers, list):
                    tickers.extend(category_tickers)
            tickers = sorted(set(tickers))

            if not tickers:
                log.info("[skew] No tickers in universe — skipping skew snapshot.")
                print("  No tickers found in universe — skipping.")
                return True

            print(f"  Snapshotting put skew for {len(tickers)} tickers...")
            log.info(f"[skew] Starting snapshot for {len(tickers)} tickers.")

            monitor = SkewMonitor(db_path=LEDGER_DB_PATH, logger=log)
            results = monitor.snapshot(tickers=tickers)

            # Summarise results
            ok      = [r for r in results if r.raw_skew is not None]
            failed  = [r for r in results if r.fetch_error is not None]
            flagged = [r for r in ok if r.low_liquidity]

            # Top 5 by norm_skew (stress signals)
            ranked = sorted(
                [r for r in ok if r.norm_skew is not None],
                key=lambda r: r.norm_skew,
                reverse=True,
            )

            print(f"  ✓ {len(ok)} succeeded  |  {len(failed)} failed  |  {len(flagged)} low-liquidity")
            if ranked:
                print("  Top skew (idiosyncratic stress):")
                for r in ranked[:5]:
                    liq = " ⚠️ low-vol" if r.low_liquidity else ""
                    print(f"    {r.ticker:<6s}  norm_skew={r.norm_skew:.3f}  raw={r.raw_skew:.3f}{liq}")

            if failed:
                for r in failed[:3]:
                    log.warning(f"[skew] {r.ticker}: {r.fetch_error}")
                if len(failed) > 3:
                    log.warning(f"[skew] ... and {len(failed) - 3} more failures.")

            log.info(
                f"[skew] Snapshot complete — {len(ok)}/{len(results)} OK, "
                f"{len(failed)} failed, {len(flagged)} low-liquidity."
            )

        except Exception as e:
            log.warning(f"[skew] Snapshot failed unexpectedly: {e}")
            print(f"  ⚠️  Skew snapshot error: {e}")

        return True  # Non-fatal always


# ── STRATEGY: Pair Sourcing ──────────────────────────────────────────────────

class PairSourcingStrategy(Strategy):
    """
    Generate intelligent pair composition for today's screening.
    Non-fatal: returns True even if sourcing fails.
    """
    
    def name(self) -> str:
        return "Intelligent Pair Sourcing"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        from shiftinnerv.pipelines.pair_sourcer import source_pairs
        
        print("\n── Intelligent Pair Sourcing ────────────────────────────────────")
        
        universe_path = ctx.universe_path or os.path.join(PROJECT_DIR, "universe.yaml")
        if not os.path.exists(universe_path):
            log.warning(f"[pair_sourcing] universe file not found: {universe_path}")
            print("  universe file not found — skipping pair sourcing")
            return True
        
        output_path = os.path.join(
            COMPOSITIONS_DIR,
            f"sourced_{date.today().strftime('%Y%m%d')}.yaml"
        )
        
        # Skip if already generated today
        if os.path.exists(output_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(output_path))
            if mtime.date() == date.today():
                log.info(f"[pair_sourcing] Using existing sourced composition: {output_path}")
                print(f"  ✓ Using existing sourced composition (generated {mtime.strftime('%H:%M')})")
                ctx.sourced_composition_path = output_path
                return True
        
        log.info("[pair_sourcing] START")
        print("  Generating intelligent pairs (correlation clustering + decay detection)...")

        # Load universe config for universe-specific parameters (e.g. lookback_years)
        try:
            with open(universe_path) as _uf:
                _universe_cfg = yaml.safe_load(_uf) or {}
        except Exception:
            _universe_cfg = {}

        try:
            source_pairs(
                universe_path=universe_path,
                output_path=output_path,
                top_n=100,
                lookback_years=_universe_cfg.get("lookback_years", 3),
                min_correlation=0.3,
                n_clusters=15,
                data_dir=DATA_DIR,
            )
            log.info(f"[pair_sourcing] OK — {output_path}")
            print(f"  ✓ Generated {output_path}")
            ctx.sourced_composition_path = output_path
            return True
        
        except Exception as e:
            import traceback
            log.error(f"[pair_sourcing] FAIL — {e}\n{traceback.format_exc()}")
            print(f"  ✗ Pair sourcing failed: {e}")
            print(f"    Reason: {type(e).__name__}: {e}")
            print(f"    Continuing without sourced composition")
            return True  # Non-fatal


# ── STRATEGY: Monitor ────────────────────────────────────────────────────────

class MonitorStrategy(Strategy):
    """
    Run monitor.py to screen pairs and detect anomalies.
    Can run either sourced composition or default anomaly detection.
    """
    
    def name(self) -> str:
        return "Anomaly Detection (Monitor)"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        if ctx.sourced_composition_path:
            # Run anomaly detection (correlation decay) against the compositions dir.
            # The sourced YAML lives in COMPOSITIONS_DIR, so the default monitor path
            # will pick it up, detect correlation anomalies, and write anomaly YAMLs
            # for the AnomalyProcessingStrategy to consume.
            # --min-score 45 additionally flags SOLID/STRONG/PRIME pairs as signals
            # so the gate evaluator pipeline runs even when no decay episode is active.
            # NOTE: --screen is a diagnostic tool only — it scores but does NOT write
            # anomaly YAMLs and therefore produces nothing for the agent pipeline.
            log.info(f"[monitor] Running anomaly detection on compositions dir (sourced: {ctx.sourced_composition_path})")
            print(f"\n── Monitor (Sourced Pairs) ──────────────────────────────────────")

            # MIN_SCORE_FOR_SIGNAL: pairs scoring >= this are forwarded to the agent.
            # Default 25 — deliberately permissive for research/candidate surfacing.
            # Raise to 45 (SOLID) or 60 (STRONG) for production capital deployment.
            # Override: export MIN_SCORE_FOR_SIGNAL=45
            #
            # Research mode env vars (set in ~/.shiftinnerv_env or shell):
            #   JOHANSEN_LAG_STRATEGY=standard  (k=1 only, more passes)
            #   JOHANSEN_DET_ORDER=best          (try det_order 0 and 1, use better)
            #   GATE1_CI_OVERRIDE=90             (90% CI instead of 95%)
            min_score_signal = float(os.getenv("MIN_SCORE_FOR_SIGNAL", "25"))

            ok = run_subprocess(
                [sys.executable, MONITOR_PY,
                 "--compositions", COMPOSITIONS_DIR,
                 "--workers", "10",
                 "--min-score", str(min_score_signal)],
                "monitor.py (sourced pairs)",
                log
            )
        else:
            print(f"\n── Monitor (Default Anomalies) ──────────────────────────────────")
            ok = run_subprocess([sys.executable, MONITOR_PY], "monitor.py", log)
        
        ctx.monitor_success = ok
        return ok  # Abort if monitor fails


# ── STRATEGY: Position Revalidation ──────────────────────────────────────────

class PositionRevalidationStrategy(Strategy):
    """
    Revalidate all open positions for SNR deterioration and mean drift.
    Non-fatal: continues even if revalidation fails.
    """
    
    def name(self) -> str:
        return "Position Revalidation"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        from shiftinnerv.sensors.position_monitor import revalidate_open_positions
        from shiftinnerv.services.trial_ledger import record_position_revalidation
        
        print("\n── Position Revalidation ────────────────────────────────────────")
        
        if not os.path.exists(LEDGER_DB_PATH):
            log.warning(f"[position_revalidation] Trial ledger not found: {LEDGER_DB_PATH}")
            print("  Trial ledger not found — skipping revalidation.")
            return True
        
        results = revalidate_open_positions(
            db_path=LEDGER_DB_PATH,
            data_dir=DATA_DIR,
            logger=log,
        )
        
        if not results:
            print("  No open positions to revalidate.")
            return True
        
        ctx.revalidation_results = results
        ctx.auto_close_count = sum(1 for r in results if r.decision == "AUTO_CLOSE")
        ctx.monitor_count    = sum(1 for r in results if r.decision == "MONITOR")
        ctx.hold_count       = sum(1 for r in results if r.decision == "HOLD")
        error_count          = sum(1 for r in results if r.error is not None)
        
        print(f"  Revalidated {len(results)} open position(s)")
        if ctx.hold_count       > 0: print(f"  ✓  {ctx.hold_count} position(s) to HOLD")
        if ctx.monitor_count    > 0: print(f"  👀 {ctx.monitor_count} position(s) flagged for MONITOR")
        if ctx.auto_close_count > 0: print(f"  ⚠️  {ctx.auto_close_count} position(s) triggered AUTO_CLOSE")
        if error_count          > 0: print(f"  ✗  {error_count} position(s) skipped (data/error)")
        
        log.info(
            f"[position_revalidation] {len(results)} checked — "
            f"{ctx.hold_count} HOLD | {ctx.monitor_count} MONITOR | "
            f"{ctx.auto_close_count} AUTO_CLOSE | {error_count} errors"
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
        
        return True  # Non-fatal


# ── STRATEGY: Anomaly Processing ────────────────────────────────────────────

class AnomalyProcessingStrategy(Strategy):
    """
    Load and process new anomaly yamls through main.py.
    Tracks which anomalies have been seen.

    By default only processes anomaly YAMLs generated TODAY, which keeps
    daily runs fast (3-4 pairs ~2-3 min) regardless of stale file accumulation.

    Override with env var:
        ANOMALY_REPROCESS_DAYS=3   — process files from the last N days
        ANOMALY_REPROCESS_DAYS=0   — process ALL files (legacy behaviour)
    """

    def name(self) -> str:
        return "Anomaly Processing"

    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        print("\n── Anomaly Processing ──────────────────────────────────────────")

        ctx.seen_anomalies = load_seen()
        all_yaml = sorted(Path(ANOMALY_DIR).glob("anomaly_*.yaml"))

        # ── Date filter ───────────────────────────────────────────────────────
        # Only process files generated within the last N days.
        # Default: today only (ANOMALY_REPROCESS_DAYS=1).
        # Set to 0 to disable the filter entirely.
        _reprocess_days = int(os.getenv("ANOMALY_REPROCESS_DAYS", "1"))
        if _reprocess_days > 0:
            _cutoff = datetime.now() - timedelta(days=_reprocess_days - 1)
            _cutoff_str = _cutoff.strftime("%Y-%m-%d")
            all_yaml = [
                f for f in all_yaml
                if _cutoff_str <= f.stem.rsplit("_", 1)[-1] <= datetime.now().strftime("%Y-%m-%d")
            ]

        # Dedup by pair+lookback key, not full path
        def _yaml_key(path: str) -> str:
            stem = Path(path).stem
            parts = stem.rsplit("_", 1)
            return parts[0]

        seen_keys = {_yaml_key(p) for p in ctx.seen_anomalies}
        ctx.new_anomalies = [str(f) for f in all_yaml if _yaml_key(str(f)) not in seen_keys]
        
        if ctx.new_anomalies:
            log.info(f"New anomaly files: {len(ctx.new_anomalies)}")
            print(f"  Found {len(ctx.new_anomalies)} new anomaly file(s)")
            for path in ctx.new_anomalies:
                label = os.path.basename(path)
                ok    = run_subprocess(
                    [sys.executable, MAIN_PY, "--pairs", path],
                    f"main.py [{label}]",
                    log,
                )
                ctx.seen_anomalies.add(_yaml_key(path))
                save_seen(ctx.seen_anomalies)
                if not ok:
                    log.warning(f"main.py non-zero for {label} — marked seen, continuing.")
                else:
                    ctx.processed_anomalies_count += 1
        else:
            print("  No new anomaly files.")
            log.info("No new anomaly files.")
        
        return True  # Non-fatal


# ── STRATEGY: Promoted Composition ──────────────────────────────────────────

class PromotedCompositionStrategy(Strategy):
    """
    Run promoted composition (morning only).
    Non-fatal: continues even if promoted run fails.
    """
    
    def name(self) -> str:
        return "Promoted Composition"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        if not ctx.promoted_flag:
            return True
        
        print("\n── Promoted Composition ─────────────────────────────────────────")
        
        log.info("Promoted run requested — refreshing promote.py...")
        print("  Refreshing promote.py...")
        run_subprocess([sys.executable, PROMOTE_PY, "--quiet"], "promote.py", log)
        
        promoted = latest_promoted()
        if promoted:
            log.info(f"Running promoted: {os.path.basename(promoted)}")
            print(f"  Running promoted: {os.path.basename(promoted)}")
            ok = run_subprocess(
                [sys.executable, MAIN_PY, "--pairs", promoted],
                f"main.py [promoted]",
                log,
            )
            ctx.promoted_path = promoted
            ctx.promoted_executed = ok
            return True  # Non-fatal
        else:
            log.warning("No promoted yaml found — skipping.")
            print("  No promoted yaml found — skipping.")
            return True


# ── STRATEGY: AI Summary ────────────────────────────────────────────────────

class AISummaryStrategy(Strategy):
    """
    Generate AI summary after promoted run.
    Non-fatal: continues even if summary generation fails.
    """
    
    def name(self) -> str:
        return "AI Summary Generation"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        if not ctx.promoted_flag or not ctx.promoted_executed:
            return True
        
        summarize_py = os.path.join(PROJECT_DIR, "shiftinnerv", "pipelines", "summarize.py")
        if not os.path.exists(summarize_py):
            log.warning("summarize.py not found — skipping summary.")
            print("  summarize.py not found — skipping summary.")
            return True
        
        print("  Generating AI run summary...")
        log.info("Generating AI run summary...")
        run_subprocess(
            [sys.executable, summarize_py],
            "summarize.py",
            log,
        )
        return True  # Non-fatal


# ── STRATEGY: Briefing Generation ──────────────────────────────────────────

class BriefingStrategy(Strategy):
    """
    Generate end-of-run briefing summary.
    Non-fatal: always returns True.
    """
    
    def name(self) -> str:
        return "Briefing Generation"
    
    def execute(self, ctx: SentinelContext, log: logging.Logger) -> bool:
        print("\n── End-of-Run Briefing ─────────────────────────────────────────")
        
        try:
            from shiftinnerv.reporting.briefing_generator import generate_sentinel_briefing
            
            # Parse sourced_composition.yaml for top pairs
            sourced_yaml = latest_sourced()
            if sourced_yaml and os.path.exists(sourced_yaml):
                try:
                    with open(sourced_yaml) as f:
                        lines = f.readlines()
                        
                        # Extract top 5 pairs with scores from header comments
                        # Ticker pattern includes '=' for FX tickers like EURUSD=X
                        for line in lines:
                            if line.startswith('#') and '/' in line and 'score=' in line:
                                match = re.search(
                                    r'#\s+([\w.=-]+)\s+/\s+([\w.=-]+)\s+score=([\d.]+)\s+corr=([\d.]+)',
                                    line
                                )
                                if match:
                                    ticker1, ticker2, score, corr = match.groups()
                                    if len(ctx.sourced_pairs) < 5:
                                        ctx.sourced_pairs.append({
                                            'ticker1': ticker1,
                                            'ticker2': ticker2,
                                            'score': float(score),
                                            'corr': float(corr)
                                        })
                            if len(ctx.sourced_pairs) >= 5:
                                break
                except Exception as e:
                    log.debug(f"Could not parse sourced yaml: {e}")
            
            # Load open positions count
            ctx.open_positions_count = 0
            try:
                if os.path.exists(LEDGER_DB_PATH):
                    conn = sqlite3.connect(LEDGER_DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM trial_ledger WHERE is_closed=0")
                    ctx.open_positions_count = cursor.fetchone()[0]
                    conn.close()
            except Exception as e:
                log.debug(f"Could not count open positions: {e}")
            
            # Generate and print briefing
            try:
                briefing = generate_sentinel_briefing(
                    regime_state=ctx.regime_state,
                    regime_vix=ctx.vix_level,
                    regime_multiplier=ctx.position_size_multiplier,
                    sourced_pairs=ctx.sourced_pairs,
                    screening_counts={},
                    verdicts=ctx.verdicts,
                    rejected_pairs=ctx.rejected_pairs,
                    open_positions=ctx.open_positions_count,
                    universe_name=ctx.universe_name,
                )
                
                # Print to console
                print(briefing)
                
                # Write to file
                report_dir = os.path.expanduser(
                    os.getenv("REPORT_DIR", os.path.join(DATA_DIR, "reports"))
                )
                os.makedirs(report_dir, exist_ok=True)
                
                briefing_path = os.path.join(
                    report_dir,
                    f"briefing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                )
                with open(briefing_path, "w") as f:
                    f.write(briefing)
                
                log.info(f"[briefing] Generated and saved: {briefing_path}")
                print(f"  ✓ Briefing saved: {briefing_path}")
            except Exception as e:
                log.warning(f"[briefing] Could not generate: {e}")
                print(f"  ⚠️  Briefing generation failed: {e}")
            
        except Exception as e:
            log.warning(f"[briefing] Unexpected error: {e}")
            print(f"  ✗ Briefing strategy error: {e}")
        
        return True  # Non-fatal


# ── SentinelOrchestrator ──────────────────────────────────────────────────────

class SentinelOrchestrator:
    """
    Orchestrates strategy execution.
    Runs strategies in sequence. Stops on fatal failure.
    """
    
    def __init__(self, log: logging.Logger):
        self.log = log
        self.strategies: list[Strategy] = []
    
    def add(self, strategy: Strategy) -> "SentinelOrchestrator":
        """Add a strategy to the chain."""
        self.strategies.append(strategy)
        return self
    
    def run(self, ctx: SentinelContext) -> bool:
        """
        Run all strategies in sequence.
        Returns True if all completed successfully, False if fatal failure.
        
        Special case: if crisis_halt is set, we exit cleanly (not a failure).
        """
        for strategy in self.strategies:
            print(f"\n── {strategy.name()} ──────────────────────────────────────────────────────")
            try:
                ok = strategy.execute(ctx, self.log)
                if not ok:
                    if ctx.crisis_halt:
                        # Crisis halt is expected — not a failure
                        self.log.info(f"Crisis halt triggered — clean exit.")
                        return True
                    else:
                        # Fatal failure
                        self.log.error(f"Strategy '{strategy.name()}' failed — aborting run.")
                        return False
            except Exception as e:
                self.log.exception(f"Strategy '{strategy.name()}' raised exception: {e}")
                return False
        
        return True


# ── Dry-run config ──────────────────────────────────────────────────────────

def print_config():
    print("ShiftInnerV Sentinel — configuration")
    print(f"  Project dir  : {PROJECT_DIR}")
    print(f"  Compositions : {COMPOSITIONS_DIR}")
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
    parser.add_argument(
        "--universe", type=str, default=None,
        help=(
            "Path to an alternate universe YAML file. "
            "Defaults to universe.yaml in the project root. "
            "Compositions are written to a subdirectory named after the "
            "universe file stem (e.g. --universe universe_bond_equity.yaml "
            "writes to compositions/bond_equity/). "
            "This keeps FX and bond/equity runs fully isolated."
        )
    )
    args = parser.parse_args()

    # ── Resolve universe-specific paths ──────────────────────────────────────
    # When --universe is supplied, compositions and anomalies land in their own
    # subdirectory so FX and bond/equity runs never cross-contaminate.
    if args.universe:
        _universe_stem = os.path.splitext(
            os.path.basename(args.universe)
        )[0].replace("universe_", "")
        _compositions_dir = os.path.join(
            PROJECT_DIR, "compositions", _universe_stem
        )
        os.makedirs(_compositions_dir, exist_ok=True)
        os.makedirs(os.path.join(_compositions_dir, "anomalies"), exist_ok=True)
        # Monkey-patch the module-level constants so all strategies pick them up
        import sentinel as _self
        _self.COMPOSITIONS_DIR = _compositions_dir
        _self.ANOMALY_DIR      = os.path.join(_compositions_dir, "anomalies")
        COMPOSITIONS_DIR       = _compositions_dir
        ANOMALY_DIR            = os.path.join(_compositions_dir, "anomalies")
        _universe_path         = os.path.abspath(args.universe)
    else:
        _universe_path = os.path.join(PROJECT_DIR, "universe.yaml")

    if args.dry_run:
        print_config()
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

        # ── Create context ────────────────────────────────────────────────────
        ctx = SentinelContext(
            promoted_flag=args.promoted,
            universe_path=_universe_path,
            universe_name=_universe_stem.replace("_", " ").title() if args.universe else "FX Currencies",
        )

        # ── Build and run strategy chain ──────────────────────────────────────
        success = (
            SentinelOrchestrator(log)
            .add(RegimeDetectionStrategy())
            .add(SkewSnapshotStrategy())
            .add(PairSourcingStrategy())
            .add(MonitorStrategy())
            .add(PositionRevalidationStrategy())
            .add(AnomalyProcessingStrategy())
            .add(PromotedCompositionStrategy())
            .add(AISummaryStrategy())
            .add(BriefingStrategy())
            .run(ctx)
        )

        log.info("Sentinel run complete.")
        
        if ctx.crisis_halt:
            log.info("Crisis halt triggered — exiting cleanly.")
            sys.exit(0)
        
        if not success:
            log.error("Sentinel run failed — check logs.")
            sys.exit(1)

    finally:
        release_lock()


if __name__ == "__main__":
    main()
