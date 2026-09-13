"""Live web dashboard — stdlib only + vendored Lightweight Charts.

Interactive, live-data terminal for the paper portfolio:

- live market scanner (Dexscreener keyless API, 60s cache)
- price charts (TradingView Lightweight Charts, Apache-2.0, vendored)
- server-side price sampler: samples watched/owned pairs every 30s
  and persists them, so charts have history without any OHLC source
  (Dexscreener has no public candle endpoint — these are REAL
  sampled prices, labeled as such, never invented)

Trade controls are PAPER-ONLY by construction: every button routes
through the same PaperPortfolio.buy/sell code as the CLI, with fees
and stop rules identical. The bot has no live execution adapter at
all, and the dashboard refuses to start on non-dry-run configs.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import BotConfig
from .dexscreener import DexScreenerClient
from .paper import PaperPortfolio
from .scanner import scan_pairs

PRICE_CACHE_TTL = 60.0      # seconds, for direct price fetches
MARKET_CACHE_TTL = 60.0     # seconds, for scan results
SAMPLE_INTERVAL = 30.0      # seconds, between sampler ticks
HISTORY_CAP = 720           # max samples per pair (6h at 30s)
STATE_PATH = "memebot_state.json"
HISTORY_PATH = "memebot_price_history.json"

_price_cache: dict[str, tuple[float, float]] = {}
_market_cache: tuple[float, list] = (0.0, [])
_history: dict[str, list[tuple[float, float]]] = {}
_history_lock = threading.Lock()
_trade_lock = threading.Lock()


def lan_ip() -> str:
    """Best-effort LAN IP of this machine. Never raises."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # routing only; no packets sent
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:  # pragma: no cover
        return "127.0.0.1"


def _client() -> DexScreenerClient:
    return DexScreenerClient()


def _load_history() -> None:
    global _history
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH) as f:
                raw = json.load(f)
            _history = {a: [tuple(p) for p in pts]
                        for a, pts in raw.items()}
        except (ValueError, OSError):
            _history = {}


def _save_history() -> None:
    with open(HISTORY_PATH, "w") as f:
        json.dump(_history, f)


def _portfolio(cfg: BotConfig) -> PaperPortfolio:
    from .main import load_state
    p = PaperPortfolio(cfg.starting_balance_usd, cfg.paper_fee_pct)
    load_state(p)
    return p


def live_prices(client: DexScreenerClient,
                pairs: list[tuple[str, str]]) -> dict[str, float]:
    """Live {addr: price} for (chain, addr) pairs, TTL-cached so
    browser polls stay polite. Absent = unavailable, never invented."""
    now = time.time()
    out: dict[str, float] = {}
    by_chain: dict[str, list[str]] = {}
    for chain, addr in pairs:
        cached = _price_cache.get(addr)
        if cached and now - cached[0] < PRICE_CACHE_TTL:
            out[addr] = cached[1]
        else:
            by_chain.setdefault(chain, []).append(addr)
    for chain, addrs in by_chain.items():
        try:
            prices = client.pair_prices(chain, addrs)
        except Exception:  # offline-safe
            continue
        for addr, price in prices.items():
            _price_cache[addr] = (now, price)
            out[addr] = price
    return out


def build_status(cfg: BotConfig,
                 client: DexScreenerClient | None = None) -> dict:
    """Dashboard payload: recorded paper facts, marked honestly."""
    client = client or _client()
    with _trade_lock:
        portfolio = _portfolio(cfg)
    positions = list(portfolio.positions.values())
    live = live_prices(client, [(p.chain_id, a)
                                for a, p in portfolio.positions.items()
                                if p.chain_id]) if positions else {}
    prices = {a: live.get(a, p.entry_price)
              for a, p in portfolio.positions.items()}
    equity = portfolio.mark_to_market(prices)
    unreal = portfolio.unrealized_pnl(prices)
    pnl = equity - portfolio.starting_balance
    stops = set(portfolio.stops_hit(prices))
    pos = []
    for addr, p in portfolio.positions.items():
        price = prices.get(addr, p.entry_price)
        pos.append({
            "pair_address": addr,
            "symbol": p.token_symbol,
            "entry_price": p.entry_price,
            "current_price": price,
            "change_pct": (price / p.entry_price - 1) * 100,
            "stop_price": p.stop_price,
            "stop_hit": addr in stops,
            "priced_live": addr in live,
            "cost_usd": p.cost_usd,
            "tokens": p.tokens,
        })
    return {
        "mode": "PAPER (dry-run)" if cfg.mode.dry_run else "LIVE",
        "positions_open": len(portfolio.positions),
        "cash": round(portfolio.cash, 2),
        "equity": round(equity, 2),
        "total_pnl": round(pnl, 2),
        "total_pnl_pct": round(pnl / portfolio.starting_balance * 100, 2),
        "unrealized": round(unreal, 2),
        "positions": pos,
        "disclaimer": "Paper trading only — memecoins can and do go to "
                      "zero; nothing here is financial advice. Prices "
                      "are live from Dexscreener when available, entry "
                      "price otherwise. This bot has no live execution "
                      "adapter by design.",
    }


