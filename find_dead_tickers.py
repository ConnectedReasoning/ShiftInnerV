"""
Identify tickers in skew_ledger that have no usable norm_skew data.
These are candidates for removal from the universe YAML.
"""
import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "trial_ledger.db"

conn = sqlite3.connect(DB_PATH)
c    = conn.cursor()

# All tickers ever seen
c.execute("SELECT DISTINCT ticker FROM skew_ledger ORDER BY ticker")
all_tickers = [r[0] for r in c.fetchall()]

dead     = []   # zero norm_skew rows
partial  = []   # has some norm_skew rows but mostly null
healthy  = []   # mostly valid

for t in all_tickers:
    c.execute("SELECT COUNT(*) FROM skew_ledger WHERE ticker = ?", (t,))
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM skew_ledger WHERE ticker = ? AND norm_skew IS NOT NULL", (t,))
    valid = c.fetchone()[0]

    if total == 0:
        continue
    if valid == 0:
        dead.append((t, total))
    elif valid < total / 2:
        partial.append((t, valid, total))
    else:
        healthy.append((t, valid, total))

print(f"\n=== Total tickers in skew_ledger: {len(all_tickers)} ===\n")
print(f"Healthy   ({len(healthy)}): regular data")
print(f"Partial   ({len(partial)}): some data, some failures")
print(f"Dead      ({len(dead)}): zero usable rows — candidates for removal\n")

if dead:
    print("=== DEAD TICKERS (drop from universe) ===")
    for t, total in dead:
        print(f"  {t:<8s}  attempted {total}x, never produced valid skew")

if partial:
    print("\n=== PARTIAL TICKERS (watch — may need to drop) ===")
    for t, valid, total in partial:
        print(f"  {t:<8s}  {valid}/{total} valid rows ({100*valid/total:.0f}%)")

conn.close()
