"""
shiftinnerv/news/currency_registry.py
Item 21 — Deterministic News & Macro Context Injection

Maps ISO 4217 currency codes to their authoritative data sources.
All interpretation happens downstream in the LLM; this module is
purely a configuration registry.
"""

CURRENCY_DATA_SOURCES: dict = {
    "USD": {
        "calendar_source": "fred",
        "calendar_series": {
            "cpi":  "CPIAUCSL",
            "rate": "FEDFUNDS",
            "gdp":  "GDP",
        },
        "cb_statement_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "cb_name": "Federal Reserve (FOMC)",
        "api_key_env": "FRED_API_KEY",
    },
    "EUR": {
        "calendar_source": "ecb",
        "calendar_series": {
            "cpi":  "ICP.M.U2.N.000000.4.ANR",
            "rate": "FM.B.U2.EUR.4F.KR.MRR_FR.LEV",
            "gdp":  "MNA.Q.Y.I8.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.GY",
        },
        "cb_statement_url": "https://www.ecb.europa.eu/press/pressconf/html/index.en.html",
        "cb_name": "European Central Bank (ECB)",
        "api_key_env": None,
    },
    "GBP": {
        "calendar_source": "fred_proxy",
        "calendar_series": {
            "cpi":  "GBRCPIALLMINMEI",
            "rate": "BOERUKM",
            "gdp":  "CLVMNACSCAB1GQUK",
        },
        "cb_statement_url": "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes",
        "cb_name": "Bank of England (BoE)",
        "api_key_env": "FRED_API_KEY",
    },
    "JPY": {
        "calendar_source": "fred_proxy",
        "calendar_series": {
            "cpi":  "JPNCPIALLMINMEI",
            "rate": "IRSTCB01JPM156N",
            "gdp":  "JPNRGDPEXP",
        },
        "cb_statement_url": "https://www.boj.or.jp/en/mopo/mpmjdeci/index.htm",
        "cb_name": "Bank of Japan (BoJ)",
        "api_key_env": "FRED_API_KEY",
    },
    "CAD": {
        "calendar_source": "boc",
        "calendar_series": {
            "cpi":  "STATIC_INFLATIONCALC",
            "rate": "CAOVERN",
            "gdp":  "CANGDPNQDSMEI",
        },
        "cb_statement_url": "https://www.bankofcanada.ca/press/interest-rate-announcements/",
        "cb_name": "Bank of Canada (BoC)",
        "api_key_env": None,
    },
    "AUD": {
        "calendar_source": "fred_proxy",
        "calendar_series": {
            "cpi":  "AUSCPIALLQINMEI",
            "rate": "IRSTCB01AUM156N",
            "gdp":  "AUSGDPNQDSMEI",
        },
        "cb_statement_url": "https://www.rba.gov.au/media-releases/",
        "cb_name": "Reserve Bank of Australia (RBA)",
        "api_key_env": "FRED_API_KEY",
    },
    "CHF": {
        "calendar_source": "fred_proxy",
        "calendar_series": {
            "cpi":  "CHECPIALLMINMEI",
            "rate": "IRSTCB01CHM156N",
            "gdp":  "CHEGDPNQDSMEI",
        },
        "cb_statement_url": "https://www.snb.ch/en/publications/communication/press-releases",
        "cb_name": "Swiss National Bank (SNB)",
        "api_key_env": "FRED_API_KEY",
    },
    "MXN": {
        "calendar_source": "banxico",
        "calendar_series": {
            "rate": "SF61745",
            "cpi":  "SP1",
        },
        "cb_statement_url": (
            "https://www.banxico.org.mx/publicaciones-y-prensa/"
            "anuncios-de-las-decisiones-de-politica-monetaria/"
        ),
        "cb_name": "Banco de México (Banxico)",
        "api_key_env": "BANXICO_TOKEN",
    },
    "BRL": {
        "calendar_source": "bcb",
        "calendar_series": {
            "rate": "432",
            "cpi":  "13522",
        },
        "cb_statement_url": "https://www.bcb.gov.br/en/monetarypolicy/copomminutes",
        "cb_name": "Banco Central do Brasil (BCB)",
        "api_key_env": None,
    },
    "CNY": {
        "calendar_source": "fred_proxy",
        "calendar_series": {
            "cpi":  "CHNCPIALLMINMEI",
            "rate": "IRSTCB01CNM156N",
            "gdp":  "CHNGDPNQDSMEI",
        },
        "cb_statement_url": None,  # PBC has no reliable English statement URL
        "cb_name": "People's Bank of China (PBC)",
        "api_key_env": "FRED_API_KEY",
    },
    "KRW": {
        "calendar_source": "fred_proxy",
        "calendar_series": {
            "cpi":  "KORCPIALLMINMEI",
            "rate": "IRSTCB01KRM156N",
            "gdp":  "KORGDPNQDSMEI",
        },
        "cb_statement_url": "https://www.bok.or.kr/eng/bbs/E0000634/list.do",
        "cb_name": "Bank of Korea (BoK)",
        "api_key_env": "FRED_API_KEY",
    },
}

# Required keys every registry entry must have
_REQUIRED_KEYS = {"calendar_source", "calendar_series", "cb_statement_url",
                  "cb_name", "api_key_env"}

# Known calendar source types
_KNOWN_SOURCES = {"fred", "ecb", "boc", "banxico", "bcb", "fred_proxy"}

# All registered currency codes — used by get_currencies_for_pair
_REGISTERED_CURRENCIES = set(CURRENCY_DATA_SOURCES.keys())

# Separator variants normalised when parsing FX pair strings
_FX_SEPARATORS = ["/", "_", "-"]


def get_currencies_for_pair(ticker1: str, ticker2: str) -> list[str]:
    """
    Extract currency codes from ticker symbols.

    Handles formats: EURUSD, EUR/USD, EUR_USD, EUR-USD (6-char FX pairs)
    and compound tickers like ``EUR/GBP``.

    For equity tickers (e.g. KWEB, FXI) returns an empty list —
    no central-bank context is applicable.

    Detection rule: a ticker is treated as an FX pair when
    - it contains a separator and both parts are registered currencies, OR
    - it is exactly 6 characters with no separator and both halves are
      registered currencies.
    """
    currencies: list[str] = []

    def _parse_one(tk: str) -> list[str]:
        tk = tk.upper().strip()
        # Normalise separators → "/"
        for sep in _FX_SEPARATORS:
            if sep in tk:
                parts = tk.split(sep)
                if len(parts) == 2:
                    a, b = parts[0].strip(), parts[1].strip()
                    if a in _REGISTERED_CURRENCIES and b in _REGISTERED_CURRENCIES:
                        return [a, b]
                return []
        # No separator — try 6-char split
        if len(tk) == 6:
            a, b = tk[:3], tk[3:]
            if a in _REGISTERED_CURRENCIES and b in _REGISTERED_CURRENCIES:
                return [a, b]
        return []

    # Try ticker1 as a standalone FX pair (e.g. "EUR/USD" passed as ticker1 alone)
    from_t1 = _parse_one(ticker1)
    if from_t1:
        currencies.extend(from_t1)
    elif ticker2:
        # Maybe ticker1 and ticker2 together form a pair
        combo = f"{ticker1}/{ticker2}"
        from_combo = _parse_one(combo)
        if from_combo:
            currencies.extend(from_combo)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for c in currencies:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result
