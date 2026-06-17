"""
Telegram Notification untuk Polybot
Kirim pesan saat: entry, TP hit, SL hit, daily stop, cooldown, startup
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from loguru import logger

import httpx

from .config import settings

WIB = timezone(timedelta(hours=7))


def _wib(dt: datetime) -> str:
    return dt.astimezone(WIB).strftime("%H:%M WIB")


def _now_wib() -> str:
    return _wib(datetime.now(timezone.utc))


def send(message: str) -> bool:
    """Kirim pesan ke Telegram. Non-blocking via thread."""
    if not settings.telegram_enabled:
        return False
    if not settings.telegram_token or not settings.telegram_chat_id:
        logger.warning("[TELEGRAM] Token atau Chat ID belum diset")
        return False

    def _send():
        url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
        try:
            with httpx.Client(timeout=10) as client:
                r = client.post(url, json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                if r.status_code != 200:
                    logger.warning(f"[TELEGRAM] Error {r.status_code}: {r.text[:100]}")
        except Exception as e:
            logger.warning(f"[TELEGRAM] Gagal kirim: {e}")

    threading.Thread(target=_send, daemon=True).start()
    return True


# ── Event notifications ───────────────────────────────────────────────────────

def notify_startup(symbol: str):
    send(
        f"🤖 <b>Polybot Online</b>\n"
        f"Symbol: <code>{symbol}</code>\n"
        f"Strategi: EMA50 Band | RR 1:{settings.min_rr_ratio}\n"
        f"Spread maks: {settings.max_spread_points} pts\n"
        f"Waktu: {_now_wib()}"
    )


def _pips(pts: int) -> str:
    """Convert points ke pips (1 pip = 10 points untuk 3-decimal broker)."""
    return f"{pts // 10} pips"


def notify_entry(symbol: str, direction: str, price: float, sl: float,
                 tp: float, lot: float, sl_pts: int, tp_pts: int,
                 entry_type: str = "",
                 tp1: float = 0.0, tp2: float = 0.0,
                 price_high: float = 0.0, point: float = 0.001):
    dir_label = "Buy Now" if direction == "BUY" else "Sell Now"
    icon = "🟢" if direction == "BUY" else "🔴"
    p_lo = min(price, price_high) if price_high else price
    p_hi = max(price, price_high) if price_high else price
    fmt = lambda v: str(round(v))
    r = lambda v: str(round(v))
    entry_str = f"@{r(p_lo)}-{r(p_hi)}" if price_high else f"@{r(price)}"
    tag = f" <i>({entry_type})</i>" if entry_type else ""

    tp1_pts = int(abs(tp1 - price) / point) if (tp1 and point) else 0
    tp2_pts = int(abs(tp2 - price) / point) if (tp2 and point) else 0

    lines = [
        f"{icon} <b>{symbol} {dir_label}</b> {entry_str}{tag}",
        f"",
        f"🚫 StopLose     : <code>{fmt(sl)}</code>",
        f"",
    ]
    if tp1:
        lines.append(f"🔵 TakeProfit 1 : <code>{fmt(tp1)}</code>")
    if tp2:
        lines.append(f"🔵 TakeProfit 2 : <code>{fmt(tp2)}</code>")
    lines.append(f"🎯 TakeProfit 3 : <code>{fmt(tp)}</code>")
    lines.append(f"")
    lines.append(f"⏰ {_now_wib()}")
    send("\n".join(lines))


def notify_close(symbol: str, direction: str, profit: float,
                 open_price: float, close_price: float,
                 reason: str = ""):
    if profit > 0:
        icon, label = "✅", "PROFIT"
    elif profit < 0:
        icon, label = "❌", "LOSS"
    else:
        icon, label = "➖", "BREAKEVEN"

    reason_str = f"\nAlasan: <i>{reason}</i>" if reason else ""
    op = round(open_price) if open_price else 0
    cp = round(close_price) if close_price else "~"
    send(
        f"{icon} <b>{label} — {symbol}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 Open  : <code>{op}</code>\n"
        f"📉 Close : <code>{cp}</code>\n"
        f"💵 P&L   : <b>{'+'if profit>0 else ''}{profit:.2f}</b>{reason_str}\n"
        f"⏰ {_now_wib()}"
    )


def notify_daily_stop(balance: float, loss: float, stop_pct: float):
    send(
        f"🚨 <b>DAILY STOP TERCAPAI</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Loss hari ini : <b>{loss:.2f}</b>\n"
        f"Limit ({stop_pct}%)  : {balance * stop_pct / 100:.2f}\n"
        f"Balance       : {balance:.2f}\n"
        f"⏸ Bot berhenti trading hingga besok\n"
        f"⏰ {_now_wib()}"
    )


def notify_sideways(symbol: str, buy_count: int, sell_count: int, lookback: int = 5):
    send(
        f"⚠️ <b>{symbol} — Market Sideways</b>\n"
        f"Trend tidak terdeteksi ({max(buy_count, sell_count)}/{lookback} candle)\n"
        f"Bot tidak entry sampai trend kembali\n"
        f"⏰ {_now_wib()}"
    )


def notify_cooldown(symbol: str, consecutive: int, minutes: int):
    send(
        f"⏸ <b>Cooldown Aktif — {symbol}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Loss berturut : {consecutive}x\n"
        f"Pause selama  : {minutes} menit\n"
        f"⏰ {_now_wib()}"
    )


def notify_spread_ok(symbol: str, spread: int):
    """Kirim notifikasi saat spread turun ke normal (London buka)."""
    send(
        f"📡 <b>Spread Normal — {symbol}</b>\n"
        f"Spread: {spread} pts ✅\n"
        f"Bot siap entry sesi London\n"
        f"⏰ {_now_wib()}"
    )


def notify_test() -> bool:
    """Test koneksi Telegram."""
    return send(
        f"✅ <b>Polybot Test</b>\n"
        f"Koneksi Telegram berhasil!\n"
        f"Token dan Chat ID valid.\n"
        f"⏰ {_now_wib()}"
    )