def market_snapshot(cfg: BotConfig, client: DexScreenerClient,
                    query: str = "SOL/USDC", limit: int = 10) -> list[dict]:
    """Top scan survivors for the query, TTL-cached."""
    global _market_cache
    now = time.time()
    cached_ts, cached = _market_cache
    if now - cached_ts < MARKET_CACHE_TTL and cached:
        return cached
    from memebot.scanner import passes_filters, parse_pair
    pairs = client.search(query) if query else []
    survivors = scan_pairs(pairs, cfg.scanner)

    def row(r, passes):
        return {
            "pair_address": r.pair_address,
            "chain_id": r.chain_id,
            "symbol": r.base_token_symbol,
            "quote": r.quote_token_symbol,
            "price_usd": r.price_usd,
            "liquidity_usd": r.liquidity_usd,
            "volume24h_usd": r.volume24h_usd,
            "txns24h": r.txns24h,
            "age_hours": round(r.age_hours, 1),
            "dex": r.dex,
            "url": r.url,
            "passes_filters": passes,
            "filter_reason": r.rejected_reason
            if not passes else None,
        }

    out = [row(r, True) for r in survivors[:limit]]
    # Wall-Street view: when few pass the bot's own filters, still
    # show the top pairs by volume — but each non-passing row is
    # labeled with WHY, never silently. (Fresh-coin filters like
    # max-age would otherwise leave the board empty.)
    if len(out) < min(limit, 5):
        seen = {r["pair_address"] for r in out}
        parsed = [r for r in (parse_pair(p) for p in pairs)
                  if r and len(r.base_token_symbol) <= 12]
        parsed.sort(key=lambda r: -r.volume24h_usd)
        for r in parsed:
            if len(out) >= limit:
                break
            if r.pair_address in seen:
                continue
            ok, reason = passes_filters(r, cfg.scanner)
            if not ok:
                r.rejected_reason = reason
                out.append(row(r, False))
                seen.add(r.pair_address)
    if out:
        _market_cache = (now, out)
    return out


def price_history(addr: str) -> list[dict]:
    """Sampled price points for one pair: [{time, value}]."""
    with _history_lock:
        pts = _history.get(addr, [])
    return [{"time": int(ts), "value": price} for ts, price in pts]


def sample_tick(cfg: BotConfig, client: DexScreenerClient) -> int:
    """One sampler pass: fetch live prices for owned + watched pairs,
    append to history, persist. Returns number of samples."""
    with _trade_lock:
        portfolio = _portfolio(cfg)
    owned = [(p.chain_id, a) for a, p in portfolio.positions.items()
             if p.chain_id]
    try:
        watched = [(m["chain_id"], m["pair_address"])
                   for m in market_snapshot(cfg, client)]
    except Exception:
        watched = []
    now = time.time()
    live = live_prices(client, owned + watched)
    n = 0
    if live:
        with _history_lock:
            for addr, price in live.items():
                pts = _history.setdefault(addr, [])
                if pts and now - pts[-1][0] < SAMPLE_INTERVAL / 2:
                    continue
                pts.append((int(now), price))
                del pts[:-HISTORY_CAP]
                n += 1
            _save_history()
    return n


