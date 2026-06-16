"""Historical data backfill.

One-shot script (manual trigger via GitHub Actions) that:
  1. Downloads ~2 years of daily OHLCV for the entire universe (recommender + tiles)
  2. Stores it in a local SQLite database (data/historical_prices.db)
  3. Computes per-ticker baselines (200d MA, 60d avg volume, 60d σ, 52w high/low)
  4. Writes baselines to data/historical_baselines.json

The scanner can then read from these baselines for context-aware detection
without making per-scan historical fetches to yfinance.

Run once initially. Re-run quarterly (or whenever) via workflow_dispatch.
The incremental daily updates happen automatically via the regular scanner.

Notes:
  - Uses yfinance (same as scanner) for consistency
  - Sleeps 1s between tickers to be polite — full run takes ~3-4 minutes for ~125 tickers
  - SQLite chosen over CSVs: single file, no extra deps (Python stdlib), git-trackable
  - Stooq fallback could be added if yfinance ever flakes — would just swap fetch_history()
"""

import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import stdev, mean

import yfinance as yf

from universe import (
    TILE_INDEXES,
    TILE_STOCKS,
    all_recommender_tickers,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATA_DIR = Path("../data")
DB_PATH = DATA_DIR / "historical_prices.db"
BASELINES_PATH = DATA_DIR / "historical_baselines.json"

HISTORY_PERIOD = "2y"   # 2 years is plenty for 200-day MAs + 52w highs
SLEEP_BETWEEN_TICKERS = 1.0


# ============================================================
# Database setup
# ============================================================

def setup_db(conn: sqlite3.Connection) -> None:
    """Create the prices table if it doesn't exist. Idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON prices(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON prices(date)")
    conn.commit()


# ============================================================
# Fetch + persist
# ============================================================

def fetch_history(ticker: str, period: str = HISTORY_PERIOD):
    """Pull daily OHLCV for one ticker. Returns DataFrame or None on failure."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval="1d")
        if hist.empty:
            return None
        return hist
    except Exception as e:
        log.warning(f"  fetch failed for {ticker}: {e}")
        return None


def write_to_db(conn: sqlite3.Connection, ticker: str, hist) -> int:
    """Persist a ticker's history to SQLite. INSERT OR REPLACE for idempotency."""
    import pandas as pd

    if hist is None or hist.empty:
        return 0

    rows = []
    for date_idx, row in hist.iterrows():
        date_str = date_idx.date().isoformat() if hasattr(date_idx, "date") else str(date_idx)[:10]
        rows.append((
            ticker,
            date_str,
            float(row["Open"]) if not pd.isna(row["Open"]) else None,
            float(row["High"]) if not pd.isna(row["High"]) else None,
            float(row["Low"]) if not pd.isna(row["Low"]) else None,
            float(row["Close"]) if not pd.isna(row["Close"]) else None,
            int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


# ============================================================
# Baseline computation
# ============================================================

def compute_baselines(conn: sqlite3.Connection) -> dict:
    """Per-ticker baseline stats for detector use.

    For each ticker with sufficient data (≥60 days), compute:
      - current_close, current_volume
      - 200-day moving average (if ≥200 days available)
      - 60-day average volume
      - 60-day daily-return σ (volatility regime)
      - 52-week high/low (or whatever's available if <252 days)
      - n_days available
    """
    baselines = {}

    cursor = conn.execute("SELECT DISTINCT ticker FROM prices ORDER BY ticker")
    tickers = [row[0] for row in cursor.fetchall()]

    for ticker in tickers:
        # Pull in descending date order so [0] is most recent
        cursor = conn.execute("""
            SELECT close, volume FROM prices
            WHERE ticker = ? AND close IS NOT NULL
            ORDER BY date DESC
        """, (ticker,))
        rows = cursor.fetchall()

        if len(rows) < 60:
            continue

        closes = [r[0] for r in rows]
        volumes = [r[1] for r in rows if r[1] is not None]

        if not volumes:
            continue

        # 200-day MA (only if we have enough)
        ma_200 = sum(closes[:200]) / 200 if len(closes) >= 200 else None

        # 60-day avg volume
        avg_vol_60d = sum(volumes[:60]) / min(60, len(volumes))

        # 60-day daily return σ (returns from day-to-day closes)
        returns_60d = []
        for i in range(min(59, len(closes) - 1)):
            prev = closes[i + 1]
            curr = closes[i]
            if prev > 0:
                returns_60d.append((curr - prev) / prev * 100)
        sigma_60d = stdev(returns_60d) if len(returns_60d) > 1 else 0.0
        mean_return_60d = mean(returns_60d) if returns_60d else 0.0

        # 52w (252 trading days) high/low — use what's available if less
        window = closes[:252]
        high_52w = max(window)
        low_52w = min(window)

        # Distance from 52w high (a momentum proxy)
        current = closes[0]
        pct_from_high = (current - high_52w) / high_52w * 100 if high_52w > 0 else 0
        pct_from_low = (current - low_52w) / low_52w * 100 if low_52w > 0 else 0

        baselines[ticker] = {
            "current_close": round(current, 4),
            "ma_200d": round(ma_200, 4) if ma_200 else None,
            "above_ma_200d": (current > ma_200) if ma_200 else None,
            "avg_volume_60d": int(avg_vol_60d),
            "sigma_60d_pct": round(sigma_60d, 3),
            "mean_return_60d_pct": round(mean_return_60d, 3),
            "high_52w": round(high_52w, 4),
            "low_52w": round(low_52w, 4),
            "pct_from_52w_high": round(pct_from_high, 2),
            "pct_from_52w_low": round(pct_from_low, 2),
            "n_days": len(rows),
        }

    return baselines


# ============================================================
# Universe collection
# ============================================================

def universe_to_backfill() -> list[str]:
    """All tickers we want historical data for. Deduped.

    Includes ^VIX explicitly (it's not in TILE/RECOMMENDER) because regime
    classification needs historical VIX data.
    """
    seen = set()
    out = []
    for t in all_recommender_tickers():
        if t not in seen:
            seen.add(t)
            out.append(t)
    for entry in TILE_INDEXES + TILE_STOCKS:
        t = entry["ticker"]
        if t not in seen:
            seen.add(t)
            out.append(t)
    # VIX needed for regime classification but not in any list
    if "^VIX" not in seen:
        out.append("^VIX")
    return out


# ============================================================
# Main
# ============================================================

def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    tickers = universe_to_backfill()
    log.info(f"Backfilling historical data for {len(tickers)} tickers ({HISTORY_PERIOD})")
    log.info(f"  → SQLite DB: {DB_PATH}")
    log.info(f"  → Baselines JSON: {BASELINES_PATH}")

    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)

    success_count = 0
    bar_count = 0
    failed = []

    for i, ticker in enumerate(tickers, 1):
        log.info(f"[{i}/{len(tickers)}] {ticker}")
        hist = fetch_history(ticker)
        n = write_to_db(conn, ticker, hist)
        if n > 0:
            success_count += 1
            bar_count += n
            log.info(f"  + {n} bars")
        else:
            failed.append(ticker)
            log.warning(f"  ✗ no data")
        time.sleep(SLEEP_BETWEEN_TICKERS)

    log.info(f"\nBackfill complete:")
    log.info(f"  Successful: {success_count}/{len(tickers)} tickers")
    log.info(f"  Total bars: {bar_count}")
    if failed:
        log.warning(f"  Failed: {failed}")

    log.info("\nComputing per-ticker baselines...")
    baselines = compute_baselines(conn)
    output = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "history_period": HISTORY_PERIOD,
        "n_tickers_with_baselines": len(baselines),
        "tickers": baselines,
    }
    BASELINES_PATH.write_text(json.dumps(output, indent=2))
    log.info(f"Baselines written for {len(baselines)} tickers")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
