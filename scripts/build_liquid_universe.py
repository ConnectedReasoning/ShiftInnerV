#!/usr/bin/env python3
"""
ShiftInnerV — Liquid Universe Builder
======================================
Builds a composition yaml from a liquid stock universe for screening.

Universe options:
  --tier 1  S&P 500 + sector ETFs (~530 tickers, ~140K pairs, ~3 hrs at 8 workers)
  --tier 2  S&P 500 + NASDAQ extras + ETFs (~600 tickers, ~180K pairs, ~4 hrs)

The S&P 500 constituent list is fetched live from Wikipedia at runtime
(standard approach used by finance libraries). A hardcoded fallback is
used if Wikipedia is unreachable.

Usage:
    python scripts/build_liquid_universe.py              # download + generate
    python scripts/build_liquid_universe.py --check      # what's missing?
    python scripts/build_liquid_universe.py --generate-only
    python scripts/build_liquid_universe.py --tier 2

After running:
    python monitor.py --screen compositions/composition_tier1_*.yaml --workers 8
    (or let sentinel pick it up on next scheduled run)
"""

import os
import sys
import time
import argparse
import datetime
import itertools
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

# ── Paths ─────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).parent.parent   # scripts/ -> project root
DATA_DIR    = Path(os.getenv("DATA_STORAGE_PATH",
                             "~/projects/ShiftInnerV_Data")).expanduser()
COMP_DIR    = PROJECT_DIR / "compositions"


# ── S&P 500 fetch ─────────────────────────────────────────────────────────────

# Hardcoded fallback — current as of May 2026
# Kept as backup if Wikipedia is unreachable
SP500_FALLBACK = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB",
    "AKAM","ALB","ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN",
    "AMCR","AEE","AAL","AEP","AXP","AIG","AMT","AWK","AMP","AME","AMGN",
    "APH","ADI","ANSS","AON","APA","AAPL","AMAT","APTV","ACGL","ADM","ANET",
    "AJG","AIZ","T","ATO","ADSK","ADP","AZO","AVB","AVY","AXON","BKR","BALL",
    "BAC","BBWI","BAX","BDX","BRK-B","BBY","BIO","TECH","BIIB","BLK","BX",
    "BA","BCR","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","BG","CDNS","CZR",
    "CPT","CPB","COF","CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE",
    "CDW","CE","COR","CNC","CNX","CDAY","CF","CRL","SCHW","CHTR","CVX","CMG",
    "CB","CHD","CI","CINF","CTAS","CSCO","C","CFG","CLX","CME","CMS","KO",
    "CTSH","CL","CMCSA","CMA","CAG","COP","ED","STZ","CEG","COO","CPRT",
    "GLW","CPAY","CTVA","CSGP","COST","CTRA","CCI","CSX","CMI","CVS","DHI",
    "DHR","DRI","DVA","DAY","DE","DELL","DAL","DVN","DXCM","FANG","DLR",
    "DFS","DG","DLTR","D","DPZ","DOV","DOW","DTE","DUK","DD","EMN",
    "ETN","EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR","ENPH","ETR","EOG",
    "EPAM","EQT","EFX","EQIX","EQR","ESS","EL","ETSY","EV","EVRG","ES","EXC",
    "EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS",
    "FITB","FSLR","FE","FI","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX",
    "GRMN","IT","GE","GEHC","GEN","GNRC","GD","GIS","GM","GPC","GILD","GPN",
    "GL","GS","HAL","HIG","HAS","HCA","DOC","HSIC","HSY","HES","HPE","HLT",
    "HOLX","HD","HON","HRL","HST","HWM","HPQ","HUBB","HUM","HBAN","HII",
    "IBM","IEX","IDXX","ITW","ILMN","INCY","IR","PODD","INTC","ICE","IFF",
    "IP","IPG","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY",
    "J","JNJ","JCI","JPM","JNPR","K","KVUE","KDP","KEY","KEYS","KMB","KIM",
    "KMI","KLAC","KHC","KR","LH","LRCX","LW","LVS","LDOS","LEN","LIN","LYV",
    "LKQ","LMT","L","LOW","LULU","LYB","MTB","MRO","MPC","MKTX","MAR","MMC",
    "MLM","MAS","MA","MTCH","MKC","MCD","MCK","MDT","MRK","META","MET","MTD",
    "MGM","MCHP","MU","MSFT","MAA","MRNA","MHK","MOH","TAP","MDLZ","MPWR",
    "MNST","MCO","MS","MOS","MSI","MSCI","NDAQ","NTAP","NFLX","NEM","NWSA",
    "NWS","NEE","NKE","NI","NDSN","NSC","NTRS","NOC","NCLH","NRG","NUE",
    "NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL","OTIS",
    "PCAR","PKG","PLTR","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE","PCG",
    "PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU",
    "PEG","PTC","PSA","PHM","QRVO","PWR","QCOM","DGX","RL","RJF","RTX",
    "O","REG","REGN","RF","RSG","RMD","RVTY","ROK","ROL","ROP","ROST","RCL",
    "SPGI","CRM","SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS","SJM",
    "SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SMCI",
    "SYF","SNPS","SYY","TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY",
    "TFX","TER","TSLA","TXN","TXT","TMO","TJX","TSCO","TT","TDG","TRV",
    "TRMB","TFC","TYL","TSN","USB","UBER","UDR","ULTA","UNP","UAL","UPS",
    "URI","UNH","UHS","VLO","VTR","VLTO","VRSN","VRSK","VZ","VRTX","VICI",
    "V","VST","VMC","WRB","GWW","WAB","WBA","WMT","DIS","WBD","WM","WAT",
    "WEC","WFC","WELL","WST","WDC","WRK","WY","WHR","WMB","WTW","WYNN","XEL",
    "XYL","YUM","ZBRA","ZBH","ZTS",
]