def paper_trade(cfg: BotConfig, action: str, pair_address: str,
                chain_id: str | None = None, symbol: str = "?",
                cost_usd: float = 0.0, stop_pct: float = 10.0,
                client: DexScreenerClient | None = None
                ) -> tuple[int, dict]:
    """Paper-only trade from the dashboard. Same PaperPortfolio code
    path as the CLI (fees, stop rules, cash checks). Buy requires a
    live price — we never open a paper position at an invented price.
    Returns (http_status, message)."""
    client = client or _client()
    if action not in ("buy", "close"):
        return 400, {"error": "action must be buy or close"}
    with _trade_lock:
        portfolio = _portfolio(cfg)
        if action == "buy":
            if not chain_id:
                return 400, {"error": "chain_id required for buy"}
            if cost_usd <= 0 or cost_usd > portfolio.cash:
                return 400, {"error":
                             f"cost_usd must be within cash "
                             f"{portfolio.cash:.2f}"}
            live = live_prices(client, [(chain_id, pair_address)])
            if pair_address not in live:
                return 503, {"error": "no live price for this pair "
                                      "right now — refusing to trade "
                                      "at an invented price"}
            price = live[pair_address]
            stop = price * (1 - stop_pct / 100.0)
            ok, reason = portfolio.buy(pair_address, symbol, price,
                                      cost_usd, stop, chain_id)
            if not ok:
                return 400, {"error": reason}
            note = (f"bought {symbol}: cost ${cost_usd:.2f} at "
                    f"{price:.10g}, stop {stop:.10g} (paper)")
        else:  # close
            if pair_address not in portfolio.positions:
                return 404, {"error": "position not found"}
            chain = portfolio.positions[pair_address].chain_id
            live = live_prices(client,
                               [(chain, pair_address)]) if chain else {}
            price = live.get(pair_address,
                             portfolio.positions[pair_address].entry_price)
            ok, reason, pnl = portfolio.sell(pair_address, price)
            if not ok:
                return 400, {"error": reason}
            note = (f"closed {symbol}: PnL ${pnl:.2f} "
                    f"(marked {'live' if pair_address in live else 'entry'})")
        from .main import save_state
        save_state(portfolio)
    return 200, {"ok": True, "note": note}


def serve(cfg: BotConfig, host: str = "0.0.0.0", port: int = 8788) -> None:
    """Run the live dashboard until interrupted."""
    if not cfg.mode.dry_run:
        raise SystemExit("refusing to serve trade controls on a "
                         "non-dry-run config (paper-only by design)")
    _load_history()
    client = _client()

    def _sampler():
        while True:
            try:
                sample_tick(cfg, client)
            except Exception:
                pass  # sampler must never kill the dashboard
            time.sleep(SAMPLE_INTERVAL)

    threading.Thread(target=_sampler, daemon=True).start()
    _Handler.cfg = cfg
    server = ThreadingHTTPServer((host, port), _Handler)
    print("MemeBot live dashboard (paper-only controls)")
    print("  this machine:  http://127.0.0.1:%d" % port)
    if host not in ("127.0.0.1", "localhost"):
        print("  on your LAN:   http://%s:%d" % (lan_ip(), port))
    print("  live market data: Dexscreener keyless API, 60s cache;")
    print("  charts: TradingView Lightweight Charts (Apache-2.0, vendored)")
    print("  stop with Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("dashboard stopped")
    finally:
        server.server_close()


def _lwc_js() -> bytes:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "static",
                        "lightweight-charts.standalone.production.js")
    with open(path, "rb") as f:
        return f.read()


class _Handler(BaseHTTPRequestHandler):
    cfg: BotConfig | None = None

    # ---- routes -----------------------------------------------------
    def do_GET(self):  # noqa: N802 (stdlib naming)
        path = self.path.split("?")[0]
        query = self.path.split("?")[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&")
                      if "=" in p)
        if path == "/":
            self._send_html(PAGE)
        elif path == "/static/lightweight-charts.standalone.production.js":
            self._send(_lwc_js(), "application/javascript")
        elif path == "/api/status":
            self._send_json(build_status(self.cfg))
        elif path == "/api/market":
            q = params.get("query", "SOL/USDC")
            try:
                data = market_snapshot(self.cfg, _client(), q)
            except Exception:
                self._send_json({"error": "market data unavailable "
                                         "(offline?)"}, status=503)
                return
            self._send_json({"query": q, "pairs": data})
        elif path == "/api/price_history":
            self._send_json({"points": price_history(
                params.get("addr", ""))})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path != "/api/paper_trade":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        status, msg = paper_trade(
            self.cfg, body.get("action", ""),
            pair_address=body.get("pair_address", ""),
            chain_id=body.get("chain_id"),
            symbol=body.get("symbol", "?"),
            cost_usd=float(body.get("cost_usd", 0) or 0),
            stop_pct=float(body.get("stop_pct", 10) or 10))
        self._send_json(msg, status=status)

    # ---- helpers ----------------------------------------------------
    def _send(self, body: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status: int = 200):
        self._send(json.dumps(data, default=str).encode(),
                   "application/json", status)

    def _send_html(self, html: str):
        self._send(html.encode(), "text/html; charset=utf-8")

    def log_message(self, fmt, *args):  # quiet
        pass


