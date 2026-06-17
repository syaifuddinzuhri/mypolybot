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


@app.get("/config")
async def get_config():
    """Expose settings aktif dari .env untuk dashboard."""
    return {
        "lot_size": settings.lot_size,
        "max_spread_points": settings.max_spread_points,
        "min_rr_ratio": settings.min_rr_ratio,
        "daily_loss_percent": settings.daily_loss_percent,
        "entry_interval_seconds": settings.entry_interval_seconds,
        "ema_slow": settings.ema_slow,
        "loss_cooldown_enabled": settings.loss_cooldown_enabled,
        "loss_cooldown_trigger": settings.loss_cooldown_trigger,
        "loss_cooldown_minutes": settings.loss_cooldown_minutes,
        "trailing_sl_enabled": settings.trailing_sl_enabled,
        "trailing_sl_points": settings.trailing_sl_points,
        "break_even_enabled": settings.break_even_enabled,
        "break_even_trigger_points": settings.break_even_trigger_points,
        "partial_close_enabled": settings.partial_close_enabled,
        "partial_close_trigger_points": settings.partial_close_trigger_points,
        "partial_close_ratio": settings.partial_close_ratio,
        "eod_close_enabled": settings.eod_close_enabled,
        "eod_hour": settings.eod_hour,
        "eod_minute": settings.eod_minute,
        "session_filter_enabled": settings.session_filter_enabled,
        "session_auto": settings.session_auto,
        "max_positions_per_symbol": settings.max_positions_per_symbol,
        "news_filter_enabled": settings.news_filter_enabled,
        "dxy_filter_enabled": settings.dxy_filter_enabled,
        "telegram_enabled": settings.telegram_enabled,
        "telegram_token": settings.telegram_token,
        "telegram_chat_id": settings.telegram_chat_id,
    }


@app.patch("/config")
async def patch_config(updates: dict):
    """Update settings di memory dan tulis ke .env agar persist setelah restart."""
    from pathlib import Path

    # Field yang boleh diubah dari dashboard (whitelist)
    ALLOWED = {
        "lot_size": float,
        "max_spread_points": int,
        "min_rr_ratio": float,
        "daily_loss_percent": float,
        "entry_interval_seconds": int,
        "loss_cooldown_enabled": bool,
        "loss_cooldown_trigger": int,
        "loss_cooldown_minutes": int,
        "trailing_sl_enabled": bool,
        "trailing_sl_points": int,
        "break_even_enabled": bool,
        "break_even_trigger_points": int,
        "partial_close_enabled": bool,
        "partial_close_trigger_points": int,
        "partial_close_ratio": float,
        "eod_close_enabled": bool,
        "eod_hour": int,
        "eod_minute": int,
        "news_filter_enabled": bool,
        "dxy_filter_enabled": bool,
        "max_positions_per_symbol": int,
        "telegram_enabled": bool,
        "telegram_token": str,
        "telegram_chat_id": str,
    }

    applied = {}
    rejected = []

    for key, value in updates.items():
        if key not in ALLOWED:
            rejected.append(key)
            continue
        cast = ALLOWED[key]
        try:
            typed = cast(value)
            setattr(settings, key, typed)
            applied[key] = typed
        except Exception as e:
            rejected.append(f"{key}: {e}")

    # Tulis ke .env agar persist
    env_path = Path(".env")
    if env_path.exists() and applied:
        lines = env_path.read_text().splitlines()
        env_map = {}
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env_map[k.strip()] = v.split("#")[0].strip()

        for key, val in applied.items():
            env_key = key.upper()
            if isinstance(val, bool):
                env_map[env_key] = "true" if val else "false"
            else:
                env_map[env_key] = str(val)

        # Rebuild .env — pertahankan komentar dan urutan
        new_lines = []
        updated_keys = set()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            k = line.partition("=")[0].strip()
            if k.upper() in {key.upper() for key in applied}:
                val = env_map[k.upper()]
                comment = ""
                if "#" in line.partition("=")[2]:
                    comment = "  #" + line.partition("=")[2].partition("#")[2]
                new_lines.append(f"{k}={val}{comment}")
                updated_keys.add(k.upper())
            else:
                new_lines.append(line)

        # Tambah key baru yang belum ada di .env
        for key, val in applied.items():
            if key.upper() not in updated_keys:
                if isinstance(val, bool):
                    new_lines.append(f"{key.upper()}={'true' if val else 'false'}")
                else:
                    new_lines.append(f"{key.upper()}={val}")

        env_path.write_text("\n".join(new_lines) + "\n")
        logger.info(f"[CONFIG] Updated: {applied}")

    return {"applied": applied, "rejected": rejected}


