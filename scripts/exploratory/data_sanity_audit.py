import os
import time
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))
PROJECT_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(PROJECT_DIR, "data")
data_dir     = DATA_DIR

# Layer 1: Macro Signal Basket
MACRO_TICKERS = {
    # Commodities
    'USO': 'Crude Oil ETF',
    'UNG': 'Natural Gas ETF',
    'GLD': 'Gold ETF',
    'SLV': 'Silver ETF',
    'CPER': 'Copper ETF',
    'DBA': 'Agriculture ETF',
    # Currencies
    'UUP': 'US Dollar Index',
    'FXY': 'Japanese Yen',
    'FXE': 'Euro',
    # Bonds
    'TLT': '20yr Treasury',
    'HYG': 'High Yield Corporate',
    'EMB': 'Emerging Market Bonds',
    # Logistics
    'ZIM': 'Shipping',
    'SAIA': 'Trucking',
    'FDX': 'FedEx',
    # Critical Materials
    'MP': 'MP Materials Rare Earth',
    'REMX': 'Rare Earth ETF',
    'COPX': 'Copper Miners',
    # Technology Components
    'SOXX': 'Semiconductors',
    'SMH': 'Chip ETF',
    # Energy Equities (your existing pairs)
    'XOM': 'ExxonMobil',
    'CVX': 'Chevron',
}

def pull_ticker(symbol, description, period="2y"):
    print(f"Pulling {symbol} ({description})...")
    try:
        data = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        if data.empty:
            print(f"  WARNING: No data returned for {symbol}")
            return
        data.columns = data.columns.get_level_values(0)
        output_path = os.path.join(data_dir, f"{symbol.lower()}_daily.csv")
        data.to_csv(output_path)
        print(f"  Saved {len(data)} days to {output_path}")
    except Exception as e:
        print(f"  Failed {symbol}: {e}")

if __name__ == "__main__":
    print(f"Pulling {len(MACRO_TICKERS)} tickers to: {data_dir}\n")
    for symbol, description in MACRO_TICKERS.items():
        pull_ticker(symbol, description)
        time.sleep(1)  # gentle rate limiting
    print("\nDone.")
