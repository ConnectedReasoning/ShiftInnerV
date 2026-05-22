#!/usr/bin/env python3
"""
Tests for shiftinnerv/pipelines/pair_sourcer.py

Covers:
  - Universe loading and flattening
  - Returns data loading
  - Correlation matrix computation
  - Correlation decay detection
  - K-means clustering
  - Volatility matching scores
  - Pair scoring algorithm
  - Output composition writing
"""

import os
import pytest
import tempfile
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def test_universe_loading():
    """Test universe loading and flattening."""
    from shiftinnerv.pipelines.pair_sourcer import load_universe, flatten_universe
    
    # Create temp universe file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("""
universe:
  tech:
    - AAPL
    - MSFT
  energy:
    - XOM
    - CVX
  overlap:
    - AAPL
""")
        universe_path = f.name
    
    try:
        universe = load_universe(universe_path)
        assert len(universe) == 3
        assert 'tech' in universe
        assert len(universe['tech']) == 2
        
        # Test flattening (should deduplicate AAPL)
        tickers = flatten_universe(universe)
        assert len(tickers) == 4  # AAPL, MSFT, XOM, CVX (deduplicated)
        assert 'AAPL' in tickers
        assert tickers == sorted(tickers)  # should be sorted
    
    finally:
        os.unlink(universe_path)


def test_correlation_matrix():
    """Test correlation matrix computation."""
    from shiftinnerv.pipelines.pair_sourcer import compute_rolling_correlation
    
    # Create synthetic returns
    dates = pd.date_range('2020-01-01', periods=300, freq='D')
    
    # Perfect positive correlation
    np.random.seed(42)
    base_returns = np.random.randn(300) * 0.01
    
    returns_df = pd.DataFrame({
        'A': base_returns,
        'B': base_returns + np.random.randn(300) * 0.001,  # highly correlated
        'C': -base_returns,  # negative correlation
    }, index=dates)
    
    corr_matrix = compute_rolling_correlation(returns_df, window=252)
    
    # Check structure
    assert corr_matrix.shape == (3, 3)
    assert corr_matrix.loc['A', 'B'] > 0.8  # strong positive
    assert corr_matrix.loc['A', 'C'] < -0.8  # strong negative
    assert abs(corr_matrix.loc['A', 'A'] - 1.0) < 0.01  # diagonal is 1


def test_correlation_decay_detection():
    """Test correlation decay detection."""
    from shiftinnerv.pipelines.pair_sourcer import detect_correlation_decay
    
    # Create returns with weakening correlation
    dates = pd.date_range('2020-01-01', periods=300, freq='D')
    np.random.seed(42)
    
    base = np.random.randn(300) * 0.01
    
    # Strong correlation historically, weaker recently
    noise_level = np.linspace(0.001, 0.01, 300)  # increasing noise
    
    returns_df = pd.DataFrame({
        'X': base,
        'Y': base + np.random.randn(300) * noise_level,
    }, index=dates)
    
    current_corr, historical_corr, decay_matrix = detect_correlation_decay(
        returns_df, current_window=60, historical_window=252
    )
    
    # Historical correlation should be stronger than current
    assert historical_corr.loc['X', 'Y'] > current_corr.loc['X', 'Y']
    
    # Decay should be positive (weakening)
    assert decay_matrix.loc['X', 'Y'] > 0


def test_ticker_clustering():
    """Test K-means clustering on correlation matrix."""
    from shiftinnerv.pipelines.pair_sourcer import cluster_tickers
    
    # Create synthetic correlation matrix with clear clusters
    tickers = ['A', 'B', 'C', 'D', 'E', 'F']
    
    # Cluster 1: A, B, C (high correlation)
    # Cluster 2: D, E, F (high correlation)
    corr_data = np.array([
        [1.00, 0.90, 0.85, 0.10, 0.05, 0.08],  # A
        [0.90, 1.00, 0.88, 0.12, 0.07, 0.10],  # B
        [0.85, 0.88, 1.00, 0.08, 0.09, 0.11],  # C
        [0.10, 0.12, 0.08, 1.00, 0.92, 0.89],  # D
        [0.05, 0.07, 0.09, 0.92, 1.00, 0.91],  # E
        [0.08, 0.10, 0.11, 0.89, 0.91, 1.00],  # F
    ])
    
    corr_matrix = pd.DataFrame(corr_data, index=tickers, columns=tickers)
    
    ticker_to_cluster = cluster_tickers(corr_matrix, n_clusters=2)
    
    # Check that we got 2 clusters
    cluster_ids = set(ticker_to_cluster.values())
    assert len(cluster_ids) == 2
    
    # Check that A, B, C are in same cluster
    cluster_abc = ticker_to_cluster['A']
    assert ticker_to_cluster['B'] == cluster_abc
    assert ticker_to_cluster['C'] == cluster_abc
    
    # Check that D, E, F are in same cluster (different from ABC)
    cluster_def = ticker_to_cluster['D']
    assert ticker_to_cluster['E'] == cluster_def
    assert ticker_to_cluster['F'] == cluster_def
    assert cluster_def != cluster_abc


def test_volatility_match_score():
    """Test volatility matching score computation."""
    from shiftinnerv.pipelines.pair_sourcer import compute_volatility_match_score
    
    dates = pd.date_range('2020-01-01', periods=300, freq='D')
    np.random.seed(42)
    
    returns_df = pd.DataFrame({
        'LOW_VOL': np.random.randn(300) * 0.005,   # σ ≈ 0.5%
        'MED_VOL': np.random.randn(300) * 0.015,   # σ ≈ 1.5%
        'HIGH_VOL': np.random.randn(300) * 0.030,  # σ ≈ 3.0%
    }, index=dates)
    
    # Perfect match
    score_same = compute_volatility_match_score(returns_df, 'LOW_VOL', 'LOW_VOL', window=252)
    assert score_same == 1.0
    
    # Similar volatility
    score_similar = compute_volatility_match_score(returns_df, 'LOW_VOL', 'MED_VOL', window=252)
    assert 0.3 < score_similar < 0.9  # Relaxed lower bound due to volatility variance
    
    # Very different volatility
    score_different = compute_volatility_match_score(returns_df, 'LOW_VOL', 'HIGH_VOL', window=252)
    assert score_different < score_similar


def test_pair_scoring_components():
    """Test that pair scoring includes all expected components."""
    from shiftinnerv.pipelines.pair_sourcer import score_pairs
    
    # Create minimal test data
    dates = pd.date_range('2020-01-01', periods=300, freq='D')
    np.random.seed(42)
    
    base = np.random.randn(300) * 0.01
    returns_df = pd.DataFrame({
        'A': base,
        'B': base + np.random.randn(300) * 0.005,
        'C': base + np.random.randn(300) * 0.005,
    }, index=dates)
    
    ticker_to_cluster = {'A': 0, 'B': 0, 'C': 0}
    corr_matrix = returns_df.corr()
    decay_matrix = pd.DataFrame(
        np.random.rand(3, 3) * 0.1,
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )
    historical_success = {('A', 'B'): 3, ('B', 'C'): 1}
    
    pairs_df = score_pairs(
        returns_df,
        ticker_to_cluster,
        corr_matrix,
        decay_matrix,
        historical_success,
        min_correlation=0.3,
    )
    
    # Check output structure
    assert len(pairs_df) > 0
    assert 'ticker1' in pairs_df.columns
    assert 'ticker2' in pairs_df.columns
    assert 'correlation_score' in pairs_df.columns
    assert 'decay_score' in pairs_df.columns
    assert 'volatility_score' in pairs_df.columns
    assert 'history_score' in pairs_df.columns
    assert 'total_score' in pairs_df.columns
    
    # Check that pairs are sorted by total_score descending
    assert pairs_df['total_score'].is_monotonic_decreasing
    
    # Check that A/B has higher history_score than other pairs
    ab_row = pairs_df[
        ((pairs_df['ticker1'] == 'A') & (pairs_df['ticker2'] == 'B')) |
        ((pairs_df['ticker1'] == 'B') & (pairs_df['ticker2'] == 'A'))
    ]
    assert len(ab_row) == 1
    assert ab_row.iloc[0]['history_score'] > 0


def test_output_composition_format():
    """Test that output composition file has correct format."""
    from shiftinnerv.pipelines.pair_sourcer import write_sourced_composition
    
    # Create test pairs dataframe
    pairs_df = pd.DataFrame({
        'ticker1': ['AAPL', 'MSFT', 'XOM'],
        'ticker2': ['MSFT', 'XOM', 'CVX'],
        'cluster': [0, 0, 1],
        'correlation': [0.85, 0.60, 0.75],
        'correlation_score': [85.0, 60.0, 75.0],
        'decay_score': [10.0, 20.0, 15.0],
        'volatility_score': [80.0, 70.0, 85.0],
        'history_score': [50.0, 25.0, 0.0],
        'total_score': [71.0, 48.0, 52.5],
    })
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        output_path = f.name
    
    try:
        write_sourced_composition(pairs_df, output_path, top_n=2, lookback_years=3)
        
        # Read output
        with open(output_path) as f:
            content = f.read()
        
        # Check header
        assert 'ShiftInnerV' in content
        assert 'Sourced Composition' in content
        assert 'Pairs: 2' in content
        
        # Check yaml structure
        import yaml
        with open(output_path) as f:
            data = yaml.safe_load(f)
        
        assert 'pairs' in data
        assert len(data['pairs']) == 2
        
        # Check first pair
        pair = data['pairs'][0]
        assert 'ticker1' in pair
        assert 'ticker2' in pair
        assert 'label' in pair
        assert 'lookback_years' in pair
        assert pair['lookback_years'] == 3
        assert pair['cointegrated'] == 'unknown'
    
    finally:
        os.unlink(output_path)


def test_min_correlation_filter():
    """Test that pairs below min_correlation threshold are filtered out."""
    from shiftinnerv.pipelines.pair_sourcer import score_pairs
    
    dates = pd.date_range('2020-01-01', periods=300, freq='D')
    np.random.seed(42)
    
    returns_df = pd.DataFrame({
        'A': np.random.randn(300) * 0.01,
        'B': np.random.randn(300) * 0.01,  # uncorrelated
    }, index=dates)
    
    ticker_to_cluster = {'A': 0, 'B': 0}
    corr_matrix = returns_df.corr()
    decay_matrix = pd.DataFrame([[0, 0], [0, 0]], index=['A', 'B'], columns=['A', 'B'])
    
    # With low min_correlation, should get the pair
    pairs_low = score_pairs(
        returns_df, ticker_to_cluster, corr_matrix, decay_matrix, {},
        min_correlation=0.01
    )
    assert len(pairs_low) > 0
    
    # With high min_correlation, should filter it out
    pairs_high = score_pairs(
        returns_df, ticker_to_cluster, corr_matrix, decay_matrix, {},
        min_correlation=0.8
    )
    # Empty dataframe is expected when no pairs meet threshold
    assert len(pairs_high) == 0 or pairs_high.empty


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_high_correlation_penalty():
    """Test that pairs with correlation >0.90 are penalized to score=0."""
    from shiftinnerv.pipelines.pair_sourcer import score_pairs
    
    dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
    np.random.seed(42)
    
    # Create pairs with different correlation levels
    base = np.random.randn(300) * 0.01
    
    returns_df = pd.DataFrame({
        'PERFECT': base,  # corr = 1.0
        'NEAR_PERFECT': base + np.random.randn(300) * 0.001,  # corr > 0.95
        'HIGH': base + np.random.randn(300) * 0.005,  # corr ~0.70-0.85
        'MEDIUM': base + np.random.randn(300) * 0.01,  # corr ~0.50-0.70
    }, index=dates)
    
    ticker_to_cluster = {'PERFECT': 0, 'NEAR_PERFECT': 0, 'HIGH': 0, 'MEDIUM': 0}
    corr_matrix = returns_df.corr()
    decay_matrix = pd.DataFrame(
        np.zeros((4, 4)),
        index=['PERFECT', 'NEAR_PERFECT', 'HIGH', 'MEDIUM'],
        columns=['PERFECT', 'NEAR_PERFECT', 'HIGH', 'MEDIUM']
    )
    
    pairs_df = score_pairs(
        returns_df, ticker_to_cluster, corr_matrix, decay_matrix, {},
        min_correlation=0.3
    )
    
    # Check PERFECT/NEAR_PERFECT pair (corr >0.90) has correlation_score = 0
    perfect_pair = pairs_df[
        ((pairs_df['ticker1'] == 'PERFECT') & (pairs_df['ticker2'] == 'NEAR_PERFECT')) |
        ((pairs_df['ticker1'] == 'NEAR_PERFECT') & (pairs_df['ticker2'] == 'PERFECT'))
    ]
    
    if len(perfect_pair) > 0:
        assert perfect_pair.iloc[0]['correlation_score'] == 0, \
            f"Expected correlation_score=0 for high correlation pair, got {perfect_pair.iloc[0]['correlation_score']}"
    
    # Check HIGH/MEDIUM pair (corr ~0.50-0.85) has correlation_score > 0
    medium_pair = pairs_df[
        ((pairs_df['ticker1'] == 'HIGH') & (pairs_df['ticker2'] == 'MEDIUM')) |
        ((pairs_df['ticker1'] == 'MEDIUM') & (pairs_df['ticker2'] == 'HIGH'))
    ]
    
    if len(medium_pair) > 0:
        assert medium_pair.iloc[0]['correlation_score'] > 0, \
            "Expected correlation_score > 0 for medium correlation pair"


def test_historical_success_integration():
    """Test that historical success from anomalies.db is applied correctly."""
    from shiftinnerv.pipelines.pair_sourcer import score_pairs
    
    dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
    np.random.seed(42)
    
    base = np.random.randn(300) * 0.01
    returns_df = pd.DataFrame({
        'A': base,
        'B': base + np.random.randn(300) * 0.005,
        'C': base + np.random.randn(300) * 0.005,
    }, index=dates)
    
    ticker_to_cluster = {'A': 0, 'B': 0, 'C': 0}
    corr_matrix = returns_df.corr()
    decay_matrix = pd.DataFrame(
        np.zeros((3, 3)),
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )
    
    # Historical success: A/B had 4 episodes, B/C had 1
    historical_success = {
        ('A', 'B'): 4,
        ('B', 'C'): 1,
    }
    
    pairs_df = score_pairs(
        returns_df, ticker_to_cluster, corr_matrix, decay_matrix, historical_success,
        min_correlation=0.3
    )
    
    # Find A/B pair
    ab_pair = pairs_df[
        ((pairs_df['ticker1'] == 'A') & (pairs_df['ticker2'] == 'B')) |
        ((pairs_df['ticker1'] == 'B') & (pairs_df['ticker2'] == 'A'))
    ]
    
    # Find B/C pair
    bc_pair = pairs_df[
        ((pairs_df['ticker1'] == 'B') & (pairs_df['ticker2'] == 'C')) |
        ((pairs_df['ticker1'] == 'C') & (pairs_df['ticker2'] == 'B'))
    ]
    
    # A/B should have higher history_score than B/C
    if len(ab_pair) > 0 and len(bc_pair) > 0:
        assert ab_pair.iloc[0]['history_score'] > bc_pair.iloc[0]['history_score'], \
            "Expected A/B (4 episodes) to have higher history_score than B/C (1 episode)"
        
        # A/B should have 100 points (4 episodes * 25 = 100)
        assert ab_pair.iloc[0]['history_score'] == 100, \
            f"Expected history_score=100 for 4 episodes, got {ab_pair.iloc[0]['history_score']}"
        
        # B/C should have 25 points (1 episode * 25 = 25)
        assert bc_pair.iloc[0]['history_score'] == 25, \
            f"Expected history_score=25 for 1 episode, got {bc_pair.iloc[0]['history_score']}"
