#!/usr/bin/env python3
"""
ShiftInnerV — Sentinel

Autonomous orchestrator. Start once, runs continuously.

What it does:
  1. Runs monitor.py on a schedule (default every 30 minutes)
  2. Watches compositions/anomalies/ for new yaml files
  3. Auto-triggers main.py on each new anomaly yaml
  4. Once per day (default 06:00) runs main.py on the latest promoted composition
  5. Logs all activity to DATA_STORAGE_PATH/sentinel.log

Usage:
    python sentinel.py                        # start with defaults
    python sentinel.py --interval 900         # monitor every 15 minutes
    python sentinel.py --daily-run 07:00      # daily promoted run at 7am
    python sentinel.py --no-daily             # skip daily promoted run
    python sentinel.py --dry-run              # print config and exit

Stop:
    Ctrl+C  (graceful shutdown — waits for current job to finish)

Env (from ~/.shiftinnerv_env):
    DATA_STORAGE_PATH   base data dir (default ~/Projects/ShiftInnerV_Data)
    REPORT_DIR          report output dir
    TIINGA_key          Tiingo API key
"""

import os
import sys
import time
import signal
import logging
import argparse
import subprocess
import threading
from datetime import datetime, timedelta
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

MONITOR_PY = os.path.join(PROJECT_DIR, "monitor.py")
MAIN_PY    = os.path.join(PROJECT_DIR, "main.py")
PROMOTE_PY = os.path.join(PROJECT_DIR, "promote.py")

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_INTERVAL   = 1800   # seconds between monitor runs (30 min)
DEFAULT_DAILY_TIME = "06:00"
MAX_CONCURRENT     = 1      # one agent job at a time (Ollama is single-threaded)


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(log_path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger("sentinel")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    # File handler — persistent log
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run_subprocess(cmd: list, label: str, log: logging.Logger) -> bool:
    """Run a subprocess, stream output to log, return True on success."""
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
            if line:
                log.info(f"  │  {line}")
        proc.wait()
        elapsed = (datetime.now() - start).seconds
        ok = proc.returncode == 0
        status = "OK" if ok else f"EXIT {proc.returncode}"
        log.info(f"END    {label} — {status} ({elapsed}s)")
        return ok
    except Exception as e:
        log.error(f"ERROR  {label} — {e}")
        return False


# ── Anomaly watcher ───────────────────────────────────────────────────────────

class AnomalyWatcher:
    """
    Tracks which anomaly yaml files have already been processed.
    Persists seen-set to DATA_DIR/sentinel_seen.txt so restarts don't reprocess.
    """

    def __init__(self, anomaly_dir: str, data_dir: str, log: logging.Logger):
        self.anomaly_dir = anomaly_dir
        self.seen_path   = os.path.join(data_dir, "sentinel_seen.txt")
        self.log         = log
        self.seen        = self._load_seen()
        os.makedirs(anomaly_dir, exist_ok=True)

    def _load_seen(self) -> set:
        if os.path.exists(self.seen_path):
            with open(self.seen_path) as f:
                return set(line.strip() for line in f if line.strip())
        return set()

    def _save_seen(self):
        with open(self.seen_path, "w") as f:
            f.write("\n".join(sorted(self.seen)))

    def new_files(self) -> list:
        """Return list of anomaly yaml paths not yet processed."""
        all_files = sorted(Path(self.anomaly_dir).glob("anomaly_*.yaml"))
        new = [str(f) for f in all_files if str(f) not in self.seen]
        return new

    def mark_seen(self, path: str):
        self.seen.add(path)
        self._save_seen()


# ── Daily run tracker ─────────────────────────────────────────────────────────

class DailyRun:
    """Tracks whether the daily promoted-composition run has fired today."""

    def __init__(self, run_time_str: str, data_dir: str, log: logging.Logger):
        self.h, self.m = map(int, run_time_str.split(":"))
        self.state_path = os.path.join(data_dir, "sentinel_daily.txt")
        self.log        = log

    def _last_run_date(self) -> str:
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return f.read().strip()
        return ""

    def _save_today(self):
        with open(self.state_path, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d"))

    def due(self) -> bool:
        now = datetime.now()
        if now.hour != self.h or now.minute != self.m:
            return False
        return self._last_run_date() != now.strftime("%Y-%m-%d")

    def mark_done(self):
        self._save_today()
        self.log.info("Daily run marked complete for today.")


# ── Get latest promoted yaml ──────────────────────────────────────────────────

def latest_promoted(compositions_dir: str) -> str | None:
    files = sorted(Path(compositions_dir).glob("promoted_*.yaml"), reverse=True)
    return str(files[0]) if files else None


# ── Main sentinel loop ────────────────────────────────────────────────────────

class Sentinel:

    def __init__(self, args):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.log         = setup_logging(LOG_PATH)
        self.interval    = args.interval
        self.no_daily    = args.no_daily
        self.daily       = (DailyRun(args.daily_run, DATA_DIR, self.log)
                            if not args.no_daily else None)
        self.watcher     = AnomalyWatcher(ANOMALY_DIR, DATA_DIR, self.log)
        self.job_lock    = threading.Lock()
        self.shutdown    = threading.Event()
        self.next_monitor = datetime.now()  # run immediately on start

        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        self.log.info("Shutdown signal received — finishing current job then exiting.")
        self.shutdown.set()

    # ── Monitor run ───────────────────────────────────────────────────────────

    def run_monitor(self):
        self.log.info(f"Monitor pass — scanning {COMPOSITIONS_DIR}")
        run_subprocess(
            [sys.executable, MONITOR_PY],
            "monitor.py",
            self.log,
        )
        self.next_monitor = datetime.now() + timedelta(seconds=self.interval)
        self.log.info(f"Next monitor pass at {self.next_monitor.strftime('%H:%M:%S')}")

    # ── Process new anomalies ─────────────────────────────────────────────────

    def process_anomalies(self):
        new = self.watcher.new_files()
        if not new:
            return
        self.log.info(f"New anomaly file(s) detected: {len(new)}")
        for path in new:
            if self.shutdown.is_set():
                break
            label = os.path.basename(path)
            with self.job_lock:
                ok = run_subprocess(
                    [sys.executable, MAIN_PY, "--pairs", path],
                    f"main.py [{label}]",
                    self.log,
                )
            self.watcher.mark_seen(path)
            if not ok:
                self.log.warning(f"main.py returned non-zero for {label} — marked seen, continuing.")

    # ── Daily promoted run ────────────────────────────────────────────────────

    def run_daily(self):
        if self.no_daily or self.daily is None:
            return
        if not self.daily.due():
            return

        self.log.info("Daily promoted-composition run starting...")

        # First regenerate promote to pick up latest screening data
        run_subprocess(
            [sys.executable, PROMOTE_PY],
            "promote.py (daily refresh)",
            self.log,
        )

        promoted = latest_promoted(COMPOSITIONS_DIR)
        if not promoted:
            self.log.warning("No promoted yaml found — skipping daily run.")
            self.daily.mark_done()
            return

        self.log.info(f"Daily run target: {os.path.basename(promoted)}")
        with self.job_lock:
            run_subprocess(
                [sys.executable, MAIN_PY, "--pairs", promoted],
                f"main.py [daily — {os.path.basename(promoted)}]",
                self.log,
            )
        self.daily.mark_done()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        self.log.info("=" * 60)
        self.log.info("ShiftInnerV Sentinel — starting")
        self.log.info(f"  Monitor interval : {self.interval}s ({self.interval//60}m)")
        self.log.info(f"  Daily run        : {'disabled' if self.no_daily else args.daily_run}")
        self.log.info(f"  Anomaly dir      : {ANOMALY_DIR}")
        self.log.info(f"  Log              : {LOG_PATH}")
        self.log.info("=" * 60)

        while not self.shutdown.is_set():
            now = datetime.now()

            # ── Monitor pass ──────────────────────────────────────────────────
            if now >= self.next_monitor:
                self.run_monitor()

            # ── New anomalies ─────────────────────────────────────────────────
            self.process_anomalies()

            # ── Daily run ─────────────────────────────────────────────────────
            self.run_daily()

            # ── Sleep 60s then check again ────────────────────────────────────
            self.shutdown.wait(timeout=60)

        self.log.info("Sentinel shut down cleanly.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV Sentinel — autonomous orchestrator"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Seconds between monitor passes (default: {DEFAULT_INTERVAL})"
    )
    parser.add_argument(
        "--daily-run", type=str, default=DEFAULT_DAILY_TIME, metavar="HH:MM",
        help=f"Time to run daily promoted composition (default: {DEFAULT_DAILY_TIME})"
    )
    parser.add_argument(
        "--no-daily", action="store_true",
        help="Disable the daily promoted-composition run"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print configuration and exit without running"
    )
    global args
    args = parser.parse_args()

    if args.dry_run:
        print("ShiftInnerV Sentinel — configuration")
        print(f"  Monitor interval : {args.interval}s ({args.interval//60}m)")
        print(f"  Daily run        : {'disabled' if args.no_daily else args.daily_run}")
        print(f"  Project dir      : {PROJECT_DIR}")
        print(f"  Anomaly dir      : {ANOMALY_DIR}")
        print(f"  Data dir         : {DATA_DIR}")
        print(f"  Log              : {LOG_PATH}")
        print(f"  monitor.py       : {'✅' if os.path.exists(MONITOR_PY) else '❌ NOT FOUND'}")
        print(f"  main.py          : {'✅' if os.path.exists(MAIN_PY) else '❌ NOT FOUND'}")
        print(f"  promote.py       : {'✅' if os.path.exists(PROMOTE_PY) else '❌ NOT FOUND'}")
        return

    sentinel = Sentinel(args)
    sentinel.run()


if __name__ == "__main__":
    main()
