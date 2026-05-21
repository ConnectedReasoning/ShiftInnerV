"""
shiftinnerv.services — I/O-facing modules: the boundary between the pure
domain and the outside world (filesystem, SQLite).

Unlike domain modules, services are explicitly allowed to read files,
query databases, and interact with the OS. Unlike sensors, services are
not evaluation modules — they don't compute verdicts or scores. They
own the data layer: loading, storing, and managing persistent state.

Modules:
    data_manager  — CSV price data access; staleness checks; yfinance
                    download orchestration (ensure_data).
    trial_ledger  — SQLite trial ledger: schema init, verdict recording,
                    position tracking, revalidation history, summaries.
"""
