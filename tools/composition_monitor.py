"""
ShiftInnerV — Composition Concentration Monitor
Item 15 of the Council Roadmap.

Enforce a hard limit on simultaneous open positions per composition category.
When limit is reached, new ACTIVE verdicts in that composition are downgraded
to MONITOR until an existing position closes.

This is a gate override: a composition-level circuit breaker that trumps
the numerical gates.

Usage:
    from tools.composition_monitor import (
        load_compositions, get_pair_composition,
        check_composition_concentration,
    )

    compositions = load_compositions(compositions_dir)
    composition_label = get_pair_composition(ticker1, ticker2, compositions)

    result = check_composition_concentration(
        db_path="trial_ledger.db",
        composition_label="defense",
        limit=2,
        logger=logger,
    )
    # result.decision: "ALLOW" | "DOWNGRADE_TO_MONITOR"
"""

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ── Composition loader ────────────────────────────────────────────────────────

def load_compositions(compositions_dir: str) -> dict:
    """
    Load all composition YAML files from directory.

    Only files matching the pattern ``composition_*.yaml`` are loaded.
    ``promoted_*.yaml`` and other temp files are intentionally excluded —
    they are transient and concentration tracking against them would be
    misleading.

    Returns
    -------
    dict
        {composition_label: {"pairs": [(ticker1, ticker2), ...], "file": "..."}}

    Example::

        {
            "defense": {
                "pairs": [("LMT", "NOC"), ("RTX", "GD"), ...],
                "file": "composition_b_defense.yaml"
            },
            "china_em": {
                "pairs": [("BABA", "JD"), ...],
                "file": "composition_c_china_em.yaml"
            }
        }
    """
    compositions: dict = {}

    if not os.path.isdir(compositions_dir):
        return compositions

    for yaml_file in sorted(Path(compositions_dir).glob("composition_*.yaml")):
        # Extract label from filename:
        #   composition_b_defense.yaml      → "defense"
        #   composition_a_commodity_equity_proxy.yaml → "commodity_equity_proxy"
        parts = yaml_file.stem.split("_")  # ["composition", "b", "defense"]
        if len(parts) >= 3:
            label = "_".join(parts[2:])    # join everything after the id segment
        else:
            label = yaml_file.stem         # fallback: use full stem

        try:
            with open(yaml_file) as fh:
                data = yaml.safe_load(fh)

            raw_pairs = data.get("pairs") if isinstance(data, dict) else None
            if not raw_pairs:
                continue

            pairs: set = set()
            for pair_dict in raw_pairs:
                if not isinstance(pair_dict, dict):
                    continue
                t1 = str(pair_dict.get("ticker1", "")).upper().strip()
                t2 = str(pair_dict.get("ticker2", "")).upper().strip()
                if t1 and t2:
                    pairs.add((t1, t2))

            compositions[label] = {
                "pairs": list(pairs),
                "file":  yaml_file.name,
            }

        except Exception as exc:
            print(f"[composition_monitor] Warning: could not load {yaml_file.name}: {exc}")

    return compositions


# ── Pair → composition lookup ─────────────────────────────────────────────────

def get_pair_composition(
    ticker1: str,
    ticker2: str,
    compositions: dict,
) -> Optional[str]:
    """
    Identify which composition a pair belongs to.

    Checks both orderings (t1, t2) and (t2, t1) since pair direction
    is not canonical across all composition files.

    Returns
    -------
    str | None
        Composition label, e.g. ``"defense"``, or ``None`` if the pair
        is not listed in any loaded composition.
    """
    t1 = ticker1.upper().strip()
    t2 = ticker2.upper().strip()

    for label, comp_data in compositions.items():
        pairs = comp_data.get("pairs", [])
        if (t1, t2) in pairs or (t2, t1) in pairs:
            return label

    return None  # "standalone" — no concentration limit applies


# ── Concentration check ───────────────────────────────────────────────────────

@dataclass
class ConcentrationCheckResult:
    """Result of a single composition concentration check."""
    composition: str = ""
    open_count: int = 0
    open_positions: list = field(default_factory=list)   # list of verdict_ids
    limit: int = 2
    decision: str = ""      # "ALLOW" | "DOWNGRADE_TO_MONITOR"
    rationale: str = ""


def check_composition_concentration(
    db_path: str,
    composition_label: str,
    limit: int = 2,
    logger=None,
) -> ConcentrationCheckResult:
    """
    Check whether a composition has reached its open-position limit.

    Parameters
    ----------
    db_path : str
        Path to ``trial_ledger.db``.
    composition_label : str
        Composition to check, e.g. ``"defense"``, ``"china_em"``.
    limit : int
        Maximum simultaneous open positions allowed (default 2).
    logger : logging.Logger, optional
        If supplied, warnings/errors are forwarded here.

    Returns
    -------
    ConcentrationCheckResult
        ``.decision`` is ``"ALLOW"`` when open count < limit, otherwise
        ``"DOWNGRADE_TO_MONITOR"``.
    """
    result = ConcentrationCheckResult(
        composition=composition_label,
        limit=limit,
    )

    # ── Ledger missing — fail open (don't block first-ever run) ──────────────
    if not os.path.exists(db_path):
        if logger:
            logger.warning(
                f"[composition_monitor] Trial ledger not found: {db_path}. "
                "Cannot check concentration — defaulting to ALLOW."
            )
        result.decision  = "ALLOW"
        result.rationale = "Ledger absent — concentration check skipped."
        return result

    # ── Query open positions for this composition ────────────────────────────
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT verdict_id, ticker1, ticker2, entry_timestamp
            FROM   trial_ledger
            WHERE  is_closed = 0
              AND  composition_label = ?
            ORDER  BY entry_timestamp ASC
            """,
            (composition_label,),
        )
        rows = cursor.fetchall()
        conn.close()

        result.open_count     = len(rows)
        result.open_positions = [r["verdict_id"] for r in rows]

    except Exception as exc:
        if logger:
            logger.error(
                f"[composition_monitor] Error querying concentration for "
                f"'{composition_label}': {exc}"
            )
        result.decision  = "ALLOW"
        result.rationale = f"DB error ({exc}) — defaulting to ALLOW."
        return result

    # ── Decision ─────────────────────────────────────────────────────────────
    if result.open_count < limit:
        result.decision  = "ALLOW"
        result.rationale = (
            f"Composition '{composition_label}': {result.open_count} open position(s) "
            f"(limit {limit}). ALLOW."
        )
    else:
        result.decision  = "DOWNGRADE_TO_MONITOR"
        result.rationale = (
            f"Composition '{composition_label}': {result.open_count} open position(s) "
            f">= limit of {limit}. DOWNGRADE to MONITOR."
        )

    return result


# ── Bulk helper ───────────────────────────────────────────────────────────────

def get_all_concentrations(
    db_path: str,
    compositions: dict,
    limit: int = 2,
    logger=None,
) -> dict:
    """
    Run ``check_composition_concentration`` for every loaded composition.

    Returns
    -------
    dict
        ``{composition_label: ConcentrationCheckResult}``
    """
    return {
        label: check_composition_concentration(
            db_path=db_path,
            composition_label=label,
            limit=limit,
            logger=logger,
        )
        for label in compositions
    }
