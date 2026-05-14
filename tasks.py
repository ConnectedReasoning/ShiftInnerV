from crewai import Task

# ── ReAct few-shot example injected into Researcher task ─────────────────────
# Based on the ReAct pattern from "Prompt Engineering for LLMs" (Berryman &
# Ziegler, O'Reilly). The 8B llama3.1 model needs an explicit Thought →
# Action → Observation example to reliably execute tools rather than
# describe them. Without this, the model reasons correctly but outputs
# a plan instead of acting.
REACT_EXAMPLE = """
Here is an example of how you must work. Follow this pattern exactly:

Thought: I need to find what happened with Cisco job cuts and AI spending in early 2024.
Action: Use the search_the_internet_with_serper tool.
Action Input: Cisco job cuts AI investment 2024
Observation: [tool returns results]
Thought: The results mention Cisco restructuring to fund AI. I now have context for the episode.
Final Answer: In early 2024, Cisco announced restructuring plans...

Now follow the same Thought → Action → Observation pattern for this task.
Always actually call the tool. Never describe a call — execute it.
"""


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

    # ── Generate pair-specific search queries from indicators ─────────────────
    if leading_indicators:
        seed1 = leading_indicators[0]
        seed2 = leading_indicators[1] if len(leading_indicators) > 1 else ticker1
        search_query_1 = f"{ticker1} {ticker2} 2024"
        search_query_2 = f"{seed1} 2024"
        search_query_3 = f"{seed2} {ticker1} supply chain 2024"
    else:
        search_query_1 = f"{ticker1} {ticker2} 2024"
        search_query_2 = f"{ticker1} {ticker2} decoupling market"
        search_query_3 = f"{ticker1} {ticker2} structural shift"

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
        description=f"""You are researching macro context for decoupling
    episodes in {label} ({ticker1} / {ticker2}).

    Known structural relationship:
    {relationship}

    Expected lead ticker: {lead} (lag ~{lag_days} days)

    Step 1 — Think first. Write out what you are looking for and why
    before taking any action. Consider what events around the episode
    onset date could explain a correlation breakdown for this pair.

    Step 2 — Execute searches one at a time using the search tool.
    Use these plain text queries in order:
    - {search_query_1}
    - {search_query_2}
    - {search_query_3}
    {REACT_EXAMPLE}
    Known leading indicators to inform your search:
    - {indicators_str}

    Additional context:
    {notes}

    Step 3 — Report what you found tied to specific dates.
    If results are empty or irrelevant, say so plainly.
    Do not invent explanations.""",
        expected_output=f"""Plain text summary of specific news events or
    macro context tied to the flagged episode onset dates for {label}.
    Include source names and dates where found.
    Explicit statement if nothing relevant was found for any episode.""",
        agent=forensic_researcher,
        context=[correlation_audit]
    )

    # ── Task 3 — Signal Quality ───────────────────────────────────────────────
    divergence_report = Task(
        description=f"""Review the Scout's correlation report and the
    Researcher's context findings for {label} ({ticker1} / {ticker2}).

    Structural tether for this pair:
    {relationship}

    The Scout's correlation report is in your context. Extract these facts
    directly from it before producing your assessment:
    - Johansen cointegration result (cointegrated YES/NO and confidence level)
    - Half-life of spread mean reversion in days
    - Rolling window used
    - Number of distinct decoupling episodes
    - Worst episode: onset date, duration, worst correlation, worst deviation

    Use these rules when interpreting the facts:
    - NOT cointegrated = structural tether is UNCERTAIN = weakens signal
    - Half-life above 250 days = spread reverts very slowly = weakens signal
    - Half-life below 60 days = spread reverts quickly = strengthens signal
    - Only one episode = insufficient evidence of persistence
    - Rolling window hit 120-day clamp = half-life too long to be useful

    Produce a signal quality assessment:
    - Is each episode statistically meaningful or volatile-period noise?
    - Does the macro context explain the pattern or leave it unexplained?
    - Does the cointegration result strengthen or weaken the signal?
    - Does the half-life support or undermine a pairs trade thesis?
    - Is this worth monitoring as a persistent structural shift?
    - Rate the signal: Strong / Moderate / Weak / Noise

    Be specific about what would need to be true for this to become
    a Strong signal worth acting on in Phase 2.""",
        expected_output=f"""Signal quality rating (Strong/Moderate/Weak/Noise)
    for {label} with clear justification referencing the actual cointegration
    result, half-life, and episode data extracted from the Scout's report.
    Specific conditions required to elevate the signal to Strong.""",
        agent=skeptic_analyst,
        context=[correlation_audit, anomaly_investigation]
    )

    return correlation_audit, anomaly_investigation, divergence_report
