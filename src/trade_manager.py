"""
Trade Manager — mengelola posisi yang sudah terbuka:
  - Trailing SL
  - Break Even
  - Partial Close
  - Close End of Day
  - Session Filter (blokir entry di luar sesi)
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional
from loguru import logger

from .types import Position, EACommand, TickData
from .config import settings


# Runtime session override — bisa diubah dari dashboard tanpa restart
_session_runtime: dict = {
    "enabled": None,          # None = ikut .env
    "open_wib": None,
    "close_wib": None,
    "active_sessions": [],    # [] = auto-detect dari jam sekarang
    "auto_mode": True,        # True = otomatis pilih sesi aktif sekarang
}

SESSION_PRESETS = {
    "asia":       {"label": "Asia",        "open_wib": 0,  "close_wib": 8,  "spread_warn": True},
    "pre_london": {"label": "Pre-London",  "open_wib": 8,  "close_wib": 14, "spread_warn": False},
    "london":     {"label": "London",      "open_wib": 14, "close_wib": 20, "spread_warn": False},
    "new_york":   {"label": "New York",    "open_wib": 19, "close_wib": 24, "spread_warn": False},
}

def _get_current_sessions(wib_hour: int) -> List[str]:
    """Sesi apa yang sedang berjalan sekarang berdasarkan jam WIB."""
    return [k for k, p in SESSION_PRESETS.items()
            if p["open_wib"] <= wib_hour < p["close_wib"]]

def _get_recommended_sessions(wib_hour: int) -> List[str]:
    """Sesi yang direkomendasikan untuk trading (skip Asia)."""
    active = _get_current_sessions(wib_hour)
    # Asia tidak direkomendasikan — spread terlalu lebar
    return [s for s in active if s != "asia"]

def get_session_config() -> dict:
    wib_hour = (datetime.now(timezone.utc).hour + 7) % 24
    current_sessions = _get_current_sessions(wib_hour)
    enabled = _session_runtime["enabled"]
    if enabled is None:
        enabled = settings.session_filter_enabled
    auto_mode = _session_runtime["auto_mode"]

    # Jika auto mode: pakai sesi yang sedang aktif sekarang (tanpa Asia)
    if auto_mode:
        active_sessions = _get_recommended_sessions(wib_hour)
    else:
        active_sessions = _session_runtime["active_sessions"]

    return {
        "enabled": enabled,
        "auto_mode": auto_mode,
        "open_wib": _session_runtime["open_wib"] or settings.session_open_hour,
        "close_wib": _session_runtime["close_wib"] or settings.session_close_hour,
        "active_sessions": active_sessions,
        "current_wib_hour": wib_hour,
        "current_session_now": current_sessions,
        "presets": SESSION_PRESETS,
    }

def set_session_config(enabled: bool = None, sessions: list = None,
                       open_wib: int = None, close_wib: int = None,
                       auto_mode: bool = None):
    if enabled is not None:
        _session_runtime["enabled"] = enabled
    if auto_mode is not None:
        _session_runtime["auto_mode"] = auto_mode
    if sessions is not None:
        _session_runtime["active_sessions"] = sessions
        _session_runtime["auto_mode"] = False  # manual override → matikan auto
        if sessions:
            opens  = [SESSION_PRESETS[s]["open_wib"]  for s in sessions if s in SESSION_PRESETS]
            closes = [SESSION_PRESETS[s]["close_wib"] for s in sessions if s in SESSION_PRESETS]
            if opens and closes:
                _session_runtime["open_wib"]  = min(opens)
                _session_runtime["close_wib"] = max(closes)
    if open_wib is not None:
        _session_runtime["open_wib"] = open_wib
    if close_wib is not None:
        _session_runtime["close_wib"] = close_wib

def is_trading_session() -> bool:
    """Return True jika sekarang dalam sesi yang dipilih (WIB)."""
    enabled = _session_runtime["enabled"]
    if enabled is None:
        enabled = settings.session_filter_enabled
    if not enabled:
        return True

    wib_hour = (datetime.now(timezone.utc).hour + 7) % 24
    auto = _session_runtime["auto_mode"]

    if auto:
        # Auto: izinkan semua sesi kecuali Asia (spread terlalu lebar)
        recommended = _get_recommended_sessions(wib_hour)
        if not recommended:
            logger.debug(f"[SESSION] Auto — Sesi Asia (WIB {wib_hour:02d}:xx), skip entry")
            return False
        return True

    # Manual: cek sesi yang dipilih user
    active = _session_runtime["active_sessions"]
    if active:
        in_any = any(
            SESSION_PRESETS[s]["open_wib"] <= wib_hour < SESSION_PRESETS[s]["close_wib"]
            for s in active if s in SESSION_PRESETS
        )
        if not in_any:
            names = ", ".join(SESSION_PRESETS[s]["label"] for s in active if s in SESSION_PRESETS)
            logger.debug(f"[SESSION] Di luar sesi pilihan ({names}) — WIB={wib_hour:02d}:xx")
        return in_any

    open_wib  = _session_runtime["open_wib"]  or settings.session_open_hour
    close_wib = _session_runtime["close_wib"] or settings.session_close_hour
    in_session = open_wib <= wib_hour < close_wib
    if not in_session:
        logger.debug(f"[SESSION] Di luar sesi {open_wib:02d}:00-{close_wib:02d}:00 WIB")
    return in_session


def is_eod_close_time() -> bool:
    """Return True jika sudah waktunya tutup semua posisi EOD."""
    if not settings.eod_close_enabled:
        return False
    now = datetime.now(timezone.utc)
    return now.hour == settings.eod_hour and now.minute >= settings.eod_minute


def _trailing_sl_commands(
    positions: List[Position], ticks: dict, points: dict
) -> List[EACommand]:
    """Geser SL mengikuti harga jika profit berkembang."""
    if not settings.trailing_sl_enabled:
        return []

    cmds: List[EACommand] = []
    for pos in positions:
        tick: Optional[TickData] = ticks.get(pos.symbol)
        point: Optional[float] = points.get(pos.symbol)
        if tick is None or point is None:
            continue

        trail = settings.trailing_sl_points * point
        step = settings.trailing_sl_step_points * point

        if pos.type == "buy":
            new_sl = round(tick.bid - trail, 5)
            if new_sl > pos.sl + step:
                cmds.append(EACommand(
                    action="MODIFY_SL",
                    symbol=pos.symbol,
                    ticket=pos.ticket,
                    sl=new_sl,
                    tp=pos.tp,
                    comment="trailing_sl",
                ))
                logger.info(
                    f"[TRAIL][{pos.symbol}] ticket={pos.ticket} "
                    f"SL {pos.sl:.5f} → {new_sl:.5f}"
                )
        else:  # sell
            new_sl = round(tick.ask + trail, 5)
            if new_sl < pos.sl - step:
                cmds.append(EACommand(
                    action="MODIFY_SL",
                    symbol=pos.symbol,
                    ticket=pos.ticket,
                    sl=new_sl,
                    tp=pos.tp,
                    comment="trailing_sl",
                ))
                logger.info(
                    f"[TRAIL][{pos.symbol}] ticket={pos.ticket} "
                    f"SL {pos.sl:.5f} → {new_sl:.5f}"
                )
    return cmds


def _break_even_commands(
    positions: List[Position], ticks: dict, points: dict
) -> List[EACommand]:
    """Geser SL ke entry (break even) saat profit cukup."""
    if not settings.break_even_enabled:
        return []

    cmds: List[EACommand] = []
    for pos in positions:
        tick: Optional[TickData] = ticks.get(pos.symbol)
        point: Optional[float] = points.get(pos.symbol)
        if tick is None or point is None:
            continue

        trigger = settings.break_even_trigger_points * point
        buffer = settings.break_even_buffer_points * point

        if pos.type == "buy":
            profit_pts = tick.bid - pos.price_open
            be_sl = round(pos.price_open + buffer, 5)
            # Hanya geser jika SL masih di bawah entry dan profit sudah cukup
            if profit_pts >= trigger and pos.sl < pos.price_open:
                cmds.append(EACommand(
                    action="MODIFY_SL",
                    symbol=pos.symbol,
                    ticket=pos.ticket,
                    sl=be_sl,
                    tp=pos.tp,
                    comment="break_even",
                ))
                logger.info(
                    f"[BE][{pos.symbol}] ticket={pos.ticket} "
                    f"SL → break even {be_sl:.5f}"
                )
        else:
            profit_pts = pos.price_open - tick.ask
            be_sl = round(pos.price_open - buffer, 5)
            if profit_pts >= trigger and pos.sl > pos.price_open:
                cmds.append(EACommand(
                    action="MODIFY_SL",
                    symbol=pos.symbol,
                    ticket=pos.ticket,
                    sl=be_sl,
                    tp=pos.tp,
                    comment="break_even",
                ))
                logger.info(
                    f"[BE][{pos.symbol}] ticket={pos.ticket} "
                    f"SL → break even {be_sl:.5f}"
                )
    return cmds


def _partial_close_commands(
    positions: List[Position], ticks: dict, points: dict,
    already_partial: set,
) -> List[EACommand]:
    """Tutup sebagian posisi saat profit mencapai target."""
    if not settings.partial_close_enabled:
        return []

    cmds: List[EACommand] = []
    for pos in positions:
        if pos.ticket in already_partial:
            continue
        tick: Optional[TickData] = ticks.get(pos.symbol)
        point: Optional[float] = points.get(pos.symbol)
        if tick is None or point is None:
            continue

        trigger = settings.partial_close_trigger_points * point

        if pos.type == "buy":
            profit_pts = tick.bid - pos.price_open
        else:
            profit_pts = pos.price_open - tick.ask

        if profit_pts >= trigger:
            close_lot = round(pos.volume * settings.partial_close_ratio, 2)
            if close_lot < 0.01:
                continue
            cmds.append(EACommand(
                action="PARTIAL_CLOSE",
                symbol=pos.symbol,
                ticket=pos.ticket,
                lot=close_lot,
                comment="partial_close",
            ))
            already_partial.add(pos.ticket)
            logger.info(
                f"[PARTIAL][{pos.symbol}] ticket={pos.ticket} "
                f"tutup {close_lot} lot (profit={profit_pts/point:.0f} pts)"
            )
    return cmds


def _pyramid_commands(
    positions: List[Position],
    ticks: dict,
    points: dict,
    pyramid_counts: dict,  # ticket -> jumlah pyramid yang sudah dibuat
) -> List[EACommand]:
    """
    Tambah posisi searah jika posisi awal sudah profit cukup.
    Setiap posisi bisa di-pyramid max pyramid_max_levels kali.
    Lot pyramid mengecil tiap level (multiplier 0.5).
    """
    if not settings.pyramid_enabled:
        return []

    cmds: List[EACommand] = []

    for pos in positions:
        # Hanya pyramid posisi original (bukan posisi hasil pyramid)
        if "pyramid" in pos.comment:
            continue

        tick: Optional[TickData] = ticks.get(pos.symbol)
        point: Optional[float] = points.get(pos.symbol)
        if tick is None or point is None:
            continue

        current_level = pyramid_counts.get(pos.ticket, 0)
        if current_level >= settings.pyramid_max_levels:
            continue

        trigger = settings.pyramid_trigger_points * point
        # Setiap level berikutnya butuh profit lebih besar
        required_profit = trigger * (current_level + 1)

        if pos.type == "buy":
            profit_pts = tick.bid - pos.price_open
            entry_price = tick.ask
        else:
            profit_pts = pos.price_open - tick.ask
            entry_price = tick.bid

        if profit_pts < required_profit:
            continue

        # Hitung lot pyramid — mengecil tiap level
        new_lot = round(
            pos.volume * (settings.pyramid_lot_multiplier ** (current_level + 1)),
            2
        )
        if new_lot < settings.pyramid_min_lot:
            new_lot = settings.pyramid_min_lot

        # SL pyramid ikut SL posisi awal (sudah di-BE atau trailing)
        # TP sama dengan posisi awal
        action = "BUY" if pos.type == "buy" else "SELL"
        cmds.append(EACommand(
            action=action,
            symbol=pos.symbol,
            lot=new_lot,
            sl=pos.sl,
            tp=pos.tp,
            comment=f"pyramid_L{current_level + 1}_t{pos.ticket}",
        ))
        pyramid_counts[pos.ticket] = current_level + 1
        logger.info(
            f"[PYRAMID][{pos.symbol}] Level {current_level + 1} "
            f"ticket={pos.ticket} profit={profit_pts/point:.0f}pts "
            f"→ {action} {new_lot} lot sl={pos.sl} tp={pos.tp}"
        )

    return cmds


def _eod_close_commands(positions: List[Position]) -> List[EACommand]:
    """Tutup semua posisi menjelang akhir hari."""
    if not is_eod_close_time() or not positions:
        return []

    cmds = []
    for pos in positions:
        cmds.append(EACommand(
            action="CLOSE",
            symbol=pos.symbol,
            ticket=pos.ticket,
            comment="eod_close",
        ))
        logger.info(f"[EOD][{pos.symbol}] Menutup posisi ticket={pos.ticket}")
    return cmds


def manage_positions(
    positions: List[Position],
    ticks: dict,           # symbol -> TickData
    points: dict,          # symbol -> float (point size)
    already_partial: set,  # set ticket yang sudah partial close
    pyramid_counts: dict,  # ticket -> jumlah pyramid yang sudah dibuat
) -> List[EACommand]:
    """
    Entry point utama trade manager.
    Dipanggil setiap kali EA push rates.
    Return list EACommand yang akan di-queue.
    """
    cmds: List[EACommand] = []

    if not positions:
        return cmds

    # EOD close — prioritas tertinggi
    eod = _eod_close_commands(positions)
    if eod:
        return eod

    cmds += _break_even_commands(positions, ticks, points)
    cmds += _partial_close_commands(positions, ticks, points, already_partial)
    cmds += _trailing_sl_commands(positions, ticks, points)
    cmds += _pyramid_commands(positions, ticks, points, pyramid_counts)

    return cmds
