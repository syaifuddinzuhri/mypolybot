from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from loguru import logger
from typing import Optional
from pathlib import Path

from .types import (
    EARatesPayload, EACommand, TickData, SymbolMeta,
    AccountInfo, Position,
)
from .bot import process_rates, pop_command, get_state, update_today_pnl
from .config import settings

app = FastAPI(title="Polybot Bridge Server", version="1.0.0")

_static = Path(__file__).parent.parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

@app.get("/")
async def dashboard():
    return FileResponse(str(_static / "dashboard.html"))

# Cache: symbol meta, ticks, positions sent by EA
_cache: dict = {
    "symbols": {},      # symbol -> SymbolMeta
    "ticks": {},        # symbol -> TickData
    "positions": [],    # list[Position]
    "account": None,
}


# ── EA pushes rates every tick ──────────────────────────────────────────────

@app.post("/ea/rates")
async def ea_rates(payload: EARatesPayload):
    symbol = payload.symbol

    # Auto-register meta dari payload (EA kirim setiap tick)
    if symbol not in _cache["symbols"]:
        _cache["symbols"][symbol] = payload.meta
        logger.info(
            f"[BRIDGE] Auto-registered meta: {symbol} "
            f"digits={payload.meta.digits} point={payload.meta.point}"
        )

    meta: SymbolMeta = _cache["symbols"][symbol]

    _cache["ticks"][symbol] = payload.tick
    _cache["positions"] = payload.positions
    _cache["account"] = payload.account
    if "bars" not in _cache:
        _cache["bars"] = {}
    _cache["bars"][symbol] = payload.bars

    # Update today PnL
    today_pnl = sum(p.profit for p in payload.positions)
    update_today_pnl(today_pnl)

    spread = int((payload.tick.ask - payload.tick.bid) / meta.point)

    process_rates(payload, meta.point, meta.digits, spread)
    return {"status": "ok"}


# ── EA polls for commands ────────────────────────────────────────────────────

@app.get("/ea/command")
async def ea_command(symbol: str):
    cmd = pop_command(symbol)
    if cmd is None:
        return {"action": "NONE"}
    return cmd.model_dump()


# ── Symbol meta registration (EA sends on init) ──────────────────────────────

class SymbolMetaRequest(BaseModel):
    symbol: str
    digits: int
    point: float
    contract_size: float
    spread: int


@app.post("/ea/symbol-meta")
async def ea_symbol_meta(data: SymbolMetaRequest):
    _cache["symbols"][data.symbol] = SymbolMeta(**data.model_dump())
    logger.info(f"[BRIDGE] Registered symbol meta: {data.symbol} point={data.point}")
    return {"status": "ok"}


# ── Info endpoints (for dashboard / monitoring) ──────────────────────────────

@app.get("/account")
async def get_account():
    if _cache["account"] is None:
        raise HTTPException(404, "No account data yet")
    return _cache["account"]


@app.get("/tick")
async def get_tick(symbol: str):
    tick = _cache["ticks"].get(symbol)
    if tick is None:
        raise HTTPException(404, f"No tick for {symbol}")
    return tick


@app.get("/symbol-meta")
async def get_symbol_meta(symbol: str):
    meta = _cache["symbols"].get(symbol)
    if meta is None:
        raise HTTPException(404, f"No meta for {symbol}")
    return meta


@app.get("/positions-count")
async def positions_count(symbol: str):
    count = sum(1 for p in _cache["positions"] if p.symbol == symbol)
    return {"symbol": symbol, "count": count}


@app.get("/positions-count-total")
async def positions_count_total():
    return {"count": len(_cache["positions"])}


@app.get("/today-pnl")
async def today_pnl():
    state = get_state()
    return {"pnl": state["today_pnl"]}


@app.get("/today-loss-count")
async def today_loss_count():
    state = get_state()
    return {"count": state["today_loss_count"]}


@app.get("/cooldown")
async def cooldown_status(symbol: str):
    from .risk import get_cooldown_status
    return get_cooldown_status(symbol)


