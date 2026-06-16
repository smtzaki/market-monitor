"""Recommender — Phase 4.

Reads:
  - data/latest.json (current active signals + regime)
  - data/signal_stats_by_ticker.json (per-ticker edge)
  - data/signal_stats_by_regime.json (regime-conditional edge)
  - data/signal_log.json (lockout state — which tickers are in open positions)
  - data/positions.json (open real + shadow positions)
  - data/historical_baselines.json (σ for position sizing)

Writes:
  - data/recommendations.json (candidate list with scoring breakdown)

Logic:
  1. For each active signal, look up the relevant edge:
     - Per-ticker stats (preferred if n ≥ 3)
     - Per-regime stats (preferred if current regime matches)
     - Fall back to aggregate stats if neither available
  2. Score each candidate based on edge × confidence × regime-fit
  3. Apply lockout filter (skip tickers with open positions)
  4. Generate allocation suggestions sized to the user's budget specs
  5. Output ranked recommendations

The recommender is "advisory" — it doesn't act. The dashboard surfaces these
candidates and the user chooses: BUY (real), SHADOW (paper-trade), or DISMISS.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("../data")
LATEST_FILE          = DATA_DIR / "latest.json"
TICKER_STATS_FILE    = DATA_DIR / "signal_stats_by_ticker.json"
REGIME_STATS_FILE    = DATA_DIR / "signal_stats_by_regime.json"
AGG_STATS_FILE       = DATA_DIR / "signal_stats.json"
BASELINES_FILE       = DATA_DIR / "historical_baselines.json"
POSITIONS_FILE       = DATA_DIR / "positions.json"
RECOMMENDATIONS_FILE = DATA_DIR / "recommendations.json"

# ============================================================
# User-configured policy
# ============================================================
POLICY = {
    "budget_cad":              150.0,   # total active-trading capital
    "max_allocation_pct":      0.50,    # max 50% per position
    "max_allocation_pct_high": 0.65,    # higher cap for "established" edge
    "stop_loss_pct":          -2.0,    # -2% from entry
    "min_hold_days":             3,    # minimum hold window
    "max_hold_days":             7,    # maximum hold window
    "cash_buffer_min_pct":     0.20,    # always keep 20% cash
    "cash_buffer_max_pct":     0.40,    # up to 40% in high-signal-density days
    "usdcad_default":          1.40,    # fallback if missing
    # Minimum criteria for a signal to even appear as a recommendation
    "min_n_per_ticker":          3,    # need at least 3 prior fires per-ticker
    "min_edge_score":         0.30,    # cutoff on the 0-1 scoring scale
}

# ============================================================
# Scoring
# ============================================================

def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _confidence_from_n(n: int) -> str:
    if n < 5:  return "noise"
    if n < 20: return "weak"
    if n < 50: return "emerging"
    return "established"


def _confidence_weight(label: str) -> float:
    return {"noise": 0.3, "weak": 0.6, "emerging": 0.85, "established": 1.0}.get(label, 0.5)


def score_candidate(
    signal: dict,
    regime: dict,
    ticker_stats: dict,
    regime_stats: dict,
    agg_stats: dict,
) -> dict:
    """Score a single active signal as a candidate.

    Returns a dict with the score plus the evidence used to arrive at it,
    so the dashboard can show "why" each recommendation has the score it does.
    """
    ticker = signal["ticker"]
    direction = signal["direction"]
    regime_cls = regime.get("classification", "unknown")

    # Per-ticker edge lookup (preferred)
    t_edge = None
    t_data = ticker_stats.get("tickers", {}).get(ticker, {}).get(direction, {})
    if "1d" in t_data and t_data["1d"]["n"] >= POLICY["min_n_per_ticker"]:
        t_edge = t_data["1d"]

    # Per-regime edge lookup
    r_edge = None
    r_path = regime_stats.get("by_regime", {}).get(regime_cls, {}).get("volume_spike", {}).get(direction, {})
    if "1d" in r_path and r_path["1d"]["n"] >= 10:
        r_edge = r_path["1d"]

    # Aggregate fallback
    a_edge = None
    a_path = agg_stats.get("by_signal_type", {}).get("volume_spike", {}).get(direction, {})
    if "1d" in a_path:
        a_edge = a_path["1d"]

    # Choose the most specific edge available
    if t_edge:
        chosen, source = t_edge, "per_ticker"
    elif r_edge:
        chosen, source = r_edge, "per_regime"
    elif a_edge:
        chosen, source = a_edge, "aggregate"
    else:
        chosen, source = None, "none"

    if not chosen:
        return {
            "ticker": ticker, "direction": direction, "score": 0.0,
            "rationale": "No historical edge data available",
            "edge_source": "none", "actionable": False,
        }

    # Core score: blend hit_rate (normalized to 0-1 with 0.5 as no-edge) × |avg|/σ × confidence
    hit_rate = chosen["hit_rate"]
    avg_pct  = chosen.get("avg_return_pct", 0)
    std_pct  = max(chosen.get("std_pct", 1), 0.1)
    n        = chosen["n"]
    confidence = _confidence_from_n(n)

    # For bullish direction, hit_rate > 0.5 + avg > 0 = good
    # For bearish direction, hit_rate > 0.5 + avg < 0 = good (we'd want to short — but we can't,
    # so bearish signals get scored on AVOID strength, not TRADE strength)
    if direction == "bullish":
        # Higher hit rate above 50% + positive avg = better
        directional_correctness = max(0, hit_rate - 0.5) * 2  # 0 to 1
        magnitude = max(0, avg_pct) / max(std_pct, 0.1)        # signal-to-noise
        actionable = avg_pct > 0  # only actionable long if expected return positive
    else:
        # Bearish signal that correctly predicts down = signal works, but we can't trade it
        # So we score it but mark not actionable for long-only
        directional_correctness = max(0, hit_rate - 0.5) * 2
        magnitude = max(0, -avg_pct) / max(std_pct, 0.1)
        actionable = False  # can't short in TFSA, so bearish signals don't generate long trades

    # Regime fit bonus: per-ticker stats might lump regimes, so check if regime stats agree
    regime_fit = 1.0
    if r_edge and t_edge:
        r_hit = r_edge["hit_rate"]
        t_hit = t_edge["hit_rate"]
        if (direction == "bullish" and r_hit > 0.5) or (direction == "bearish" and r_hit > 0.5):
            regime_fit = 1.1
        elif (direction == "bullish" and r_hit < 0.4) or (direction == "bearish" and r_hit < 0.4):
            regime_fit = 0.6  # regime disagrees — penalize

    conf_weight = _confidence_weight(confidence)
    raw_score = directional_correctness * magnitude * conf_weight * regime_fit
    # Squash to 0-1 (we don't expect raw_score > ~2)
    score = min(1.0, raw_score / 2.0)

    rationale_parts = [
        f"{source}: n={n}, hit={hit_rate*100:.0f}%, avg={avg_pct:+.2f}%, σ={std_pct:.2f}",
        f"confidence={confidence}",
    ]
    if regime_fit != 1.0:
        if regime_fit > 1.0:
            rationale_parts.append(f"regime confirms")
        else:
            rationale_parts.append(f"regime disagrees")
    if not actionable:
        rationale_parts.append("INFO ONLY — bearish signal, can't short in TFSA")

    return {
        "ticker": ticker,
        "direction": direction,
        "score": round(score, 3),
        "actionable": actionable,
        "edge_source": source,
        "n": n,
        "hit_rate": hit_rate,
        "avg_return_pct": avg_pct,
        "std_pct": std_pct,
        "confidence": confidence,
        "regime": regime_cls,
        "regime_fit": round(regime_fit, 2),
        "rationale": " · ".join(rationale_parts),
        "current_price": signal.get("price"),
        "price_change_pct": signal.get("price_change_pct"),
        "multiplier": signal.get("multiplier"),
    }


def _allocation_for(score: float, budget_remaining: float, baseline: dict) -> dict:
    """Position sizing: scale allocation by score, respect max % cap, scale down on high-σ."""
    sigma = baseline.get("sigma_60d_pct", 2.0) if baseline else 2.0

    # Higher score → higher allocation, but capped
    base_pct = min(POLICY["max_allocation_pct"], 0.20 + score * 0.30)
    if score >= 0.7:
        base_pct = min(POLICY["max_allocation_pct_high"], base_pct)

    # σ scaling — wild tickers get smaller positions
    if sigma > 4.0:
        base_pct *= 0.7
    elif sigma > 3.0:
        base_pct *= 0.85

    allocation_cad = round(budget_remaining * base_pct, 2)
    return {
        "allocation_pct": round(base_pct, 3),
        "allocation_cad": allocation_cad,
        "sigma_60d": sigma,
    }


# ============================================================
# Lockout — open positions exclude their tickers from new recommendations
# ============================================================

def _locked_tickers(positions: dict) -> set:
    locked = set()
    for p in positions.get("positions", []):
        if p.get("status") == "open":
            locked.add(p["ticker"])
    return locked


# ============================================================
# Main
# ============================================================

def generate_recommendations() -> dict:
    latest = _load(LATEST_FILE)
    ticker_stats = _load(TICKER_STATS_FILE)
    regime_stats = _load(REGIME_STATS_FILE)
    agg_stats = _load(AGG_STATS_FILE)
    baselines = _load(BASELINES_FILE).get("tickers", {})
    positions = _load(POSITIONS_FILE) or {"positions": []}

    active_signals = latest.get("active_signals", [])
    regime = latest.get("regime", {})
    locked = _locked_tickers(positions)

    candidates = []
    for sig in active_signals:
        if sig["ticker"] in locked:
            continue
        scored = score_candidate(sig, regime, ticker_stats, regime_stats, agg_stats)
        if scored["score"] >= POLICY["min_edge_score"]:
            candidates.append(scored)

    candidates.sort(key=lambda x: -x["score"])

    # Allocation pass — total budget shared across candidates, respecting buffer
    cash_buffer = POLICY["cash_buffer_min_pct"]
    deployable = POLICY["budget_cad"] * (1 - cash_buffer)
    budget_remaining = deployable

    recommendations = []
    for c in candidates:
        if not c["actionable"]:
            recommendations.append({**c, "allocation": None})
            continue
        if budget_remaining <= 5:  # don't bother with tiny positions
            recommendations.append({**c, "allocation": None, "note": "budget exhausted"})
            continue
        baseline = baselines.get(c["ticker"], {})
        alloc = _allocation_for(c["score"], budget_remaining, baseline)
        if alloc["allocation_cad"] < 5:
            continue
        c["allocation"] = alloc
        c["target_exit_date_min"] = POLICY["min_hold_days"]
        c["target_exit_date_max"] = POLICY["max_hold_days"]
        c["stop_loss_pct"] = POLICY["stop_loss_pct"]
        budget_remaining -= alloc["allocation_cad"]
        recommendations.append(c)

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "policy": POLICY,
        "active_signals_seen": len(active_signals),
        "locked_tickers": sorted(locked),
        "budget_total_cad": POLICY["budget_cad"],
        "budget_deployed_cad": round(deployable - budget_remaining, 2),
        "budget_remaining_cad": round(budget_remaining, 2),
        "recommendations": recommendations,
    }
    return out


def main() -> int:
    out = generate_recommendations()
    RECOMMENDATIONS_FILE.write_text(json.dumps(out, indent=2))
    log.info(
        f"Wrote {len(out['recommendations'])} recommendations "
        f"({sum(1 for r in out['recommendations'] if r.get('actionable'))} actionable)"
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
