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
    Trend dari EMA50 High/Low Band — evaluasi 5 candle terakhir (majority vote):
      - Uptrend   → mayoritas candle close DI ATAS band atas
      - Downtrend → mayoritas candle close DI BAWAH band bawah
      - Campuran  → netral

    Pendekatan ini menangkap trend meski harga sedang pullback ke dalam band.
    """
    period = settings.ema_slow
    lookback = 5  # candle terakhir yang dievaluasi
    if len(bars) < period + lookback + 2:
        return None

    ema_high, ema_low = _ema_band(bars, period)

    buy_count = 0
    sell_count = 0
    for i in range(lookback):
        idx = -(2 + i)  # mulai dari candle terakhir yang sudah close
        c = bars[idx].close
        eh, el = ema_high[idx], ema_low[idx]
        if c > eh:
            buy_count += 1
        elif c < el:
            sell_count += 1

    # Minimal 3 dari 5 candle harus konsisten
    if buy_count >= 3:
        eh_now = ema_high[-2]
        el_now = ema_low[-2]
        logger.debug(f"[TREND] BUY — {buy_count}/{lookback} candle di atas band atas")
        return Direction.BUY
    if sell_count >= 3:
        logger.debug(f"[TREND] SELL — {sell_count}/{lookback} candle di bawah band bawah")
        return Direction.SELL

    c_last = bars[-2].close
    eh_now, el_now = ema_high[-2], ema_low[-2]
    logger.debug(
        f"[TREND] Netral — buy={buy_count} sell={sell_count}/{lookback} "
        f"close={c_last:.3f} band=[{el_now:.3f},{eh_now:.3f}]"
    )
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
    bars_entry: Optional[List[RateBar]] = None,
) -> Optional[TradeSignal]:
    """
    Multi-Timeframe Strategy:
    - bars       : M15 — sudah dipakai untuk detect_trend(), dipakai ulang untuk EMA band reference
    - bars_entry : M5  — candle konfirmasi entry (lebih cepat). Fallback ke bars jika None.

    BUY  (uptrend M15): M5 candle breakout/pullback ke atas band
    SELL (downtrend M15): M5 candle breakout/pullback ke bawah band
    """
    period = settings.ema_slow

    # Gunakan M5 bars untuk entry jika tersedia, fallback ke M15
    entry_bars = bars_entry if (bars_entry and len(bars_entry) >= period + 3) else bars
    tf_label = "M5" if (bars_entry and len(bars_entry) >= period + 3) else "M15"

    if len(entry_bars) < period + 3:
        return None

    ema_high, ema_low = _ema_band(entry_bars, period)

    # Candle konfirmasi = candle terakhir yang SUDAH close (dari M5)
    conf = entry_bars[-2]
    eh, el = ema_high[-2], ema_low[-2]

    price = tick.ask if direction == Direction.BUY else tick.bid
    rr_target = settings.min_rr_ratio
    atr = _atr(entry_bars)
    buffer = max(atr * 2.0, 4000 * point)   # SL 2x ATR di luar wick, min $4.00

    body = abs(conf.close - conf.open)
    band_str = f"[{el:.{digits}f}, {eh:.{digits}f}]"

    if direction == Direction.BUY:
        # Skenario A: Breakout — close di atas upper band (trend sudah konfirmasi dari detect_trend)
        breakout = conf.close > eh
        # Skenario B: Pullback — low menyentuh upper band, close masih dalam/di atas band
        pullback = conf.low <= eh and conf.close >= el and conf.close > conf.open

        if not (breakout or pullback):
            logger.info(
                f"[NO TRADE][{symbol}] BUY tidak ada setup band {band_str} — "
                f"breakout={breakout} pullback={pullback}"
            )
            return None

        entry_type = "breakout" if breakout else "pullback"
        sl = conf.low - buffer
        sl_dist = price - sl
        if sl_dist <= 0:
            logger.info(f"[NO TRADE][{symbol}] BUY SL invalid ({entry_type})")
            return None
        tp = price + sl_dist * rr_target

    else:  # SELL
        # Skenario A: Breakout — close di bawah lower band (trend sudah konfirmasi dari detect_trend)
        breakout = conf.close < el
        # Skenario B: Pullback — high menyentuh upper band, close masih dalam/di bawah band
        pullback = conf.high >= eh and conf.close <= eh and conf.close < conf.open

        if not (breakout or pullback):
            logger.info(
                f"[NO TRADE][{symbol}] SELL tidak ada setup band {band_str} — "
                f"breakout={breakout} pullback={pullback}"
            )
            return None

        entry_type = "breakout" if breakout else "pullback"
        # SL: di atas wick untuk pullback, di atas upper band untuk breakout
        sl_ref = conf.high if pullback else max(conf.high, eh)
        sl = sl_ref + buffer
        sl_dist = sl - price
        if sl_dist <= 0:
            logger.info(f"[NO TRADE][{symbol}] SELL SL invalid ({entry_type})")
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
        comment=f"polybot_{direction.value.lower()}_{entry_type}",
    )
    logger.info(
        f"[SIGNAL][{symbol}] {direction.value} [{entry_type}] [{tf_label}] price={price:.{digits}f} "
        f"band={band_str} SL={sl} TP={tp} RR=1:{rr} (SL dist={int(sl_dist/point)}pts)"
    )
    return signal
