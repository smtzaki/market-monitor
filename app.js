// Watchlist symbols for TradingView mini widgets.
// Order matches the curated picks: US tech core, then Canadian.
const WATCHLIST = [
  { symbol: "NASDAQ:NVDA",  name: "NVIDIA"             },
  { symbol: "NASDAQ:AAPL",  name: "Apple"              },
  { symbol: "NASDAQ:MSFT",  name: "Microsoft"          },
  { symbol: "NASDAQ:GOOGL", name: "Alphabet"           },
  { symbol: "NASDAQ:TSLA",  name: "Tesla"              },
  { symbol: "NASDAQ:AMD",   name: "AMD"                },
  { symbol: "TSX:SHOP",     name: "Shopify"            },
  { symbol: "TSX:RY",       name: "Royal Bank"         },
];

const DATA_URL = "./data/latest.json";
const STATS_URL = "./data/signal_stats.json";

// ============================================================
// Build TradingView mini widgets
// ============================================================
function renderWatchlist() {
  const grid = document.getElementById("watchlist-grid");
  WATCHLIST.forEach(item => {
    const wrapper = document.createElement("div");
    wrapper.className = "tile-wrapper";

    const container = document.createElement("div");
    container.className = "tradingview-widget-container";
    container.style.height = "100%";

    const widgetDiv = document.createElement("div");
    widgetDiv.className = "tradingview-widget-container__widget";
    widgetDiv.style.height = "100%";
    container.appendChild(widgetDiv);

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.async = true;
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js";
    script.text = JSON.stringify({
      symbol: item.symbol,
      width: "100%",
      height: "100%",
      locale: "en",
      dateRange: "1D",
      colorTheme: "dark",
      isTransparent: true,
      autosize: true,
      largeChartUrl: "",
      noTimeScale: false,
      chartOnly: false,
      trendLineColor: "#d4af6a",
      underLineColor: "rgba(212, 175, 106, 0.15)",
      underLineBottomColor: "rgba(212, 175, 106, 0)",
    });
    container.appendChild(script);
    wrapper.appendChild(container);
    grid.appendChild(wrapper);
  });
}

