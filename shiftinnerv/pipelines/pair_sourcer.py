#!/usr/bin/env python3
"""
ShiftInnerV — Intelligent Pair Sourcer

Generates composition files using correlation-driven pair selection instead of
brute-force random sampling.

Strategy:
  1. Load returns for all tickers in universe
  2. Compute rolling correlation matrix (252-day window)
  3. Cluster tickers by correlation behavior (K-means)
  4. Score pairs within clusters by:
     - Correlation strength (higher = better structural link)
     - Correlation decay (current < historical = divergence opportunity)
     - Volatility matching (similar σ = cleaner hedge)
     - Past success (query anomalies.db for historical cointegration)
  5. Output top N pairs ranked by cointegration likelihood

Usage:
    python pair_sourcer.py --top 100 --lookback 3
    python pair_sourcer.py --top 50 --min-correlation 0.4 --clusters 20
"""

import os
import sys
import yaml
import argparse
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from typing import List, Tuple, Dict
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

# ── Paths ─────────────────────────────────────────────────────────────────────

PROJECT_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.join(PROJECT_DIR, "data")
ANOMALIES_DB = os.path.join(DATA_DIR, "anomalies.db")

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_TOP_N = 100
DEFAULT_LOOKBACK_YEARS = 3
MIN_CORRELATION = 0.3        # Only pair tickers with >0.3 rolling correlation
CORRELATION_WINDOW = 252     # 1 year rolling window for correlation
DECAY_WINDOW = 126           # 6 months — detect recent correlation weakening
MIN_EPISODES_HISTORY = 1     # Prefer pairs with historical cointegration success
N_CLUSTERS = 15              # K-means cluster count


# ── Load universe ─────────────────────────────────────────────────────────────

def load_universe(universe_path: str) -> Dict[str, List[str]]:
    """Load ticker universe from yaml."""
    with open(universe_path) as f:
        data = yaml.safe_load(f)
    return data["universe"]


def flatten_universe(universe: Dict[str, List[str]]) -> List[str]:
    """Flatten universe to single ticker list (deduplicated)."""
    tickers = []
    for category_tickers in universe.values():
        tickers.extend(category_tickers)
    return sorted(set(tickers))


# ── Returns data ──────────────────────────────────────────────────────────────

def load_returns(tickers: List[str], data_dir: str, lookback_years: int, skip_download: bool = False) -> pd.DataFrame:
    """
    Load returns for all tickers from CSV files.
    Returns: DataFrame with tickers as columns, dates as index, daily returns as values.

    Args:
        tickers: List of ticker symbols
        data_dir: Directory containing CSV files
        lookback_years: Years of historical data to load
        skip_download: If True, skip ensure_data() call (for testing with pre-created CSVs)
    """
    from shiftinnerv.services.data_manager import ensure_data

    # Ensure data is downloaded (skip if testing with pre-created CSVs)
    print(f"Loading returns for {len(tickers)} tickers...")
    if not skip_download:
        ensure_data(tickers, data_dir=data_dir)

    # Load CSVs
    returns_dict = {}
    failed = []

    for ticker in tickers:
        csv_path = os.path.join(data_dir, f"{ticker.lower()}_daily.csv")
        if not os.path.exists(csv_path):
            failed.append(ticker)
            continue

        try:
            df = pd.read_csv(csv_path)

            # Handle different date column formats (case-insensitive)
            date_col = None
            for col in df.columns:
                if col.lower() == 'date':
                    date_col = col
                    break

            if date_col:
                df[date_col] = pd.to_datetime(df[date_col])
                df = df.set_index(date_col)
            elif df.index.name and 'date' in df.index.name.lower():
                # Index is already date
                df.index = pd.to_datetime(df.index)
            else:
                # Assume first column is date
                df.index = pd.to_datetime(df.iloc[:, 0])
                df = df.iloc[:, 1:]

            df.index.name = "date"

            # Handle different price column names (case-insensitive)
            price_col = None
            for col in df.columns:
                if col.lower() in ["adjclose", "adj close", "close"]:
                    price_col = col
                    break

            if price_col is None:
                failed.append(ticker)
                continue

            # Standardize to 'adjClose'
            if price_col != "adjClose":
                df["adjClose"] = df[price_col]

            # Compute daily returns
            returns = df["adjClose"].pct_change().dropna()

            # Filter to lookback window
            cutoff = datetime.now() - timedelta(days=lookback_years * 365)
            returns = returns[returns.index >= cutoff]

            if len(returns) < CORRELATION_WINDOW:
                failed.append(ticker)
                continue

            returns_dict[ticker] = returns
        except Exception as e:
            print(f"  WARNING: Failed to load {ticker}: {e}")
            failed.append(ticker)

    if failed:
        print(f"  Skipped {len(failed)} tickers: {', '.join(failed[:10])}{'...' if len(failed) > 10 else ''}")

    # Align to common date index
    returns_df = pd.DataFrame(returns_dict)
    returns_df = returns_df.dropna(axis=1, how="all")  # drop tickers with no data
    returns_df = returns_df.fillna(0)  # fill missing dates with 0 return

    print(f"  Loaded {len(returns_df.columns)} tickers with {len(returns_df)} days of returns")
    return returns_df


