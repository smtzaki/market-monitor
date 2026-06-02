"""Ticker universes.

TILE_* lists drive the at-a-glance section of the dashboard.
RECOMMENDER_* lists are scanned for signals every 15 minutes.

Edit these freely. To expand recommender to full S&P 500 + TSX Composite,
swap the hardcoded lists for dynamic fetches (TODO comment below).
"""

# Tile section — what the user sees at a glance on the dashboard
TILE_INDEXES = [
    {"ticker": "^GSPTSE", "name": "TSX Composite",  "currency": "CAD"},
    {"ticker": "^GSPC",   "name": "S&P 500",        "currency": "USD"},
    {"ticker": "^NDX",    "name": "Nasdaq 100",     "currency": "USD"},
]

TILE_STOCKS = [
    {"ticker": "NVDA",    "name": "NVIDIA",                "currency": "USD"},
    {"ticker": "AAPL",    "name": "Apple",                 "currency": "USD"},
    {"ticker": "MSFT",    "name": "Microsoft",             "currency": "USD"},
    {"ticker": "GOOGL",   "name": "Alphabet",              "currency": "USD"},
    {"ticker": "TSLA",    "name": "Tesla",                 "currency": "USD"},
    {"ticker": "AMD",     "name": "AMD",                   "currency": "USD"},
    {"ticker": "SHOP.TO", "name": "Shopify",               "currency": "CAD"},
    {"ticker": "RY.TO",   "name": "Royal Bank of Canada",  "currency": "CAD"},
]

# Recommender universe — broader scan for signal generation.
# Curated starter list (~85 tickers). Expand to full S&P 500 + TSX Composite
# once Phase 1 is stable. (TODO: dynamic fetch from Wikipedia / TMX listings)

RECOMMENDER_US = [
    # Mega-cap tech
    "NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "AVGO",
    # Tech / semis / cloud / cyber
    "AMD", "INTC", "ORCL", "CRM", "ADBE", "NFLX", "PLTR", "SMCI",
    "CRWD", "PANW", "SNOW", "DDOG", "NET", "MDB", "COIN", "ARM",
    "ASML", "TSM", "QCOM", "MU", "MRVL",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "V", "MA", "AXP", "SCHW",
    # Consumer / retail
    "WMT", "COST", "HD", "NKE", "DIS", "SBUX", "MCD", "TGT",
    # Healthcare / biotech
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO",
    # Energy
    "XOM", "CVX", "COP",
    # Industrials / defense
    "BA", "CAT", "GE", "LMT", "RTX",
]

RECOMMENDER_TSX = [
    # Big six banks
    "RY.TO", "TD.TO", "BNS.TO", "BMO.TO", "CM.TO", "NA.TO",
    # Energy
    "CNQ.TO", "SU.TO", "ENB.TO", "TRP.TO", "CVE.TO", "IMO.TO",
    # Tech
    "SHOP.TO", "CSU.TO", "OTEX.TO", "GIB-A.TO", "DSG.TO", "KXS.TO",
    # Telecom / utilities
    "BCE.TO", "T.TO", "RCI-B.TO", "FTS.TO", "EMA.TO",
    # Mining / materials
    "ABX.TO", "AEM.TO", "K.TO", "FM.TO", "TECK-B.TO", "NTR.TO",
    # Industrials / rail / insurance
    "CNR.TO", "CP.TO", "WCN.TO", "WSP.TO", "MFC.TO", "SLF.TO",
    # Retail / consumer
    "L.TO", "ATD.TO", "DOL.TO", "QSR.TO", "MG.TO",
]

# Small-cap watchlist for accumulation signals — add names you're curious about
RECOMMENDER_SMALLCAP: list[str] = [
    # e.g. "BB.TO", "OPEN", "RKLB" — add freely
]


def all_recommender_tickers() -> list[str]:
    """Return deduped list of all tickers to scan for signals."""
    seen = set()
    out = []
    for t in RECOMMENDER_US + RECOMMENDER_TSX + RECOMMENDER_SMALLCAP:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
