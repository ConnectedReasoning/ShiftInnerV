import os
from crewai import Agent
from langchain_ollama import OllamaLLM
from dotenv import load_dotenv
from crewai_tools import SerperDevTool
from tools.correlation_tool import CorrelationDecayTool

load_dotenv(os.path.expanduser("~/.innershiftv_env"))

local_llm = "ollama/llama3.1"

search_tool = SerperDevTool()
correlation_tool = CorrelationDecayTool()

quant_scout = Agent(
    role='Lead Quantitative Scout',
    goal="""Map structural relationships between macro assets and identify
    when those relationships shift regime. Report specific dates, correlation
    values, and which direction the relationship moved.""",
    backstory="""You are a quantitative analyst specializing in inter-market
    dynamics. You measure how assets move together over time and flag when
    historically stable relationships change — not to find wrongdoing, but
    to find where the market structure has shifted before prices fully
    reflect it. You report facts: dates, numbers, direction of change.""",
    llm=local_llm,
    tools=[correlation_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=3,
    max_rpm=15
)

forensic_researcher = Agent(
    role='Macro Context Researcher',
    goal="""Find the economic or geopolitical context that explains structural
    shifts in asset relationships. Search for specific dates and tickers
    identified by the Scout.""",
    backstory="""You connect mathematical patterns to real-world events.
    When two assets decouple, you search for the underlying story — policy
    shifts, supply changes, demand shocks, capital flows, geopolitical events.
    You search specifically for the dates and assets flagged by the Scout,
    not generic topics.""",
    llm=local_llm,
    tools=[search_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=3,
    max_rpm=15
)

skeptic_analyst = Agent(
    role='Signal Quality Analyst',
    goal="""Determine whether a detected pattern is statistically meaningful
    or coincidental noise. Assign a signal quality rating and explain
    whether it warrants further investigation.""",
    backstory="""You distinguish signal from noise. Your job is to ask whether
    a detected pattern is robust, persistent, and meaningful — or just
    statistical artifact from a volatile period. You are skeptical of false
    positives and resist narratives not supported by the data. You rate
    signal quality as: Strong, Moderate, Weak, or Noise.""",
    llm=local_llm,
    verbose=True,
    max_iter=3,
    max_rpm=15
)
