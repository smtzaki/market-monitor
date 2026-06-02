# Market Activity Monitor

A free, self-hosted (well, GitHub-hosted) market signal scanner with Discord alerts.

**Status: Phase 1 of 5.** The scanner runs on GitHub Actions every 15 minutes during US market hours, writes results to `data/latest.json`, and pushes alerts to Discord. Dashboard, signal-tracking, and recommender come in later phases.

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
├── .github/workflows/scan.yml    # cron-triggered Action
├── scanner/
│   ├── scan.py                   # orchestrator (entrypoint)
│   ├── universe.py               # ticker lists — edit to customize
│   ├── detectors.py              # signal logic (volume spike)
│   ├── alerts.py                 # Discord webhook
│   └── state.py                  # dedup state across runs
├── data/                         # scanner outputs (committed back)
│   ├── latest.json               # current snapshot — dashboard reads this
│   ├── signal_log.json           # historical signals (for Phase 3 forward returns)
│   └── alert_state.json          # dedup cache
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

## What's coming (next phases)

- **Phase 3**: Forward-return tracking — second Action runs end-of-day, populates `forward_returns` on past signals at 1d/3d/7d/30d intervals. This is the "feeder stage" that lets the recommender learn what actually works.
- **Phase 4**: Recommender tab — uses signal track record to suggest fractional-share allocations of $150 CAD with USD conversion, expected returns, confidence intervals, and the Invested/Sold workflow.
- **Phase 5**: Stocks tab — raw current signals view (more detail than the dashboard's headline feed).

---

## Known limitations

- **yfinance is unofficial Yahoo scraping.** If Yahoo rate-limits GitHub Actions IPs, we'd switch to Finnhub or Polygon free tier (both require an API key but are free).
- **GitHub Actions cron is best-effort** — scheduled runs can drift 5–15 minutes during high-load periods. Acceptable for a 15-min cadence scanner.
- **No US market holiday calendar.** Scanner runs on holidays but finds nothing (market closed = stale data). Cosmetic issue.
- **Signals are correlation, not prediction.** Volume spikes precede big moves sometimes, often don't, sometimes the move is already done by the time you see it. Phase 3's forward-return tracking is what will eventually tell you whether any of these signals have real edge.