if __name__ == "__main__":  # pragma: no cover
    from .config import load_config
    serve(load_config("config/config.example.yaml"))


PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MemeBot — Live Terminal</title>
<script src="/static/lightweight-charts.standalone.production.js"></script>
<style>
:root { --bg:#04070a; --panel:#0a0f14; --panel2:#0c1218;
  --green:#00e68a; --red:#ff4d5e; --blue:#4da3ff;
  --blue-dim:#071a2e; --green-dim:#06231a; --red-dim:#26060d;
  --text:#c8d6e0; --muted:#5f7180; --border:#14212c;
  --mono:ui-monospace,'JetBrains Mono','Fira Code','SF Mono',Consolas,monospace; }
* { box-sizing:border-box; }
body { font-family:var(--mono); background:var(--bg); color:var(--text);
  margin:0; padding:16px; max-width:1200px; margin-inline:auto;
  background-image:
    radial-gradient(ellipse 800px 300px at 50% -10%, rgba(0,230,138,.06), transparent),
    radial-gradient(ellipse 600px 200px at 10% 110%, rgba(77,163,255,.05), transparent); }
header { border-bottom:1px solid var(--border); padding-bottom:12px;
  margin-bottom:14px; display:flex; align-items:baseline;
  justify-content:space-between; flex-wrap:wrap; gap:8px; }
h1 { font-size:17px; margin:0; letter-spacing:.08em; color:var(--green);
  text-shadow:0 0 14px rgba(0,230,138,.35); }
h1 .bot { color:var(--blue); text-shadow:0 0 14px rgba(77,163,255,.35); }
.live-dot { display:inline-block; width:9px; height:9px; border-radius:50%;
  background:var(--green); margin-right:6px; box-shadow:0 0 8px var(--green);
  animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }
.sub { color:var(--muted); font-size:12px; }
.cards { display:grid; gap:12px; grid-template-columns:
  repeat(auto-fit,minmax(160px,1fr)); margin-bottom:14px; }
.card { background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border); border-radius:10px; padding:12px;
  position:relative; overflow:hidden; }
.card::before { content:""; position:absolute; inset:0 auto auto 0;
  width:3px; height:100%; background:var(--blue); opacity:.8; }
.card.g::before { background:var(--green); } .card.r::before { background:var(--red); }
.label { font-size:10px; letter-spacing:.12em; color:var(--muted);
  text-transform:uppercase; margin-bottom:6px; }
.value { font-size:18px; font-weight:700; }
.pos { color:var(--green); } .neg { color:var(--red); } .blue { color:var(--blue); }
#chartwrap { background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border); border-radius:10px; padding:10px;
  margin-bottom:14px; }
#chart { width:100%; height:340px; }
.chart-head { display:flex; justify-content:space-between; align-items:center;
  padding:0 4px 8px; flex-wrap:wrap; gap:8px; }
select,button,input { font-family:var(--mono); font-size:13px;
  background:var(--panel2); color:var(--text); border:1px solid var(--border);
  border-radius:6px; padding:6px 10px; }
button { cursor:pointer; }
button.buy { color:var(--green); border-color:var(--green); }
button.buy:hover { background:var(--green-dim); }
button.sell { color:var(--red); border-color:var(--red); }
button.sell:hover { background:var(--red-dim); }
table { width:100%; border-collapse:collapse; font-size:13px;
  background:var(--panel); border:1px solid var(--border);
  border-radius:10px; overflow:hidden; margin-bottom:14px; }
th { text-align:left; padding:9px 10px; color:var(--blue);
  background:var(--blue-dim); font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; }
