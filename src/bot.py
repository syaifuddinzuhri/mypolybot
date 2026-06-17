from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from .types import EARatesPayload, Direction, EACommand, TickData, Position
from .strategy import analyze, detect_trend
from .risk import (
    check_daily_loss, check_max_positions, check_spread,
    check_cooldown, record_loss, record_win,
)
from .trade_manager import manage_positions, is_trading_session
from .state_store import save as save_state, load as load_state, restore_cooldown
from .news_filter import check_macro_ok
from .performance_tracker import record_trade
from .config import settings
from .telegram_notifier import (
    notify_entry, notify_close, notify_daily_stop,
    notify_cooldown, notify_startup, notify_sideways,
)

_state: dict = {
    "today_pnl": 0.0,
    "today_loss_count": 0,
    "pending_commands": [],
    "ticks": {},
    "points": {},
    "already_partial": set(),
    "pyramid_counts": {},
    "open_tickets": {},
    "last_entry_time": {},      # symbol -> datetime
    "_last_pnl_snapshot": {},   # ticket -> float profit saat terakhir lihat
    "bot_paused": False,        # True = bot tidak akan entry baru
    "_last_trend": {},          # symbol -> "BUY"|"SELL"|"NEUTRAL" untuk deteksi perubahan
}


def get_state() -> dict:
    return _state


def pause_bot() -> None:
    _state["bot_paused"] = True
    logger.warning("[BOT] Bot di-PAUSE — tidak ada entry baru")


def resume_bot() -> None:
    _state["bot_paused"] = False
    logger.info("[BOT] Bot di-RESUME — entry aktif kembali")


def init_state() -> None:
    """Dipanggil saat startup — restore state dari file."""
    saved = load_state()
    if saved:
        _state["today_pnl"] = saved.get("today_pnl", 0.0)
        _state["today_loss_count"] = saved.get("today_loss_count", 0)
        restore_cooldown(saved)
        logger.info(
            f"[STATE] Restored — pnl={_state['today_pnl']:.2f} "
            f"losses={_state['today_loss_count']}"
        )
    else:
        logger.info("[STATE] Fresh start, tidak ada state tersimpan")


def _detect_closed_positions(symbol: str, current_positions: list[Position]) -> None:
    prev_tickets: set = _state["open_tickets"].get(symbol, set())
    curr_tickets: set = {p.ticket for p in current_positions if p.symbol == symbol}
    closed = prev_tickets - curr_tickets

    for ticket in closed:
        pnl_before = _state["_last_pnl_snapshot"].get(ticket)
        meta = _state.get("_position_meta", {}).get(ticket, {})
        if pnl_before is not None and pnl_before < 0:
            record_loss(symbol)
            _state["today_loss_count"] += 1
            save_state(_state)
            # Telegram notify cooldown jika triggered
            from .risk import get_cooldown_status
            cd = get_cooldown_status(symbol)
            if cd["in_cooldown"]:
                notify_cooldown(symbol, cd["consecutive_losses"], settings.loss_cooldown_minutes)
        elif pnl_before is not None and pnl_before >= 0:
            record_win(symbol)
            save_state(_state)
        if pnl_before is not None:
            # Estimasi close_price dari tick terakhir
            tick_now = _state["ticks"].get(symbol)
            direction = meta.get("direction", "?")
            if tick_now:
                close_price = tick_now.bid if direction == "BUY" else tick_now.ask
            else:
                close_price = 0.0
            reason = "TP" if (meta.get("tp") and pnl_before > 0) else "SL" if pnl_before < 0 else "Manual"
            notify_close(
                symbol=symbol,
                direction=direction,
                profit=round(pnl_before, 2),
                open_price=meta.get("entry", 0),
                close_price=round(close_price),
                reason=reason,
            )
            record_trade(
                symbol=symbol,
                direction=meta.get("direction", "?"),
                lot=meta.get("lot", 0),
                entry_price=meta.get("entry", 0),
                exit_price=0,  # tidak tersedia dari MT5 bridge
                sl=meta.get("sl", 0),
                tp=meta.get("tp", 0),
                profit=pnl_before,
                comment=meta.get("comment", ""),
            )
        _state["_last_pnl_snapshot"].pop(ticket, None)
        _state["pyramid_counts"].pop(ticket, None)
        _state.setdefault("_position_meta", {}).pop(ticket, None)

    # Assign meta ke ticket baru yang baru terbuka
    new_tickets = curr_tickets - prev_tickets
    pending_meta = _state.get("_position_meta_pending", [])
    for ticket in new_tickets:
        if pending_meta:
            _state.setdefault("_position_meta", {})[ticket] = pending_meta.pop(0)

    for p in current_positions:
        if p.symbol == symbol:
            _state["_last_pnl_snapshot"][p.ticket] = p.profit

    _state["open_tickets"][symbol] = curr_tickets


def _count_same_direction(symbol: str, direction: Direction, positions: list[Position]) -> int:
    dir_map = {"BUY": "buy", "SELL": "sell"}
    return sum(
        1 for p in positions
        if p.symbol == symbol and p.type == dir_map[direction.value]
    )


