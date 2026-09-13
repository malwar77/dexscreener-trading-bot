"""Read-only web dashboard — stdlib only.

Serves a terminal-style dashboard on your LAN (default 0.0.0.0:8788)
so you can watch the paper portfolio from any device on the same
network. It is a WATCHING surface: no buy/sell buttons live here by
design — execution stays in `memebot run`, paper-mode only.

Live prices are fetched from Dexscreener's keyless public API with a
60-second server-side cache, so a 5-second browser poll never hammers
the API. When prices are unavailable (offline, API down), positions
are marked at entry price and the dashboard says so.
"""
from __future__ import annotations

import json
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import BotConfig, load_config
from .dexscreener import DexScreenerClient
from .paper import PaperPortfolio

PRICE_CACHE_TTL = 60.0  # seconds
_price_cache: dict[str, tuple[float, float]] = {}  # addr -> (ts, price)


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


def _cached_prices(client: DexScreenerClient,
                   portfolio: PaperPortfolio) -> dict[str, float]:
    """Live prices for held positions, with a TTL cache so dashboard
    polls don't hammer Dexscreener. Falls back to entry price."""
    now = time.time()
    fresh = {}
    by_chain: dict[str, list[str]] = {}
    for addr, p in portfolio.positions.items():
        cached = _price_cache.get(addr)
        if cached and now - cached[0] < PRICE_CACHE_TTL:
            fresh[addr] = cached[1]
        elif p.chain_id:
            by_chain.setdefault(p.chain_id, []).append(addr)
    for chain, addrs in by_chain.items():
        try:
            prices = client.pair_prices(chain, addrs)
        except Exception:  # offline-safe: keep entry marks
            continue
        for addr, price in prices.items():
            _price_cache[addr] = (now, price)
            fresh[addr] = price
    return fresh


def build_status(cfg: BotConfig,
                 client: DexScreenerClient | None = None) -> dict:
    """Dashboard payload: recorded paper facts, marked honestly."""
    from .main import load_state
    client = client or DexScreenerClient()
    portfolio = PaperPortfolio(cfg.starting_balance_usd, cfg.paper_fee_pct)
    load_state(portfolio)
    live = _cached_prices(client, portfolio) if portfolio.positions else {}
    prices = {a: live.get(a, p.entry_price)
              for a, p in portfolio.positions.items()}
    equity = portfolio.mark_to_market(prices)
    unreal = portfolio.unrealized_pnl(prices)
    pnl = equity - portfolio.starting_balance
    stops = set(portfolio.stops_hit(prices))
    positions = []
    for addr, p in portfolio.positions.items():
        price = prices.get(addr, p.entry_price)
        positions.append({
            "symbol": p.token_symbol,
            "entry_price": p.entry_price,
            "current_price": price,
            "change_pct": (price / p.entry_price - 1) * 100,
            "stop_price": p.stop_price,
            "stop_hit": addr in stops,
            "priced_live": addr in live,
            "cost_usd": p.cost_usd,
        })
    return {
        "mode": "PAPER (dry-run)" if cfg.mode.dry_run else "LIVE",
        "positions_open": len(portfolio.positions),
        "cash": round(portfolio.cash, 2),
        "equity": round(equity, 2),
        "total_pnl": round(pnl, 2),
        "total_pnl_pct": round(pnl / portfolio.starting_balance * 100, 2),
        "unrealized": round(unreal, 2),
        "positions": positions,
        "disclaimer": "Numbers are simulated — memecoins can and do go "
                      "to zero. This skeleton has no live execution; "
                      "the dashboard is read-only. Nothing here is "
                      "financial advice.",
    }


PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MemeBot — Terminal</title>
<style>
:root { --bg:#04070a; --panel:#0a0f14; --panel2:#0c1218;
  --green:#00e68a; --red:#ff4d5e; --blue:#4da3ff;
  --blue-dim:#071a2e; --text:#c8d6e0; --muted:#5f7180; --border:#14212c;
  --mono:ui-monospace,'JetBrains Mono','Fira Code','SF Mono',Consolas,monospace; }
* { box-sizing:border-box; }
body { font-family:var(--mono); background:var(--bg); color:var(--text);
  margin:0; padding:20px; max-width:1100px; margin-inline:auto;
  background-image:
    radial-gradient(ellipse 800px 300px at 50% -10%, rgba(0,230,138,.06), transparent),
    radial-gradient(ellipse 600px 200px at 10% 110%, rgba(77,163,255,.05), transparent); }
header { border-bottom:1px solid var(--border); padding-bottom:14px;
  margin-bottom:20px; display:flex; align-items:baseline;
  justify-content:space-between; flex-wrap:wrap; gap:8px; }
h1 { font-size:17px; margin:0; letter-spacing:.08em; color:var(--green);
  text-shadow:0 0 14px rgba(0,230,138,.35); }
h1 .bot { color:var(--blue); text-shadow:0 0 14px rgba(77,163,255,.35); }
.live-dot { display:inline-block; width:9px; height:9px; border-radius:50%;
  background:var(--green); margin-right:6px; box-shadow:0 0 8px var(--green);
  animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }
.sub { color:var(--muted); font-size:12px; }
.cards { display:grid; gap:14px; grid-template-columns:
  repeat(auto-fit,minmax(190px,1fr)); margin-bottom:20px; }
