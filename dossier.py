#!/usr/bin/env python3
"""
ShiftInnerV — Pair Dossier

On-demand deep-dive report for a flagged pair.
Called after monitor.py surfaces an anomaly or main.py returns ACTIVE/MONITOR-NEAR.

Data stack (all free, no cloud strategy leakage):
  - Tiingo    : price history + EOD (clean, reliable)
  - yfinance  : fundamentals (P/E, leverage, cash flow, earnings date)
  - yfinance  : recent news headlines
  - SEC EDGAR : recent 8-K and 10-Q filings (material events)

Usage:
    python dossier.py TICKER1 TICKER2
    python dossier.py AAPL MSFT --lookback 90
    python dossier.py AAPL MSFT --save          # write markdown to report_dir
    python dossier.py AAPL MSFT --save --quiet  # save without terminal output

Env (loaded from ~/.shiftinnerv_env — same file as monitor.py):
    TIINGA_KEY          Tiingo API key
    REPORT_DIR          where to write saved dossiers (default: ~/Projects/ShiftInnerV_Data/reports)
"""

import os
import sys
import argparse
import textwrap
from datetime import datetime, timedelta

import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

try:
    import yfinance as yf
except ImportError:
    yf = None

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

TIINGO_KEY = os.getenv("TIINGA_KEY", "")
REPORT_DIR = os.path.expanduser(os.getenv("REPORT_DIR", "~/Projects/ShiftInnerV_Data/reports"))

TIINGO_HEADERS = {"Content-Type": "application/json"}
TIINGO_BASE    = "https://api.tiingo.com"
EDGAR_BASE     = "https://efts.sec.gov/LATEST/search-index"
EDGAR_RSS      = "https://www.sec.gov/cgi-bin/browse-edgar"


# ── Tiingo: price history ─────────────────────────────────────────────────────

def tiingo_prices(ticker: str, lookback_days: int = 90) -> pd.Series:
    """Return a daily Close price series from Tiingo."""
    if not TIINGO_KEY:
        return None
    start = (datetime.today() - timedelta(days=lookback_days + 10)).strftime("%Y-%m-%d")
    url   = f"{TIINGO_BASE}/tiingo/daily/{ticker}/prices"
    params = {
        "startDate": start,
        "token":     TIINGO_KEY,
        "format":    "json",
    }
    try:
        r = requests.get(url, params=params, headers=TIINGO_HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date").sort_index()
        col = "adjClose" if "adjClose" in df.columns else "close"
        return df[col].dropna().tail(lookback_days)
    except Exception as e:
        print(f"  Tiingo price fetch failed for {ticker}: {e}")
        return None


# ── Spread math ───────────────────────────────────────────────────────────────

def compute_spread(s1: pd.Series, s2: pd.Series) -> dict:
    """
    Align two price series, compute log-price spread, z-score, half-life proxy.
    Returns dict of metrics.
    """
    s1, s2 = s1.align(s2, join="inner")
    if len(s1) < 20:
        return {}

    log1 = np.log(s1)
    log2 = np.log(s2)

    # OLS hedge ratio
    from numpy.polynomial import polynomial as P
    coef = np.polyfit(log2, log1, 1)
    hedge_ratio = coef[0]

    spread = log1 - hedge_ratio * log2
    spread_mean = spread.mean()
    spread_std  = spread.std()
    current     = spread.iloc[-1]
    z_score     = (current - spread_mean) / spread_std if spread_std > 0 else 0.0

    # crude half-life via AR(1)
    lag      = spread.shift(1).dropna()
    delta    = spread.diff().dropna()
    lag, delta = lag.align(delta, join="inner")
    if len(lag) > 5:
        beta     = np.polyfit(lag, delta, 1)[0]
        half_life = -np.log(2) / beta if beta < 0 else float("nan")
    else:
        half_life = float("nan")

    return {
        "hedge_ratio":  round(hedge_ratio, 4),
        "spread_mean":  round(spread_mean, 6),
        "spread_std":   round(spread_std, 6),
        "current_spread": round(current, 6),
        "z_score":      round(z_score, 3),
        "half_life_days": round(half_life, 1) if not np.isnan(half_life) else "N/A",
        "n_bars":       len(spread),
        "spread_series": spread,
    }


# ── yfinance: fundamentals ────────────────────────────────────────────────────

def yf_fundamentals(ticker: str) -> dict:
    """Pull key fundamentals from yfinance."""
    if yf is None:
        return {}
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}

        def _get(*keys):
            for k in keys:
                v = info.get(k)
                if v is not None:
                    return v
            return "N/A"

        # next earnings
        cal = {}
        try:
            cal = t.calendar or {}
        except Exception:
            pass
        earnings_date = "N/A"
        if cal:
            ed = cal.get("Earnings Date") or cal.get("earningsDate")
            if ed is not None:
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)
                    if ed:
                        earnings_date = str(ed[0])[:10]
                else:
                    earnings_date = str(ed)[:10]

        return {
            "name":           _get("longName", "shortName"),
            "sector":         _get("sector"),
            "industry":       _get("industry"),
            "market_cap_b":   round(_get("marketCap") / 1e9, 2) if isinstance(_get("marketCap"), (int, float)) else "N/A",
            "pe_ratio":       _get("trailingPE", "forwardPE"),
            "debt_equity":    _get("debtToEquity"),
            "current_ratio":  _get("currentRatio"),
            "free_cashflow_b": round(_get("freeCashflow") / 1e9, 2) if isinstance(_get("freeCashflow"), (int, float)) else "N/A",
            "revenue_growth": _get("revenueGrowth"),
            "earnings_date":  earnings_date,
            "short_ratio":    _get("shortRatio"),
            "analyst_target": _get("targetMeanPrice"),
        }
    except Exception as e:
        return {"error": str(e)}


