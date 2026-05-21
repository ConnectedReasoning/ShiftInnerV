"""
shiftinnerv.sensors — Evaluation modules that touch the outside world.

A sensor takes inputs (prices, ledger state, market data) and returns a
verdict, score, or decision. Unlike domain modules, sensors are allowed
to read CSVs, query SQLite, or hit the network — anything they need to
gather inputs. They should not, however, hold state across invocations.

Modules:
    correlation         — OLS-based correlation and cointegration analysis
                          (reads price CSVs).
    position_monitor    — Open-position SNR revalidation and drift detection
                          (reads ledger + price data).
    regime_monitor      — Market regime classification via VIX + pair-SPY
                          correlation (hits yfinance + reads CSVs).
    composition_monitor — Concentration limits per composition category
                          (reads ledger + YAML compositions).

Pure math used by these sensors (and elsewhere) lives in shiftinnerv.domain.
"""