@app.post("/cooldown/reset")
async def cooldown_reset(symbol: str = None):
    from .risk import _cooldown, record_win
    from .state_store import save as save_state
    if symbol:
        _cooldown.pop(symbol, None)
        logger.info(f"[BRIDGE] Cooldown reset: {symbol}")
        result = {"reset": symbol}
    else:
        _cooldown.clear()
        logger.info("[BRIDGE] Cooldown reset: semua symbol")
        result = {"reset": "all"}
    save_state(get_state())
    return result


@app.get("/rates")
async def get_rates(symbol: str, timeframe: str, bars: int = 100):
    # Returns cached bars if available (populated by latest EA push)
    # For now just confirms the endpoint is alive
    return {"symbol": symbol, "timeframe": timeframe, "bars_requested": bars}


@app.get("/health")
async def health():
    return {"status": "ok", "symbols": list(_cache["symbols"].keys())}


@app.get("/logs/recent")
async def logs_recent(n: int = 200):
    """Ambil N baris log terakhir."""
    import asyncio
    from pathlib import Path
    log_file = Path("data/polybot.log")
    if not log_file.exists():
        return {"lines": []}
    lines = log_file.read_text(errors="replace").splitlines()
    return {"lines": lines[-n:]}


@app.get("/logs/stream")
async def logs_stream():
    """SSE endpoint — stream log realtime ke browser."""
    import asyncio
    from pathlib import Path
    from fastapi.responses import StreamingResponse

    log_file = Path("data/polybot.log")

    async def generate():
        # Kirim 50 baris terakhir dulu sebagai history
        if log_file.exists():
            lines = log_file.read_text(errors="replace").splitlines()
            for line in lines[-50:]:
                import json
                yield f"data: {json.dumps(line)}\n\n"

        # Lalu tail file secara realtime
        with open(log_file, "r", errors="replace") as f:
            f.seek(0, 2)  # jump ke akhir file
            while True:
                line = f.readline()
                if line:
                    import json
                    yield f"data: {json.dumps(line.rstrip())}\n\n"
                else:
                    await asyncio.sleep(0.2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/session/config")
async def session_config_get():
    from .trade_manager import get_session_config
    return get_session_config()

class SessionConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    sessions: Optional[list] = None
    open_wib: Optional[int] = None
    close_wib: Optional[int] = None
    auto_mode: Optional[bool] = None

@app.post("/session/config")
async def session_config_set(req: SessionConfigRequest):
    from .trade_manager import set_session_config
    set_session_config(
        enabled=req.enabled,
        sessions=req.sessions,
        open_wib=req.open_wib,
        close_wib=req.close_wib,
        auto_mode=req.auto_mode,
    )
    logger.info(f"[SESSION] Config diubah via dashboard: {req.model_dump(exclude_none=True)}")
    from .trade_manager import get_session_config
    return get_session_config()


@app.get("/macro")
async def macro_status():
    from .news_filter import get_macro_status
    return get_macro_status()


@app.get("/report")
async def weekly_report(weeks_back: int = 1):
    """Laporan performa mingguan bot."""
    from .performance_tracker import generate_weekly_report
    return generate_weekly_report(weeks_back)


@app.get("/report/today")
async def today_report():
    """Ringkasan performa hari ini."""
    from .performance_tracker import get_today_summary
    return get_today_summary()


@app.get("/report/trades")
async def all_trades():
    """Semua log trade tersimpan."""
    import json
    from pathlib import Path
    f = Path("data/trade_log.json")
    if not f.exists():
        return []
    return json.loads(f.read_text())


@app.get("/ea/analysis")
async def ea_analysis(symbol: str = None):
    """Kondisi pasar realtime: Trend, EMA, ATR, Spread, Daily H/L."""
    import numpy as np

    sym = symbol or (list(_cache["symbols"].keys())[0] if _cache["symbols"] else None)
    if not sym:
        raise HTTPException(404, "No symbol data yet")

    bars = _cache.get("bars", {}).get(sym, [])
    meta = _cache["symbols"].get(sym)
    tick = _cache["ticks"].get(sym)

    if not bars or not meta:
        raise HTTPException(404, "No bar data yet")

    closes = np.array([b.close for b in bars])
    highs  = np.array([b.high  for b in bars])
    lows   = np.array([b.low   for b in bars])

    def ema(arr, p):
        k, e = 2/(p+1), np.zeros_like(arr)
        e[0] = arr[0]
        for i in range(1, len(arr)): e[i] = arr[i]*k + e[i-1]*(1-k)
        return e

    # EMA50 High/Low band — sesuai strategi bot
    band_high = ema(highs, 50)[-1]
    band_low  = ema(lows, 50)[-1]
    ema20 = band_high   # label dashboard: band atas
    ema50 = band_low    # label dashboard: band bawah

    # ATR 14 — oldest to newest (bars dari EA urutan index 0=oldest)
    atr_vals = []
    for i in range(1, min(15, len(bars))):
        tr = max(
            highs[-i] - lows[-i],
            abs(highs[-i] - closes[-i-1]),
            abs(lows[-i]  - closes[-i-1])
        )
        atr_vals.append(tr)
    atr = float(np.mean(atr_vals)) if atr_vals else 0.0

    # Trend dari EMA50 band: close terakhir di atas band atas = BUY, di bawah band bawah = SELL
    last_close = float(closes[-1])
    if last_close > band_high:
        trend = "BUY"
    elif last_close < band_low:
        trend = "SELL"
    else:
        trend = "NEUTRAL"

    # Daily High/Low (dari seluruh bars hari ini)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    today_bars = [b for b in bars if hasattr(b, 'time') and b.time and
                  datetime.fromtimestamp(b.time, tz=timezone.utc).date() == today]
    day_high = round(max(b.high for b in today_bars), meta.digits) if today_bars else None
    day_low  = round(min(b.low  for b in today_bars), meta.digits) if today_bars else None

    spread = int((tick.ask - tick.bid) / meta.point) if tick else 0
    price  = round(tick.bid, meta.digits) if tick else 0

    return {
        "symbol":   sym,
        "trend":    trend,
        "ema20":    round(ema20, meta.digits),
        "ema50":    round(ema50, meta.digits),
        "atr":      round(atr, meta.digits),
        "atr_pts":  int(atr / meta.point),
        "spread":   spread,
        "price":    price,
        "day_high": day_high,
        "day_low":  day_low,
        "bars_used": len(bars),
    }