// ============================================================
// Fetch latest.json — populate freshness, FX, signals
// ============================================================
async function loadData() {
  try {
    // Cache-bust so we always get the latest commit, not a CDN-cached version
    const resp = await fetch(`${DATA_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderFreshness(data.scanned_at);
    renderFX(data.usdcad_rate);
    renderSignals(data.active_signals || []);
  } catch (e) {
    document.getElementById("last-scan").textContent = "no data yet";
    document.getElementById("signals-list").innerHTML =
      '<p class="empty">Waiting for the first scan. Runs every 15 min during US market hours.</p>';
  }
}

function renderFreshness(iso) {
  const dt = new Date(iso);
  const mins = Math.floor((Date.now() - dt.getTime()) / 60000);
  let text;
  if (mins < 1)        text = "just now";
  else if (mins < 60)  text = `${mins} min ago`;
  else if (mins < 1440) text = `${Math.floor(mins / 60)}h ago`;
  else                 text = dt.toLocaleDateString();
  document.getElementById("last-scan").textContent = text;
}

function renderFX(rate) {
  if (rate) document.getElementById("fx-rate").textContent = rate.toFixed(4);
}

function renderSignals(signals) {
  const container = document.getElementById("signals-list");
  if (!signals.length) {
    container.innerHTML = '<p class="empty">No active signals right now. The scanner is watching.</p>';
    return;
  }

  // Sort by strength of signal (multiplier) so biggest moves surface first
  signals.sort((a, b) => b.multiplier - a.multiplier);

  container.innerHTML = signals.map(s => {
    const bull = s.direction === "bullish";
    const cls = bull ? "bull" : "bear";
    const arrow = bull ? "↑" : "↓";
    const headline = `${s.multiplier.toFixed(1)}× volume spike — ${bull ? "bullish surge" : "bearish dump"}`;
    return `
      <div class="signal-card">
        <div class="signal-ticker ${cls}">$${s.ticker}</div>
        <div class="signal-body">
          <div class="signal-headline">${headline}</div>
          <div class="signal-detail">
            Today ${formatVol(s.today_volume)} · 20-day avg ${formatVol(s.avg_volume_20d)}
          </div>
        </div>
        <div class="signal-price">
          $${s.price.toFixed(2)}
          <span class="change ${cls}">${arrow} ${Math.abs(s.price_change_pct).toFixed(2)}%</span>
        </div>
      </div>
    `;
  }).join("");
}

// Compact volume formatting: 79,400,949 → 79.4M
function formatVol(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString();
}

// ============================================================
// Track Record — reads signal_stats.json (Phase 3.5)
// ============================================================
async function loadStats() {
  try {
    const resp = await fetch(`${STATS_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    renderTrackRecord(data);
  } catch {
    document.getElementById("track-record-body").innerHTML =
      '<p class="empty">No stats yet. The forward-return Action runs nightly at 21:45 UTC on weekdays.</p>';
  }
}

function renderTrackRecord(data) {
  const meta = document.getElementById("track-meta");
  const body = document.getElementById("track-record-body");

  const logged = data.total_signals_logged || 0;
  const withReturns = data.total_signals_with_any_return || 0;
  meta.innerHTML = `
    <span>${logged} signal${logged !== 1 ? "s" : ""} logged</span> ·
    <span>${withReturns} with realized returns</span> ·
    <span>Updated ${formatRelTime(data.computed_at)}</span>
  `;

  const types = Object.keys(data.by_signal_type || {});
  if (!types.length || withReturns === 0) {
    body.innerHTML = `
      <p class="empty">
        Collecting evidence. First realized returns land after the next forward-return run (21:45 UTC weekdays).
      </p>
    `;
    return;
  }

  body.innerHTML = types.flatMap(st =>
    Object.entries(data.by_signal_type[st]).map(([dir, intervals]) =>
      renderBucket(st, dir, intervals)
    )
  ).join("");
}

function renderBucket(signalType, direction, intervals) {
  const labels = ["1d", "3d", "7d", "30d"];
  const rows = labels.map(label => {
    const d = intervals[label];
    if (!d) {
      return `<tr class="empty-row"><td>${label}</td><td colspan="5">—</td></tr>`;
    }
    const conf = confidenceLabel(d.n);
    const avgCls = d.avg_return_pct > 0 ? "bull" : (d.avg_return_pct < 0 ? "bear" : "");
    const avgSign = d.avg_return_pct >= 0 ? "+" : "";
    return `
      <tr>
        <td>${label}</td>
        <td>${d.n}</td>
        <td>${(d.hit_rate * 100).toFixed(1)}%</td>
        <td class="${avgCls}">${avgSign}${d.avg_return_pct.toFixed(2)}%</td>
        <td>${d.std_pct.toFixed(2)}</td>
        <td><span class="conf ${conf.cls}">${conf.label}</span></td>
      </tr>
    `;
  }).join("");

  return `
    <div class="track-bucket">
      <h3>${prettyName(signalType)} <em>· ${direction}</em></h3>
      <table class="track-table">
        <thead>
          <tr>
            <th>Interval</th><th>N</th><th>Hit %</th><th>Avg %</th><th>σ</th><th>Confidence</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function confidenceLabel(n) {
  // Same thresholds the recommender (Phase 4) will use to decide whether to act on a signal.
  if (n < 5)  return { label: "noise",       cls: "noise"       };
  if (n < 20) return { label: "weak",        cls: "weak"        };
  if (n < 50) return { label: "emerging",    cls: "emerging"    };
  return       { label: "established", cls: "established" };
}

function prettyName(s) {
  return s.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

function formatRelTime(iso) {
  if (!iso) return "—";
  const dt = new Date(iso);
  const mins = Math.floor((Date.now() - dt.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.floor(mins/60)}h ago`;
  return dt.toLocaleDateString();
}

// ============================================================
// Init
// ============================================================
renderWatchlist();
loadData();
loadStats();
// Refresh both every 5 min (data files update via Actions)
setInterval(() => { loadData(); loadStats(); }, 5 * 60 * 1000);
