from __future__ import annotations
import numpy as np
from loguru import logger
from typing import Optional, List

from .types import RateBar, SRZone, TickData, Direction, TradeSignal
from .config import settings
from .fibonacci import check_fib_confluence


# ── EMA ─────────────────────────────────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    ema = np.zeros_like(values)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = values[i] * k + ema[i - 1] * (1 - k)
    return ema


# ── Trend Detection ──────────────────────────────────────────────────────────

def detect_trend(bars: List[RateBar], point: float) -> Optional[Direction]:
    """
    Deteksi trend dari EMA + majority candle.
    BUY  → EMA20 > EMA50 DAN mayoritas 5 candle terakhir bullish
    SELL → EMA20 < EMA50 DAN mayoritas 5 candle terakhir bearish
    """
    if len(bars) < settings.ema_slow + 5:
        return None

    closes = np.array([b.close for b in bars])
    ema_fast = _ema(closes, settings.ema_fast)
    ema_slow = _ema(closes, settings.ema_slow)

    fast_last = ema_fast[-1]
    slow_last = ema_slow[-1]

    if fast_last > slow_last:
        trend = Direction.BUY
    elif fast_last < slow_last:
        trend = Direction.SELL
    else:
        return None

    # Cukup EMA sudah cukup sebagai filter trend
    # Majority vote dihapus — pullback berlawanan arah adalah momen entry yang bagus

    # Candle terakhir tidak boleh doji
    last = bars[-1]
    body = abs(last.close - last.open)
    if body < settings.min_candle_body_points * point:
        logger.debug(f"[TREND] Doji skip (body={body:.5f})")
        return None

    logger.debug(
        f"[TREND] {trend.value} — EMA{settings.ema_fast}={fast_last:.3f} "
        f"EMA{settings.ema_slow}={slow_last:.3f}"
    )
    return trend


# ── SR Zone Detection ────────────────────────────────────────────────────────

