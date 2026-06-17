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
    """EMA dari high & low → membentuk band (channel) dinamis."""
    highs = np.array([b.high for b in bars])
    lows = np.array([b.low for b in bars])
    return _ema(highs, period), _ema(lows, period)


# ── Trend Detection (EMA50 High/Low Band) ─────────────────────────────────────

def detect_trend(bars: List[RateBar], point: float) -> Optional[Direction]:
    """
    Trend dari EMA50 High/Low Band:
      - Uptrend   → candle close DI ATAS band atas (EMA50 dari high)
      - Downtrend → candle close DI BAWAH band bawah (EMA50 dari low)
      - Di dalam band → netral, tidak ada trend

    Dievaluasi pada candle terakhir yang SUDAH close (bars[-2]).
    """
    period = settings.ema_slow
    if len(bars) < period + 3:
        return None

    ema_high, ema_low = _ema_band(bars, period)
    idx = -2  # candle terakhir yang sudah close
    c = bars[idx].close
    eh, el = ema_high[idx], ema_low[idx]

    if c > eh:
        logger.debug(f"[TREND] BUY — close {c:.3f} di atas band atas {eh:.3f}")
        return Direction.BUY
    if c < el:
        logger.debug(f"[TREND] SELL — close {c:.3f} di bawah band bawah {el:.3f}")
        return Direction.SELL

    logger.debug(f"[TREND] Netral — close {c:.3f} di dalam band [{el:.3f}, {eh:.3f}]")
    return None


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

def _atr(bars: List[RateBar], period: int = 14) -> float:
    """Average True Range — ukuran volatilitas rata-rata."""
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i-1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return float(np.mean(trs[-period:]))


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
    Strategi EMA50 High/Low Band + Rejection Candle:

    BUY  (uptrend, harga di atas band):
      - candle pullback turun MENYENTUH band atas (low <= EMA50_high)
      - lalu CLOSE balik di atas band (close > EMA50_high) — keluar dari EMA
      - candle bullish dengan lower wick (rejection)
    SELL (downtrend, harga di bawah band):
      - candle pullback naik MENYENTUH band bawah (high >= EMA50_low)
      - lalu CLOSE balik di bawah band (close < EMA50_low)
      - candle bearish dengan upper wick (rejection)

    SL di luar wick rejection, TP = RR (default 1:3 dari .env MIN_RR_RATIO).
    """
    period = settings.ema_slow
    if len(bars) < period + 3:
        return None

    ema_high, ema_low = _ema_band(bars, period)

    # Candle konfirmasi = candle terakhir yang SUDAH close
    conf = bars[-2]
    eh, el = ema_high[-2], ema_low[-2]

    price = tick.ask if direction == Direction.BUY else tick.bid
    rr_target = settings.min_rr_ratio
    atr = _atr(bars)
    buffer = max(atr * 0.3, 30 * point)   # SL sedikit di luar wick

    body = abs(conf.close - conf.open)
    band_str = f"[{el:.{digits}f}, {eh:.{digits}f}]"

    if direction == Direction.BUY:
        touched    = conf.low <= eh            # menyentuh band atas (pullback)
        closed_out = conf.close > eh           # close balik keluar (di atas band)
        bullish    = conf.close > conf.open
        lower_wick = min(conf.open, conf.close) - conf.low
        rejection  = lower_wick >= body * 0.8 or lower_wick >= 50 * point

        if not (touched and closed_out and bullish and rejection):
            logger.info(
                f"[NO TRADE][{symbol}] BUY belum konfirmasi band {band_str} — "
                f"touch={touched} close_out={closed_out} bull={bullish} reject={rejection}"
            )
            return None

        sl = conf.low - buffer
        sl_dist = price - sl
        if sl_dist <= 0:
            logger.info(f"[NO TRADE][{symbol}] BUY SL invalid (harga sudah di bawah wick)")
            return None
        tp = price + sl_dist * rr_target

    else:  # SELL
        touched    = conf.high >= el           # menyentuh band bawah (pullback)
        closed_out = conf.close < el           # close balik keluar (di bawah band)
        bearish    = conf.close < conf.open
        upper_wick = conf.high - max(conf.open, conf.close)
        rejection  = upper_wick >= body * 0.8 or upper_wick >= 50 * point

        if not (touched and closed_out and bearish and rejection):
            logger.info(
                f"[NO TRADE][{symbol}] SELL belum konfirmasi band {band_str} — "
                f"touch={touched} close_out={closed_out} bear={bearish} reject={rejection}"
            )
            return None

        sl = conf.high + buffer
        sl_dist = sl - price
        if sl_dist <= 0:
            logger.info(f"[NO TRADE][{symbol}] SELL SL invalid (harga sudah di atas wick)")
            return None
        tp = price - sl_dist * rr_target

    sl, tp = round(sl, 5), round(tp, 5)
    rr = round(abs(tp - price) / abs(sl - price), 2)

    signal = TradeSignal(
        symbol=symbol,
        direction=direction,
        lot=settings.lot_size,
        sl=sl,
        tp=tp,
        comment=f"polybot_{direction.value.lower()}_emaband",
    )
    logger.info(
        f"[SIGNAL][{symbol}] {direction.value} price={price:.{digits}f} "
        f"band={band_str} SL={sl} TP={tp} RR=1:{rr} (SL dist={int(sl_dist/point)}pts)"
    )
    return signal
