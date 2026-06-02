# Market Activity Monitor

A free, self-hosted (well, GitHub-hosted) market signal scanner with Discord alerts.

**Status: Phase 3.5 of 5.** Scanner, dashboard, forward-return tracking, and live track-record display are all deployed. Recommender (Phase 4) and Stocks tab (Phase 5) come next once the system accumulates enough signal history.

---

## What it does (right now)

Every 15 minutes during US market hours, the scanner:

1. Fetches the current USD/CAD rate.
2. Pulls prices for the **tile section** (3 indexes + 8 stocks).
3. Scans the **recommender universe** (~85 US + Canadian tickers) for **volume spike signals**:
   - Today's projected daily volume is ≥ 2.5× the 20-day average
   - **AND** the stock has moved ≥ 1% intraday (kills noise from low-conviction rebalancing)
4. Writes `data/latest.json` + `data/signal_log.json`.
5. Pushes Discord alerts for new signals (4-hour cooldown per ticker so you don't get spammed).

Options scanning is intentionally **removed** — it produced 50 alerts in a minute on liquid names like NVDA, which is unactionable.

---

## Setup (one-time)

### 1. Fork or create the repo

Push this folder structure to a **public** GitHub repo (public = unlimited free Actions minutes).

### 2. Create a Discord webhook

In your Discord server: **Server Settings → Integrations → Webhooks → New Webhook**. Copy the URL.

### 3. Add the webhook as a GitHub secret

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `DISCORD_WEBHOOK_URL`
- Value: paste your webhook URL

### 4. Enable Actions

**Settings → Actions → General → Allow all actions**. Also ensure **Workflow permissions → Read and write permissions** is enabled (the workflow needs to commit `data/` back).

### 5. Test it

Go to **Actions → Market Scan → Run workflow**. Watch the logs. After ~1 min you should see:
- `data/latest.json` committed to the repo
- A test message in Discord if a signal happened to fire (otherwise just the data file)

After this first manual run, the cron schedule takes over and it runs every 15 minutes automatically during market hours.

---

## File layout

```
market-monitor/
├── .github/workflows/
│   ├── scan.yml                  # 15-min cron: scan + alert + write data
│   └── forward-returns.yml       # daily cron: realize returns + compute stats
├── scanner/
│   ├── scan.py                   # scanner orchestrator
│   ├── universe.py               # ticker lists
│   ├── detectors.py              # signal logic (volume spike)
│   ├── alerts.py                 # Discord webhook
│   ├── state.py                  # alert dedup
│   └── update_returns.py         # forward-return + stats updater
├── data/
│   ├── latest.json               # current snapshot (dashboard reads this)
│   ├── signal_log.json           # historical signals + realized returns
│   ├── signal_stats.json         # aggregate hit rates per signal type
│   └── alert_state.json          # alert dedup cache
├── index.html                    # dashboard
├── style.css                     # dashboard styles
├── app.js                        # dashboard JS
├── .nojekyll                     # disable Jekyll on GitHub Pages
├── requirements.txt
└── README.md
```

---

## Customizing

**Add tickers to scan**: edit `scanner/universe.py`. The recommender will scan whatever's in the three lists (US, TSX, smallcap).

**Adjust signal sensitivity**: edit `scanner/detectors.py`:
- `VOLUME_SPIKE_MULTIPLIER` (default 2.5) — higher = fewer, stronger signals
- `VOLUME_MIN_PRICE_MOVE_PCT` (default 1.0) — higher = filters out tiny moves

**Change alert cooldown**: edit `scanner/state.py` → `ALERT_COOLDOWN_HOURS` (default 4).

**Run locally for testing**:
```bash
pip install -r requirements.txt
cd scanner
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python scan.py
```

---

## Dashboard (Phase 2 — ✅ included)

Static dashboard at the repo root: `index.html`, `style.css`, `app.js`. It uses:

- **TradingView embeds** for the indexes ticker tape + 8 stock tiles (live prices, free, no API key)
- **Custom signals feed** reading directly from `data/latest.json`

### Enable GitHub Pages

In your repo: **Settings → Pages**
- **Source**: Deploy from a branch
- **Branch**: `main` / `(root)`
- Click Save

After ~1 minute your dashboard is live at `https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/`.

Note: every time the scanner Action commits new data, GitHub Pages re-deploys. There's typically a ~30-90s delay between commit and the dashboard reflecting the update. The page itself refreshes signals every 5 min in the browser.

### Customizing the dashboard

**Watchlist** (the 8 mini-chart tiles): edit `WATCHLIST` in `app.js`. TradingView symbol format: `EXCHANGE:TICKER` (e.g. `NASDAQ:NVDA`, `TSX:SHOP`, `NYSE:BAC`).

**Index ticker tape**: edit the `symbols` array inside `index.html`.

**Aesthetics**: all colors and fonts are CSS variables in `:root` at the top of `style.css`. Change `--bull`, `--bear`, `--accent`, or swap `--serif`/`--mono` to other Google Fonts.

---

## Forward-return tracking (Phase 3 — ✅ included)

A second cron Action (`.github/workflows/forward-returns.yml`) runs **once per weekday at 21:45 UTC** (~5:45 PM EDT / 4:45 PM EST, safely after market close).

It walks every signal in `signal_log.json` and fills in the realized return at 1, 3, 7, and 30 calendar days after fire. Returns are computed against the *first trading day's close on or after* the target date — so a Friday signal's "1d" return is Monday's close, holidays handled the same way.

It also writes `data/signal_stats.json` — aggregate stats per signal type:

```json
{
  "by_signal_type": {
    "volume_spike": {
      "bullish": {
        "1d":  { "n": 23, "hit_rate": 0.609, "avg_return_pct": 1.42, "std_pct": 2.81, ... },
        "3d":  { "n": 21, "hit_rate": 0.571, "avg_return_pct": 2.10, ... },
        ...
      },
      "bearish": { ... }
    }
  }
}
```

**Hit rate** = % of signals where the move went the direction the signal predicted.
For bullish signals, that's % with positive return; for bearish, % with negative return.

**Sample size matters.** These numbers are meaningless until ~20–30 fires per bucket. Expect 3–4 weeks of running before the stats start telling a real story. The recommender (Phase 4) will display sample size and warn when confidence is low.

**This phase is silent** — no dashboard changes yet, no Discord pings. It just quietly accumulates the empirical evidence the recommender needs.

To verify it's working: after the first 21:45 UTC run on a weekday, check that `data/signal_stats.json` exists and that any signals older than 1 day in `signal_log.json` have at least their `"1d"` slot filled.

---

## What's coming (next phases)

- **Phase 4**: Recommender tab — uses `signal_stats.json` to suggest fractional-share allocations of $150 CAD across active signals, with USD/CAD conversion, expected returns, confidence intervals, recommended hold periods, and the Invested/Sold workflow.
- **Phase 5**: Stocks tab — raw current signals view (more detail than the dashboard's headline feed).

---

## Known limitations

- **yfinance is unofficial Yahoo scraping.** If Yahoo rate-limits GitHub Actions IPs, we'd switch to Finnhub or Polygon free tier (both require an API key but are free).
- **GitHub Actions cron is best-effort** — scheduled runs can drift 5–15 minutes during high-load periods. Acceptable for a 15-min cadence scanner.
- **No US market holiday calendar.** Scanner runs on holidays but finds nothing (market closed = stale data). Cosmetic issue.
- **Signals are correlation, not prediction.** Volume spikes precede big moves sometimes, often don't, sometimes the move is already done by the time you see it. Phase 3's forward-return tracking is what will eventually tell you whether any of these signals have real edge.
