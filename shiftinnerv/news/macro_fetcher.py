"""
shiftinnerv/news/macro_fetcher.py
Item 21 — Deterministic News & Macro Context Injection

Fetches recent high-impact economic data releases for a given currency.
This module is purely a data fetcher — no statistical computation,
no interpretation. All interpretation happens downstream in the LLM.

Supported sources:
  - fred / fred_proxy : FRED REST API (api.stlouisfed.org)
  - ecb               : ECB SDMX-JSON API (data-api.ecb.europa.eu)
  - boc               : Bank of Canada Valet API
  - banxico           : Banxico SIE REST API (requires BANXICO_TOKEN)
  - bcb               : Banco Central do Brasil SGS API
"""

import logging
import os
from datetime import datetime, timezone

import requests

from shiftinnerv.news.currency_registry import CURRENCY_DATA_SOURCES

log = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds per HTTP request

# Series labels used for human-readable event names in the brief
_SERIES_LABELS = {
    "cpi":  "CPI",
    "rate": "Policy Rate",
    "gdp":  "GDP",
}


# ── Beat/miss classification ──────────────────────────────────────────────────

def _classify_beat_miss(actual: float, previous: float) -> str:
    """
    Classify a data release relative to the prior observation.

    Uses previous observation as a proxy for consensus (FRED does not
    provide survey consensus data for most series).

    Returns "BEAT", "MISS", or "IN-LINE".
    Threshold: within 0.1% of previous value → IN-LINE.
    """
    if previous == 0:
        return "IN-LINE"
    pct_change = (actual - previous) / abs(previous)
    if pct_change > 0.001:
        return "BEAT"
    if pct_change < -0.001:
        return "MISS"
    return "IN-LINE"


def _make_event(currency: str, series_key: str,
                actual: float, previous: float,
                obs_date: str) -> dict:
    label = _SERIES_LABELS.get(series_key, series_key.upper())
    beat_miss = _classify_beat_miss(actual, previous)
    return {
        "currency":   currency,
        "event":      label,
        "actual":     actual,
        "forecast":   None,      # consensus not available from primary APIs
        "previous":   previous,
        "impact":     "HIGH",
        "timestamp":  obs_date,
        "beat_miss":  beat_miss,
    }


# ── FRED fetch path ───────────────────────────────────────────────────────────

