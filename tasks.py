from crewai import Task
from agents import quant_scout, forensic_researcher, skeptic_analyst

# 1. The Quantitative Mission (The Math)
correlation_audit = Task(
    description="""Use the correlation_decay_analyzer tool with
    ticker1='REMX' and ticker2='SOXX', window=30.

    Report:
    - The full date range of the data
    - Total number of decoupling events (correlation below 0.6)
    - The three most severe decoupling periods with specific dates
    - Whether the decoupling is recent, historical, or persistent
    - Direction: are they moving independently or inversely?

    Do not interpret causes. Just report the math.""",
    expected_output="""A factual statistical summary: date range, number of
    decoupling events, worst periods with dates and correlation values,
    and overall relationship characterization.""",
    agent=quant_scout
)

# 2. The Contextual Mission (The Why)
anomaly_investigation = Task(
    description="""Using the specific decoupling periods identified by the
    Scout, search for macro context that explains the relationship shift.

    Search specifically for:
    - 'REMX rare earth 2024 2025'
    - 'semiconductor supply chain 2024 2025'
    - 'China rare earth export restrictions 2024'
    - Any major policy or supply events on the specific dates flagged

    Report what you find. If nothing relevant surfaces, say so plainly
    rather than inventing explanations.""",
    expected_output="""Specific news events or macro context tied to the
    flagged dates. Plain statement if nothing relevant was found.""",
    agent=forensic_researcher,
    context=[correlation_audit]
)

# 3. The Signal Quality Mission
divergence_report = Task(
    description="""Review the Scout's correlation data and the Researcher's
    context findings. Produce a signal quality assessment.

    Evaluate:
    - Is the decoupling statistically meaningful or just volatile period noise?
    - Does the macro context explain the pattern or leave it unexplained?
    - Is this worth monitoring as a persistent structural shift?
    - Rate the signal: Strong / Moderate / Weak / Noise

    Be specific about what would need to be true for this to become
    a Strong signal worth acting on in Phase 2.""",
    expected_output="""Signal quality rating with justification. Specific
    conditions that would elevate or dismiss this signal.""",
    agent=skeptic_analyst,
    context=[correlation_audit, anomaly_investigation]
)
