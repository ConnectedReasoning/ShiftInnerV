#!/usr/bin/env python3
"""
Pair Sourcing Diagnostic — Compare historical cointegrations to today's sourced pairs.
Run this to understand if your sourcing algorithm is finding the right candidates.

Usage:
    python diagnostic_pairs.py /path/to/anomalies.db
"""

import sqlite3
import pandas as pd
import sys
from pathlib import Path

def run_diagnostic(db_path: str):
    """Run diagnostic on pair sourcing effectiveness."""
    
    if not Path(db_path).exists():
        print(f"ERROR: {db_path} not found")
        sys.exit(1)
    
    conn = sqlite3.connect(db_path)
    
    print("=" * 90)
    print("PAIR SOURCING DIAGNOSTIC — Historical vs Today's Candidates")
    print("=" * 90)
    print()
    
    # 1. Score distribution of historically cointegrated pairs
    print("1. HISTORICAL COINTEGRATION DISTRIBUTION")
    print("-" * 90)
    query = """
    SELECT 
      ticker1, ticker2,
      COUNT(DISTINCT DATE(timestamp)) as episodes,
      ROUND(AVG(rating), 1) as avg_rating,
      ROUND(MAX(rating), 1) as max_rating,
      ROUND(MIN(rating), 1) as min_rating
    FROM screening
    WHERE episodes > 0
    GROUP BY 
      CASE WHEN ticker1 < ticker2 THEN ticker1 ELSE ticker2 END,
      CASE WHEN ticker1 < ticker2 THEN ticker2 ELSE ticker1 END
    ORDER BY episodes DESC
    LIMIT 30
    """
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    print()
    
    # 2. Rating distribution
    print("2. RATING GRADES (pairs with episodes > 0)")
    print("-" * 90)
    query = """
    SELECT 
      CASE 
        WHEN rating >= 90 THEN '★★★ PRIME (90-100)'
        WHEN rating >= 75 THEN '★★ STRONG (75-90)'
        WHEN rating >= 60 THEN '★ SOLID (60-75)'
        WHEN rating >= 40 THEN '◆ WATCH (40-60)'
        ELSE '✗ WEAK (<40)'
      END as grade,
      COUNT(*) as pair_count,
      ROUND(AVG(rating), 1) as avg_rating
    FROM screening
    WHERE episodes > 0
    GROUP BY grade
    ORDER BY avg_rating DESC
    """
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    print()
    
    # 3. Episode frequency
    print("3. EPISODE FREQUENCY (how often do pairs cointegrate?)")
    print("-" * 90)
    query = """
    SELECT 
      episodes,
      COUNT(*) as pair_count,
      ROUND(AVG(rating), 1) as avg_rating
    FROM screening
    WHERE episodes > 0
    GROUP BY episodes
    ORDER BY episodes DESC
    LIMIT 15
    """
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    print()
    
    # 4. Key statistics
    print("4. KEY STATISTICS")
    print("-" * 90)
    query = """
    SELECT 
      COUNT(DISTINCT 
        CASE WHEN ticker1 < ticker2 THEN ticker1||'/'||ticker2 
             ELSE ticker2||'/'||ticker1 END
      ) as unique_pairs_ever_cointegrated,
      COUNT(*) as total_episodes,
      ROUND(AVG(rating), 2) as avg_rating_when_cointegrated,
      MIN(rating) as min_rating,
      MAX(rating) as max_rating
    FROM screening
    WHERE episodes > 0
    """
    df = pd.read_sql_query(query, conn)
    for col in df.columns:
        val = df[col].values[0]
        print(f"  {col:40s}: {val}")
    print()
    
    # 5. The critical question: score distribution by rating tier
    print("5. SCORE INSIGHT — What scores do profitable pairs have?")
    print("-" * 90)
    query = """
    SELECT 
      CASE 
        WHEN rating >= 90 THEN '★★★ PRIME'
        WHEN rating >= 75 THEN '★★ STRONG'
        WHEN rating >= 60 THEN '★ SOLID'
        WHEN rating >= 40 THEN '◆ WATCH'
        ELSE '✗ WEAK'
      END as grade,
      COUNT(*) as episodes_in_grade,
      ROUND(AVG(rating), 1) as avg_rating,
      MIN(rating) as min_rating,
      MAX(rating) as max_rating
    FROM screening
    WHERE episodes > 0
    GROUP BY grade
    ORDER BY avg_rating DESC
    """
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    print()
    
    # 6. Today's top sourced pairs
    print("6. TODAY'S TOP SOURCED PAIRS (from sourced_YYYYMMDD.yaml)")
    print("-" * 90)
    today_pairs = [
        ("AUDJPY=X", "CADJPY=X", 60.3),
        ("VLUE", "XLI", 59.6),
        ("EEM", "FAN", 59.5),
        ("FCX", "REMX", 59.4),
        ("EWJ", "IWM", 59.1),
    ]
    
    print(f"{'Pair':<25} {'Score':<8} {'History':<50}")
    print("-" * 90)
    
    for t1, t2, score in today_pairs:
        query = f"""
        SELECT COUNT(*) as count, ROUND(AVG(rating), 1) as avg_rating
        FROM screening
        WHERE (ticker1 = ? AND ticker2 = ?) 
           OR (ticker1 = ? AND ticker2 = ?)
        """
        result = pd.read_sql_query(query, conn, params=(t1, t2, t2, t1))
        count = int(result['count'].values[0])
        avg_rating = result['avg_rating'].values[0]
        
        if count > 0:
            history = f"✓ {count:2d} episodes, avg_rating={avg_rating}"
        else:
            history = "✗ NEVER cointegrated before"
        
        print(f"{t1:12s}/{t2:12s} {score:6.1f}  {history}")
    
    print()
    
    # 7. The verdict
    print("7. INTERPRETATION")
    print("-" * 90)
    
    # Calculate percentages
    query = """
    SELECT 
      COUNT(CASE WHEN rating >= 90 THEN 1 END) as prime_count,
      COUNT(CASE WHEN rating >= 75 THEN 1 END) as strong_count,
      COUNT(CASE WHEN rating >= 60 THEN 1 END) as solid_count,
      COUNT(*) as total_episodes
    FROM screening
    WHERE episodes > 0
    """
    df = pd.read_sql_query(query, conn)
    prime_pct = (df['prime_count'].values[0] / df['total_episodes'].values[0] * 100) if df['total_episodes'].values[0] > 0 else 0
    strong_pct = (df['strong_count'].values[0] / df['total_episodes'].values[0] * 100) if df['total_episodes'].values[0] > 0 else 0
    solid_pct = (df['solid_count'].values[0] / df['total_episodes'].values[0] * 100) if df['total_episodes'].values[0] > 0 else 0
    
    print(f"  Of all cointegration episodes:")
    print(f"    {prime_pct:5.1f}% are PRIME (rating >= 90)")
    print(f"    {strong_pct:5.1f}% are STRONG (rating >= 75)")
    print(f"    {solid_pct:5.1f}% are SOLID (rating >= 60)")
    print()
    print(f"  Today's top pair scores: 60.3, 59.6, 59.5, 59.4, 59.1")
    print(f"  Historical pairs with these scores: Check 'Rating Grades' section")
    print()
    
    if prime_pct > 20:
        print("  ✓ GOOD SIGN: >20% of episodes are PRIME grade")
        print("    Your universe contains genuinely high-quality pairs.")
    else:
        print("  ⚠ CAUTION: <20% of episodes are PRIME grade")
        print("    Most cointegrations are noisy or weak.")
    
    if prime_pct < 5:
        print()
        print("  ✗ RED FLAG: Almost no PRIME episodes")
        print("    Possible issues:")
        print("    1. Universe is too broad (commodity ETFs, currency pairs)")
        print("    2. Historical data is old (correlations have shifted)")
        print("    3. Scoring weights don't align with tradeable signal")
    
    conn.close()
    print()
    print("=" * 90)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python diagnostic_pairs.py /path/to/anomalies.db")
        print()
        print("Example:")
        print("  python diagnostic_pairs.py /Volumes/Elessar/ShiftInnerV_Data/anomalies.db")
        sys.exit(1)
    
    run_diagnostic(sys.argv[1])