# Sector + thematic ETFs to add on top of the stock universe
ETFS = [
    # Broad market
    "SPY","QQQ","IWM","DIA","VTI",
    # SPDR sectors (complete set)
    "XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLB","XLU","XLRE","XLC",
    # Thematic equity
    "SOXX","SMH","IBB","GDX","GDXJ","ITB","XRT","KRE","KBE","IAI",
    # Commodities + alternatives
    "GLD","IAU","SLV","USO","BNO","UNG","DBC","CORN","WEAT","SOYB",
    # Fixed income
    "TLT","IEF","SHY","HYG","LQD","AGG","BND","MBB",
    # International / EM
    "EEM","EWJ","EWZ","FXI","KWEB","ASHR","CQQQ","EWT","EWY",
    # Currency
    "UUP","UDN","FXE","FXB","FXC","FXF",
]

# NASDAQ extras not in S&P 500 (for tier 2)
NDX_EXTRA = [
    "MELI","ABNB","DDOG","CRWD","ZS","OKTA","TEAM","WDAY","SNOW","COIN",
    "RBLX","PLTR","HOOD","AFRM","SOFI","RIVN","LCID","CHWY","PINS","SNAP",
    "UBER","LYFT","DASH","SPOT","TWLO","NET","MDB","BILL","HUBS","VEEV",
]


def fetch_sp500_wikipedia() -> list[str]:
    """
    Fetch current S&P 500 tickers from Wikipedia.
    Returns list of ticker symbols, or empty list on failure.
    """
    try:
        print("  Fetching S&P 500 constituents from Wikipedia...", end=" ", flush=True)
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        df = tables[0]
        tickers = df["Symbol"].tolist()
        # Wikipedia uses dots for BRK.B, BF.B etc — convert to hyphens for yfinance
        tickers = [t.replace(".", "-") for t in tickers]
        print(f"{len(tickers)} tickers ✓")
        return tickers
    except Exception as exc:
        print(f"failed ({exc}) — using hardcoded fallback")
        return []


def build_universe(tier: int = 1) -> list[str]:
    """Build the full ticker universe for the given tier."""
    # Try live fetch first
    sp500 = fetch_sp500_wikipedia()

    if not sp500:
        print(f"  Using hardcoded fallback ({len(SP500_FALLBACK)} tickers)")
        sp500 = SP500_FALLBACK

    base = sp500 + ETFS
    if tier >= 2:
        base = base + NDX_EXTRA

    return list(dict.fromkeys(base))  # deduplicate, preserve order


# ── Download ──────────────────────────────────────────────────────────────────

