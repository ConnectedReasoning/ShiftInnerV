#!/usr/bin/env python3
"""
ShiftInnerV — Liquid Universe Builder
======================================
Builds an intra-sector composition yaml from the S&P 500 universe.

Intra-sector mode (default): only generates pairs where both tickers
share the same GICS sector. Reduces ~123K cross-sector pairs to ~8-10K
economically coherent pairs. Mean reversion is faster and more reliable
within sector (shared macro drivers, similar cost structures).

GICS Sectors (11):
  Communication Services, Consumer Discretionary, Consumer Staples,
  Energy, Financials, Health Care, Industrials, Information Technology,
  Materials, Real Estate, Utilities

Usage:
    python scripts/build_liquid_universe.py              # intra-sector (default)
    python scripts/build_liquid_universe.py --all-pairs  # all combinations
    python scripts/build_liquid_universe.py --check      # data status
    python scripts/build_liquid_universe.py --generate-only
    python scripts/build_liquid_universe.py --tier 2     # adds sub-industry pairs

Runtime: ~45-60 min first download, ~30s to generate yaml
"""

import os
import sys
import time
import argparse
import datetime
import itertools
from pathlib import Path
from collections import defaultdict

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

# ── Paths ─────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR    = Path(os.getenv("DATA_STORAGE_PATH",
                             "~/projects/ShiftInnerV_Data")).expanduser()
COMP_DIR    = PROJECT_DIR / "compositions"

# ── Sector ETFs (always included, paired cross-sector intentionally) ──────────

SECTOR_ETFS = {
    # Broad market
    "SPY": "Broad Market", "QQQ": "Broad Market", "IWM": "Broad Market",
    "DIA": "Broad Market", "VTI": "Broad Market",
    # SPDR sectors
    "XLK": "Information Technology", "XLF": "Financials",
    "XLE": "Energy",                  "XLV": "Health Care",
    "XLI": "Industrials",             "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",        "XLB": "Materials",
    "XLU": "Utilities",               "XLRE": "Real Estate",
    "XLC": "Communication Services",
    # Thematic
    "SOXX": "Information Technology", "SMH": "Information Technology",
    "IBB": "Health Care",             "GDX": "Materials",
    "GDXJ": "Materials",              "KRE": "Financials",
    "KBE": "Financials",              "IAI": "Financials",
    "ITB": "Consumer Discretionary",  "XRT": "Consumer Discretionary",
    # Commodities + alts
    "GLD": "Commodities", "IAU": "Commodities", "SLV": "Commodities",
    "USO": "Commodities", "BNO": "Commodities", "UNG": "Commodities",
    "DBC": "Commodities", "CORN": "Commodities", "WEAT": "Commodities",
    # Fixed income
    "TLT": "Fixed Income", "IEF": "Fixed Income", "SHY": "Fixed Income",
    "HYG": "Fixed Income", "LQD": "Fixed Income", "AGG": "Fixed Income",
    # International
    "EEM": "International", "EWJ": "International", "EWZ": "International",
    "FXI": "International", "KWEB": "International", "ASHR": "International",
    "CQQQ": "International", "EWT": "International", "EWY": "International",
    # Currency
    "UUP": "Currency", "UDN": "Currency", "FXE": "Currency",
    "FXB": "Currency",  "FXC": "Currency",  "FXF": "Currency",
}

# ── Hardcoded S&P 500 fallback with GICS sectors ─────────────────────────────
# Format: "TICKER:SECTOR_CODE"
# Sector codes: IT=Information Technology, FIN=Financials, HC=Health Care,
#   CD=Consumer Discretionary, CS=Consumer Staples, IND=Industrials,
#   COM=Communication Services, EN=Energy, MAT=Materials, RE=Real Estate, UT=Utilities