.card { background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border); border-radius:10px; padding:16px;
  position:relative; overflow:hidden; }
.card::before { content:""; position:absolute; inset:0 auto auto 0;
  width:3px; height:100%; background:var(--blue); opacity:.8; }
.card.g::before { background:var(--green); } .card.r::before { background:var(--red); }
.label { font-size:11px; letter-spacing:.12em; color:var(--muted);
  text-transform:uppercase; margin-bottom:8px; }
.value { font-size:20px; font-weight:700; }
.pos { color:var(--green); } .neg { color:var(--red); } .blue { color:var(--blue); }
table { width:100%; border-collapse:collapse; font-size:13px;
  background:var(--panel); border:1px solid var(--border);
  border-radius:10px; overflow:hidden; }
th { text-align:left; padding:10px 12px; color:var(--blue);
  background:var(--blue-dim); font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; }
td { padding:9px 12px; border-top:1px solid var(--border); }
tr:hover td { background:rgba(0,230,138,.04); }
.note { color:var(--muted); font-size:12px; margin-top:16px; line-height:1.6; }
h2 { font-size:13px; letter-spacing:.1em; color:var(--blue); }
@media (prefers-reduced-motion:reduce) { .live-dot { animation:none; } }
</style></head><body>
<header><div>
  <h1><span class="live-dot"></span>MEME<span class="bot">BOT</span></h1>
  <div class="sub">read-only terminal &middot; paper-mode skeleton &middot; no buy/sell buttons by design</div>
</div><div class="sub">mode: <span id="mode" class="blue">...</span>
  &middot; updated <span id="updated" class="blue">...</span></div></header>
<div class="cards">
  <div class="card g"><div class="label">Equity (marked)</div>
    <div class="value" id="equity">...</div></div>
  <div class="card"><div class="label">Cash</div>
    <div class="value" id="cash">...</div></div>
  <div class="card"><div class="label">Total PnL</div>
    <div class="value" id="pnl">...</div></div>
  <div class="card"><div class="label">Open positions</div>
    <div class="value" id="open">...</div></div>
  <div class="card"><div class="label">Unrealized</div>
    <div class="value" id="unreal">...</div></div>
</div>
<h2>PAPER POSITIONS</h2>
<table><thead><tr><th>Token</th><th>Entry</th><th>Now</th>
  <th>Change</th><th>Stop</th><th>Price source</th></tr></thead>
  <tbody id="pos-body"><tr><td colspan="6" class="sub">...</td></tr></tbody></table>
<div class="note" id="disclaimer"></div>
<script>
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function cls(v){return v>=0?"pos":"neg";}
function sgn(v){return (v>=0?"+":"")+Number(v).toFixed(2);}
async function refresh(){
  try{
    const s = await (await fetch("/api/status")).json();
    document.getElementById("mode").textContent = s.mode;
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    const eq = document.getElementById("equity");
    eq.textContent = "$" + Number(s.equity).toFixed(2);
    document.getElementById("cash").textContent = "$" + Number(s.cash).toFixed(2);
    document.getElementById("open").textContent = s.positions_open;
    const pnl = document.getElementById("pnl");
    pnl.textContent = sgn(s.total_pnl) + " (" + sgn(s.total_pnl_pct) + "%)";
    pnl.className = "value " + cls(s.total_pnl);
    const un = document.getElementById("unreal");
    un.textContent = sgn(s.unrealized);
    un.className = "value " + cls(s.unrealized);
    const body = document.getElementById("pos-body");
    if ((s.positions||[]).length){
      body.innerHTML = s.positions.map(p =>
        `<tr><td class="blue">${esc(p.symbol)}${p.stop_hit?" <span class=neg>[STOP HIT]</span>":""}</td>
        <td>${Number(p.entry_price).toPrecision(4)}</td>
        <td>${Number(p.current_price).toPrecision(4)}</td>
        <td class="${cls(p.change_pct)}">${sgn(p.change_pct)}%</td>
        <td class="sub">${Number(p.stop_price).toPrecision(4)}</td>
        <td class="${p.priced_live?"pos":"sub"}">${p.priced_live?"live dexscreener":"entry (offline)"}</td></tr>`).join("");
    } else { body.innerHTML = `<tr><td colspan="6" class="sub">no open paper positions</td></tr>`; }
    if (s.disclaimer) document.getElementById("disclaimer").textContent = s.disclaimer;
  }catch(e){ document.getElementById("updated").textContent = "connection lost"; }
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    cfg: BotConfig | None = None

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            payload = build_status(self.cfg)
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):  # quiet
        pass


def serve(cfg: BotConfig, host: str = "0.0.0.0", port: int = 8788) -> None:
    """Run the read-only dashboard until interrupted."""
    _Handler.cfg = cfg
    server = ThreadingHTTPServer((host, port), _Handler)
    print("MemeBot dashboard (read-only — paper-mode skeleton)")
    print("  this machine:  http://127.0.0.1:%d" % port)
    if host not in ("127.0.0.1", "localhost"):
        print("  on your LAN:   http://%s:%d" % (lan_ip(), port))
        print("  read-only: anyone on your network can VIEW it, "
              "nobody can trade from it")
    print("  stop with Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("dashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover
    from .config import load_config
    cfg = load_config(None or "config/config.example.yaml")
    serve(cfg)