def needs_download(ticker: str, stale_days: int = 1) -> tuple[bool, str]:
    path = DATA_DIR / f"{ticker.lower()}_daily.csv"
    if not path.exists():
        return True, "missing"
    age = (datetime.date.today() -
           datetime.date.fromtimestamp(path.stat().st_mtime)).days
    if age >= stale_days:
        return True, f"stale ({age}d)"
    return False, "fresh"


def download_ticker(ticker: str) -> tuple[bool, int]:
    try:
        df = yf.download(
            ticker,
            period="5y",
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if df.empty:
            return False, 0
        path = DATA_DIR / f"{ticker.lower()}_daily.csv"
        df.to_csv(path)
        return True, len(df)
    except Exception as exc:
        print(f"    FAIL {ticker}: {exc}")
        return False, 0


def download_universe(tickers: list[str], force: bool = False) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    to_download = []

    for ticker in tickers:
        needed, reason = needs_download(ticker)
        if force or needed:
            to_download.append((ticker, reason))
        else:
            results[ticker] = "fresh"

    print(f"\n{'─'*60}")
    print(f"Universe: {len(tickers)} tickers")
    print(f"Already fresh: {len(tickers) - len(to_download)}")
    print(f"To download:   {len(to_download)}")
    print(f"Estimated download time: ~{len(to_download) * 0.5 / 60:.0f} min")
    print(f"{'─'*60}\n")

    if not to_download:
        print("All data is fresh — nothing to download.")
        return results

    failed = []
    for i, (ticker, reason) in enumerate(to_download, 1):
        print(f"  [{i:>3}/{len(to_download)}]  {ticker:<8} ({reason}) ...", end=" ", flush=True)
        ok, rows = download_ticker(ticker)
        if ok:
            print(f"{rows} rows ✓")
            results[ticker] = "updated"
        else:
            print("FAILED ✗")
            results[ticker] = "failed"
            failed.append(ticker)
        time.sleep(0.4)  # gentle rate limiting

    print(f"\nDownload complete.")
    print(f"  Success: {len(to_download) - len(failed)}")
    print(f"  Failed:  {len(failed)}")
    if failed:
        print(f"  Failed tickers: {', '.join(failed)}")
    return results


# ── ADV filter ────────────────────────────────────────────────────────────────

def measure_adv(ticker: str, days: int = 63) -> float | None:
    """Average daily dollar volume in $M from local CSV data."""
    path = DATA_DIR / f"{ticker.lower()}_daily.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if "Close" not in df.columns or "Volume" not in df.columns:
            return None
        recent = df.tail(days)
        adv = (recent["Close"] * recent["Volume"]).mean()
        return float(adv / 1_000_000)
    except Exception:
        return None


# ── YAML generation ───────────────────────────────────────────────────────────

def generate_yaml(
    tickers: list[str],
    adv_threshold_m: float = 50.0,
    lookback_years: int = 3,
    output_path: Path | None = None,
    tier: int = 1,
) -> Path:
    etf_set = set(ETFS)

    print(f"\nValidating ADV (threshold: ${adv_threshold_m:.0f}M)...")
    passing, skipped = [], []
    for ticker in tickers:
        adv = measure_adv(ticker)
        if adv is None:
            skipped.append((ticker, "no data"))
        elif adv < adv_threshold_m:
            skipped.append((ticker, f"ADV ${adv:.0f}M < ${adv_threshold_m:.0f}M"))
        else:
            passing.append(ticker)

    print(f"  Passing: {len(passing)}")
    print(f"  Skipped: {len(skipped)}")
    if skipped[:5]:
        for t, r in skipped[:5]:
            print(f"    {t}: {r}")
        if len(skipped) > 5:
            print(f"    ... and {len(skipped)-5} more")

    n_pairs = len(passing) * (len(passing) - 1) // 2
    est_mins = n_pairs * 0.5 / 8 / 60
    today = datetime.date.today().strftime("%Y-%m-%d")

    print(f"\nGenerating yaml:")
    print(f"  Tickers: {len(passing)}")
    print(f"  Pairs:   {n_pairs:,}")
    print(f"  Est. screen time at 8 workers: ~{est_mins:.0f} min (~{est_mins/60:.1f} hrs)")

    pair_lines = []
    for t1, t2 in itertools.combinations(passing, 2):
        is_etf1 = t1 in etf_set
        is_etf2 = t2 in etf_set
        if is_etf1 and is_etf2:
            lb = 3
        elif not is_etf1 and not is_etf2:
            lb = lookback_years
        else:
            lb = 2   # mixed stock/ETF — shorter lookback

        pair_lines.append(
            f"- ticker1: {t1}\n"
            f"  ticker2: {t2}\n"
            f"  label: '{t1} vs {t2}'\n"
            f"  lookback_years: {lb}\n"
            f"  cointegrated: unknown\n"
        )

    fname = f"composition_tier{tier}_liquid_{today}.yaml"
    if output_path is None:
        output_path = COMP_DIR / fname

    header = f"""\
# ShiftInnerV — Liquid Universe Composition
# Tier {tier} — S&P 500 {'+ NASDAQ extras ' if tier >= 2 else ''}+ ETFs
# ADV filter: > ${adv_threshold_m:.0f}M
# Generated: {today}
# Tickers: {len(passing)}
# Pairs:   {n_pairs:,}
# Est. screen time: ~{est_mins:.0f} min at 8 workers (~{est_mins/60:.1f} hrs)
#
# Lookback: {lookback_years}yr stock/stock | 2yr stock/ETF | 3yr ETF/ETF
#
# Run:
#   python monitor.py --screen {output_path.name} --workers 8

pairs:
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header + "\n".join(pair_lines))

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nYAML written: {output_path}")
    print(f"File size: {size_mb:.1f} MB")
    return output_path


# ── Check ─────────────────────────────────────────────────────────────────────

def check_universe(tickers: list[str]) -> None:
    missing, stale, fresh = [], [], []
    for ticker in tickers:
        needed, reason = needs_download(ticker)
        if needed:
            (missing if reason == "missing" else stale).append(ticker)
        else:
            fresh.append(ticker)

    print(f"\nUniverse: {len(tickers)} tickers")
    print(f"  Fresh:   {len(fresh)}")
    print(f"  Stale:   {len(stale)}")
    print(f"  Missing: {len(missing)}")
    if missing:
        print(f"\nMissing ({len(missing)}):")
        for i in range(0, len(missing), 10):
            print(f"  {' '.join(missing[i:i+10])}")
    n_pairs = len(tickers) * (len(tickers) - 1) // 2
    est = n_pairs * 0.5 / 8 / 60
    print(f"\nPairs if all downloaded: {n_pairs:,} (~{est:.0f} min at 8 workers)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build liquid universe and generate ShiftInnerV composition yaml"
    )
    parser.add_argument("--tier", type=int, default=1, choices=[1, 2],
        help="1=S&P500+ETFs (~530), 2=adds NASDAQ extras (~560)")
    parser.add_argument("--adv", type=float, default=50.0,
        help="Min average daily dollar volume in $M (default: 50)")
    parser.add_argument("--lookback", type=int, default=3,
        help="Lookback years for stock/stock pairs (default: 3)")
    parser.add_argument("--generate-only", action="store_true",
        help="Skip downloads, generate yaml from existing data only")
    parser.add_argument("--download-only", action="store_true",
        help="Download data only, skip yaml generation")
    parser.add_argument("--check", action="store_true",
        help="Show data status without downloading or generating")
    parser.add_argument("--force", action="store_true",
        help="Re-download all tickers even if data is fresh")
    parser.add_argument("--output", type=str, default=None,
        help="Custom output yaml path")
    args = parser.parse_args()

    print(f"\nShiftInnerV — Liquid Universe Builder")
    print(f"{'═'*50}")
    print(f"  Tier:     {args.tier}")
    print(f"  ADV min:  ${args.adv:.0f}M")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Comp dir: {COMP_DIR}")

    tickers = build_universe(args.tier)
    n = len(tickers)
    n_pairs = n * (n - 1) // 2
    print(f"  Tickers:  {n}")
    print(f"  Pairs:    {n_pairs:,}")

    if args.check:
        check_universe(tickers)
        return

    if not args.generate_only:
        download_universe(tickers, force=args.force)

    if not args.download_only:
        out = Path(args.output) if args.output else None
        yaml_path = generate_yaml(
            tickers,
            adv_threshold_m=args.adv,
            lookback_years=args.lookback,
            output_path=out,
            tier=args.tier,
        )
        print(f"\nNext step:")
        print(f"  python monitor.py --screen {yaml_path.name} --workers 8")


if __name__ == "__main__":
    main()
