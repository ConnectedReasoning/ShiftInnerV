#!/usr/bin/env python3
"""
Asset class diagnostic query — Compare Treasury, Currency, and Equity pair performance
"""

import sqlite3
import pandas as pd
from pathlib import Path

# Update this path to your actual data location
db_path = '/Volumes/Elessar/ShiftInnerV_Data/anomalies.db'

# Check if database exists
if not Path(db_path).exists():
    print(f"ERROR: Database not found at {db_path}")
    print("Update the db_path variable to point to your anomalies.db")
    exit(1)

print(f"Connecting to: {db_path}")
print()

conn = sqlite3.connect(db_path)

# Query 1: Asset class breakdown
query = """
SELECT
  CASE
    WHEN (ticker1 LIKE '%=%' OR ticker2 LIKE '%=%') THEN 'Currency'
    WHEN (ticker1 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')
       OR ticker2 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')) THEN 'Treasury/Bond'
    ELSE 'Equity'
  END as asset_class,
  COUNT(DISTINCT CASE WHEN ticker1 < ticker2 THEN ticker1||'/'||ticker2 ELSE ticker2||'/'||ticker1 END) as unique_pairs,
  COUNT(*) as total_episodes,
  ROUND(AVG(rating), 1) as avg_rating,
  ROUND(AVG(episodes), 1) as avg_episodes_per_pair,
  MIN(rating) as min_rating,
  MAX(rating) as max_rating
FROM screening
WHERE episodes > 0
GROUP BY asset_class
ORDER BY avg_rating DESC
"""

print("="*80)
print("ASSET CLASS PERFORMANCE COMPARISON")
print("="*80)
print()

df = pd.read_sql_query(query, conn)
print(df.to_string(index=False))
print()

# Query 2: Top pairs by asset class
print("="*80)
print("TOP PAIRS BY ASSET CLASS")
print("="*80)
print()

for asset_class in ['Treasury/Bond', 'Currency', 'Equity']:
    print(f"\n{asset_class.upper()}")
    print("-"*80)

    query2 = f"""
    SELECT
      ticker1,
      ticker2,
      COUNT(DISTINCT DATE(timestamp)) as episodes,
      ROUND(AVG(rating), 1) as avg_rating,
      MAX(rating) as best_rating
    FROM screening
    WHERE episodes > 0
      AND (
        CASE
          WHEN (ticker1 LIKE '%=%' OR ticker2 LIKE '%=%') THEN 'Currency'
          WHEN (ticker1 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')
             OR ticker2 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')) THEN 'Treasury/Bond'
          ELSE 'Equity'
        END
      ) = '{asset_class}'
    GROUP BY ticker1, ticker2
    ORDER BY episodes DESC
    LIMIT 10
    """

    df2 = pd.read_sql_query(query2, conn)
    if len(df2) > 0:
        print(df2.to_string(index=False))
    else:
        print(f"No {asset_class} pairs found")

print()
print("="*80)

# Query 3: Rating distribution by asset class
print("\nRATING DISTRIBUTION BY ASSET CLASS")
print("-"*80)
print()

query3 = """
SELECT
  CASE
    WHEN (ticker1 LIKE '%=%' OR ticker2 LIKE '%=%') THEN 'Currency'
    WHEN (ticker1 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')
       OR ticker2 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')) THEN 'Treasury/Bond'
    ELSE 'Equity'
  END as asset_class,
  CASE
    WHEN rating >= 90 THEN '★★★ PRIME (90-100)'
    WHEN rating >= 75 THEN '★★ STRONG (75-90)'
    WHEN rating >= 60 THEN '★ SOLID (60-75)'
    WHEN rating >= 40 THEN '◆ WATCH (40-60)'
    ELSE '✗ WEAK (<40)'
  END as grade,
  COUNT(*) as episodes
FROM screening
WHERE episodes > 0
GROUP BY asset_class, grade
ORDER BY asset_class, grade DESC
"""

df3 = pd.read_sql_query(query3, conn)
print(df3.to_string(index=False))

print()
print("="*80)
print("INTERPRETATION")
print("="*80)
print()

# Calculate percentages
df_pct = pd.read_sql_query("""
SELECT
  CASE
    WHEN (ticker1 LIKE '%=%' OR ticker2 LIKE '%=%') THEN 'Currency'
    WHEN (ticker1 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')
       OR ticker2 IN ('IEF', 'LQD', 'SHY', 'TLT', 'AGG', 'BND', 'MBB', 'VCIT', 'VCSH')) THEN 'Treasury/Bond'
    ELSE 'Equity'
  END as asset_class,
  ROUND(100.0 * COUNT(CASE WHEN rating >= 90 THEN 1 END) / COUNT(*), 1) as pct_prime,
  ROUND(100.0 * COUNT(CASE WHEN rating >= 75 THEN 1 END) / COUNT(*), 1) as pct_strong,
  ROUND(100.0 * COUNT(CASE WHEN rating >= 60 THEN 1 END) / COUNT(*), 1) as pct_solid
FROM screening
WHERE episodes > 0
GROUP BY asset_class
ORDER BY pct_prime DESC
""", conn)

for _, row in df_pct.iterrows():
    ac = row['asset_class']
    prime = row['pct_prime']
    strong = row['pct_strong']
    solid = row['pct_solid']
    print(f"{ac:20s}: {prime:5.1f}% PRIME, {strong:5.1f}% STRONG, {solid:5.1f}% SOLID")

print()
print("✓ If Treasury/Bond has the highest % PRIME → Treasury pairs are your best edge")
print("✓ If Currency has the highest % PRIME → Currency pairs are your best edge")
print("✓ If they're similar → Asset class doesn't matter; individual pairs do")

conn.close()