# ── yfinance: news ────────────────────────────────────────────────────────────

def yf_news(ticker: str, max_items: int = 5) -> list:
    """Fetch recent news headlines via yfinance."""
    if yf is None:
        return []
    try:
        t     = yf.Ticker(ticker)
        items = t.news or []
        out   = []
        for item in items[:max_items]:
            ct = item.get("content", {})
            title = ct.get("title") or item.get("title", "")
            pub   = ct.get("pubDate") or item.get("providerPublishTime", "")
            if pub and not isinstance(pub, str):
                try:
                    pub = datetime.fromtimestamp(pub).strftime("%Y-%m-%d")
                except Exception:
                    pub = str(pub)
            elif isinstance(pub, str) and "T" in pub:
                pub = pub[:10]
            src = ct.get("provider", {})
            if isinstance(src, dict):
                src = src.get("displayName", "")
            out.append({"date": pub, "title": title, "source": src})
        return out
    except Exception:
        return []


# ── SEC EDGAR: recent filings ─────────────────────────────────────────────────

def edgar_filings(ticker: str, max_items: int = 5) -> list:
    """
    Fetch recent 8-K and 10-Q filings from SEC EDGAR full-text search.
    Returns list of dicts with date, form, description, url.
    """
    try:
        url    = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q":            f'"{ticker}"',
            "dateRange":    "custom",
            "startdt":      (datetime.today() - timedelta(days=180)).strftime("%Y-%m-%d"),
            "enddt":        datetime.today().strftime("%Y-%m-%d"),
            "forms":        "8-K,10-Q",
            "_source":      "filing",
            "hits.hits.total.value": 1,
        }
        # Use EDGAR full-text search API
        search_url = "https://efts.sec.gov/LATEST/search-index"
        efts_url   = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
            f"&dateRange=custom"
            f"&startdt={(datetime.today()-timedelta(days=180)).strftime('%Y-%m-%d')}"
            f"&enddt={datetime.today().strftime('%Y-%m-%d')}"
            f"&forms=8-K,10-Q"
        )
        # Prefer the simpler EDGAR company search
        cik_url = f"https://data.sec.gov/submissions/CIK{ticker}.json"
        # Use the EDGAR company facts search instead — more reliable by ticker
        search = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q":         f'"{ticker}"',
                "forms":     "8-K,10-Q",
                "dateRange": "custom",
                "startdt":   (datetime.today() - timedelta(days=180)).strftime("%Y-%m-%d"),
                "enddt":     datetime.today().strftime("%Y-%m-%d"),
            },
            headers={"User-Agent": "ShiftInnerV research@localhost"},
            timeout=10,
        )
        search.raise_for_status()
        hits = search.json().get("hits", {}).get("hits", [])
        out  = []
        for h in hits[:max_items]:
            src  = h.get("_source", {})
            form = src.get("form_type", "")
            date = src.get("file_date", "")[:10]
            desc = src.get("display_date_filed", date)
            name = src.get("entity_name", ticker)
            accn = src.get("file_num", "") or src.get("accession_no", "")
            link = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type={form}&dateb=&owner=include&count=10"
            out.append({
                "date": date,
                "form": form,
                "entity": name,
                "url": link,
            })
        return out
    except Exception as e:
        return [{"error": str(e)}]