SP500_WITH_SECTORS = [
    # Information Technology
    ("AAPL","IT"),("MSFT","IT"),("NVDA","IT"),("AVGO","IT"),("AMD","IT"),
    ("ORCL","IT"),("CRM","IT"),("ACN","IT"),("IBM","IT"),("CSCO","IT"),
    ("TXN","IT"),("ADBE","IT"),("QCOM","IT"),("AMAT","IT"),("NOW","IT"),
    ("INTU","IT"),("ADI","IT"),("LRCX","IT"),("MU","IT"),("KLAC","IT"),
    ("PANW","IT"),("CDNS","IT"),("SNPS","IT"),("ANSS","IT"),("APH","IT"),
    ("TEL","IT"),("FTNT","IT"),("NXPI","IT"),("ON","IT"),("MCHP","IT"),
    ("SWKS","IT"),("QRVO","IT"),("HPQ","IT"),("HPE","IT"),("WDC","IT"),
    ("STX","IT"),("NTAP","IT"),("CDW","IT"),("PTC","IT"),("TDY","IT"),
    ("KEYS","IT"),("JNPR","IT"),("GLW","IT"),("FFIV","IT"),("ZBRA","IT"),
    ("TER","IT"),("ENPH","IT"),("MPWR","IT"),("SMCI","IT"),
    # Financials
    ("JPM","FIN"),("BAC","FIN"),("WFC","FIN"),("GS","FIN"),("MS","FIN"),
    ("BRK-B","FIN"),("V","FIN"),("MA","FIN"),("AXP","FIN"),("BLK","FIN"),
    ("SCHW","FIN"),("SPGI","FIN"),("MCO","FIN"),("ICE","FIN"),("CME","FIN"),
    ("CB","FIN"),("MMC","FIN"),("AON","FIN"),("AJG","FIN"),("TRV","FIN"),
    ("AFL","FIN"),("ALL","FIN"),("MET","FIN"),("PRU","FIN"),("AIG","FIN"),
    ("PGR","FIN"),("CI","FIN"),("HUM","FIN"),("ELV","FIN"),("CNC","FIN"),
    ("USB","FIN"),("PNC","FIN"),("TFC","FIN"),("MTB","FIN"),("CFG","FIN"),
    ("HBAN","FIN"),("RF","FIN"),("KEY","FIN"),("FI","FIN"),("FIS","FIN"),
    ("PYPL","FIN"),("COF","FIN"),("DFS","FIN"),("SYF","FIN"),("FITB","FIN"),
    ("IVZ","FIN"),("BEN","FIN"),("RJF","FIN"),("MKTX","FIN"),("WRB","FIN"),
    ("LNC","FIN"),("GL","FIN"),("AIZ","FIN"),("ACGL","FIN"),
    # Health Care
    ("JNJ","HC"),("LLY","HC"),("ABBV","HC"),("MRK","HC"),("TMO","HC"),
    ("ABT","HC"),("DHR","HC"),("AMGN","HC"),("GILD","HC"),("REGN","HC"),
    ("VRTX","HC"),("ISRG","HC"),("SYK","HC"),("MDT","HC"),("BSX","HC"),
    ("CVS","HC"),("EW","HC"),("BIIB","HC"),("IDXX","HC"),("DXCM","HC"),
    ("IQV","HC"),("ZBH","HC"),("BAX","HC"),("BDX","HC"),("HOLX","HC"),
    ("ALGN","HC"),("PFE","HC"),("HCA","HC"),("UNH","HC"),("MOH","HC"),
    ("HUM","HC"),("INCY","HC"),("MRNA","HC"),("ILMN","HC"),("PODD","HC"),
    ("RVTY","HC"),("MTD","HC"),("A","HC"),("TECH","HC"),("HSIC","HC"),
    ("GEHC","HC"),("SOLV","HC"),("CRL","HC"),
    # Consumer Discretionary
    ("AMZN","CD"),("TSLA","CD"),("HD","CD"),("MCD","CD"),("NKE","CD"),
    ("LOW","CD"),("SBUX","CD"),("BKNG","CD"),("TJX","CD"),("ORLY","CD"),
    ("MAR","CD"),("GM","CD"),("F","CD"),("ROST","CD"),("YUM","CD"),
    ("DHI","CD"),("LEN","CD"),("PHM","CD"),("NVR","CD"),("TOL","CD"),
    ("CMG","CD"),("DPZ","CD"),("HLT","CD"),("WYNN","CD"),("LVS","CD"),
    ("MGM","CD"),("NCLH","CD"),("RCL","CD"),("CCL","CD"),("EXPE","CD"),
    ("ABNB","CD"),("EBAY","CD"),("ETSY","CD"),("BBY","CD"),("TGT","CD"),
    ("KMX","CD"),("AZO","CD"),("GPC","CD"),("LKQ","CD"),("APTV","CD"),
    ("RL","CD"),("BBWI","CD"),("TPR","CD"),("PVH","CD"),("HAS","CD"),
    ("MTCH","CD"),("LYV","CD"),
    # Consumer Staples
    ("PG","CS"),("KO","CS"),("PEP","CS"),("COST","CS"),("WMT","CS"),
    ("PM","CS"),("MO","CS"),("MDLZ","CS"),("KHC","CS"),("GIS","CS"),
    ("K","CS"),("CAG","CS"),("HRL","CS"),("MKC","CS"),("CPB","CS"),
    ("SJM","CS"),("TAP","CS"),("BG","CS"),("ADM","CS"),("MOS","CS"),
    ("NTR","CS"),("KMB","CS"),("CL","CS"),("CHD","CS"),("EL","CS"),
    ("COTY","CS"),("KVUE","CS"),("KDP","CS"),("MNST","CS"),
    # Industrials
    ("RTX","IND"),("HON","IND"),("BA","IND"),("CAT","IND"),("DE","IND"),
    ("GE","IND"),("MMM","IND"),("LMT","IND"),("NOC","IND"),("GD","IND"),
    ("HII","IND"),("LHX","IND"),("TDG","IND"),("TXT","IND"),("HWM","IND"),
    ("ETN","IND"),("EMR","IND"),("ITW","IND"),("PH","IND"),("ROK","IND"),
    ("AME","IND"),("IR","IND"),("CARR","IND"),("OTIS","IND"),("XYL","IND"),
    ("PCAR","IND"),("CMI","IND"),("CSX","IND"),("NSC","IND"),("UNP","IND"),
    ("FDX","IND"),("UPS","IND"),("DAL","IND"),("UAL","IND"),("LUV","IND"),
    ("AAL","IND"),("EXPD","IND"),("JBHT","IND"),("ODFL","IND"),("CPRT","IND"),
    ("RSG","IND"),("WM","IND"),("FAST","IND"),("GWW","IND"),("MSI","IND"),
    ("CTAS","IND"),("ROL","IND"),("LDOS","IND"),("SAIC","IND"),("J","IND"),
    ("VRSK","IND"),("DNB","IND"),("NDSN","IND"),("ITT","IND"),
    # Communication Services
    ("GOOGL","COM"),("GOOG","COM"),("META","COM"),("NFLX","COM"),
    ("CMCSA","COM"),("DIS","COM"),("WBD","COM"),("PARA","COM"),
    ("FOXA","COM"),("FOX","COM"),("NWSA","COM"),("NWS","COM"),
    ("TMUS","COM"),("VZ","COM"),("T","COM"),("LUMN","COM"),
    ("OMC","COM"),("IPG","COM"),("EA","COM"),("TTWO","COM"),
    ("MTCH","COM"),("ZM","COM"),("SNAP","COM"),("PINS","COM"),
    # Energy
    ("XOM","EN"),("CVX","EN"),("COP","EN"),("EOG","EN"),("SLB","EN"),
    ("MPC","EN"),("PSX","EN"),("VLO","EN"),("OXY","EN"),("DVN","EN"),
    ("FANG","EN"),("HES","EN"),("HAL","EN"),("BKR","EN"),("APA","EN"),
    ("OKE","EN"),("TRGP","EN"),("WMB","EN"),("KMI","EN"),("ET","EN"),
    ("CNX","EN"),("EQT","EN"),("COG","EN"),("RRC","EN"),("AR","EN"),
    ("MRO","EN"),("NRG","EN"),("VST","EN"),("CEG","EN"),
    # Materials
    ("LIN","MAT"),("APD","MAT"),("ECL","MAT"),("SHW","MAT"),("PPG","MAT"),
    ("FCX","MAT"),("NEM","MAT"),("ALB","MAT"),("EMN","MAT"),("CF","MAT"),
    ("MOS","MAT"),("NTR","MAT"),("PKG","MAT"),("IP","MAT"),("WRK","MAT"),
    ("AVY","MAT"),("SEE","MAT"),("SON","MAT"),("BLL","MAT"),("BALL","MAT"),
    ("CE","MAT"),("DD","MAT"),("DOW","MAT"),("LYB","MAT"),("MLM","MAT"),
    ("VMC","MAT"),("FMC","MAT"),("IFF","MAT"),("RPM","MAT"),
    # Real Estate
    ("PLD","RE"),("AMT","RE"),("EQIX","RE"),("CCI","RE"),("SBAC","RE"),
    ("SPG","RE"),("O","RE"),("PSA","RE"),("EQR","RE"),("AVB","RE"),
    ("ESS","RE"),("MAA","RE"),("UDR","RE"),("CPT","RE"),("AIR","RE"),
    ("ARE","RE"),("BXP","RE"),("VTR","RE"),("WELL","RE"),("DOC","RE"),
    ("HST","RE"),("RHP","RE"),("PEB","RE"),("SLG","RE"),("KIM","RE"),
    ("REG","RE"),("FRT","RE"),("NNN","RE"),("INVH","RE"),("TRNO","RE"),
    ("IRM","RE"),("DLR","RE"),("EXR","RE"),("CUBE","RE"),("LSI","RE"),
    # Utilities
    ("NEE","UT"),("DUK","UT"),("SO","UT"),("AEP","UT"),("EXC","UT"),
    ("SRE","UT"),("D","UT"),("PCG","UT"),("PEG","UT"),("ED","UT"),
    ("XEL","UT"),("WEC","UT"),("ES","UT"),("DTE","UT"),("ETR","UT"),
    ("PPL","UT"),("AEE","UT"),("CMS","UT"),("NI","UT"),("EVRG","UT"),
    ("LNT","UT"),("PNW","UT"),("ATO","UT"),("NWE","UT"),
]

