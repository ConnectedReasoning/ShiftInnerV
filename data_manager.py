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
