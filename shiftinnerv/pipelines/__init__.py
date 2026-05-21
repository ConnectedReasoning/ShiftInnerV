"""
shiftinnerv.pipelines — Multi-step orchestration workflows.

Modules in this package coordinate domain, sensor, and service layers
to produce a complete result: a screening run, a dossier, a summary,
a pair generation pass, or a CrewAI agent crew.

Unlike domain (pure math) or services (raw I/O), pipelines own the
*sequence* of operations: load data → evaluate → record → report.

Modules:
    monitor        — Screening and monitoring loop (run_screening, run_monitor).
    dossier        — Per-pair research dossier generation.
    summarize      — Post-run LLM summary of screening results.
    generate_pairs — Pair generation from universe.yaml → compositions/.
    agents         — CrewAI agent definitions (requires crewai).
    tasks          — CrewAI task definitions (requires crewai).

Entry points (root-level, NOT in this package):
    main.py, sentinel.py, run_all.py, promote.py
"""