td { padding:8px 10px; border-top:1px solid var(--border); }
tr:hover td { background:rgba(0,230,138,.04); }
h2 { font-size:13px; letter-spacing:.1em; color:var(--blue); margin:6px 0 8px; }
.note { color:var(--muted); font-size:11px; margin-top:8px; line-height:1.6; }
#toast { position:fixed; bottom:16px; right:16px; max-width:340px;
  background:var(--panel); border:1px solid var(--blue); border-radius:8px;
  padding:10px 14px; font-size:12px; display:none; }
@media (prefers-reduced-motion:reduce) { .live-dot { animation:none; } }
</style></head><body>
<header><div>
  <h1><span class="live-dot"></span>MEME<span class="bot">BOT</span> <span class="sub">live terminal</span></h1>
  <div class="sub">paper-only controls &middot; live Dexscreener data &middot; charts: TradingView Lightweight Charts (Apache-2.0, vendored)</div>
</div><div class="sub">updated <span id="updated" class="blue">...</span></div></header>
<div class="cards">
  <div class="card g"><div class="label">Equity (marked)</div><div class="value" id="equity">...</div></div>
  <div class="card"><div class="label">Cash</div><div class="value" id="cash">...</div></div>
  <div class="card"><div class="label">Total PnL</div><div class="value" id="pnl">...</div></div>
  <div class="card"><div class="label">Open positions</div><div class="value" id="open">...</div></div>
  <div class="card"><div class="label">Unrealized</div><div class="value" id="unreal">...</div></div>
</div>
<div id="chartwrap"><div class="chart-head">
  <span class="sub" id="chart-title">select a token below to view its price</span>
  <span class="sub">server samples prices every 30s — real samples, not OHLC</span>
</div><div id="chart"></div></div>
<h2>MARKET SCAN</h2>
<table><thead><tr><th>Token</th><th>Price</th><th>Liquidity</th>
  <th>Vol 24h</th><th>Txns 24h</th><th>Age</th><th>Dex</th><th>Trade</th></tr></thead>
  <tbody id="market-body"><tr><td colspan="8" class="sub">loading scan...</td></tr></tbody></table>
<h2>PAPER POSITIONS</h2>
<table><thead><tr><th>Token</th><th>Entry</th><th>Now</th><th>Change</th>
  <th>Stop</th><th>Cost</th><th>Chart</th><th>Close</th></tr></thead>
  <tbody id="pos-body"><tr><td colspan="8" class="sub">...</td></tr></tbody></table>
<div class="note" id="disclaimer"></div>
<div id="toast"></div>
<script>
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function cls(v){return v>=0?"pos":"neg";}
function sgn(v){return (v>=0?"+":"")+Number(v).toFixed(2);}
function fmtUsd(v){return "$"+Number(v).toLocaleString("en-US",{maximumFractionDigits:0});}
function toast(msg,err){const t=document.getElementById("toast");
  t.textContent=msg; t.style.display="block";
  t.style.borderColor=err?"var(--red)":"var(--blue)";
  clearTimeout(window._tt); window._tt=setTimeout(()=>{t.style.display="none";},4000);}
