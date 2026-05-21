"""
shiftinnerv.domain — Pure logic and data shapes.

Modules in this package have NO I/O dependencies. They:
  - Do not read files, query databases, or hit the network.
  - Do not depend on environment variables or wall-clock time.
  - Take inputs as arguments, return outputs as values.
  - Can be tested by passing literals and asserting return values.

This is the most stable layer. If you need to add `import sqlite3`,
`import yfinance`, or `import os` to something in here, it doesn't
belong in domain.

Modules:
    gate_evaluator  — Deterministic five-gate verdict evaluation (Item 4).
    cost_model      — Transaction cost and net P&L estimation (Item 3).
    spread_math     — Half-life, SNR, Johansen, BH correction, scoring
                      (extracted from monitor.py, step 2).
    position_math   — SNR-from-prices, mean drift detection, result container
                      (extracted from position_monitor.py, step 3).
    regime_math     — RegimeState, RegimeSnapshot, classify_regime,
                      get_position_size_multiplier
                      (extracted from regime_monitor.py, step 3).
"""
