#!/usr/bin/env python3
"""
Tests for CSV format compatibility in pair_sourcer.py

Tests various CSV formats that data_manager.py might produce:
  - Different date column names (Date, date, DATE)
  - Different price column names (Close, adjClose, Adj Close, close)
  - Date as index vs. date as column
  - Mixed case variations
"""

import os
import pytest
import tempfile
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def test_csv_format_date_capital_close():
    """Test CSV with 'Date' (capital) and 'Close' columns (real format from user)."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create CSV matching user's actual format
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        df = pd.DataFrame({
            'Date': dates,
            'Close': np.random.randn(300) * 0.01 + 100,
            'High': np.random.randn(300) * 0.01 + 101,
            'Low': np.random.randn(300) * 0.01 + 99,
            'Open': np.random.randn(300) * 0.01 + 100,
            'Volume': np.random.randint(1000000, 10000000, 300),
        })
        
        csv_path = os.path.join(tmpdir, 'aapl_daily.csv')
        df.to_csv(csv_path, index=False)
        
        # Load returns
        returns_df = load_returns(['AAPL'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        # Verify successful load
        assert len(returns_df) > 0
        assert 'AAPL' in returns_df.columns
        assert returns_df.index.name == 'date'


def test_csv_format_lowercase_date_adjclose():
    """Test CSV with 'date' (lowercase) and 'adjClose' columns."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        df = pd.DataFrame({
            'date': dates,
            'adjClose': np.random.randn(300) * 0.01 + 100,
        })
        
        csv_path = os.path.join(tmpdir, 'msft_daily.csv')
        df.to_csv(csv_path, index=False)
        
        returns_df = load_returns(['MSFT'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        assert len(returns_df) > 0
        assert 'MSFT' in returns_df.columns


def test_csv_format_adj_close_with_space():
    """Test CSV with 'Adj Close' (space) column."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        df = pd.DataFrame({
            'Date': dates,
            'Adj Close': np.random.randn(300) * 0.01 + 100,
        })
        
        csv_path = os.path.join(tmpdir, 'googl_daily.csv')
        df.to_csv(csv_path, index=False)
        
        returns_df = load_returns(['GOOGL'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        assert len(returns_df) > 0
        assert 'GOOGL' in returns_df.columns


def test_csv_format_date_as_index():
    """Test CSV with date as index (no date column)."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        df = pd.DataFrame({
            'Close': np.random.randn(300) * 0.01 + 100,
        }, index=dates)
        df.index.name = 'date'
        
        csv_path = os.path.join(tmpdir, 'tsla_daily.csv')
        df.to_csv(csv_path)  # saves index as first column
        
        returns_df = load_returns(['TSLA'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        assert len(returns_df) > 0
        assert 'TSLA' in returns_df.columns


def test_csv_format_uppercase_date():
    """Test CSV with 'DATE' (all uppercase)."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        df = pd.DataFrame({
            'DATE': dates,
            'CLOSE': np.random.randn(300) * 0.01 + 100,
        })
        
        csv_path = os.path.join(tmpdir, 'nvda_daily.csv')
        df.to_csv(csv_path, index=False)
        
        returns_df = load_returns(['NVDA'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        assert len(returns_df) > 0
        assert 'NVDA' in returns_df.columns


def test_csv_format_missing_date_column_fails():
    """Test that CSV without any date column fails gracefully."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # CSV with no date-like column at all
        df = pd.DataFrame({
            'Close': np.random.randn(300) * 0.01 + 100,
            'Volume': np.random.randint(1000000, 10000000, 300),
        })
        
        csv_path = os.path.join(tmpdir, 'bad_daily.csv')
        df.to_csv(csv_path, index=False)
        
        # Should skip this ticker
        returns_df = load_returns(['BAD'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        # No tickers loaded
        assert len(returns_df.columns) == 0


def test_csv_format_missing_price_column_fails():
    """Test that CSV without any price column fails gracefully."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        df = pd.DataFrame({
            'Date': dates,
            'Volume': np.random.randint(1000000, 10000000, 300),
        })
        
        csv_path = os.path.join(tmpdir, 'noprice_daily.csv')
        df.to_csv(csv_path, index=False)
        
        # Should skip this ticker
        returns_df = load_returns(['NOPRICE'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        assert len(returns_df.columns) == 0


def test_csv_format_multiple_tickers_mixed_formats():
    """Test loading multiple tickers with different CSV formats simultaneously."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        
        # Ticker 1: Date + Close (user's format)
        df1 = pd.DataFrame({
            'Date': dates,
            'Close': np.random.randn(300) * 0.01 + 100,
        })
        df1.to_csv(os.path.join(tmpdir, 'aapl_daily.csv'), index=False)
        
        # Ticker 2: date + adjClose
        df2 = pd.DataFrame({
            'date': dates,
            'adjClose': np.random.randn(300) * 0.01 + 200,
        })
        df2.to_csv(os.path.join(tmpdir, 'msft_daily.csv'), index=False)
        
        # Ticker 3: Date as index
        df3 = pd.DataFrame({
            'Close': np.random.randn(300) * 0.01 + 150,
        }, index=dates)
        df3.index.name = 'date'
        df3.to_csv(os.path.join(tmpdir, 'googl_daily.csv'))
        
        # Load all three
        returns_df = load_returns(['AAPL', 'MSFT', 'GOOGL'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        # All three should load successfully
        assert len(returns_df.columns) == 3
        assert 'AAPL' in returns_df.columns
        assert 'MSFT' in returns_df.columns
        assert 'GOOGL' in returns_df.columns
        
        # Returns should be aligned on same date index
        assert returns_df.index.name == 'date'
        assert len(returns_df) > 0


def test_csv_format_insufficient_history():
    """Test that tickers with insufficient history are skipped."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Only 100 days of data (< 252 required for correlation window)
        dates = pd.date_range('2020-01-01', periods=100, freq='D')
        df = pd.DataFrame({
            'Date': dates,
            'Close': np.random.randn(100) * 0.01 + 100,
        })
        
        csv_path = os.path.join(tmpdir, 'new_ipo_daily.csv')
        df.to_csv(csv_path, index=False)
        
        # Should skip due to insufficient history
        returns_df = load_returns(['NEW_IPO'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        assert len(returns_df.columns) == 0


def test_csv_format_returns_calculation():
    """Test that returns are calculated correctly from prices."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
        
        # Create prices with known pattern
        prices = [100.0]
        for i in range(299):
            prices.append(prices[-1] * 1.01)  # 1% daily return
        
        df = pd.DataFrame({
            'Date': dates,
            'Close': prices,
        })
        
        csv_path = os.path.join(tmpdir, 'test_daily.csv')
        df.to_csv(csv_path, index=False)
        
        returns_df = load_returns(['TEST'], skip_download=True, data_dir=tmpdir, lookback_years=1)
        
        # Check returns are approximately 1% (0.01)
        mean_return = returns_df['TEST'].mean()
        assert 0.009 < mean_return < 0.011  # allow small rounding error


def test_csv_format_date_parsing_robustness():
    """Test various date formats are parsed correctly."""
    from shiftinnerv.pipelines.pair_sourcer import load_returns
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test different date string formats
        date_formats = [
            '2020-01-01',  # ISO
            '01/01/2020',  # US format
            '2020/01/01',  # Alternative
        ]
        
        for i, date_fmt in enumerate(date_formats):
            dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=400), periods=300, freq='D')
            df = pd.DataFrame({
                'Date': dates.strftime(date_fmt.replace('2020', '%Y').replace('01', '%m').replace('01', '%d')),
                'Close': np.random.randn(300) * 0.01 + 100,
            })
            
            ticker = f'T{i}'
            csv_path = os.path.join(tmpdir, f'{ticker.lower()}_daily.csv')
            df.to_csv(csv_path, index=False)
            
            # Should parse successfully
            returns_df = load_returns([ticker], skip_download=True, data_dir=tmpdir, lookback_years=1)
            assert len(returns_df) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