@app.get("/ea/draw")
async def ea_draw(symbol: str):
    """EA poll endpoint ini untuk mendapatkan data visual (SR zones + Fib levels)."""
    from .strategy import _find_sr_zones
    from .fibonacci import get_fib_levels
    from .types import Direction

    meta = _cache["symbols"].get(symbol)
    bars = _cache.get("bars", {}).get(symbol, [])
    if not meta or not bars:
        return {"zones": [], "fib": None}

    point = meta.point
    digits = meta.digits

    # SR Zones — filter hanya yang dekat harga sekarang (±500 points)
    current_price = _cache["ticks"].get(symbol)
    price_now = current_price.bid if current_price else None

    zones = _find_sr_zones(bars, point)
    zones_data = []
    for z in zones:
        if price_now and abs((z.high + z.low) / 2 - price_now) > 500 * point:
            continue  # skip zona jauh dari harga sekarang
        zones_data.append({
            "low": round(z.low, digits),
            "high": round(z.high, digits),
            "type": z.zone_type,
            "strength": z.strength,
        })

    # Fibonacci — gunakan arah trend terakhir
    from .strategy import detect_trend
    trend = detect_trend(bars, point)
    direction = trend if trend else Direction.BUY

    fib = get_fib_levels(bars, direction, point, digits, symbol)
    fib_data = None
    if fib:
        fib_data = {
            "swing_high": round(fib.swing_high, digits),
            "swing_low": round(fib.swing_low, digits),
            "direction": direction.value,
            "retracements": {
                f"{int(k*100)}": round(v, digits)
                for k, v in fib.retracements.items()
            },
            "extensions": {
                f"{int(k*100)}": round(v, digits)
                for k, v in fib.extensions.items()
            },
        }

    return {"zones": zones_data, "fib": fib_data}