def _fetch_fred_series(series_id: str, api_key: str) -> tuple[float, float, str] | None:
    """
    Fetch the two most recent observations for a FRED series.
    Returns (current_value, previous_value, observation_date) or None on failure.
    """
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id":  series_id,
        "api_key":    api_key,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      2,
    }
    try:
        r = requests.get(url, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        if len(obs) < 2:
            return None
        # obs[0] is most recent (desc order)
        current_val  = float(obs[0]["value"])
        previous_val = float(obs[1]["value"])
        obs_date     = obs[0]["date"]
        return current_val, previous_val, obs_date
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.debug(f"FRED fetch failed for {series_id}: {exc}")
        return None


def _fetch_fred(currency: str) -> list[dict]:
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        log.warning(f"[macro_fetcher] FRED_API_KEY not set — skipping FRED fetch for {currency}")
        return []

    entry  = CURRENCY_DATA_SOURCES[currency]
    series = entry["calendar_series"]
    events = []

    for series_key, series_id in series.items():
        if series_id.startswith("STATIC_"):
            continue  # placeholder series used for BoC CAD GDP fallback
        result = _fetch_fred_series(series_id, api_key)
        if result is None:
            continue
        actual, previous, obs_date = result
        events.append(_make_event(currency, series_key, actual, previous, obs_date))

    return events


# ── ECB SDMX-JSON fetch path ──────────────────────────────────────────────────

def _fetch_ecb(currency: str) -> list[dict]:
    entry  = CURRENCY_DATA_SOURCES[currency]
    series = entry["calendar_series"]
    events = []

    for series_key, series_id in series.items():
        url = (
            f"https://data-api.ecb.europa.eu/service/data/{series_id}"
            f"?lastNObservations=2&format=jsondata"
        )
        try:
            r = requests.get(url, timeout=_TIMEOUT,
                             headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
            # SDMX-JSON: dataSets[0].series is a dict; values are obs arrays
            datasets = data.get("dataSets", [])
            if not datasets:
                continue
            series_data = datasets[0].get("series", {})
            if not series_data:
                continue
            # Take first (only) series entry
            first_series = next(iter(series_data.values()))
            obs_dict = first_series.get("observations", {})
            if len(obs_dict) < 2:
                continue
            # Keys are string indices; sort numerically to get latest
            sorted_keys = sorted(obs_dict.keys(), key=lambda x: int(x))
            current_val  = float(obs_dict[sorted_keys[-1]][0])
            previous_val = float(obs_dict[sorted_keys[-2]][0])
            # Observation dates from structure — use dimension dates if available
            struct = data.get("structure", {})
            dims   = struct.get("dimensions", {}).get("observation", [])
            obs_date = ""
            for d in dims:
                if d.get("id") in ("TIME_PERIOD", "TIME"):
                    values = d.get("values", [])
                    if values and len(values) >= 1:
                        obs_date = values[-1].get("id", "")
                    break
            events.append(_make_event(currency, series_key,
                                      current_val, previous_val, obs_date))
        except (requests.RequestException, ValueError, KeyError, StopIteration) as exc:
            log.debug(f"ECB fetch failed for {series_id}: {exc}")

    return events


# ── Bank of Canada Valet path ─────────────────────────────────────────────────

def _fetch_boc(currency: str) -> list[dict]:
    entry  = CURRENCY_DATA_SOURCES[currency]
    series = entry["calendar_series"]
    events = []

    for series_key, series_id in series.items():
        if series_id.startswith("STATIC_"):
            # BoC inflation calculator is not a standard Valet series — skip
            continue
        if series_id.startswith("CAN") or series_id in ("CANGDPNQDSMEI",):
            # GDP fallback uses FRED — delegate there
            fred_result = _fetch_fred_series(series_id, os.getenv("FRED_API_KEY", ""))
            if fred_result:
                actual, previous, obs_date = fred_result
                events.append(_make_event(currency, series_key, actual, previous, obs_date))
            continue

        url = f"https://www.bankofcanada.ca/valet/observations/{series_id}/json?recent=2"
        try:
            r = requests.get(url, timeout=_TIMEOUT)
            r.raise_for_status()
            obs_list = r.json().get("observations", [])
            if len(obs_list) < 2:
                continue
            # Most recent last in BoC Valet response
            current_val  = float(obs_list[-1][series_id]["v"])
            previous_val = float(obs_list[-2][series_id]["v"])
            obs_date     = obs_list[-1].get("d", "")
            events.append(_make_event(currency, series_key,
                                      current_val, previous_val, obs_date))
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.debug(f"BoC Valet fetch failed for {series_id}: {exc}")

    return events


# ── Banxico SIE path ──────────────────────────────────────────────────────────

def _fetch_banxico(currency: str) -> list[dict]:
    token = os.getenv("BANXICO_TOKEN", "")
    if not token:
        log.warning("[macro_fetcher] BANXICO_TOKEN not set — skipping Banxico fetch")
        return []

    entry  = CURRENCY_DATA_SOURCES[currency]
    series = entry["calendar_series"]
    events = []

    for series_key, series_id in series.items():
        url = (
            f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/"
            f"{series_id}/datos/oportuno"
        )
        try:
            r = requests.get(url, timeout=_TIMEOUT,
                             headers={"Bmx-Token": token})
            r.raise_for_status()
            data   = r.json()
            series_data = data.get("bmx", {}).get("series", [])
            if not series_data:
                continue
            datos = series_data[0].get("datos", [])
            if len(datos) < 2:
                continue
            # Most recent last
            current_val  = float(datos[-1]["dato"].replace(",", ""))
            previous_val = float(datos[-2]["dato"].replace(",", ""))
            obs_date     = datos[-1].get("fecha", "")
            events.append(_make_event(currency, series_key,
                                      current_val, previous_val, obs_date))
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.debug(f"Banxico fetch failed for {series_id}: {exc}")

    return events


# ── BCB SGS path ──────────────────────────────────────────────────────────────

def _fetch_bcb(currency: str) -> list[dict]:
    entry  = CURRENCY_DATA_SOURCES[currency]
    series = entry["calendar_series"]
    events = []

    for series_key, series_id in series.items():
        url = (
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}"
            f"/dados/ultimos/2?formato=json"
        )
        try:
            r = requests.get(url, timeout=_TIMEOUT)
            r.raise_for_status()
            obs_list = r.json()
            if len(obs_list) < 2:
                continue
            current_val  = float(obs_list[-1]["valor"].replace(",", "."))
            previous_val = float(obs_list[-2]["valor"].replace(",", "."))
            obs_date     = obs_list[-1].get("data", "")
            events.append(_make_event(currency, series_key,
                                      current_val, previous_val, obs_date))
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.debug(f"BCB SGS fetch failed for {series_id}: {exc}")

    return events


# ── Public API ────────────────────────────────────────────────────────────────

_SOURCE_DISPATCH = {
    "fred":       _fetch_fred,
    "fred_proxy": _fetch_fred,
    "ecb":        _fetch_ecb,
    "boc":        _fetch_boc,
    "banxico":    _fetch_banxico,
    "bcb":        _fetch_bcb,
}


def fetch_calendar_context(currency_code: str,
                            lookback_hours: int = 48,
                            lookahead_hours: int = 24) -> list[dict]:
    """
    Fetch high-impact economic calendar events for a currency.

    Returns a list of dicts:
        {currency, event, actual, forecast, previous,
         impact, timestamp, beat_miss}

    beat_miss: "BEAT" | "MISS" | "IN-LINE" | None (if no forecast available)

    Dispatches to the correct source based on CURRENCY_DATA_SOURCES.
    Falls back to FRED for calendar_source == "fred_proxy".
    Returns [] on any fetch failure — never raises.

    Note: lookback_hours / lookahead_hours are retained for future
    integration with a real economic calendar API (e.g. Trading Economics).
    The current implementation returns the most recent observation regardless
    of recency window, as the primary APIs (FRED, ECB, BoC, etc.) do not
    expose a calendar endpoint — they expose data series.
    """
    if currency_code not in CURRENCY_DATA_SOURCES:
        return []

    entry  = CURRENCY_DATA_SOURCES[currency_code]
    source = entry["calendar_source"]
    fetch_fn = _SOURCE_DISPATCH.get(source)

    if fetch_fn is None:
        log.warning(f"[macro_fetcher] Unknown calendar_source '{source}' for {currency_code}")
        return []

    try:
        return fetch_fn(currency_code)
    except Exception as exc:
        log.warning(f"[macro_fetcher] Fetch failed for {currency_code}: {exc}")
        return []
