"""
Quick Backtest — Validate sourced pairs before screening.

For each sourced pair, backtests 30-day mean reversion:
  Entry: spread > 2 sigma from 30-day rolling mean
  Exit:  spread < 0.5 sigma OR 20 days, whichever first

Output: Sharpe ratio, win rate, avg return for each pair.
This tells you which pairs are actually tradeable.
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import logging


def backtest_pair(ticker1: str, ticker2: str, lookback_days: int = 252, 
                  entry_zscore: float = 2.0, exit_zscore: float = 0.5,
                  max_hold_days: int = 20, window_days: int = 30) -> dict:
    """
    Backtest a single pair on 30-day mean reversion.
    """
    
    try:
        # Download data — with timeout protection
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        
        try:
            df1 = yf.download(ticker1, start=start_date, end=end_date, progress=False, timeout=10)['Close']
            df2 = yf.download(ticker2, start=start_date, end=end_date, progress=False, timeout=10)['Close']
        except Exception as dl_err:
            return {
                'pair': f"{ticker1}/{ticker2}",
                'trades': 0,
                'wins': 0,
                'win_rate': 0.0,
                'avg_return_bps': 0.0,
                'sharpe': 0.0,
                'error': f"Download failed: {str(dl_err)[:50]}"
            }
        
        if df1 is None or df2 is None or len(df1) < window_days or len(df2) < window_days:
            return {
                'pair': f"{ticker1}/{ticker2}",
                'trades': 0,
                'wins': 0,
                'win_rate': 0.0,
                'avg_return_bps': 0.0,
                'sharpe': 0.0,
                'error': f"Insufficient data"
            }
        
        # Align dates
        common_dates = df1.index.intersection(df2.index)
        df1 = df1.loc[common_dates]
        df2 = df2.loc[common_dates]
        
        # Log prices and spread
        log_prices_1 = np.log(df1)
        log_prices_2 = np.log(df2)
        spread = log_prices_1 - log_prices_2
        
        # Rolling mean and std
        rolling_mean = spread.rolling(window=window_days).mean()
        rolling_std = spread.rolling(window=window_days).std()
        
        # Z-score
        z_score = (spread - rolling_mean) / rolling_std
        
        # Backtest logic
        trades = []
        in_trade = False
        entry_price = None
        entry_idx = None
        
        for i in range(window_days, len(z_score)):
            z = z_score.iloc[i]
            price = spread.iloc[i]
            
            if not in_trade and z > entry_zscore:
                # Entry signal
                in_trade = True
                entry_price = price
                entry_idx = i
            
            elif in_trade:
                # Check exit conditions
                should_exit = False
                
                # Exit 1: Spread < 0.5 sigma (mean reversion happened)
                if z < exit_zscore:
                    should_exit = True
                    exit_type = "reversion"
                
                # Exit 2: Max hold days reached
                elif i - entry_idx >= max_hold_days:
                    should_exit = True
                    exit_type = "timeout"
                
                if should_exit:
                    exit_price = price
                    pnl_bps = (exit_price - entry_price) * 10000  # basis points
                    is_win = pnl_bps > 0
                    
                    trades.append({
                        'entry_z': z_score.iloc[entry_idx],
                        'exit_z': z,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl_bps': pnl_bps,
                        'is_win': is_win,
                        'hold_days': i - entry_idx,
                        'exit_type': exit_type
                    })
                    
                    in_trade = False
                    entry_price = None
                    entry_idx = None
        
        if len(trades) == 0:
            return {
                'pair': f"{ticker1}/{ticker2}",
                'trades': 0,
                'wins': 0,
                'win_rate': 0.0,
                'avg_return_bps': 0.0,
                'sharpe': 0.0,
                'error': "No trades triggered"
            }
        
        # Calculate metrics
        trades_df = pd.DataFrame(trades)
        num_trades = len(trades_df)
        num_wins = trades_df['is_win'].sum()
        win_rate = num_wins / num_trades * 100
        avg_return = trades_df['pnl_bps'].mean()
        
        # Sharpe ratio (assuming 252 trading days/year)
        returns = trades_df['pnl_bps'].values
        if len(returns) > 1:
            std_return = np.std(returns)
            if std_return > 0:
                sharpe = (avg_return / std_return) * np.sqrt(252)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0
        
        return {
            'pair': f"{ticker1}/{ticker2}",
            'trades': num_trades,
            'wins': int(num_wins),
            'win_rate': win_rate,
            'avg_return_bps': avg_return,
            'sharpe': sharpe,
            'error': None
        }
    
    except Exception as e:
        return {
            'pair': f"{ticker1}/{ticker2}",
            'error': str(e)
        }


def backtest_sourced_pairs(sourced_pairs: list, lookback_days: int = 252) -> list:
    """
    Backtest all sourced pairs.
    
    Args:
        sourced_pairs: List of {'ticker1', 'ticker2', 'score', 'corr'}
        lookback_days: Historical period to test
    
    Returns:
        List of backtest results, sorted by Sharpe ratio (descending)
    """
    
    results = []
    for pair in sourced_pairs:
        result = backtest_pair(
            pair['ticker1'],
            pair['ticker2'],
            lookback_days=lookback_days
        )
        results.append(result)
    
    # Sort by Sharpe ratio
    valid_results = [r for r in results if r.get('error') is None]
    valid_results.sort(key=lambda x: x.get('sharpe', 0), reverse=True)
    
    return valid_results
