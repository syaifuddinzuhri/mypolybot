"""
News & Macro Filter untuk Gold (XAU)
-------------------------------------
1. Economic Calendar — ForexFactory JSON
   Pause trading 15 menit sebelum & sesudah high-impact news USD/XAU

2. DXY Proxy — EURUSD inverse sebagai indikator kekuatan USD
   Jika USD menguat kuat → hindari BUY gold / hindari SELL gold jika USD lemah
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from loguru import logger

import httpx

from .config import settings
from .types import Direction


# ── Config ────────────────────────────────────────────────────────────────────

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DXY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=5m&range=1d"

IMPACT_HIGH = "High"
NEWS_CURRENCIES = {"USD", "XAU"}  # currency yang relevan untuk gold

# ── State ─────────────────────────────────────────────────────────────────────

_lock = threading.Lock()

_calendar: List[dict] = []
_calendar_fetched_at: Optional[datetime] = None
_calendar_fetching: bool = False     # guard agar tidak spawn multiple threads

_dxy_value: Optional[float] = None
_dxy_fetched_at: Optional[datetime] = None
_dxy_ema: Optional[float] = None
_dxy_fetching: bool = False


# ── Economic Calendar ─────────────────────────────────────────────────────────

def _fetch_calendar() -> None:
    global _calendar, _calendar_fetched_at, _calendar_fetching
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(FF_CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                with _lock:
                    _calendar = data
                    _calendar_fetched_at = datetime.now(timezone.utc)
                logger.info(f"[NEWS] Calendar fetched: {len(data)} events")
            elif r.status_code == 429:
                # Rate limited — tunggu lebih lama, set fetched agar tidak retry cepat
                with _lock:
                    _calendar_fetched_at = datetime.now(timezone.utc)
                logger.warning("[NEWS] Calendar rate-limited (429) — retry dalam 2 jam")
            else:
                logger.warning(f"[NEWS] Calendar HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[NEWS] Calendar fetch error: {e}")
    finally:
        with _lock:
            _calendar_fetching = False


def refresh_calendar_if_needed() -> None:
    global _calendar_fetching
    now = datetime.now(timezone.utc)
    with _lock:
        fetched = _calendar_fetched_at
        fetching = _calendar_fetching
    # Fetch setiap 2 jam, dan hanya 1 thread sekaligus
    if not fetching and (fetched is None or (now - fetched).total_seconds() > 7200):
        with _lock:
            _calendar_fetching = True
        threading.Thread(target=_fetch_calendar, daemon=True).start()


def _parse_event_time(date_str: str) -> Optional[datetime]:
    """Parse ForexFactory datetime string ke UTC datetime."""
    try:
        # Format: "2026-06-16T13:30:00-04:00"
        from datetime import timezone as tz
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def check_news_window() -> tuple[bool, str]:
    """
    Return (ok_to_trade, reason).
    ok_to_trade=False jika ada high-impact news dalam window blackout.
    """
    if not settings.news_filter_enabled:
        return True, ""

    refresh_calendar_if_needed()

    now = datetime.now(timezone.utc)
    blackout_before = timedelta(minutes=settings.news_blackout_before_min)
    blackout_after = timedelta(minutes=settings.news_blackout_after_min)

    with _lock:
        events = list(_calendar)

    for event in events:
        currency = event.get("currency", "")
        impact = event.get("impact", "")
        title = event.get("title", "")
        date_str = event.get("date", "")

        if currency not in NEWS_CURRENCIES:
            continue
        if impact != IMPACT_HIGH:
            continue

        event_time = _parse_event_time(date_str)
        if event_time is None:
            continue

        # Cek apakah sekarang dalam blackout window
        if event_time - blackout_before <= now <= event_time + blackout_after:
            delta = int((event_time - now).total_seconds() / 60)
            if delta >= 0:
                reason = f"News '{title}' ({currency}) dalam {delta} menit"
            else:
                reason = f"Baru lewat news '{title}' ({currency}) {abs(delta)} menit lalu"
            logger.info(f"[NEWS] Blackout aktif — {reason}")
            return False, reason

    return True, ""


def get_upcoming_news(hours: int = 4) -> List[dict]:
    """Ambil news high-impact dalam N jam ke depan untuk monitoring."""
    refresh_calendar_if_needed()
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)

    with _lock:
        events = list(_calendar)

    result = []
    for event in events:
        if event.get("currency") not in NEWS_CURRENCIES:
            continue
        if event.get("impact") != IMPACT_HIGH:
            continue
        t = _parse_event_time(event.get("date", ""))
        if t and now <= t <= cutoff:
            result.append({
                "time_utc": t.strftime("%H:%M"),
                "currency": event.get("currency"),
                "title": event.get("title"),
                "impact": event.get("impact"),
            })
    return sorted(result, key=lambda x: x["time_utc"])


# ── DXY Filter ────────────────────────────────────────────────────────────────

def _fetch_dxy() -> None:
    global _dxy_value, _dxy_fetched_at, _dxy_ema, _dxy_fetching
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(DXY_URL, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                # Ambil nilai terbaru yang tidak None
                valid = [c for c in closes if c is not None]
                if valid:
                    latest = valid[-1]
                    with _lock:
                        # EMA sederhana: alpha=0.1
                        if _dxy_ema is None:
                            _dxy_ema = latest
                        else:
                            _dxy_ema = 0.1 * latest + 0.9 * _dxy_ema
                        _dxy_value = latest
                        _dxy_fetched_at = datetime.now(timezone.utc)
                    logger.info(f"[DXY] DXY={latest:.3f} EMA={_dxy_ema:.3f}")
            else:
                logger.warning(f"[DXY] HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[DXY] Fetch error: {e}")
    finally:
        with _lock:
            _dxy_fetching = False


def refresh_dxy_if_needed() -> None:
    global _dxy_fetching
    now = datetime.now(timezone.utc)
    with _lock:
        fetched = _dxy_fetched_at
        fetching = _dxy_fetching
    if not fetching and (fetched is None or (now - fetched).total_seconds() > settings.dxy_refresh_seconds):
        with _lock:
            _dxy_fetching = True
        threading.Thread(target=_fetch_dxy, daemon=True).start()


def check_dxy(direction: Direction) -> tuple[bool, str]:
    """
    Return (ok_to_trade, reason).
    - DXY naik kuat (ema rising) → hindari BUY gold (USD menguat = Gold turun)
    - DXY turun kuat (ema falling) → hindari SELL gold
    """
    if not settings.dxy_filter_enabled:
        return True, ""

    refresh_dxy_if_needed()

    with _lock:
        dxy = _dxy_value
        ema = _dxy_ema
        fetched = _dxy_fetched_at

    if dxy is None or ema is None:
        return True, ""  # data belum ada, jangan blokir

    # Jika data DXY terlalu lama (>30 menit), jangan blokir
    now = datetime.now(timezone.utc)
    if fetched and (now - fetched).total_seconds() > 1800:
        return True, ""

    threshold = settings.dxy_trend_threshold  # default 0.15 = DXY naik 0.15 dari EMA

    dxy_rising = dxy > ema + threshold   # USD menguat
    dxy_falling = dxy < ema - threshold  # USD melemah

    if direction == Direction.BUY and dxy_rising:
        reason = f"DXY menguat ({dxy:.3f} > EMA {ema:.3f}+{threshold}) — hindari BUY gold"
        logger.info(f"[DXY] {reason}")
        return False, reason

    if direction == Direction.SELL and dxy_falling:
        reason = f"DXY melemah ({dxy:.3f} < EMA {ema:.3f}-{threshold}) — hindari SELL gold"
        logger.info(f"[DXY] {reason}")
        return False, reason

    return True, ""


# ── Combined Check ────────────────────────────────────────────────────────────

def check_macro_ok(direction: Direction) -> tuple[bool, str]:
    """
    Main check — panggil ini dari bot.py sebelum entry.
    Return (ok, reason).
    """
    ok, reason = check_news_window()
    if not ok:
        return False, reason

    ok, reason = check_dxy(direction)
    if not ok:
        return False, reason

    return True, ""


def get_macro_status() -> dict:
    """Status untuk monitoring endpoint."""
    with _lock:
        dxy = _dxy_value
        ema = _dxy_ema
        cal_fetched = _calendar_fetched_at
        dxy_fetched = _dxy_fetched_at
        n_events = len(_calendar)

    upcoming = get_upcoming_news(hours=4)
    ok_news, news_reason = check_news_window()

    return {
        "news_filter_enabled": settings.news_filter_enabled,
        "dxy_filter_enabled": settings.dxy_filter_enabled,
        "news_ok": ok_news,
        "news_reason": news_reason,
        "upcoming_news": upcoming,
        "calendar_events_cached": n_events,
        "calendar_fetched_at": cal_fetched.isoformat() if cal_fetched else None,
        "dxy_value": round(dxy, 3) if dxy else None,
        "dxy_ema": round(ema, 3) if ema else None,
        "dxy_fetched_at": dxy_fetched.isoformat() if dxy_fetched else None,
    }
