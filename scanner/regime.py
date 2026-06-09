"""Market regime classifier.

Determines what kind of market environment we're in, so signals can be
contextualized. The same signal can have very different predictive value
in a bull market vs a correction vs a crisis — and the recommender needs
to know which regime it's seeing.

Classifications:
  - bull       : SPY uptrend, low VIX, above 200-day MA
  - neutral    : in-between, no strong signal either way
  - correction : VIX elevated, recent SPY weakness, but not panic
  - bear       : SPY below 200-day MA, sustained downtrend
  - crisis     : VIX > 30, extreme fear

These thresholds are reasonable starting points based on common
practitioner heuristics — they're not magic numbers and may need
tuning as we observe how signals actually behave per regime.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)


def classify_current_regime() -> dict:
    """Pull current VIX + SPY data and classify the regime.

    Returns a dict with classification + supporting metrics. Caller should
    treat 'unknown' as a fallback that means "couldn't fetch market data."
    """
    try:
        spy = yf.Ticker("^GSPC")
        vix = yf.Ticker("^VIX")

        # 1 year of SPY for 200-day MA + recent momentum
        spy_hist = spy.history(period="1y", interval="1d")
        # Just need recent VIX
        vix_hist = vix.history(period="5d", interval="1d")

        if spy_hist.empty or vix_hist.empty:
            return _unknown_regime("missing market data")

        vix_now = float(vix_hist.iloc[-1]["Close"])
        spy_now = float(spy_hist.iloc[-1]["Close"])

        # 5-day SPY % change (recent momentum)
        if len(spy_hist) >= 6:
            spy_5d_ago = float(spy_hist.iloc[-6]["Close"])
            spy_5d_pct = (spy_now - spy_5d_ago) / spy_5d_ago * 100
        else:
            spy_5d_pct = 0.0

        # 20-day SPY % change (medium-term trend)
        if len(spy_hist) >= 21:
            spy_20d_ago = float(spy_hist.iloc[-21]["Close"])
            spy_20d_pct = (spy_now - spy_20d_ago) / spy_20d_ago * 100
        else:
            spy_20d_pct = 0.0

        # 200-day MA position
        if len(spy_hist) >= 200:
            spy_200d_ma = float(spy_hist.tail(200)["Close"].mean())
            above_200d = spy_now > spy_200d_ma
        else:
            spy_200d_ma = None
            above_200d = None

        classification = _classify(vix_now, spy_5d_pct, spy_20d_pct, above_200d)

        return {
            "classification": classification,
            "vix": round(vix_now, 2),
            "spy_5d_pct": round(spy_5d_pct, 2),
            "spy_20d_pct": round(spy_20d_pct, 2),
            "spy_above_200d": above_200d,
            "spy_200d_ma": round(spy_200d_ma, 2) if spy_200d_ma else None,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        log.warning(f"Regime classification failed: {e}")
        return _unknown_regime(str(e))


def _classify(vix: float, spy_5d: float, spy_20d: float, above_200d: Optional[bool]) -> str:
    """Apply the actual classification logic.

    Ordering of checks matters — most extreme conditions first.
    """
    # Crisis: extreme VIX overrides everything
    if vix >= 30:
        return "crisis"

    # Bear: structurally below 200d MA AND not bouncing
    if above_200d is False and spy_20d < -3:
        return "bear"

    # Correction: elevated VIX + recent weakness, or sharp recent drop regardless
    if (vix >= 22 and spy_5d < -2) or spy_5d < -5:
        return "correction"

    # Bull: low VIX + positive momentum + above 200d
    if vix < 18 and spy_5d > 0 and above_200d is not False:
        return "bull"

    # Default: in-between, no strong regime signal
    return "neutral"


def _unknown_regime(reason: str) -> dict:
    return {
        "classification": "unknown",
        "reason": reason,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