let chart=null, series=null;
function ensureChart(){
  if (chart) return;
  chart = LightweightCharts.createChart(document.getElementById("chart"),
    {layout:{background:{color:"transparent"},textColor:"#5f7180"},
     grid:{vertLines:{color:"#14212c"},horzLines:{color:"#14212c"}},
     timeScale:{timeVisible:true,secondsVisible:false},
     autoSize:true, height:340});
  series = chart.addAreaSeries({lineColor:"#00e68a", topColor:"rgba(0,230,138,.28)",
    bottomColor:"rgba(0,230,138,0)", lineWidth:2, priceLineVisible:true});
}
function showHistory(addr, symbol){
  ensureChart();
  document.getElementById("chart-title").textContent =
    symbol + " — sampled price (30s intervals)";
  fetch("/api/price_history?addr="+encodeURIComponent(addr))
    .then(r=>r.json()).then(d=>{
      if (!d.points || !d.points.length){
        series.setData([]); toast("no samples yet for "+symbol+" — the sampler runs every 30s"); return; }
      series.setData(d.points);
      chart.timeScale().fitContent();
    });
}
async function refresh(){
  try{
    const s = await (await fetch("/api/status")).json();
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    document.getElementById("equity").textContent = "$"+Number(s.equity).toFixed(2);
    document.getElementById("cash").textContent = "$"+Number(s.cash).toFixed(2);
    document.getElementById("open").textContent = s.positions_open;
    const pnl=document.getElementById("pnl");
    pnl.textContent = sgn(s.total_pnl)+" ("+sgn(s.total_pnl_pct)+"%)";
    pnl.className = "value "+cls(s.total_pnl);
    const un=document.getElementById("unreal");
    un.textContent = sgn(s.unrealized); un.className = "value "+cls(s.unrealized);
    const pb=document.getElementById("pos-body");
    if ((s.positions||[]).length){
      pb.innerHTML = s.positions.map(p =>
        `<tr><td class="blue">${esc(p.symbol)}${p.stop_hit?" <span class=neg>[STOP HIT]</span>":""}</td>
        <td>${Number(p.entry_price).toPrecision(4)}</td>
        <td>${Number(p.current_price).toPrecision(4)}</td>
        <td class="${cls(p.change_pct)}">${sgn(p.change_pct)}%</td>
        <td class="sub">${Number(p.stop_price).toPrecision(4)}</td>
        <td>${fmtUsd(p.cost_usd)}</td>
        <td><button onclick="showHistory('${esc(p.pair_address)}','${esc(p.symbol)}')">view</button></td>
        <td><button class="sell" onclick="doTrade('close','${esc(p.pair_address)}','${esc(p.symbol)}',0,0)">close</button></td></tr>`).join("");
    } else { pb.innerHTML = `<tr><td colspan="8" class="sub">no open paper positions</td></tr>`; }
    if (s.disclaimer) document.getElementById("disclaimer").textContent = s.disclaimer;
  }catch(e){ document.getElementById("updated").textContent = "connection lost"; }
}
async function refreshMarket(){
  try{
    const m = await (await fetch("/api/market")).json();
    const mb = document.getElementById("market-body");
    if (m.error){ mb.innerHTML = `<tr><td colspan="8" class="sub">${esc(m.error)}</td></tr>`; return; }
    if (!(m.pairs||[]).length){ mb.innerHTML = `<tr><td colspan="8" class="sub">no pairs found for the default scan</td></tr>`; return; }
    mb.innerHTML = m.pairs.map(p => {
      const dim = p.passes_filters ? "" : "sub";
      const verdict = p.passes_filters ? `<span class="pos">PASS</span>`
        : `<span class="neg" title="${esc(p.filter_reason||'')}">FILTERED</span>`;
      const btn = `<button class="buy" onclick="buyPrompt('${esc(p.pair_address)}','${esc(p.chain_id)}','${esc(p.symbol)}',${p.passes_filters?"true":"false"})">buy</button>`;
      return `<tr><td class="${dim} blue">${esc(p.symbol)}/${esc(p.quote)} <span class="sub">${verdict}</span></td>
       <td class="${dim}">${Number(p.price_usd).toPrecision(4)}</td>
       <td class="${dim}">${fmtUsd(p.liquidity_usd)}</td><td class="${dim}">${fmtUsd(p.volume24h_usd)}</td>
       <td class="${dim}">${p.txns24h}</td><td class="${dim}">${p.age_hours}h</td>
       <td class="${dim} sub">${esc(p.dex)}</td><td>${btn}</td></tr>`;}).join("");
  }catch(e){ /* keep old table */ }
}
function buyPrompt(addr, chain, symbol, passes){
  if (passes===false && !confirm(symbol+" does NOT pass the scan filters.\\nPaper-buy anyway?")) return;
  const cost = prompt("Paper buy "+symbol+"\\nCost in USD (max = your cash):","25");
  if (cost===null) return;
  const stop = prompt("Stop-loss percent (e.g. 10 = sell if -10%):","10");
  if (stop===null) return;
  doTrade("buy", addr, symbol, parseFloat(cost), parseFloat(stop), chain);
}
async function doTrade(action, addr, symbol, cost, stop, chain){
  try{
    const r = await fetch("/api/paper_trade", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({action, pair_address:addr, symbol,
        cost_usd:cost, stop_pct:stop, chain_id:chain||null})});
    const d = await r.json();
    if (r.ok){ toast(d.note||"done"); refresh(); }
    else { toast(d.error||"failed", true); }
  }catch(e){ toast("connection error", true); }
}
refresh(); refreshMarket();
setInterval(refresh, 5000);
setInterval(refreshMarket, 60000);
</script></body></html>"""