def process_rates(payload: EARatesPayload, point: float, digits: int, spread: int) -> None:
    symbol = payload.symbol
    positions = payload.positions
    tick = payload.tick
    bars = payload.bars

    _state["ticks"][symbol] = tick
    _state["points"][symbol] = point
    _state["today_pnl"] = sum(p.profit for p in positions)

    _detect_closed_positions(symbol, positions)

    # Manage posisi terbuka
    mgmt_cmds = manage_positions(
        positions,
        _state["ticks"],
        _state["points"],
        _state["already_partial"],
        _state["pyramid_counts"],
        position_meta=_state.get("_position_meta", {}),
    )
    _state["pending_commands"].extend(mgmt_cmds)

    # ── Entry logic ──────────────────────────────────────────

    if _state["bot_paused"]:
        return

    if not is_trading_session():
        return

    if settings.daily_loss_enabled and not check_daily_loss(payload.account, _state["today_pnl"]):
        notify_daily_stop(
            payload.account.balance,
            abs(_state["today_pnl"]),
            settings.daily_loss_percent,
        )
        return

    if not check_cooldown(symbol):
        return

    if not check_spread(spread, symbol):
        return

    # Trend detection
    direction = detect_trend(bars, point)
    prev_trend = _state["_last_trend"].get(symbol, "NEUTRAL")
    curr_trend = direction.value if direction else "NEUTRAL"
    if curr_trend != prev_trend:
        _state["_last_trend"][symbol] = curr_trend
        if direction is None and prev_trend in ("BUY", "SELL"):
            # Trend baru saja berubah ke sideways — kirim notif sekali
            notify_sideways(symbol, buy_count=0, sell_count=0)
    if direction is None:
        return

    # Cek max posisi (searah maupun berlawanan)
    same_dir_count = _count_same_direction(symbol, direction, positions)
    if same_dir_count >= settings.max_positions_per_symbol:
        logger.debug(
            f"[BOT][{symbol}] Max {direction.value} positions reached "
            f"({same_dir_count}/{settings.max_positions_per_symbol})"
        )
        return

    if len(positions) >= settings.max_total_positions:
        return

    # Entry interval — jeda minimum antar entry
    now = datetime.now(timezone.utc)
    last_entry = _state["last_entry_time"].get(symbol)
    if last_entry:
        elapsed = (now - last_entry).total_seconds()
        if elapsed < settings.entry_interval_seconds:
            remaining = int(settings.entry_interval_seconds - elapsed)
            logger.debug(f"[BOT][{symbol}] Interval sisa {remaining}s")
            return

    # News & macro filter
    macro_ok, macro_reason = check_macro_ok(direction)
    if not macro_ok:
        logger.info(f"[NO TRADE][{symbol}] Macro filter: {macro_reason}")
        return

    # Entry signal dari M5 (jika tersedia), trend dari M15
    bars_m5 = payload.bars_m5 if payload.bars_m5 else None
    signal = analyze(symbol, bars, tick, point, digits, direction, bars_entry=bars_m5)
    if signal is None:
        return

    # Setiap entry hitung SL/TP sendiri agar RR selalu 1:2

    cmd = EACommand(
        action=signal.direction.value,
        symbol=signal.symbol,
        lot=signal.lot,
        sl=signal.sl,
        tp=signal.tp,
        comment=signal.comment,
    )
    _state["pending_commands"].append(cmd)
    _state["last_entry_time"][symbol] = now
    # Simpan meta untuk performance tracker saat posisi tutup nanti
    _tick = _state["ticks"].get(symbol)
    _entry_price = (_tick.ask if signal.direction == Direction.BUY else _tick.bid) if _tick else 0
    _state.setdefault("_position_meta_pending", []).append({
        "direction": signal.direction.value,
        "lot": signal.lot,
        "entry": round(_entry_price, 5),
        "sl": signal.sl,
        "sl_original": signal.sl,   # untuk multi-TP SL management
        "tp": signal.tp,
        "comment": signal.comment,
    })
    logger.success(
        f"[BOT][{symbol}] #{same_dir_count + 1} {cmd.action} "
        f"lot={cmd.lot} sl={cmd.sl} tp={cmd.tp}"
    )

    # Telegram — notify entry
    entry_price = _entry_price
    sl_pts = int(abs(entry_price - signal.sl) / point) if point else 0
    tp_pts = int(abs(signal.tp - entry_price) / point) if point else 0
    entry_type = "breakout" if "breakout" in signal.comment else "pullback"
    tick_now = _state["ticks"].get(symbol)
    # Range entry = bid–ask (spread zone)
    price_bid = round(tick_now.bid, digits) if tick_now else 0.0
    price_ask = round(tick_now.ask, digits) if tick_now else 0.0
    notify_entry(
        symbol=symbol,
        direction=signal.direction.value,
        price=round(entry_price, digits),
        sl=signal.sl,
        tp=signal.tp,
        lot=signal.lot,
        sl_pts=sl_pts,
        tp_pts=tp_pts,
        entry_type=entry_type,
        tp1=signal.tp1,
        tp2=signal.tp2,
        price_bid=price_bid,
        price_ask=price_ask,
        point=point,
    )


def pop_command(symbol: str) -> Optional[EACommand]:
    for i, cmd in enumerate(_state["pending_commands"]):
        if cmd.symbol == symbol:
            return _state["pending_commands"].pop(i)
    return None


def update_today_pnl(pnl: float) -> None:
    _state["today_pnl"] = pnl


def increment_loss_count() -> None:
    _state["today_loss_count"] += 1