SECTOR_NAMES = {
    "IT": "Information Technology",
    "FIN": "Financials",
    "HC": "Health Care",
    "CD": "Consumer Discretionary",
    "CS": "Consumer Staples",
    "IND": "Industrials",
    "COM": "Communication Services",
    "EN": "Energy",
    "MAT": "Materials",
    "RE": "Real Estate",
    "UT": "Utilities",
}


# ── Fetch S&P 500 with sectors from Wikipedia ─────────────────────────────────

def fetch_sp500_with_sectors() -> dict[str, str]:
    """
    Returns {ticker: gics_sector} for all S&P 500 constituents.
    Falls back to hardcoded list if Wikipedia is unreachable.
    """
    try:
        print("  Fetching S&P 500 + GICS sectors from Wikipedia...", end=" ", flush=True)
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        df = tables[0]
        df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
        sector_map = dict(zip(df["Symbol"], df["GICS Sector"]))
        print(f"{len(sector_map)} tickers ✓")
        return sector_map
    except Exception as exc:
        print(f"failed ({exc}) — using hardcoded fallback")
        return {}


def build_sector_map(tier: int = 1) -> dict[str, str]:
    """
    Returns {ticker: sector_label} for the full universe including ETFs.
    Tries Wikipedia first, falls back to hardcoded list.
    """
    # Try live Wikipedia fetch
    sector_map = fetch_sp500_with_sectors()

    if not sector_map:
        # Use hardcoded fallback
        print(f"  Using hardcoded fallback ({len(SP500_WITH_SECTORS)} stocks)")
        sector_map = {
            t: SECTOR_NAMES[s] for t, s in SP500_WITH_SECTORS
        }

    # Add sector ETFs
    sector_map.update(SECTOR_ETFS)

    # Tier 2: add NASDAQ extras with their sectors
    if tier >= 2:
        ndx_extras = {
            "MELI": "Consumer Discretionary", "ABNB": "Consumer Discretionary",
            "DDOG": "Information Technology",  "CRWD": "Information Technology",
            "ZS":   "Information Technology",  "OKTA": "Information Technology",
            "TEAM": "Information Technology",  "WDAY": "Information Technology",
            "SNOW": "Information Technology",  "COIN": "Financials",
            "RBLX": "Communication Services",  "PLTR": "Information Technology",
            "UBER": "Industrials",              "LYFT": "Industrials",
            "DASH": "Consumer Discretionary",  "SPOT": "Communication Services",
            "TWLO": "Information Technology",  "NET":  "Information Technology",
            "MDB":  "Information Technology",  "BILL": "Financials",
            "HUBS": "Information Technology",  "VEEV": "Health Care",
        }
        sector_map.update(ndx_extras)

    return sector_map


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
            ticker, period="5y", auto_adjust=True,
            progress=False, multi_level_index=False,
        )
        if df.empty:
            return False, 0
        (DATA_DIR / f"{ticker.lower()}_daily.csv").write_text(df.to_csv())
        return True, len(df)
    except Exception as exc:
        print(f"    FAIL {ticker}: {exc}")
        return False, 0


