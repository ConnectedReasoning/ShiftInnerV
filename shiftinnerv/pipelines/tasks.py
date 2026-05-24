"""
shiftinnerv/pipelines/tasks.py

Single-task architecture. The Analyst receives the full pre-assembled brief
and produces interpretation + strategy. Gate evaluation is deterministic and
already complete before this task runs.
"""

from crewai import Task


def build_analyst_task(analyst, brief: str, ticker1: str, ticker2: str,
                        label: str, verdict: str) -> Task:
    """
    Build the single Analyst task.

    Parameters
    ----------
    analyst : Agent
        The Analyst agent from make_analyst().
    brief : str
        The fully assembled statistical brief — tool output + gate verdicts
        + news context. Built by main.py before this task is created.
    ticker1, ticker2 : str
        Ticker symbols for display.
    label : str
        Human-readable pair label.
    verdict : str
        Deterministic verdict: ACTIVE | MONITOR | MONITOR-NEAR | REJECT.
        The Analyst must not override this.
    """
    return Task(
        description=f"""Analyse the following statistical brief for the pair
    {label} ({ticker1} / {ticker2}).

    The deterministic verdict is: {verdict}
    You must not override or contradict this verdict.

    BRIEF:
    {brief}

    Write your analysis in exactly three labelled sections:

    INTERPRETATION:
    [What do the statistics mean for this pair? Translate SNR, half-life,
    cointegration strength, and episode history into plain language.
    Be specific about numeric values — do not round or paraphrase them away.]

    MACRO CONTEXT:
    [If NEWS & MACRO CONTEXT is present in the brief, connect it directly
    to this pair. Name the relevant currency, the release, and the directional
    implication for the spread. If no macro context is present, write:
    "No current macro context available."]

    STRATEGY:
    [For ACTIVE: confirm entry/exit parameters from the brief, note mean drift
    caveats if present, flag position sizing. For MONITOR: state the exact
    numeric condition for upgrade and re-evaluation date (today + 30 days).
    For REJECT: one sentence on what would need to change for re-evaluation.]""",
        expected_output=f"""A three-section analysis of {label} with sections
    labelled INTERPRETATION, MACRO CONTEXT, and STRATEGY. Every numeric claim
    is drawn from the brief. The deterministic verdict ({verdict}) is accepted
    and not overridden.""",
        agent=analyst,
    )
