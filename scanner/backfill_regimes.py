"""Historical regime backfill.

Walks signal_log.json and assigns regime tags to old signals based on what
the market environment actually was on each signal's fire date. Uses
historical_prices.db for SPY and VIX history.

Idempotent — re-running on already-tagged signals is a no-op unless their
existing regime is "unknown" or missing.

Run once after backfilling historical prices. Can re-run any time to catch
signals tagged before this script existed.
"""

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Optional

import yfinance as yf

import regime  # for the same classifier the live scanner uses


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATA_DIR = Path("../data")
SIGNAL_LOG_FILE = DATA_DIR / "signal_log.json"
DB_PATH = DATA_DIR / "historical_prices.db"


def get_spy_history(conn: sqlite3.Connection) -> dict:
    """Return SPY history as {date_str: close} dict."""
    cursor = conn.execute("""
        SELECT date, close FROM prices
        WHERE ticker = '^GSPC' AND close IS NOT NULL
        ORDER BY date ASC
    """)
    return {row[0]: row[1] for row in cursor.fetchall()}


def get_vix_history(conn: sqlite3.Connection) -> dict:
    """Return VIX history as {date_str: close} dict.

    Tries the historical DB first. If empty (because ^VIX wasn't in the
    original backfill universe), falls back to fetching directly from yfinance.
    """
    cursor = conn.execute("""
        SELECT date, close FROM prices
        WHERE ticker = '^VIX' AND close IS NOT NULL
        ORDER BY date ASC
    """)
    db_data = {row[0]: row[1] for row in cursor.fetchall()}
    if db_data:
        return db_data

    # Fallback: fetch directly from yfinance
    log.info("VIX not in DB — fetching directly from yfinance")
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="2y", interval="1d")
        if hist.empty:
            return {}
        out = {}
        for date_idx, row in hist.iterrows():
            date_str = date_idx.date().isoformat()
            out[date_str] = float(row["Close"])
        log.info(f"Fetched {len(out)} VIX bars")
        return out
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")
        return {}


def classify_for_date(target_date: date, spy_history: dict, vix_history: dict) -> dict:
    """Classify regime for a historical date using the same logic as live regime.py."""
    dates_sorted = sorted(spy_history.keys())

    # Find the most recent SPY trading day on or before target_date
    target_str = target_date.isoformat()
    on_or_before = [d for d in dates_sorted if d <= target_str]
    if not on_or_before:
        return regime._unknown_regime("no SPY history before target date")
    anchor_date = on_or_before[-1]
    anchor_idx = dates_sorted.index(anchor_date)
    spy_now = spy_history[anchor_date]

    # 5-day SPY % change
    if anchor_idx >= 5:
        spy_5d_ago = spy_history[dates_sorted[anchor_idx - 5]]
        spy_5d_pct = (spy_now - spy_5d_ago) / spy_5d_ago * 100
    else:
        spy_5d_pct = 0.0

    # 20-day SPY % change
    if anchor_idx >= 20:
        spy_20d_ago = spy_history[dates_sorted[anchor_idx - 20]]
        spy_20d_pct = (spy_now - spy_20d_ago) / spy_20d_ago * 100
    else:
        spy_20d_pct = 0.0

    # 200-day MA position
    if anchor_idx >= 199:
        ma_window = [spy_history[dates_sorted[i]] for i in range(anchor_idx - 199, anchor_idx + 1)]
        spy_200d_ma = sum(ma_window) / 200
        above_200d = spy_now > spy_200d_ma
    else:
        spy_200d_ma = None
        above_200d = None

    # VIX on that date — may be missing entirely
    vix_now = vix_history.get(anchor_date)
    if vix_now is None:
        # Try a few days back
        for offset in range(1, 6):
            if anchor_idx - offset >= 0:
                candidate_date = dates_sorted[anchor_idx - offset]
                if candidate_date in vix_history:
                    vix_now = vix_history[candidate_date]
                    break

    if vix_now is None:
        # Can't classify without VIX. Mark as unknown — but include the SPY data
        # we did manage to compute, for diagnostics.
        return {
            "classification": "unknown",
            "reason": "VIX data unavailable for date",
            "spy_5d_pct": round(spy_5d_pct, 2),
            "spy_20d_pct": round(spy_20d_pct, 2),
            "spy_above_200d": above_200d,
            "anchor_date": anchor_date,
        }

    classification = regime._classify(vix_now, spy_5d_pct, spy_20d_pct, above_200d)

    return {
        "classification": classification,
        "vix": round(float(vix_now), 2),
        "spy_5d_pct": round(spy_5d_pct, 2),
        "spy_20d_pct": round(spy_20d_pct, 2),
        "spy_above_200d": above_200d,
        "spy_200d_ma": round(spy_200d_ma, 2) if spy_200d_ma else None,
        "backfilled": True,
        "anchor_date": anchor_date,
    }


def main() -> int:
    if not SIGNAL_LOG_FILE.exists():
        log.error(f"No signal_log.json found at {SIGNAL_LOG_FILE}")
        return 1
    if not DB_PATH.exists():
        log.error(f"No historical DB found at {DB_PATH} — run backfill.py first")
        return 1

    data = json.loads(SIGNAL_LOG_FILE.read_text())
    signals = data.get("signals", [])
    log.info(f"Loaded {len(signals)} signals")

    conn = sqlite3.connect(DB_PATH)
    spy = get_spy_history(conn)
    vix = get_vix_history(conn)
    log.info(f"SPY history: {len(spy)} bars, VIX history: {len(vix)} bars")

    if not vix:
        log.warning("VIX history empty — will mostly produce 'unknown' classifications")
        log.warning("Consider adding ^VIX to universe and re-running backfill.py")

    updated = 0
    skipped = 0
    failed = 0

    for sig in signals:
        existing = sig.get("regime", {}).get("classification")
        if existing and existing != "unknown":
            skipped += 1
            continue

        try:
            fired_at = datetime.fromisoformat(sig["fired_at"])
            fire_date = fired_at.date()
            new_regime = classify_for_date(fire_date, spy, vix)
            sig["regime"] = new_regime
            if new_regime.get("classification") != "unknown":
                updated += 1
            else:
                failed += 1
        except Exception as e:
            log.warning(f"Failed for signal {sig.get('id', '?')}: {e}")
            failed += 1

    SIGNAL_LOG_FILE.write_text(json.dumps(data, indent=2))
    conn.close()

    log.info(f"Done: {updated} backfilled, {skipped} already tagged, {failed} unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
