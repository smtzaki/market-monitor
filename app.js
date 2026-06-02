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
// Init
// ============================================================
renderWatchlist();
loadData();
// Refresh signals every 5 min (the data file updates every 15 min via Action)
setInterval(loadData, 5 * 60 * 1000);
