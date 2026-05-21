"""
shiftinnerv.domain — Pure logic and data shapes.

Modules in this package have NO I/O dependencies. They:
  - Do not read files, query databases, or hit the network.
  - Do not depend on environment variables or wall-clock time.
  - Take inputs as arguments, return outputs as values.
  - Can be tested by passing literals and asserting return values.

This is the layer that should be the easiest to reason about and the
most stable across refactors. If you need to add an `import sqlite3`
or `import yfinance` to something in here, it doesn't belong in domain.

Modules:
    gate_evaluator — Deterministic five-gate verdict evaluation (Item 4).
    cost_model     — Transaction cost and net P&L estimation (Item 3).
    spread_math    — Pure math for spread analysis: half-life, SNR,
                     Johansen, BH correction, scoring. (extracted from
                     monitor.py in step 2 of the reorganization)
"""
