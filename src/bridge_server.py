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