# ── Spark-line ASCII chart ────────────────────────────────────────────────────

def ascii_sparkline(series: pd.Series, width: int = 60) -> str:
    """Render a tiny ASCII spread chart."""
    vals = series.dropna().values
    if len(vals) < 2:
        return ""
    mn, mx = vals.min(), vals.max()
    rng    = mx - mn
    if rng == 0:
        return "─" * width
    step   = max(1, len(vals) // width)
    sampled = vals[::step][-width:]
    chars  = " ▁▂▃▄▅▆▇█"
    line   = ""
    for v in sampled:
        idx  = int((v - mn) / rng * (len(chars) - 1))
        line += chars[idx]
    return line


# ── Render dossier ────────────────────────────────────────────────────────────

def render_dossier(
    ticker1: str,
    ticker2: str,
    lookback_days: int = 90,
) -> str:
    ticker1 = ticker1.upper()
    ticker2 = ticker2.upper()
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    def h(text): lines.append(f"\n{'='*64}\n{text}\n{'='*64}")
    def s(text): lines.append(f"\n── {text} {'─'*(58-len(text))}")
    def p(text): lines.append(text)

    h(f"PAIR DOSSIER  {ticker1} / {ticker2}  [{now}]")

    # ── 1. Price & Spread ─────────────────────────────────────────────────────
    s("PRICE & SPREAD")
    p(f"Fetching {lookback_days}d price history from Tiingo...")

    prices1 = tiingo_prices(ticker1, lookback_days)
    prices2 = tiingo_prices(ticker2, lookback_days)

    if prices1 is None or prices2 is None:
        p("  ERROR: Could not fetch price data. Check TIINGA_KEY in ~/.shiftinnerv_env")
        spread_data = {}
    else:
        p(f"  {ticker1}: {len(prices1)} bars  last={prices1.iloc[-1]:.2f}")
        p(f"  {ticker2}: {len(prices2)} bars  last={prices2.iloc[-1]:.2f}")
        spread_data = compute_spread(prices1, prices2)

    if spread_data:
        z    = spread_data["z_score"]
        hl   = spread_data["half_life_days"]
        flag = ""
        if   abs(z) >= 3.0: flag = "  ⚠️  EXTREME — near stop-loss territory"
        elif abs(z) >= 2.0: flag = "  ✅  ENTRY ZONE (≥2σ)"
        elif abs(z) >= 1.5: flag = "  👀  WATCH (approaching entry)"
        else:                flag = "  — below entry threshold"

        p(f"\n  Hedge ratio     : {spread_data['hedge_ratio']}")
        p(f"  Spread mean     : {spread_data['spread_mean']}")
        p(f"  Spread σ        : {spread_data['spread_std']}")
        p(f"  Current spread  : {spread_data['current_spread']}")
        p(f"  Z-score         : {z:+.3f}{flag}")
        p(f"  Half-life (AR1) : {hl} days")
        p(f"  Bars used       : {spread_data['n_bars']}")

        spark = ascii_sparkline(spread_data["spread_series"])
        p(f"\n  Spread ({lookback_days}d):")
        p(f"  {spark}")
        p(f"  └{'─'*len(spark)}┘")
        p(f"   low{' '*(len(spark)-7)}high")

    # ── 2. Fundamentals ───────────────────────────────────────────────────────
    s("FUNDAMENTALS")
    for ticker in [ticker1, ticker2]:
        f = yf_fundamentals(ticker)
        p(f"\n  {ticker} — {f.get('name','')}")
        p(f"    Sector        : {f.get('sector','N/A')} / {f.get('industry','N/A')}")
        p(f"    Market Cap    : ${f.get('market_cap_b','N/A')}B")
        p(f"    P/E Ratio     : {f.get('pe_ratio','N/A')}")
        p(f"    Debt/Equity   : {f.get('debt_equity','N/A')}")
        p(f"    Current Ratio : {f.get('current_ratio','N/A')}")
        p(f"    Free Cash Flow: ${f.get('free_cashflow_b','N/A')}B")
        p(f"    Rev Growth    : {f.get('revenue_growth','N/A')}")
        p(f"    Short Ratio   : {f.get('short_ratio','N/A')}")
        p(f"    Analyst Target: ${f.get('analyst_target','N/A')}")
        p(f"    Next Earnings : {f.get('earnings_date','N/A')}  ← check before entry")

    # ── 3. Recent News ────────────────────────────────────────────────────────
    s("RECENT NEWS")
    for ticker in [ticker1, ticker2]:
        news = yf_news(ticker)
        p(f"\n  {ticker}:")
        if not news:
            p("    (no headlines retrieved)")
        for item in news:
            p(f"    [{item.get('date','')}] {item.get('title','')}  ({item.get('source','')})")

    # ── 4. SEC EDGAR Filings ──────────────────────────────────────────────────
    s("SEC EDGAR FILINGS (8-K / 10-Q — last 180 days)")
    for ticker in [ticker1, ticker2]:
        filings = edgar_filings(ticker)
        p(f"\n  {ticker}:")
        if not filings:
            p("    (no filings retrieved)")
        for f in filings:
            if "error" in f:
                p(f"    ERROR: {f['error']}")
            else:
                p(f"    [{f.get('date','')}] {f.get('form','')}  — {f.get('entity','')}")
                p(f"    → {f.get('url','')}")

    # ── 5. Trade Setup Summary ────────────────────────────────────────────────
    s("TRADE SETUP SUMMARY")
    if spread_data:
        z  = spread_data["z_score"]
        hl = spread_data["half_life_days"]
        p(f"\n  Current z-score  : {z:+.3f}")
        p(f"  Entry threshold  : ±2.0σ")
        p(f"  Exit threshold   : ±0.5σ")
        p(f"  Stop-loss        : ±3.0σ")
        if isinstance(hl, float):
            p(f"  Expected hold    : ~{hl:.0f} trading days (1× half-life)")
            p(f"  Position sizing  : scale DOWN if half-life > 60d")
        p(f"\n  Direction (if entering now):")
        if z > 0:
            p(f"    SHORT {ticker1} / LONG {ticker2}  (spread above mean)")
        elif z < 0:
            p(f"    LONG {ticker1} / SHORT {ticker2}  (spread below mean)")
        else:
            p(f"    Spread at mean — no directional edge")
        p(f"\n  ⚠️  Check earnings dates above before entry.")
        p(f"  ⚠️  Review any 8-K filings — recent material events may have broken the relationship.")
    else:
        p("  Spread data unavailable — cannot generate setup.")

    p(f"\n{'='*64}")
    p(f"END OF DOSSIER  {ticker1}/{ticker2}  generated {now}")
    p(f"{'='*64}\n")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV — Pair Dossier: deep-dive report on a flagged pair"
    )
    parser.add_argument("ticker1", type=str, help="First ticker (e.g. AAPL)")
    parser.add_argument("ticker2", type=str, help="Second ticker (e.g. MSFT)")
    parser.add_argument(
        "--lookback", type=int, default=90,
        help="Days of price history to analyse (default: 90)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save dossier as markdown to REPORT_DIR"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress terminal output (use with --save)"
    )
    args = parser.parse_args()

    if not TIINGO_KEY:
        print("ERROR: TIINGA_KEY not set in ~/.shiftinnerv_env")
        sys.exit(1)

    report = render_dossier(args.ticker1, args.ticker2, args.lookback)

    if not args.quiet:
        print(report)

    if args.save:
        os.makedirs(REPORT_DIR, exist_ok=True)
        t1   = args.ticker1.upper()
        t2   = args.ticker2.upper()
        date = datetime.now().strftime("%Y%m%d_%H%M")
        path = os.path.join(REPORT_DIR, f"dossier_{t1}_{t2}_{date}.md")
        with open(path, "w") as fh:
            fh.write(report)
        print(f"\n  Dossier saved → {path}")


if __name__ == "__main__":
    main()