def _find_sr_zones(bars: List[RateBar], point: float) -> List[SRZone]:
    if len(bars) < 10:
        return []

    highs = np.array([b.high for b in bars])
    lows = np.array([b.low for b in bars])
    merge_pts = settings.sr_zone_merge_points * point

    zones: List[SRZone] = []

    for i in range(2, len(bars) - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            zones.append(SRZone(
                low=highs[i] - merge_pts / 2,
                high=highs[i] + merge_pts / 2,
                strength=1, zone_type="resistance"
            ))

        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            zones.append(SRZone(
                low=lows[i] - merge_pts / 2,
                high=lows[i] + merge_pts / 2,
                strength=1, zone_type="support"
            ))

    # Merge overlapping
    zones.sort(key=lambda z: z.low)
    merged: List[SRZone] = []
    for z in zones:
        if merged and z.low <= merged[-1].high:
            prev = merged[-1]
            merged[-1] = SRZone(
                low=min(prev.low, z.low),
                high=max(prev.high, z.high),
                strength=prev.strength + 1,
                zone_type=prev.zone_type if prev.strength >= z.strength else z.zone_type,
            )
        else:
            merged.append(z)

    return merged


# ── SL / TP ──────────────────────────────────────────────────────────────────

def _calculate_sl_tp(
    direction: Direction,
    entry: float,
    zones: List[SRZone],
    point: float,
) -> tuple[float, float]:
    min_sl = 150 * point

    if direction == Direction.BUY:
        sl = entry - min_sl
        support_zones = sorted([z for z in zones if z.high < entry], key=lambda z: z.high, reverse=True)
        if support_zones:
            sl_candidate = support_zones[0].low - 10 * point
            if entry - sl_candidate >= min_sl:
                sl = sl_candidate

        sl_dist = entry - sl
        min_tp = sl_dist * settings.min_rr_ratio
        tp = entry + min_tp

        resist_zones = sorted([z for z in zones if z.low > entry], key=lambda z: z.low)
        if resist_zones:
            tp_candidate = resist_zones[0].low - 10 * point
            if tp_candidate - entry >= min_tp:
                tp = tp_candidate

    else:
        sl = entry + min_sl
        resist_zones = sorted([z for z in zones if z.low > entry], key=lambda z: z.low)
        if resist_zones:
            sl_candidate = resist_zones[0].high + 10 * point
            if sl_candidate - entry >= min_sl:
                sl = sl_candidate

        sl_dist = sl - entry
        min_tp = sl_dist * settings.min_rr_ratio
        tp = entry - min_tp

        support_zones = sorted([z for z in zones if z.high < entry], key=lambda z: z.high, reverse=True)
        if support_zones:
            tp_candidate = support_zones[0].high + 10 * point
            if entry - tp_candidate >= min_tp:
                tp = tp_candidate

    rr = round(abs(tp - entry) / abs(sl - entry), 2)
    logger.debug(f"[SL/TP] {direction.value} entry={entry:.3f} sl={round(sl,3)} tp={round(tp,3)} RR=1:{rr}")
    return round(sl, 5), round(tp, 5)


# ── Main Analyze ─────────────────────────────────────────────────────────────

def analyze(
    symbol: str,
    bars: List[RateBar],
    tick: TickData,
    point: float,
    digits: int,
    direction: Direction,
) -> Optional[TradeSignal]:
    """
    Entry hanya saat harga MENYENTUH zona SR yang tepat:
    - BUY  → harga menyentuh zona SUPPORT dari atas (pullback ke support)
    - SELL → harga menyentuh zona RESISTANCE dari bawah (pullback ke resistance)

    Threshold sangat ketat: harga harus benar-benar di dalam atau
    sangat dekat zona (maks 150 points).
    """
    zones = _find_sr_zones(bars, point)
    if not zones:
        logger.debug(f"[NO TRADE][{symbol}] Tidak ada SR zone terdeteksi")
        return None

    price = tick.ask if direction == Direction.BUY else tick.bid

    # Threshold ketat: harga harus menyentuh zona (bukan sekedar dekat)
    touch_buffer = 150 * point  # 0.150 untuk XAUUSDm

    min_strength = 2

    # Cek apakah zona masih holding: minimal 2 dari 3 candle terakhir close di dalam/atas zona
    def zone_still_holding_support(z: SRZone) -> bool:
        recent = bars[-3:]
        holding = sum(1 for b in recent if b.close >= z.low)
        return holding >= 2

    def zone_still_holding_resistance(z: SRZone) -> bool:
        recent = bars[-3:]
        holding = sum(1 for b in recent if b.close <= z.high)
        return holding >= 2

    if direction == Direction.BUY:
        target_zones = [
            z for z in zones
            if z.zone_type == "support"
            and z.low <= price <= z.high + touch_buffer
            and z.strength >= min_strength
            and price >= z.low
            and zone_still_holding_support(z)  # zona belum ditembus candle
        ]
        target_zones.sort(key=lambda z: abs(price - z.high))

    else:  # SELL
        target_zones = [
            z for z in zones
            if z.zone_type == "resistance"
            and z.low - touch_buffer <= price <= z.high
            and z.strength >= min_strength
            and price <= z.high
            and zone_still_holding_resistance(z)
        ]
        target_zones.sort(key=lambda z: abs(price - z.low))

    if not target_zones:
        if direction == Direction.BUY:
            all_support = sorted([z for z in zones if z.zone_type == "support"],
                                  key=lambda z: z.high, reverse=True)
            if all_support:
                nearest = all_support[0]
                dist = int((price - nearest.high) / point)
                broken = not zone_still_holding_support(nearest)
                status = "BROKEN/ditembus" if broken else f"{dist} pts di atas zona"
                logger.info(f"[NO TRADE][{symbol}] Support [{nearest.low:.{digits}f},{nearest.high:.{digits}f}] — {status}")
            else:
                logger.info(f"[NO TRADE][{symbol}] Tidak ada support zone valid (BUY)")
        else:
            all_resist = sorted([z for z in zones if z.zone_type == "resistance"],
                                 key=lambda z: z.low)
            if all_resist:
                nearest = all_resist[0]
                dist = int((nearest.low - price) / point)
                broken = not zone_still_holding_resistance(nearest)
                status = "BROKEN/ditembus" if broken else f"{dist} pts di bawah zona"
                logger.info(f"[NO TRADE][{symbol}] Resistance [{nearest.low:.{digits}f},{nearest.high:.{digits}f}] — {status}")
            else:
                logger.info(f"[NO TRADE][{symbol}] Tidak ada resistance zone valid (SELL)")
        return None

    hit_zone = target_zones[0]
    zone_str = f"[{hit_zone.low:.{digits}f}, {hit_zone.high:.{digits}f}]"

    # ── Fibonacci — gunakan sebagai TP enhancement, bukan blocker entry ──
    _, fib_tp, _ = check_fib_confluence(price, bars, direction, point, digits, symbol)

    # Hitung SL/TP dari SR zone
    sl, tp = _calculate_sl_tp(direction, price, zones, point)

    # Override TP dengan Fibonacci extension jika lebih baik (lebih jauh)
    if fib_tp is not None:
        if direction == Direction.BUY and fib_tp > tp:
            logger.info(f"[FIB][{symbol}] TP dinaikkan ke Fib extension: {tp:.{digits}f} → {fib_tp:.{digits}f}")
            tp = round(fib_tp, 5)
        elif direction == Direction.SELL and fib_tp < tp:
            logger.info(f"[FIB][{symbol}] TP diturunkan ke Fib extension: {tp:.{digits}f} → {fib_tp:.{digits}f}")
            tp = round(fib_tp, 5)

    signal = TradeSignal(
        symbol=symbol,
        direction=direction,
        lot=settings.lot_size,
        sl=sl,
        tp=tp,
        comment=f"polybot_{direction.value.lower()}_{hit_zone.zone_type}_fib",
    )
    logger.info(
        f"[SIGNAL][{symbol}] {direction.value} price={price:.{digits}f} "
        f"zone={zone_str} sl={sl} tp={tp}"
    )
    return signal
