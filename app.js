// ============================================================
// Configuration
// ============================================================
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

const DATA_URL          = "./data/latest.json";
const STATS_URL         = "./data/signal_stats.json";
const STATS_TICKER_URL  = "./data/signal_stats_by_ticker.json";
const STATS_REGIME_URL  = "./data/signal_stats_by_regime.json";
const SIGNAL_LOG_URL    = "./data/signal_log.json";

// State held in memory for filter interactions
const state = {
  signalLog: null,
  byTicker: null,
};

// ============================================================
// Tab switching
// ============================================================
function setupTabs() {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const targetId = "panel-" + btn.dataset.tab;
      document.getElementById(targetId).classList.add("active");
    });
  });
}

// ============================================================
// TradingView watchlist
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
// Latest data (regime + active signals)
// ============================================================
async function loadLatest() {
  try {
    const resp = await fetch(`${DATA_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderFreshness(data.scanned_at);
    renderFX(data.usdcad_rate);
    renderRegime(data.regime);
    renderActiveSignals(data.active_signals || []);
  } catch (e) {
    document.getElementById("last-scan").textContent = "no data yet";
    document.getElementById("active-signals-body").innerHTML =
      '<p class="empty">Waiting for the first scan.</p>';
  }
}

function renderFreshness(iso) {
  document.getElementById("last-scan").textContent = formatRelTime(iso);
}

function renderFX(rate) {
  if (rate) document.getElementById("fx-rate").textContent = rate.toFixed(4);
}

function renderRegime(regime) {
  if (!regime) return;
  const cls = regime.classification || "unknown";
  const valEl = document.getElementById("regime-value");
  valEl.textContent = cls;
  valEl.className = "regime-value " + cls;

  document.getElementById("regime-vix").textContent =
    regime.vix !== undefined && regime.vix !== null ? regime.vix.toFixed(2) : "—";

  const spy5d = document.getElementById("regime-spy5d");
  if (regime.spy_5d_pct !== undefined && regime.spy_5d_pct !== null) {
    spy5d.textContent = `${regime.spy_5d_pct >= 0 ? "+" : ""}${regime.spy_5d_pct.toFixed(2)}%`;
    spy5d.className = "regime-num " + (regime.spy_5d_pct >= 0 ? "up" : "down");
  }

  const spy20d = document.getElementById("regime-spy20d");
  if (regime.spy_20d_pct !== undefined && regime.spy_20d_pct !== null) {
    spy20d.textContent = `${regime.spy_20d_pct >= 0 ? "+" : ""}${regime.spy_20d_pct.toFixed(2)}%`;
    spy20d.className = "regime-num " + (regime.spy_20d_pct >= 0 ? "up" : "down");
  }
}

function renderActiveSignals(signals) {
  const container = document.getElementById("active-signals-body");
  const meta = document.getElementById("active-meta");
  meta.textContent = `${signals.length} signal${signals.length !== 1 ? "s" : ""} currently active`;
  if (!signals.length) {
    container.innerHTML = '<p class="empty">No active signals right now. The scanner is watching.</p>';
    return;
  }
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

// ============================================================
// Track Record (overview tab)
// ============================================================
async function loadStats() {
  try {
    const resp = await fetch(`${STATS_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    renderTrackRecord(data);
  } catch {
    document.getElementById("track-record-body").innerHTML =
      '<p class="empty">No stats yet — forward-return Action runs nightly at 21:45 UTC.</p>';
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
    body.innerHTML = `<p class="empty">Collecting evidence. First realized returns land after the next forward-return run.</p>`;
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
    if (!d) return `<tr class="empty-row"><td>${label}</td><td colspan="5">—</td></tr>`;
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

// ============================================================
// Per-Ticker Performance
// ============================================================
async function loadByTicker() {
  try {
    const resp = await fetch(`${STATS_TICKER_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error();
    state.byTicker = await resp.json();
    renderByTicker();
  } catch {
    document.getElementById("by-ticker-body").innerHTML =
      '<p class="empty">No per-ticker stats yet.</p>';
  }
}

function renderByTicker() {
  if (!state.byTicker) return;
  const body = document.getElementById("by-ticker-body");
  const minN = parseInt(document.getElementById("ticker-min-n").value);
  const direction = document.getElementById("ticker-direction").value;
  const sortBy = document.getElementById("ticker-sort").value;

  // Build flat rows: one per (ticker, direction, interval)
  const rows = [];
  for (const [ticker, dirs] of Object.entries(state.byTicker.tickers || {})) {
    for (const [dir, intervals] of Object.entries(dirs)) {
      if (direction !== "all" && dir !== direction) continue;
      for (const [interval, d] of Object.entries(intervals)) {
        if (interval !== "1d" && interval !== "3d") continue;
        if (d.n < minN) continue;
        const edge = d.hit_rate * Math.abs(d.avg_return_pct);
        rows.push({ ticker, direction: dir, interval, ...d, edge });
      }
    }
  }

  // Sort
  const sortKeyFns = {
    edge: r => r.edge,
    hit_rate: r => r.hit_rate,
    avg_return: r => Math.abs(r.avg_return_pct),
    n: r => r.n,
  };
  rows.sort((a, b) => sortKeyFns[sortBy](b) - sortKeyFns[sortBy](a));

  if (!rows.length) {
    body.innerHTML = '<p class="empty">No tickers match the current filters.</p>';
    return;
  }

  body.innerHTML = `
    <table class="dtable">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Direction</th>
          <th>Interval</th>
          <th class="right">N</th>
          <th class="right">Hit %</th>
          <th class="right">Avg %</th>
          <th class="right">σ</th>
          <th class="right">Edge</th>
          <th class="center">Confidence</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => {
          const conf = confidenceLabel(r.n);
          const avgCls = r.avg_return_pct > 0 ? "bull" : "bear";
          const sign = r.avg_return_pct >= 0 ? "+" : "";
          const dirCls = r.direction === "bullish" ? "bull" : "bear";
          return `
            <tr>
              <td class="ticker-cell">${r.ticker}</td>
              <td class="${dirCls}">${r.direction}</td>
              <td class="dim">${r.interval}</td>
              <td class="right">${r.n}</td>
              <td class="right">${(r.hit_rate * 100).toFixed(1)}%</td>
              <td class="right ${avgCls}">${sign}${r.avg_return_pct.toFixed(2)}%</td>
              <td class="right">${r.std_pct.toFixed(2)}</td>
              <td class="right">${r.edge.toFixed(2)}</td>
              <td class="center"><span class="conf ${conf.cls}">${conf.label}</span></td>
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>
  `;
}

function setupByTickerFilters() {
  const minN = document.getElementById("ticker-min-n");
  const minNVal = document.getElementById("ticker-min-n-value");
  minN.addEventListener("input", () => {
    minNVal.textContent = minN.value;
    renderByTicker();
  });
  document.getElementById("ticker-direction").addEventListener("change", renderByTicker);
  document.getElementById("ticker-sort").addEventListener("change", renderByTicker);
}

// ============================================================
// By Regime
// ============================================================
async function loadByRegime() {
  try {
    const resp = await fetch(`${STATS_REGIME_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    renderByRegime(data);
  } catch {
    document.getElementById("by-regime-body").innerHTML =
      '<p class="empty">No per-regime stats yet.</p>';
  }
}

function renderByRegime(data) {
  const body = document.getElementById("by-regime-body");
  const regimes = Object.keys(data.by_regime || {});
  if (!regimes.length) {
    body.innerHTML = '<p class="empty">No regime-tagged signals yet. Tag signals will populate after backfill or as new signals fire.</p>';
    return;
  }

  // Order regimes: bull, neutral, correction, bear, crisis, unknown
  const order = ["bull", "neutral", "correction", "bear", "crisis", "unknown"];
  regimes.sort((a, b) => order.indexOf(a) - order.indexOf(b));

  body.innerHTML = `
    <div class="regime-grid">
      ${regimes.map(regime => renderRegimeBlock(regime, data.by_regime[regime])).join("")}
    </div>
  `;
}

function renderRegimeBlock(regimeName, types) {
  const allRows = [];
  for (const [st, dirs] of Object.entries(types)) {
    for (const [dir, intervals] of Object.entries(dirs)) {
      for (const [interval, d] of Object.entries(intervals)) {
        if (interval !== "1d" && interval !== "3d") continue;
        allRows.push({ signalType: st, direction: dir, interval, ...d });
      }
    }
  }

  // Total n across this regime
  const totalN = allRows.reduce((sum, r) => sum + r.n, 0);

  if (!allRows.length) {
    return `
      <div class="regime-block">
        <h3 class="${regimeName}">${regimeName}</h3>
        <div class="regime-block-meta">No data</div>
      </div>
    `;
  }

  return `
    <div class="regime-block">
      <h3 class="${regimeName}">${regimeName}</h3>
      <div class="regime-block-meta">${totalN} signal-intervals tracked</div>
      <table class="dtable">
        <thead>
          <tr><th>Dir</th><th>Int</th><th class="right">N</th><th class="right">Hit %</th><th class="right">Avg %</th></tr>
        </thead>
        <tbody>
          ${allRows.map(r => {
            const avgCls = r.avg_return_pct > 0 ? "bull" : "bear";
            const sign = r.avg_return_pct >= 0 ? "+" : "";
            const dirCls = r.direction === "bullish" ? "bull" : "bear";
            return `
              <tr>
                <td class="${dirCls}">${r.direction.slice(0, 4)}</td>
                <td class="dim">${r.interval}</td>
                <td class="right">${r.n}</td>
                <td class="right">${(r.hit_rate * 100).toFixed(1)}%</td>
                <td class="right ${avgCls}">${sign}${r.avg_return_pct.toFixed(2)}%</td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}

// ============================================================
// Signal Log (filterable raw view)
// ============================================================
async function loadSignalLog() {
  try {
    const resp = await fetch(`${SIGNAL_LOG_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error();
    state.signalLog = await resp.json();
    renderSignalLog();
  } catch {
    document.getElementById("signal-log-body").innerHTML =
      '<p class="empty">No signal log yet.</p>';
  }
}

function renderSignalLog() {
  if (!state.signalLog) return;
  const body = document.getElementById("signal-log-body");
  const search = document.getElementById("log-search").value.trim().toUpperCase();
  const direction = document.getElementById("log-direction").value;
  const regime = document.getElementById("log-regime").value;
  const limit = parseInt(document.getElementById("log-limit").value);

  let signals = state.signalLog.signals || [];

  if (search) {
    signals = signals.filter(s => s.ticker.toUpperCase().includes(search));
  }
  if (direction !== "all") {
    signals = signals.filter(s => (s.details?.direction || "") === direction);
  }
  if (regime !== "all") {
    signals = signals.filter(s => (s.regime?.classification || "unknown") === regime);
  }

  // Most recent first
  signals.sort((a, b) => new Date(b.fired_at) - new Date(a.fired_at));
  const total = signals.length;
  signals = signals.slice(0, limit);

  body.innerHTML = `
    <div class="log-meta">Showing ${signals.length} of ${total} matching signals</div>
    <table class="dtable">
      <thead>
        <tr>
          <th>When</th>
          <th>Ticker</th>
          <th>Dir</th>
          <th class="right">Mult</th>
          <th class="right">Δ Price</th>
          <th class="right">1d</th>
          <th class="right">3d</th>
          <th class="right">7d</th>
          <th class="right">30d</th>
          <th>Regime</th>
        </tr>
      </thead>
      <tbody>
        ${signals.map(s => {
          const fired = formatDateShort(s.fired_at);
          const dir = s.details?.direction || "—";
          const dirCls = dir === "bullish" ? "bull" : dir === "bearish" ? "bear" : "";
          const mult = s.details?.multiplier ? s.details.multiplier.toFixed(2) + "x" : "—";
          const pc = s.details?.price_change_pct;
          const pcCls = pc > 0 ? "bull" : pc < 0 ? "bear" : "";
          const pcStr = pc !== undefined ? `${pc >= 0 ? "+" : ""}${pc.toFixed(2)}%` : "—";
          const r = s.forward_returns || {};
          const rCell = (val) => {
            if (val === null || val === undefined) return '<td class="right dim">—</td>';
            const cls = val > 0 ? "bull" : val < 0 ? "bear" : "";
            const sign = val >= 0 ? "+" : "";
            return `<td class="right ${cls}">${sign}${val.toFixed(2)}%</td>`;
          };
          const regimeCls = s.regime?.classification || "unknown";
          return `
            <tr>
              <td class="dim">${fired}</td>
              <td class="ticker-cell">${s.ticker}</td>
              <td class="${dirCls}">${dir.slice(0, 4)}</td>
              <td class="right">${mult}</td>
              <td class="right ${pcCls}">${pcStr}</td>
              ${rCell(r["1d"])}
              ${rCell(r["3d"])}
              ${rCell(r["7d"])}
              ${rCell(r["30d"])}
              <td><span class="regime-pill ${regimeCls}">${regimeCls}</span></td>
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>
  `;
}

function setupSignalLogFilters() {
  ["log-search", "log-direction", "log-regime", "log-limit"].forEach(id => {
    document.getElementById(id).addEventListener("input", renderSignalLog);
    document.getElementById(id).addEventListener("change", renderSignalLog);
  });
}

// ============================================================
// Utilities
// ============================================================
function confidenceLabel(n) {
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
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return dt.toLocaleDateString();
}

function formatDateShort(iso) {
  const dt = new Date(iso);
  return dt.toLocaleString("en-CA", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false
  });
}

function formatVol(n) {
  if (n === null || n === undefined) return "—";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString();
}

// ============================================================
// Init
// ============================================================
setupTabs();
setupByTickerFilters();
setupSignalLogFilters();
renderWatchlist();
loadLatest();
loadStats();
loadByTicker();
loadByRegime();
loadSignalLog();

// Refresh everything every 5 min
setInterval(() => {
  loadLatest();
  loadStats();
  loadByTicker();
  loadByRegime();
  loadSignalLog();
}, 5 * 60 * 1000);