def download_universe(tickers: list[str], force: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    to_download = [(t, r) for t in tickers
                   for needed, r in [needs_download(t)] if force or needed]
    fresh = len(tickers) - len(to_download)

    print(f"\n{'─'*60}")
    print(f"Universe: {len(tickers)} tickers")
    print(f"Already fresh: {fresh}")
    print(f"To download:   {len(to_download)}")
    print(f"Est. time: ~{len(to_download)*0.4/60:.0f} min")
    print(f"{'─'*60}\n")

    if not to_download:
        print("All data is fresh.")
        return

    failed = []
    for i, (ticker, reason) in enumerate(to_download, 1):
        print(f"  [{i:>3}/{len(to_download)}]  {ticker:<8} ({reason}) ...", end=" ", flush=True)
        ok, rows = download_ticker(ticker)
        print(f"{rows} rows ✓" if ok else "FAILED ✗")
        if not ok:
            failed.append(ticker)
        time.sleep(0.4)

    print(f"\nDone. Success: {len(to_download)-len(failed)}  Failed: {len(failed)}")
    if failed:
        print(f"Failed: {', '.join(failed)}")


# ── ADV filter ────────────────────────────────────────────────────────────────

def measure_adv(ticker: str, days: int = 63) -> float | None:
    path = DATA_DIR / f"{ticker.lower()}_daily.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if "Close" not in df.columns or "Volume" not in df.columns:
            return None
        r = df.tail(days)
        return float((r["Close"] * r["Volume"]).mean() / 1_000_000)
    except Exception:
        return None


# ── YAML generation ───────────────────────────────────────────────────────────

def generate_yaml(
    sector_map: dict[str, str],
    adv_threshold_m: float = 50.0,
    lookback_years: int = 3,
    intra_sector: bool = True,
    output_path: Path | None = None,
    tier: int = 1,
) -> Path:

    # ADV filter
    print(f"\nValidating ADV (threshold: ${adv_threshold_m:.0f}M)...")
    passing, skipped = [], []
    for ticker in sector_map:
        adv = measure_adv(ticker)
        if adv is None:
            skipped.append((ticker, "no data"))
        elif adv < adv_threshold_m:
            skipped.append((ticker, f"ADV ${adv:.0f}M"))
        else:
            passing.append(ticker)

    print(f"  Passing: {len(passing)}  Skipped: {len(skipped)}")

    # Group by sector
    by_sector = defaultdict(list)
    for t in passing:
        by_sector[sector_map[t]].append(t)

    print(f"\nSector breakdown (passing ADV filter):")
    for sector in sorted(by_sector):
        n = len(by_sector[sector])
        pairs = n * (n-1) // 2
        print(f"  {sector:<30} {n:>3} tickers  {pairs:>5} pairs")

    # Generate pairs
    today = datetime.date.today().strftime("%Y-%m-%d")
    pair_lines = []
    pair_count_by_sector = defaultdict(int)

    etf_set = set(SECTOR_ETFS.keys())

    if intra_sector:
        # Stock/stock pairs within sector + ETF/ETF within sector
        for sector, tickers in by_sector.items():
            stocks = [t for t in tickers if t not in etf_set]
            etfs   = [t for t in tickers if t in etf_set]

            # Stock vs stock (intra-sector)
            for t1, t2 in itertools.combinations(stocks, 2):
                pair_lines.append(_pair_entry(t1, t2, lookback_years, "stock"))
                pair_count_by_sector[sector] += 1

            # ETF vs ETF (same sector bucket)
            for t1, t2 in itertools.combinations(etfs, 2):
                pair_lines.append(_pair_entry(t1, t2, 3, "etf"))
                pair_count_by_sector[sector] += 1

            # Stock vs sector ETF (same sector)
            for stock in stocks:
                for etf in etfs:
                    pair_lines.append(_pair_entry(stock, etf, 2, "cross"))
                    pair_count_by_sector[sector] += 1
    else:
        # All combinations
        for t1, t2 in itertools.combinations(passing, 2):
            is_etf1 = t1 in etf_set
            is_etf2 = t2 in etf_set
            lb = 3 if (is_etf1 and is_etf2) else (2 if (is_etf1 or is_etf2) else lookback_years)
            pair_lines.append(_pair_entry(t1, t2, lb, ""))

    n_pairs = len(pair_lines)
    est_mins = n_pairs * 0.5 / 8 / 60
    mode = "intra-sector" if intra_sector else "all-pairs"

    print(f"\n{'─'*50}")
    print(f"Mode: {mode}")
    print(f"Total pairs: {n_pairs:,}")
    print(f"Est. screen time at 8 workers: ~{est_mins:.0f} min")

    fname = f"composition_tier{tier}_{mode.replace('-','_')}_{today}.yaml"
    if output_path is None:
        output_path = COMP_DIR / fname

    header = f"""\
# ShiftInnerV — Liquid Universe Composition
# Mode: {mode} | Tier {tier}
# ADV filter: > ${adv_threshold_m:.0f}M
# Generated: {today}
# Tickers: {len(passing)}
# Pairs:   {n_pairs:,}
# Est. screen time: ~{est_mins:.0f} min at 8 workers
#
# Sectors included ({len(by_sector)}):
{''.join(f"#   {s}: {pair_count_by_sector[s]} pairs{chr(10)}" for s in sorted(by_sector))}#
# Lookback: {lookback_years}yr stock/stock | 2yr stock/ETF | 3yr ETF/ETF
#
# Run:
#   python monitor.py --screen {output_path.name} --workers 8

pairs:
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header + "\n".join(pair_lines))

    size_kb = output_path.stat().st_size / 1024
    print(f"\nYAML written: {output_path}")
    print(f"File size: {size_kb:.0f} KB")
    return output_path


def _pair_entry(t1: str, t2: str, lb: int, kind: str) -> str:
    return (
        f"- ticker1: '{t1}'\n"
        f"  ticker2: '{t2}'\n"
        f"  label: '{t1} vs {t2}'\n"
        f"  lookback_years: {lb}\n"
        f"  cointegrated: unknown\n"
    )


# ── Check ─────────────────────────────────────────────────────────────────────

def check_universe(sector_map: dict[str, str]) -> None:
    tickers = list(sector_map.keys())
    missing = [t for t in tickers if needs_download(t)[1] == "missing"]
    fresh   = [t for t in tickers if not needs_download(t)[0]]
    stale   = [t for t in tickers if needs_download(t)[0] and t not in missing]

    print(f"\nUniverse: {len(tickers)} tickers")
    print(f"  Fresh:   {len(fresh)}")
    print(f"  Stale:   {len(stale)}")
    print(f"  Missing: {len(missing)}")
    if missing:
        print(f"\nMissing ({len(missing)}):")
        for i in range(0, len(missing), 10):
            print(f"  {' '.join(missing[i:i+10])}")

    # Intra-sector pair count estimate
    by_sector = defaultdict(list)
    for t in tickers:
        by_sector[sector_map[t]].append(t)
    intra = sum(n*(n-1)//2 for n in (len(v) for v in by_sector.values()))
    total = len(tickers) * (len(tickers)-1) // 2
    print(f"\nPair estimates (all tickers):")
    print(f"  Intra-sector: {intra:,} (~{intra*0.5/8/60:.0f} min at 8 workers)")
    print(f"  All-pairs:    {total:,} (~{total*0.5/8/60:.0f} min at 8 workers)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build intra-sector liquid universe for ShiftInnerV"
    )
    parser.add_argument("--tier", type=int, default=1, choices=[1, 2])
    parser.add_argument("--adv", type=float, default=50.0,
        help="Min ADV in $M (default: 50)")
    parser.add_argument("--lookback", type=int, default=3)
    parser.add_argument("--all-pairs", action="store_true",
        help="Generate all combinations (default: intra-sector only)")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    intra = not args.all_pairs

    print(f"\nShiftInnerV — Liquid Universe Builder")
    print(f"{'═'*50}")
    print(f"  Tier:     {args.tier}")
    print(f"  Mode:     {'intra-sector' if intra else 'all-pairs'}")
    print(f"  ADV min:  ${args.adv:.0f}M")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Comp dir: {COMP_DIR}")

    sector_map = build_sector_map(args.tier)
    tickers    = list(sector_map.keys())
    print(f"  Tickers:  {len(tickers)}")

    if args.check:
        check_universe(sector_map)
        return

    if not args.generate_only:
        download_universe(tickers, force=args.force)

    if not args.download_only:
        out = Path(args.output) if args.output else None
        yaml_path = generate_yaml(
            sector_map,
            adv_threshold_m=args.adv,
            lookback_years=args.lookback,
            intra_sector=intra,
            output_path=out,
            tier=args.tier,
        )
        print(f"\nNext step:")
        print(f"  python monitor.py --screen {yaml_path.name} --workers 8")


if __name__ == "__main__":
    main()
