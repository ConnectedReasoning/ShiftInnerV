import os
import time
import datetime
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None


def ensure_data(tickers: list, data_dir: str, stale_days: int = 1) -> dict:
    """
    For each ticker, check if a fresh CSV exists in data_dir.
    Pull from yfinance if missing or stale (older than stale_days).

    Parameters
    ----------
    tickers    : list of ticker symbols needed for this run
    data_dir   : path to data storage directory
    stale_days : re-download if CSV is older than this many days (default 1)

    Returns
    -------
    dict mapping ticker -> status: 'fresh' | 'updated' | 'failed' | 'skipped'
    """
    if yf is None:
        print("  WARNING: yfinance not installed. Run: pip install yfinance")
        return {t: "skipped" for t in tickers}

    os.makedirs(data_dir, exist_ok=True)
    results = {}
    today = datetime.date.today()

    for symbol in tickers:
        csv_path = os.path.join(data_dir, f"{symbol.lower()}_daily.csv")
        needs_pull = False

        if not os.path.exists(csv_path):
            needs_pull = True
            reason = "missing"
        else:
            modified = datetime.date.fromtimestamp(os.path.getmtime(csv_path))
            age_days = (today - modified).days
            if age_days >= stale_days:
                needs_pull = True
                reason = f"stale ({age_days}d old)"
            else:
                reason = "fresh"

        if not needs_pull:
            print(f"  {symbol}: {reason}")
            results[symbol] = "fresh"
            continue

        print(f"  {symbol}: {reason} — downloading...")
        try:
            data = yf.download(
                symbol,
                period="5y",
                auto_adjust=True,
                progress=False,
                multi_level_index=False
            )
            if data.empty:
                print(f"  {symbol}: WARNING — no data returned")
                results[symbol] = "failed"
            else:
                data.to_csv(csv_path)
                print(f"  {symbol}: saved {len(data)} rows")
                results[symbol] = "updated"
        except Exception as e:
            print(f"  {symbol}: FAILED — {e}")
            results[symbol] = "failed"

        time.sleep(0.5)  # gentle rate limiting

    return results


def tickers_from_pairs(pairs: list) -> list:
    """Extract unique tickers from the pairs composition."""
    tickers = set()
    for pair in pairs:
        tickers.add(pair["ticker1"])
        tickers.add(pair["ticker2"])
    return sorted(tickers)


def check_data_staleness(
    tickers: list,
    data_dir: str,
    staleness_hours: int = 26,
    logger=None,
) -> dict:
    """
    Check if all required price files are fresh.

    Returns a dict: {ticker -> freshness_status}
    freshness_status: 'fresh' | 'stale' | 'missing'

    Does NOT download; only checks existing files.
    Use after ensure_data() to validate data is recent enough.

    Parameters
    ----------
    tickers : list
        Ticker symbols to check
    data_dir : str
        Directory containing ticker_daily.csv files
    staleness_hours : int
        Maximum age in hours before data is considered stale (default 26)
    logger : logging.Logger
        Optional logger for warnings

    Returns
    -------
    dict mapping ticker -> 'fresh' | 'stale' | 'missing'
    """
    staleness_threshold = datetime.timedelta(hours=staleness_hours)
    now = datetime.datetime.now()
    results = {}

    for ticker in tickers:
        csv_path = os.path.join(data_dir, f"{ticker.lower()}_daily.csv")

        if not os.path.exists(csv_path):
            results[ticker] = "missing"
            continue

        mtime_timestamp = os.path.getmtime(csv_path)
        mtime = datetime.datetime.fromtimestamp(mtime_timestamp)
        age = now - mtime

        if age > staleness_threshold:
            age_hours = age.total_seconds() / 3600
            results[ticker] = "stale"
            msg = (
                f"Data stale: {ticker} last updated {age_hours:.1f}h ago "
                f"(threshold: {staleness_hours}h)"
            )
            if logger:
                logger.warning(msg)
            else:
                print(f"  WARNING: {msg}")
        else:
            age_hours = age.total_seconds() / 3600
            results[ticker] = "fresh"
            if logger:
                logger.debug(f"Data fresh: {ticker} ({age_hours:.1f}h old)")

    return results


def get_stalest_ticker(staleness_results: dict) -> tuple:
    """
    Find the first non-fresh ticker in the results.

    Returns (ticker, status) for the first stale/missing entry,
    or (None, 'all_fresh') if everything is fresh.
    """
    stale_tickers = [t for t, s in staleness_results.items() if s != "fresh"]
    if not stale_tickers:
        return None, "all_fresh"
    return stale_tickers[0], staleness_results[stale_tickers[0]]


# ── Universe helpers (migrated from pair_sourcer) ─────────────────────────────

def load_universe(universe_path: str):
    """Load ticker universe from a YAML config file.

    Returns the dict under the top-level `universe:` key. Each entry is a
    category name → list of tickers.
    """
    import yaml
    with open(universe_path) as f:
        data = yaml.safe_load(f)
    return data["universe"]


def flatten_universe(universe) -> list:
    """Flatten a universe dict (category → tickers) into a single sorted,
    deduplicated ticker list."""
    tickers = []
    for category_tickers in universe.values():
        tickers.extend(category_tickers)
    return sorted(set(tickers))