# ── Correlation analysis ──────────────────────────────────────────────────────

def compute_rolling_correlation(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Compute rolling correlation matrix using last `window` days.
    Returns: correlation matrix (ticker x ticker).
    """
    print(f"Computing {window}-day rolling correlation matrix...")
    recent_returns = returns_df.tail(window)
    corr_matrix = recent_returns.corr()
    return corr_matrix


def detect_correlation_decay(returns_df: pd.DataFrame,
                              current_window: int,
                              historical_window: int) -> pd.DataFrame:
    """
    Detect pairs where correlation has weakened recently.
    Returns: DataFrame with (ticker1, ticker2, current_corr, historical_corr, decay).
    """
    print(f"Detecting correlation decay (current={current_window}d vs historical={historical_window}d)...")

    current_corr = returns_df.tail(current_window).corr()
    historical_corr = returns_df.tail(historical_window).corr()

    # Compute decay: historical - current (positive = weakening)
    decay_matrix = historical_corr - current_corr

    return current_corr, historical_corr, decay_matrix


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_tickers(corr_matrix: pd.DataFrame, n_clusters: int) -> Dict[str, int]:
    """
    Cluster tickers by correlation behavior using K-means.
    Returns: Dict mapping ticker -> cluster_id.
    """
    n_clusters = min(n_clusters, len(corr_matrix))
    print(f"Clustering tickers into {n_clusters} groups...")

    # Use correlation matrix as features
    X = corr_matrix.values

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # K-means
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X_scaled)

    # Map tickers to clusters
    ticker_to_cluster = {ticker: int(label) for ticker, label in zip(corr_matrix.index, labels)}

    # Print cluster sizes
    cluster_sizes = pd.Series(labels).value_counts().sort_index()
    print(f"  Cluster sizes: {dict(cluster_sizes)}")

    return ticker_to_cluster


# ── Historical success ────────────────────────────────────────────────────────

def load_historical_success(db_path: str) -> Dict[Tuple[str, str], int]:
    """
    Query anomalies.db for pairs that have historically cointegrated.
    Returns: Dict mapping (ticker1, ticker2) -> episodes count.
    """
    if not os.path.exists(db_path):
        print(f"  No anomalies.db found — skipping historical success scoring")
        return {}

    print(f"Loading historical cointegration success from {db_path}...")

    conn = sqlite3.connect(db_path)
    query = """
        SELECT ticker1, ticker2, MAX(episodes) as max_episodes
        FROM screening
        WHERE episodes > 0
        GROUP BY ticker1, ticker2
    """

    try:
        df = pd.read_sql_query(query, conn)
        conn.close()

        success_map = {}
        for _, row in df.iterrows():
            t1, t2 = sorted([row["ticker1"], row["ticker2"]])
            success_map[(t1, t2)] = row["max_episodes"]

        print(f"  Found {len(success_map)} pairs with historical cointegration")
        return success_map
    except Exception as e:
        print(f"  WARNING: Failed to query anomalies.db: {e}")
        conn.close()
        return {}


# ── Volatility matching ───────────────────────────────────────────────────────

def compute_volatility_match_score(returns_df: pd.DataFrame,
                                     ticker1: str,
                                     ticker2: str,
                                     window: int = 252) -> float:
    """
    Compute volatility matching score.
    Returns: 1.0 - |σ1 - σ2| / max(σ1, σ2)
    Higher score = more similar volatility = cleaner hedge.
    """
    recent = returns_df.tail(window)

    if ticker1 not in recent.columns or ticker2 not in recent.columns:
        return 0.0

    vol1 = recent[ticker1].std()
    vol2 = recent[ticker2].std()

    if vol1 == 0 or vol2 == 0:
        return 0.0

    # Similarity score
    vol_diff = abs(vol1 - vol2)
    vol_max = max(vol1, vol2)

    score = 1.0 - (vol_diff / vol_max)
    return max(0.0, score)


# ── Pair scoring ──────────────────────────────────────────────────────────────

def score_pairs(returns_df: pd.DataFrame,
                ticker_to_cluster: Dict[str, int],
                corr_matrix: pd.DataFrame,
                decay_matrix: pd.DataFrame,
                historical_success: Dict[Tuple[str, str], int],
                min_correlation: float) -> pd.DataFrame:
    """
    Score all pairs within the same cluster by cointegration likelihood.

    Scoring components:
      - correlation_score: current correlation strength (0-100)
      - decay_score: correlation weakening opportunity (0-100)
      - volatility_score: volatility matching (0-100)
      - history_score: past cointegration success (0-100)
      - total_score: weighted sum

    Returns: DataFrame with (ticker1, ticker2, cluster, scores..., total_score).
    """
    print(f"Scoring pairs (min_correlation={min_correlation})...")

    pairs = []

    # Group tickers by cluster
    cluster_to_tickers = {}
    for ticker, cluster_id in ticker_to_cluster.items():
        cluster_to_tickers.setdefault(cluster_id, []).append(ticker)

    # Score pairs within each cluster
    for cluster_id, tickers in cluster_to_tickers.items():
        if len(tickers) < 2:
            continue

        for i, t1 in enumerate(tickers):
            for t2 in tickers[i+1:]:
                # Filter by minimum correlation
                corr = corr_matrix.loc[t1, t2]
                if abs(corr) < min_correlation:
                    continue

                # Correlation strength score (0-100)
                # Penalize near-perfect correlation (>0.90) - these are likely identical/similar ETFs
                # Optimal range: 0.5-0.85 (structural link but room to diverge)
                if abs(corr) > 0.90:
                    # Near-perfect correlation = not tradeable (identical/similar products)
                    correlation_score = 0
                elif abs(corr) < 0.5:
                    # Too weak = no structural relationship
                    correlation_score = abs(corr) * 50  # 0-25 points
                else:
                    # Sweet spot: 0.5-0.85 correlation
                    # Peak score at 0.70 correlation
                    distance_from_optimal = abs(abs(corr) - 0.70)
                    correlation_score = 100 - (distance_from_optimal * 200)  # 60-100 points
                    correlation_score = max(0, correlation_score)

                # Decay score (0-100) — higher decay = more opportunity
                # This is the PRIMARY signal for pairs trading
                decay = decay_matrix.loc[t1, t2]
                decay_score = max(0, decay * 200)  # decay typically 0-0.5

                # Volatility matching score (0-100)
                vol_match = compute_volatility_match_score(returns_df, t1, t2)
                volatility_score = vol_match * 100

                # Historical success score (0-100)
                pair_key = tuple(sorted([t1, t2]))
                episodes = historical_success.get(pair_key, 0)
                history_score = min(100, episodes * 25)  # cap at 100

                # Weighted total score
                # NEW WEIGHTS for pairs trading (avoids ETF twins):
                #   - Correlation decay (40%): PRIMARY signal - divergence opportunity
                #   - Correlation strength (20%): structural link (penalized if >0.95)
                #   - Volatility matching (20%): hedge quality
                #   - Historical success (20%): past validation
                total_score = (
                    0.20 * correlation_score +
                    0.40 * decay_score +
                    0.20 * volatility_score +
                    0.20 * history_score
                )

                pairs.append({
                    "ticker1": t1,
                    "ticker2": t2,
                    "cluster": cluster_id,
                    "correlation": corr,
                    "correlation_score": correlation_score,
                    "decay_score": decay_score,
                    "volatility_score": volatility_score,
                    "history_score": history_score,
                    "total_score": total_score,
                })

    pairs_df = pd.DataFrame(pairs)

    # Handle empty result (no pairs meet min_correlation threshold)
    if len(pairs_df) == 0:
        print(f"  No pairs found meeting min_correlation threshold")
        return pairs_df

    pairs_df = pairs_df.sort_values("total_score", ascending=False)

    print(f"  Scored {len(pairs_df)} pairs")
    return pairs_df


# ── Output composition ────────────────────────────────────────────────────────

def write_sourced_composition(pairs_df: pd.DataFrame,
                               output_path: str,
                               top_n: int,
                               lookback_years: int) -> None:
    """
    Write top N pairs to a composition yaml file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    top_pairs = pairs_df.head(top_n)

    # Build pair blocks
    pair_blocks = []
    for _, row in top_pairs.iterrows():
        pair_blocks.append({
            "ticker1": row["ticker1"],
            "ticker2": row["ticker2"],
            "label": f"{row['ticker1']} vs {row['ticker2']}",
            "lookback_years": lookback_years,
            "cointegrated": "unknown",
        })

    # Header
    header = f"""# ShiftInnerV — Sourced Composition (Intelligent Pair Selection)
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
# Pairs: {len(pair_blocks)}
# Selection method: Correlation clustering + decay detection + volatility matching + historical success
#
# Top pair scores:
"""
    for i, row in top_pairs.head(10).iterrows():
        header += f"#   {row['ticker1']:6s} / {row['ticker2']:6s}  score={row['total_score']:.1f}  corr={row['correlation']:.3f}  cluster={row['cluster']}\n"

    header += f"""#
# SCREENING FILE — run through monitor.py --screen before promoting to production
# python monitor.py --screen {os.path.basename(output_path)}

"""

    with open(output_path, "w") as f:
        f.write(header)
        yaml.dump({"pairs": pair_blocks}, f,
                  default_flow_style=False,
                  allow_unicode=True,
                  sort_keys=False)

    print(f"\nWritten: {output_path} ({len(pair_blocks)} pairs)")
    print(f"\nTop 5 pairs by score:")
    for i, row in top_pairs.head(5).iterrows():
        print(f"  {row['ticker1']:6s} / {row['ticker2']:6s}  "
              f"score={row['total_score']:5.1f}  "
              f"corr={row['correlation']:5.3f}  "
              f"cluster={row['cluster']:2d}")


# ── Main pipeline ─────────────────────────────────────────────────────────────

def source_pairs(
    universe_path: str,
    output_path: str,
    top_n: int = DEFAULT_TOP_N,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
    min_correlation: float = MIN_CORRELATION,
    n_clusters: int = N_CLUSTERS,
    data_dir: str = DATA_DIR,
) -> str:
    """
    Main pair sourcing pipeline.

    Returns: Path to generated composition file.
    """
    print(f"\n{'='*70}")
    print(f"ShiftInnerV — Intelligent Pair Sourcing")
    print(f"{'='*70}\n")

    # Load universe
    universe = load_universe(universe_path)
    tickers = flatten_universe(universe)
    print(f"Loaded universe: {len(tickers)} tickers across {len(universe)} categories\n")

    # Load returns
    returns_df = load_returns(tickers, data_dir, lookback_years)

    if len(returns_df.columns) < 10:
        n = len(returns_df.columns)
        raise RuntimeError(
            f"Insufficient tickers with data ({n} < 10). "
            f"Check DATA_DIR and that CSV files exist for the universe tickers."
        )

    print()

    # Compute correlation matrices
    corr_matrix = compute_rolling_correlation(returns_df, CORRELATION_WINDOW)
    current_corr, historical_corr, decay_matrix = detect_correlation_decay(
        returns_df, DECAY_WINDOW, CORRELATION_WINDOW
    )
    print()

    # Cluster tickers
    ticker_to_cluster = cluster_tickers(corr_matrix, n_clusters)
    print()

    # Load historical success
    historical_success = load_historical_success(ANOMALIES_DB)
    print()

    # Score pairs
    pairs_df = score_pairs(
        returns_df,
        ticker_to_cluster,
        current_corr,
        decay_matrix,
        historical_success,
        min_correlation,
    )
    print()

    # Write output
    write_sourced_composition(pairs_df, output_path, top_n, lookback_years)

    print(f"\n{'='*70}")
    print(f"Next step — screen the sourced composition:")
    print(f"  python monitor.py --screen {output_path} --workers 10")
    print(f"{'='*70}\n")

    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV — Intelligent Pair Sourcer"
    )
    parser.add_argument(
        "--universe", type=str, default=None,
        help="Path to universe.yaml (default: ./universe.yaml)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output yaml path (default: compositions/sourced_YYYYMMDD.yaml)"
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP_N,
        help=f"Number of top pairs to output (default: {DEFAULT_TOP_N})"
    )
    parser.add_argument(
        "--lookback", type=int, default=DEFAULT_LOOKBACK_YEARS, choices=[1, 3, 5],
        help=f"Lookback years for returns (default: {DEFAULT_LOOKBACK_YEARS})"
    )
    parser.add_argument(
        "--min-correlation", type=float, default=MIN_CORRELATION,
        help=f"Minimum correlation threshold (default: {MIN_CORRELATION})"
    )
    parser.add_argument(
        "--clusters", type=int, default=N_CLUSTERS,
        help=f"Number of K-means clusters (default: {N_CLUSTERS})"
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help=f"Data directory (default: {DATA_DIR})"
    )

    args = parser.parse_args()

    # Paths
    universe_path = args.universe or os.path.join(PROJECT_DIR, "universe.yaml")
    if not os.path.exists(universe_path):
        print(f"ERROR: universe.yaml not found at {universe_path}")
        sys.exit(1)

    output_path = args.output or os.path.join(
        PROJECT_DIR,
        "compositions",
        f"sourced_{datetime.now().strftime('%Y%m%d')}.yaml"
    )

    data_dir = args.data_dir or DATA_DIR

    # Run
    source_pairs(
        universe_path=universe_path,
        output_path=output_path,
        top_n=args.top,
        lookback_years=args.lookback,
        min_correlation=args.min_correlation,
        n_clusters=args.clusters,
        data_dir=data_dir,
    )


if __name__ == "__main__":
    main()
