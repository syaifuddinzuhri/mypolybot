from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional
from loguru import logger
from .types import AccountInfo, Position, Direction
from .config import settings

WIB = timezone(timedelta(hours=7))

def _fmt_wib(dt: datetime) -> str:
    return dt.astimezone(WIB).strftime("%H:%M:%S WIB")

# Cooldown state per symbol
_cooldown: dict[str, dict] = {}
# {"XAUUSDm": {"consecutive_losses": 2, "cooldown_until": datetime|None}}


def _get_cooldown(symbol: str) -> dict:
    if symbol not in _cooldown:
        _cooldown[symbol] = {"consecutive_losses": 0, "cooldown_until": None}
    return _cooldown[symbol]


def record_loss(symbol: str) -> None:
    """Dipanggil saat posisi closed dengan loss."""
    if not settings.loss_cooldown_enabled:
        return
    state = _get_cooldown(symbol)
    state["consecutive_losses"] += 1
    remaining_until_trigger = settings.loss_cooldown_trigger - state["consecutive_losses"]
    logger.info(
        f"[COOLDOWN][{symbol}] Loss ke-{state['consecutive_losses']} tercatat "
        f"(cooldown aktif dalam {remaining_until_trigger} loss lagi)"
    )
    if state["consecutive_losses"] >= settings.loss_cooldown_trigger:
        until = datetime.now(timezone.utc) + timedelta(minutes=settings.loss_cooldown_minutes)
        state["cooldown_until"] = until
        logger.warning(
            f"[COOLDOWN][{symbol}] {state['consecutive_losses']} loss berturut-turut — "
            f"pause entry selama {settings.loss_cooldown_minutes} menit "
            f"(sampai {_fmt_wib(until)})"
        )


def record_win(symbol: str) -> None:
    """Reset consecutive loss counter saat posisi profit."""
    state = _get_cooldown(symbol)
    if state["consecutive_losses"] > 0:
        logger.info(f"[COOLDOWN][{symbol}] Win — reset loss counter")
    state["consecutive_losses"] = 0
    state["cooldown_until"] = None


def check_cooldown(symbol: str) -> bool:
    """Returns True jika boleh entry (tidak sedang cooldown)."""
    if not settings.loss_cooldown_enabled:
        return True
    state = _get_cooldown(symbol)
    until: Optional[datetime] = state.get("cooldown_until")
    if until is None:
        return True
    now = datetime.now(timezone.utc)
    if now < until:
        remaining = int((until - now).total_seconds() / 60)
        logger.warning(
            f"[COOLDOWN][{symbol}] Masih cooldown — sisa ±{remaining} menit "
            f"(selesai {_fmt_wib(until)})"
        )
        return False
    # Cooldown selesai — reset
    state["cooldown_until"] = None
    state["consecutive_losses"] = 0
    logger.info(f"[COOLDOWN][{symbol}] Cooldown selesai, entry diizinkan kembali")
    return True


def get_cooldown_status(symbol: str) -> dict:
    """Info cooldown untuk endpoint monitoring."""
    state = _get_cooldown(symbol)
    until = state.get("cooldown_until")
    now = datetime.now(timezone.utc)
    return {
        "symbol": symbol,
        "consecutive_losses": state["consecutive_losses"],
        "in_cooldown": until is not None and now < until,
        "cooldown_until": until.isoformat() if until else None,
        "remaining_seconds": max(0, int((until - now).total_seconds())) if until and now < until else 0,
    }


def check_daily_loss(account: AccountInfo, today_pnl: float) -> bool:
    # Hard stop: 3% dari balance hari ini
    hard_stop = account.balance * 0.03
    if today_pnl <= -hard_stop:
        logger.warning(
            f"[RISK] Hard stop harian tercapai — rugi {abs(today_pnl):.2f} "
            f"(3% dari balance {account.balance:.2f} = {hard_stop:.2f}). "
            f"Bot berhenti hingga besok."
        )
        return False
    return True


def check_max_positions(symbol: str, positions: list[Position]) -> bool:
    symbol_positions = [p for p in positions if p.symbol == symbol]
    if len(symbol_positions) >= settings.max_positions_per_symbol:
        logger.warning(
            f"[RISK][{symbol}] Max positions per symbol reached: "
            f"{len(symbol_positions)}/{settings.max_positions_per_symbol}"
        )
        return False
    if len(positions) >= settings.max_total_positions:
        logger.warning(
            f"[RISK] Max total positions reached: "
            f"{len(positions)}/{settings.max_total_positions}"
        )
        return False
    return True


def check_spread(spread: int, symbol: str) -> bool:
    if spread > settings.max_spread_points:
        logger.warning(
            f"[RISK][{symbol}] Spread too high: {spread} > {settings.max_spread_points}"
        )
        return False
    return True


def check_duplicate(symbol: str, direction: Direction, positions: list[Position]) -> bool:
    dir_map = {"BUY": "buy", "SELL": "sell"}
    existing = [
        p for p in positions
        if p.symbol == symbol and p.type == dir_map[direction.value]
    ]
    if existing:
        logger.debug(
            f"[RISK][{symbol}] Duplicate {direction.value} position exists "
            f"(ticket={existing[0].ticket})"
        )
        return False
    return True