@app.get("/ea/bars")
async def ea_bars(symbol: str = None, limit: int = 100):
    """OHLCV bars + EMA50 band + RSI + ATR + Volume untuk chart realtime."""
    import numpy as np
    import json
    from pathlib import Path

    sym = symbol or (list(_cache["symbols"].keys())[0] if _cache["symbols"] else None)
    if not sym:
        raise HTTPException(404, "No symbol data")

    # Ambil lebih banyak bar untuk perhitungan indikator yang akurat
    all_bars = _cache.get("bars", {}).get(sym, [])
    meta     = _cache["symbols"].get(sym)
    tick     = _cache["ticks"].get(sym)
    if not all_bars or not meta:
        raise HTTPException(404, "No bar data")

    # Gunakan semua bar untuk hitung indikator, tampilkan hanya `limit` terakhir
    closes = np.array([b.close for b in all_bars], dtype=float)
    highs  = np.array([b.high  for b in all_bars], dtype=float)
    lows   = np.array([b.low   for b in all_bars], dtype=float)

    def _ema(arr, p):
        k, e = 2/(p+1), np.zeros_like(arr, dtype=float)
        e[0] = arr[0]
        for i in range(1, len(arr)):
            e[i] = arr[i]*k + e[i-1]*(1-k)
        return e

    # EMA50 band
    band_high = _ema(highs, 50)
    band_low  = _ema(lows,  50)

    # RSI (14)
    rsi_period = 14
    rsi_vals = np.full(len(closes), float('nan'))
    if len(closes) > rsi_period:
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_g  = np.mean(gains[:rsi_period])
        avg_l  = np.mean(losses[:rsi_period])
        for i in range(rsi_period, len(closes) - 1):
            avg_g = (avg_g * (rsi_period - 1) + gains[i]) / rsi_period
            avg_l = (avg_l * (rsi_period - 1) + losses[i]) / rsi_period
            rs = avg_g / avg_l if avg_l != 0 else 100
            rsi_vals[i + 1] = round(100 - 100 / (1 + rs), 2)

    # ATR (14) — rolling
    atr_period = 14
    atr_vals = np.full(len(closes), float('nan'))
    if len(closes) > atr_period + 1:
        trs = np.array([
            max(highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i]  - closes[i-1]))
            for i in range(1, len(closes))
        ])
        for i in range(atr_period, len(trs)):
            atr_vals[i + 1] = round(float(np.mean(trs[i-atr_period+1:i+1])), meta.digits)

    # Ambil trade log untuk entry markers
    markers = []
    trade_log = Path("data/trade_log.json")
    if trade_log.exists():
        try:
            trades = json.loads(trade_log.read_text())
            bar_times = set(int(b.time) for b in all_bars if hasattr(b, 'time') and b.time)
            for t in trades[-50:]:  # 50 trade terakhir
                entry_time = t.get("entry_time_ts") or t.get("open_time")
                if not entry_time:
                    continue
                direction = t.get("direction", t.get("type", "")).upper()
                profit    = t.get("profit", 0) or 0
                markers.append({
                    "time":      int(entry_time),
                    "direction": direction,
                    "profit":    round(profit, 2),
                    "price":     t.get("entry_price", t.get("open_price", 0)),
                })
        except Exception:
            pass

    # Slice ke limit terakhir
    start = max(0, len(all_bars) - limit)
    bars_slice = all_bars[start:]

    candles = []
    for i, b in enumerate(bars_slice):
        gi = start + i  # global index
        t  = int(b.time) if hasattr(b, "time") and b.time else gi
        rsi = None if np.isnan(rsi_vals[gi]) else float(rsi_vals[gi])
        atr = None if np.isnan(atr_vals[gi]) else float(atr_vals[gi])
        atr_pts = int(atr / meta.point) if atr else None
        candles.append({
            "time":      t,
            "open":      round(b.open,  meta.digits),
            "high":      round(b.high,  meta.digits),
            "low":       round(b.low,   meta.digits),
            "close":     round(b.close, meta.digits),
            "band_high": round(float(band_high[gi]), meta.digits),
            "band_low":  round(float(band_low[gi]),  meta.digits),
            "rsi":       rsi,
            "atr":       atr,
            "atr_pts":   atr_pts,
        })

    spread = int((tick.ask - tick.bid) / meta.point) if tick else 0
    price  = round(tick.bid, meta.digits) if tick else 0

    return {
        "symbol":  sym,
        "digits":  meta.digits,
        "candles": candles,
        "markers": markers,
        "price":   price,
        "spread":  spread,
    }


@app.get("/telegram/test")
async def telegram_test():
    """Test kirim pesan ke Telegram."""
    from .telegram_notifier import notify_test
    if not settings.telegram_enabled:
        return {"ok": False, "reason": "TELEGRAM_ENABLED=false"}
    if not settings.telegram_token or not settings.telegram_chat_id:
        return {"ok": False, "reason": "Token atau Chat ID belum diset"}
    notify_test()
    return {"ok": True, "message": "Pesan test dikirim ke Telegram"}


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
