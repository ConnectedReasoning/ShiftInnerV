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
    DATA_DIR   base data dir (default ~/Projects/ShiftInnerV/data)
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
DATA_DIR         = os.path.join(PROJECT_DIR, "data")
COMPOSITIONS_DIR = os.path.join(PROJECT_DIR, "compositions")
ANOMALY_DIR      = os.path.join(COMPOSITIONS_DIR, "anomalies")
LOG_PATH         = os.path.join(DATA_DIR, "sentinel.log")
LOCK_PATH        = os.path.join(DATA_DIR, "sentinel.lock")

SEEN_PATH       = os.path.join(DATA_DIR, "sentinel_seen.txt")
LEDGER_DB_PATH  = os.path.join(DATA_DIR, "trial_ledger.db")


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
    open_positions_count: int = 0
    vix_level: float = 0.0

    # Skew signals
    skew_signals: list = field(default_factory=list)

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
    """Retained for backwards compatibility; unused in skew strategy."""
    return None


def latest_sourced() -> str | None:
    """Retained for backwards compatibility; unused in skew strategy."""
    return None


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

        print("\n── Market Regime Detection ─────────────────────────────────────")
        detector = RegimeDetector(data_dir=DATA_DIR, logger=log)

        # Skew strategy has no pairs ledger — pass empty list for correlation check
        regime = detector.detect_regime(open_positions=[], logger=log)

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
            f"Open=0 | Correlated={len(regime.correlated_pairs)}"
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


# ── STRATEGY: Skew Signal Generation ─────────────────────────────────────────

class SkewSignalStrategy(Strategy):
    """
    Compute rolling z-score signals from skew_ledger and store in ctx.
    Runs after SkewSnapshotStrategy so today's snapshot is already persisted.
    Non-fatal: signal failure never blocks downstream strategies.
    """

    def name(self) -> str:
        return "Skew Signal Generation"

    def execute(self, ctx: "SentinelContext", log: logging.Logger) -> bool:
        try:
            from shiftinnerv.sensors.skew_signal import SkewSignalGenerator
            from shiftinnerv.services.data_manager import load_universe, flatten_universe

            universe_path = ctx.universe_path or os.path.join(PROJECT_DIR, "universe.yaml")
            if not os.path.exists(universe_path):
                log.warning("[skew_signal] Universe file not found — skipping.")
                return True

            universe = load_universe(universe_path)
            tickers  = flatten_universe(universe)

            if not tickers:
                log.info("[skew_signal] No tickers — skipping.")
                return True

            print(f"  Generating skew signals for {len(tickers)} tickers...")
            gen     = SkewSignalGenerator(db_path=LEDGER_DB_PATH, logger=log)
            signals = gen.generate(tickers)

            # Store actionable signals in context for briefing
            ctx.skew_signals = signals

            shorts = [s for s in signals if s.signal == "SHORT"]
            longs  = [s for s in signals if s.signal == "LONG"]
            insuf  = [s for s in signals if s.signal == "INSUFFICIENT_DATA"]

            print(f"  Skew signals: {len(shorts)} SHORT  {len(longs)} LONG  "
                  f"{len(signals) - len(shorts) - len(longs) - len(insuf)} HOLD  "
                  f"{len(insuf)} warming up")

            if shorts or longs:
                print("  Actionable signals:")
                for s in sorted(shorts + longs, key=lambda x: abs(x.z_score or 0), reverse=True):
                    direction = "⬇ SHORT" if s.signal == "SHORT" else "⬆ LONG"
                    print(f"    {s.ticker:<6s}  {direction}  z={s.z_score:+.2f}  "
                          f"norm_skew={s.norm_skew:.3f}  ({s.history_days}d history)")

            log.info(f"[skew_signal] {len(shorts)} SHORT  {len(longs)} LONG  "
                     f"{len(insuf)} insufficient data")

        except Exception as e:
            log.warning(f"[skew_signal] Failed: {e}")
            print(f"  ⚠️  Skew signal error: {e}")
            ctx.skew_signals = []

        return True  # Non-fatal always


# ── STRATEGY: Pair Sourcing ──────────────────────────────────────────────────

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
# ── Dead strategy classes removed (PairSourcing, Monitor, PositionRevalidation,
#    AnomalyProcessing, PromotedComposition, AISummary) — pairs trading paradigm.
#    Current chain: RegimeDetection → SkewSnapshot → SkewSignal → Briefing.

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

            # Skew strategy has no open positions ledger yet — leave at 0.
            # When position tracking is added for skew trades, populate ctx.open_positions_count here.

            # Generate and print briefing
            try:
                # Look up company names only for actionable signals (cheap — usually 0-5)
                actionable_signals = [
                    s for s in getattr(ctx, "skew_signals", [])
                    if s.signal in ("SHORT", "LONG")
                ]
                ticker_name_map = {}
                if actionable_signals:
                    try:
                        from shiftinnerv.services.ticker_names import get_ticker_names
                        ticker_name_map = get_ticker_names(
                            [s.ticker for s in actionable_signals],
                            db_path=LEDGER_DB_PATH,
                            logger=log,
                        )
                    except Exception as e:
                        log.debug(f"[briefing] ticker name lookup failed: {e}")

                briefing = generate_sentinel_briefing(
                    regime_state=ctx.regime_state,
                    regime_vix=ctx.vix_level,
                    regime_multiplier=ctx.position_size_multiplier,
                    sourced_pairs=[],          # unused — kept for signature compatibility
                    screening_counts={},
                    verdicts={},
                    rejected_pairs=[],
                    open_positions=ctx.open_positions_count,
                    universe_name=ctx.universe_name,
                    skew_signals=getattr(ctx, "skew_signals", []),
                    ticker_names=ticker_name_map,
                )

                # Print to console
                print(briefing)

                # Write to file

                report_dir = os.path.join(DATA_DIR, "reports")
                os.makedirs(report_dir, exist_ok=True)
                print("THIS IS WHERE THE REPORT IS WRITTEN! == ", report_dir)
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
    print(f"  Data dir     : {DATA_DIR}")
    print(f"  Log          : {LOG_PATH}")
    print(f"  Lock         : {LOCK_PATH}")
    print(f"  Seen file    : {SEEN_PATH}")
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
            .add(SkewSignalStrategy())
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
