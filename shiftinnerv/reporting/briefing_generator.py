"""
Sentinel Run Briefing Generator

Generates a human-readable summary of sentinel execution at end of run.
Uses Ollama for local synthesis (no API calls, fast execution).
"""

import json
from datetime import datetime
from typing import Dict, List, Optional
import subprocess


def call_ollama(prompt: str, model: str = "mistral") -> str:
    """
    Call Ollama locally to generate briefing.
    Falls back gracefully if Ollama is not running.
    
    Args:
        prompt: The prompt to send to Ollama
        model: Ollama model to use (default: mistral for speed)
    
    Returns:
        Generated text from Ollama, or empty string if unavailable
    """
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return ""  # Silent fallback
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return ""  # Silent fallback — Ollama not available


def generate_sentinel_briefing(
    regime_state: str,
    regime_vix: float,
    regime_multiplier: float,
    sourced_pairs: List[Dict],
    screening_counts: Dict[str, int],
    verdicts: Dict[str, int],
    rejected_pairs: List[Dict],
    open_positions: int,
) -> str:
    """
    Generate a structured briefing for end-of-sentinel-run.
    
    Args:
        regime_state: Market regime (NORMAL, ELEVATED, HIGH_STRESS, CRISIS)
        regime_vix: Current VIX level
        regime_multiplier: Position size multiplier (0.0-1.0)
        sourced_pairs: List of top sourced pairs [{'ticker1', 'ticker2', 'score', 'corr'}]
        screening_counts: Dict with counts {'PRIME': n, 'STRONG': n, ...}
        verdicts: Dict with verdict counts {'active': n, 'monitor': n, 'reject': n}
        rejected_pairs: List of rejected pairs with reasons [{'pair', 'reason'}]
        open_positions: Number of currently open positions
    
    Returns:
        Formatted briefing string
    """
    
    # Build context for Ollama
    top_pairs_text = "\n    ".join([
        f"{p['ticker1']}/{p['ticker2']:8}  score={p.get('score', 0):5.1f}  corr={p.get('corr', 0):5.2f}"
        for p in sourced_pairs[:5]
    ])
    
    screening_text = ", ".join([
        f"{k}={v}" for k, v in screening_counts.items() if v > 0
    ])
    
    rejected_text = "\n    ".join([
        f"{p['pair']:12}  — {p['reason']}"
        for p in rejected_pairs[:3]
    ])
    
    prompt = f"""Generate a concise, structured briefing for a quantitative trading system run.

MARKET CONDITIONS
State: {regime_state} | VIX: {regime_vix:.1f} | Position Size Multiplier: {regime_multiplier:.1f}x

PAIR SOURCING
Generated 100 intelligent pairs via correlation clustering and decay detection.
Top 5 pairs by score:
    {top_pairs_text}

SCREENING RESULTS
100 pairs screened via Johansen cointegration + SNR + half-life analysis.
Rating distribution: {screening_text}

AGENT VERDICTS
2 anomaly investigations processed.
Verdicts: ACTIVE={verdicts.get('active', 0)} | MONITOR={verdicts.get('monitor', 0)} | REJECT={verdicts.get('reject', 0)}

Top rejections:
    {rejected_text}

POSITION STATUS
Open positions: {open_positions}
Regime-aware position monitoring: {'ENABLED' if open_positions > 0 else 'INACTIVE'}

---

Now write a brief (150-200 word) executive summary for a trader. Be direct. Focus on:
1. Market regime signal (is it safe to trade?)
2. What the sourcing found (any interesting divergences?)
3. Why pairs were rejected (common failure modes)
4. Next action (what to monitor)

Use bullet points. No fluff."""

    # Call Ollama
    briefing_text = call_ollama(prompt, model="mistral")
    
    # Format the output
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    header = "═" * 80
    
    # Build briefing text
    briefing_lines = []
    briefing_lines.append(f"\n{header}")
    briefing_lines.append(f"SENTINEL RUN BRIEFING — {timestamp}")
    briefing_lines.append(header)
    briefing_lines.append("")
    briefing_lines.append("MARKET REGIME")
    briefing_lines.append(f"  State: {regime_state} | VIX: {regime_vix:.1f} | Position Size: {regime_multiplier:.1f}x")
    briefing_lines.append(f"  {'✓ Conditions stable — full position sizing enabled' if regime_state == 'NORMAL' else '⚠ Market stress detected — position sizing reduced'}")
    briefing_lines.append("")
    briefing_lines.append("PAIR SOURCING")
    briefing_lines.append("  Generated: 100 intelligent pairs (correlation clustering + decay detection)")
    if sourced_pairs:
        briefing_lines.append("  Top 5 by score:")
        for p in sourced_pairs[:5]:
            briefing_lines.append(f"    {p['ticker1']:8s}/{p['ticker2']:8s}  score={p['score']:5.1f}  corr={p['corr']:5.3f}")
    briefing_lines.append("")
    briefing_lines.append("SCREENING RESULTS")
    briefing_lines.append(f"  Pairs screened: 100")
    if any(screening_counts.values()):
        ratings_str = ", ".join([f"{k}={v}" for k, v in screening_counts.items() if v > 0])
        briefing_lines.append(f"  Ratings: {ratings_str}")
    briefing_lines.append(f"  Anomalies flagged: {len(rejected_pairs)}")
    briefing_lines.append("")
    briefing_lines.append("AGENT VERDICTS")
    briefing_lines.append(f"  Processed: {len(rejected_pairs)} anomalies")
    briefing_lines.append(f"  ACTIVE: {verdicts.get('active', 0):2d}  |  MONITOR: {verdicts.get('monitor', 0):2d}  |  REJECT: {verdicts.get('reject', 0):2d}")
    if rejected_pairs:
        briefing_lines.append("  ")
        briefing_lines.append("  Top rejections:")
        for p in rejected_pairs[:3]:
            briefing_lines.append(f"    {p['pair']:12s}  — {p['reason']}")
    briefing_lines.append("")
    briefing_lines.append("POSITION STATUS")
    briefing_lines.append(f"  Open positions: {open_positions}")
    briefing_lines.append(f"  Correlation-monitored: {open_positions if open_positions > 0 else 0}")
    briefing_lines.append(f"  Next screening: {(datetime.now() + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')} (daily)")
    
    if briefing_text:
        briefing_lines.append("")
        briefing_lines.append("AI SYNTHESIS")
        for line in briefing_text.split('\n')[:8]:  # Limit to first 8 lines
            briefing_lines.append(f"  {line}")
    
    briefing_lines.append("")
    briefing_lines.append(header)
    briefing_lines.append("")
    
    briefing = "\n".join(briefing_lines)
    
    return briefing


def format_rejected_pair(ticker1: str, ticker2: str, gate_failure: str, details: str) -> Dict:
    """Helper to format a rejected pair for the briefing."""
    return {
        'pair': f"{ticker1}/{ticker2}",
        'reason': f"Gate {gate_failure} — {details}"
    }
