from __future__ import annotations
import numpy as np
from loguru import logger
from typing import Optional, List

from .types import RateBar, SRZone, TickData, Direction, TradeSignal
from .config import settings


# ── EMA ─────────────────────────────────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    ema = np.zeros_like(values)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = values[i] * k + ema[i - 1] * (1 - k)
    return ema


# ── EMA50 High/Low Band ──────────────────────────────────────────────────────

def _ema_band(bars: List[RateBar], period: int) -> tuple[np.ndarray, np.ndarray]:
    highs = np.array([b.high for b in bars])
    lows  = np.array([b.low  for b in bars])
    return _ema(highs, period), _ema(lows, period)


# ── ATR ──────────────────────────────────────────────────────────────────────

def _atr(bars: List[RateBar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return float(np.mean(trs[-period:]))


# ── SR Zone Detection ────────────────────────────────────────────────────────

def _find_sr_zones(bars: List[RateBar], point: float) -> List[SRZone]:
    if len(bars) < 10:
        return []

    highs = np.array([b.high for b in bars])
    lows  = np.array([b.low  for b in bars])
    merge_pts = settings.sr_zone_merge_points * point

    zones: List[SRZone] = []
    for i in range(2, len(bars) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            zones.append(SRZone(
                low=highs[i] - merge_pts / 2,
                high=highs[i] + merge_pts / 2,
                strength=1, zone_type="resistance"
            ))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            zones.append(SRZone(
                low=lows[i] - merge_pts / 2,
                high=lows[i] + merge_pts / 2,
                strength=1, zone_type="support"
            ))

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


def _near_sr_zone(price: float, zones: List[SRZone], zone_type: str, threshold: float) -> bool:
    """Cek apakah price dekat dengan zona SR yang relevan."""
    for z in zones:
        if z.zone_type != zone_type:
            continue
        center = (z.low + z.high) / 2
        if abs(price - center) <= threshold:
            return True
    return False


# ── Trend Detection ───────────────────────────────────────────────────────────

def detect_trend(bars: List[RateBar], point: float) -> Optional[Direction]:
    """
    Trend dari EMA50 Band — evaluasi 5 candle terakhir.
    Minimal 4/5 candle konsisten untuk sinyal lebih kuat.
    """
    period   = settings.ema_slow
    lookback = 5
    min_vote = 4  # naik dari 3 ke 4 — filter lebih ketat

    if len(bars) < period + lookback + 2:
        return None

    ema_high, ema_low = _ema_band(bars, period)

    buy_count = sell_count = 0
    for i in range(lookback):
        idx = -(2 + i)
        c  = bars[idx].close
        eh = ema_high[idx]
        el = ema_low[idx]
        if c > eh:
            buy_count += 1
        elif c < el:
            sell_count += 1

    if buy_count >= min_vote:
        logger.debug(f"[TREND] BUY {buy_count}/{lookback}")
        return Direction.BUY
    if sell_count >= min_vote:
        logger.debug(f"[TREND] SELL {sell_count}/{lookback}")
        return Direction.SELL

    c_last       = bars[-2].close
    eh_now, el_now = ema_high[-2], ema_low[-2]
    logger.debug(
        f"[TREND] Netral — buy={buy_count} sell={sell_count}/{lookback} "
        f"close={c_last:.3f} band=[{el_now:.3f},{eh_now:.3f}]"
    )
    return None


# ── Main Analyze ──────────────────────────────────────────────────────────────

def analyze(
    symbol: str,
    bars: List[RateBar],
    tick: TickData,
    point: float,
    digits: int,
    direction: Direction,
    bars_entry: Optional[List[RateBar]] = None,
) -> Optional[TradeSignal]:
    """
    Entry hanya valid jika semua filter lolos:
    1. EMA band setup (breakout atau pullback)
    2. Candle body cukup besar (bukan doji)
    3. ATR dalam range wajar (tidak terlalu flat, tidak terlalu liar)
    4. SR zone confluence (opsional, boosts confidence)
    """
    period = settings.ema_slow

    if len(bars) < period + 3:
        return None

    # Band dari M15
    ema_high, ema_low = _ema_band(bars, period)
    eh, el = ema_high[-2], ema_low[-2]
    band_width = eh - el

    # Candle konfirmasi dari M5 jika tersedia
    has_m5    = bars_entry and len(bars_entry) >= period + 3
    entry_bars = bars_entry if has_m5 else bars
    tf_label   = "M5" if has_m5 else "M15"
    conf       = entry_bars[-2]

    price      = tick.ask if direction == Direction.BUY else tick.bid
    rr_target  = settings.min_rr_ratio

    # ── Filter 1: ATR ──────────────────────────────────────────────────────
    # Skip jika market terlalu flat (ATR < 1.0) atau terlalu volatile (ATR > 8.0)
    atr = _atr(bars)
    atr_min = 1.0   # gold perlu bergerak minimal $1 per 15 menit
    atr_max = 8.0   # di atas $8 terlalu chaotic untuk entry aman
    if atr < atr_min or atr > atr_max:
        logger.info(
            f"[NO TRADE][{symbol}] ATR filter — ATR={atr:.3f} "
            f"(range [{atr_min}, {atr_max}])"
        )
        return None

    # ── Filter 2: Candle body ──────────────────────────────────────────────
    # Body harus > MIN_CANDLE_BODY_POINTS agar bukan doji
    candle_body = abs(conf.close - conf.open)
    min_body    = settings.min_candle_body_points * point
    if candle_body < min_body:
        logger.info(
            f"[NO TRADE][{symbol}] Candle terlalu kecil — "
            f"body={int(candle_body/point)}pts < min={settings.min_candle_body_points}pts"
        )
        return None

    # ── SL buffer ─────────────────────────────────────────────────────────
    buffer = max(atr * settings.sl_atr_multiplier, settings.sl_min_points * point)
    buffer = min(buffer, settings.sl_max_points * point)

    # ── SR Zone confluence ─────────────────────────────────────────────────
    sr_zones  = _find_sr_zones(bars, point)
    sr_thresh = settings.sr_zone_threshold_points * point
    has_sr    = False

    band_str = f"[{el:.{digits}f}, {eh:.{digits}f}]"

    # ── Filter 3: Setup EMA band ───────────────────────────────────────────
    if direction == Direction.BUY:
        breakout = conf.close > eh
        pullback = conf.low <= eh and conf.close >= el and conf.close > conf.open

        if not (breakout or pullback):
            logger.info(
                f"[NO TRADE][{symbol}] BUY — tidak ada setup band {band_str} "
                f"breakout={breakout} pullback={pullback}"
            )
            return None

        entry_type = "breakout" if breakout else "pullback"

        # SR confluence: untuk BUY, cek apakah dekat support
        has_sr = _near_sr_zone(price, sr_zones, "support", sr_thresh)
        # Band bawah juga dianggap support dinamis
        if not has_sr and abs(price - el) <= sr_thresh * 2:
            has_sr = True

        # Pullback WAJIB ada SR confluence — breakout boleh tanpa SR
        if entry_type == "pullback" and not has_sr:
            logger.info(
                f"[NO TRADE][{symbol}] BUY pullback — tidak ada SR confluence "
                f"(price={price:.3f}, zones={len(sr_zones)})"
            )
            return None

        sl      = round(conf.low - buffer, digits)
        sl_dist = price - sl
        if sl_dist <= 0:
            logger.info(f"[NO TRADE][{symbol}] BUY SL invalid")
            return None

        tp1 = round(price + sl_dist * settings.tp1_rr, digits)
        tp2 = round(price + sl_dist * settings.tp2_rr, digits)
        tp  = round(price + sl_dist * rr_target, digits)

    else:  # SELL
        breakout = conf.close < el
        pullback = conf.high >= eh and conf.close <= eh and conf.close < conf.open

        if not (breakout or pullback):
            logger.info(
                f"[NO TRADE][{symbol}] SELL — tidak ada setup band {band_str} "
                f"breakout={breakout} pullback={pullback}"
            )
            return None

        entry_type = "breakout" if breakout else "pullback"

        # SR confluence: untuk SELL, cek apakah dekat resistance
        has_sr = _near_sr_zone(price, sr_zones, "resistance", sr_thresh)
        # Band atas juga dianggap resistance dinamis
        if not has_sr and abs(price - eh) <= sr_thresh * 2:
            has_sr = True

        if entry_type == "pullback" and not has_sr:
            logger.info(
                f"[NO TRADE][{symbol}] SELL pullback — tidak ada SR confluence "
                f"(price={price:.3f}, zones={len(sr_zones)})"
            )
            return None

        sl_ref  = conf.high if pullback else max(conf.high, eh)
        sl      = round(sl_ref + buffer, digits)
        sl_dist = sl - price
        if sl_dist <= 0:
            logger.info(f"[NO TRADE][{symbol}] SELL SL invalid")
            return None

        tp1 = round(price - sl_dist * settings.tp1_rr, digits)
        tp2 = round(price - sl_dist * settings.tp2_rr, digits)
        tp  = round(price - sl_dist * rr_target, digits)

    rr     = round(abs(tp - price) / abs(sl - price), 2)
    sr_tag = " +SR" if has_sr else ""

    signal = TradeSignal(
        symbol=symbol,
        direction=direction,
        lot=settings.lot_size,
        sl=sl,
        tp=tp,
        tp1=tp1,
        tp2=tp2,
        comment=f"polybot_{direction.value.lower()}_{entry_type}",
    )
    logger.info(
        f"[SIGNAL][{symbol}] {direction.value} [{entry_type}]{sr_tag} [{tf_label}] "
        f"price={price:.{digits}f} band={band_str} ATR={atr:.2f} "
        f"SL={sl} TP1={tp1} TP2={tp2} TP3={tp} RR=1:{rr} "
        f"(SL dist={int(sl_dist/point)}pts)"
    )
    return signal
