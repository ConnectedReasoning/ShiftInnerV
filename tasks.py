from crewai import Task


def build_tasks(pair: dict, agents: tuple) -> tuple:
    """
    Build the three-task crew pipeline for a single pair.

    Parameters
    ----------
    pair : dict
        A single entry from pairs.yaml — contains ticker1, ticker2, label,
        relationship, leading_indicators, notes, etc.
    agents : tuple
        (quant_scout, forensic_researcher, skeptic_analyst)

    Returns
    -------
    tuple of (correlation_audit, anomaly_investigation, divergence_report)
    """
    quant_scout, forensic_researcher, skeptic_analyst = agents

    ticker1            = pair["ticker1"]
    ticker2            = pair["ticker2"]
    label              = pair["label"]
    relationship       = pair.get("relationship", "Not specified.")
    lead               = pair.get("lead", "Unknown")
    lag_days           = pair.get("lag_days", 0)
    leading_indicators = pair.get("leading_indicators", [])
    notes              = pair.get("notes", "")

    indicators_str = "\n    - ".join(leading_indicators) if leading_indicators else "None specified."

    # ── Task 1 — The Math ─────────────────────────────────────────────────────
    correlation_audit = Task(
        description=f"""Run the correlation_decay_analyzer tool with
    ticker1='{ticker1}' and ticker2='{ticker2}'.

    After the tool returns, copy its COMPLETE output text into your
    final answer exactly as-is. Do not summarize it. Do not reformat it.
    Do not call any other tool. The tool output starts with the line:
    === CORRELATION DECAY REPORT ===
    Your final answer must start with that same line.""",
        expected_output=f"""The complete verbatim text output from the
    correlation_decay_analyzer tool, starting with:
    === CORRELATION DECAY REPORT ===
    Include every line the tool returned, unchanged.""",
        agent=quant_scout
    )

    # ── Task 2 — The Why ─────────────────────────────────────────────────────
    anomaly_investigation = Task(
        description=f"""The Scout has identified decoupling episodes for
    {label} ({ticker1} / {ticker2}). Search for macro context.

    Known structural relationship:
    {relationship}

    Expected lead ticker: {lead} (lag ~{lag_days} days)

    Search for context around each episode onset date the Scout found.
    Use these plain text search queries — one at a time, no JSON:
    - {ticker1} {ticker2} rare earth 2024
    - China rare earth export controls January 2024
    - rare earth semiconductor supply 2024

    Also check these known leading indicators:
    - {indicators_str}

    Additional context:
    {notes}

    Report what you find tied to specific dates.
    If search results are empty or irrelevant, say so plainly.
    Do not invent explanations. Do not wrap queries in JSON or brackets.""",
        expected_output=f"""Specific news events or macro context tied to each
    flagged episode onset date for {label}. Plain text summary of findings.
    Explicit statement if nothing relevant was found.""",
        agent=forensic_researcher,
        context=[correlation_audit]
    )

    # ── Task 3 — Signal Quality ───────────────────────────────────────────────
    divergence_report = Task(
        description=f"""Review the Scout's correlation report and the
    Researcher's context findings for {label} ({ticker1} / {ticker2}).

    Structural tether for this pair:
    {relationship}

    KEY FACTS FROM THE SCOUT'S TOOL (use these directly):
    - Johansen cointegration result: NOT cointegrated at 95% CI
      (trace stat below critical value — structural tether is UNCERTAIN)
    - Half-life of spread mean reversion: ~1021 days
      (this is VERY slow — a half-life above 250 days weakens the signal
      because the spread takes years to mean-revert, making pairs trading
      impractical at normal holding periods)
    - Rolling window used: 120 days (hit the maximum clamp)
    - One distinct decoupling episode: onset 2024-01-31, 72 days duration,
      worst correlation -0.728, -2.5 std devs below baseline mean

    Produce a signal quality assessment:
    - Is the single episode statistically meaningful or volatile-period noise?
    - Does the macro context explain the pattern or leave it unexplained?
    - Does the NOT cointegrated result strengthen or weaken the signal?
    - Does the 1021-day half-life support or undermine a pairs trade thesis?
    - Is this worth monitoring as a persistent structural shift?
    - Rate the signal: Strong / Moderate / Weak / Noise

    Be specific about what would need to be true for this to become
    a Strong signal worth acting on in Phase 2.""",
        expected_output=f"""Signal quality rating (Strong/Moderate/Weak/Noise)
    for {label} with clear justification referencing the cointegration result,
    half-life, and episode data. Specific conditions required to elevate
    the signal to Strong.""",
        agent=skeptic_analyst,
        context=[correlation_audit, anomaly_investigation]
    )

    return correlation_audit, anomaly_investigation, divergence_report
