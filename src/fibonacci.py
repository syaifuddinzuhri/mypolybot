"""
Fibonacci Retracement & Extension
- Deteksi swing high/low otomatis dari bars
- Hitung level retracement (entry) dan extension (TP)
- Cek konfluensi dengan harga sekarang
"""
from __future__ import annotations
import numpy as np
from typing import Optional, List
from loguru import logger

from .types import RateBar, Direction
from .config import settings


class FibLevels:
    def __init__(
        self,
        swing_high: float,
        swing_low: float,
        direction: Direction,
    ):
        self.swing_high = swing_high
        self.swing_low = swing_low
        self.direction = direction
        self.range = swing_high - swing_low

        # Parse entry levels dari config
        self.entry_levels = [float(x) for x in settings.fib_entry_levels.split(",")]
        self.tp_extensions = [float(x) for x in settings.fib_tp_extension.split(",")]

        # Hitung retracement prices
        # BUY:  swing high → swing low (harga turun), entry di retracement naik kembali
        # SELL: swing low → swing high (harga naik), entry di retracement turun kembali
        self.retracements: dict[float, float] = {}
        self.extensions: dict[float, float] = {}

        for level in self.entry_levels:
            if direction == Direction.BUY:
                # Retracement dari move turun: entry di level pullback
                self.retracements[level] = swing_high - (self.range * level)
            else:
                # Retracement dari move naik: entry di level pullback
                self.retracements[level] = swing_low + (self.range * level)

        for level in self.tp_extensions:
            if direction == Direction.BUY:
                # Extension ke atas dari swing low
                self.extensions[level] = swing_low + (self.range * level)
            else:
                # Extension ke bawah dari swing high
                self.extensions[level] = swing_high - (self.range * level)

    def nearest_entry_level(self, price: float, point: float) -> Optional[tuple[float, float]]:
        """
        Return (level, price) jika harga dalam buffer dari salah satu level entry.
        Return None jika tidak ada level yang dekat.
        """
        buffer = settings.fib_confluence_buffer * point
        best = None
        best_dist = float("inf")

        for level, fib_price in self.retracements.items():
            dist = abs(price - fib_price)
            if dist <= buffer and dist < best_dist:
                best_dist = dist
                best = (level, fib_price)

        return best

    def best_tp_extension(self, entry: float, point: float) -> Optional[float]:
        """Return TP terbaik dari Fibonacci extension."""
        min_dist = 200 * point  # TP minimal 200 pts dari entry

        candidates = []
        for level, ext_price in self.extensions.items():
            if self.direction == Direction.BUY and ext_price > entry + min_dist:
                candidates.append((level, ext_price))
            elif self.direction == Direction.SELL and ext_price < entry - min_dist:
                candidates.append((level, ext_price))

        if not candidates:
            return None

        # Ambil yang terdekat dari entry
        if self.direction == Direction.BUY:
            candidates.sort(key=lambda x: x[1])
        else:
            candidates.sort(key=lambda x: x[1], reverse=True)

        return candidates[0][1]

    def log_levels(self, symbol: str, digits: int) -> None:
        logger.debug(
            f"[FIB][{symbol}] {self.direction.value} "
            f"swing H={self.swing_high:.{digits}f} L={self.swing_low:.{digits}f} "
            f"range={self.range:.{digits}f}"
        )
        for level, price in sorted(self.retracements.items()):
            logger.debug(f"[FIB][{symbol}]   {level*100:.1f}% = {price:.{digits}f}")


def _find_swing(bars: List[RateBar], lookback: int) -> tuple[float, float, int, int]:
    """
    Cari swing high dan swing low dalam N bar terakhir.
    Return (swing_high, swing_low, high_idx, low_idx)
    """
    recent = bars[-lookback:]
    highs = [b.high for b in recent]
    lows = [b.low for b in recent]

    swing_high = max(highs)
    swing_low = min(lows)
    high_idx = highs.index(swing_high)
    low_idx = lows.index(swing_low)

    return swing_high, swing_low, high_idx, low_idx


def get_fib_levels(
    bars: List[RateBar],
    direction: Direction,
    point: float,
    digits: int,
    symbol: str,
) -> Optional[FibLevels]:
    """
    Hitung level Fibonacci berdasarkan swing high/low terakhir.
    Arah swing disesuaikan dengan direction trade.
    """
    if not settings.fib_enabled:
        return None

    lookback = min(settings.fib_swing_lookback, len(bars))
    if lookback < 10:
        return None

    swing_high, swing_low, high_idx, low_idx = _find_swing(bars, lookback)
    range_size = swing_high - swing_low

    # Range minimal 300 points agar Fibonacci bermakna
    if range_size < 300 * point:
        logger.debug(
            f"[FIB][{symbol}] Range terlalu kecil ({range_size/point:.0f} pts), skip"
        )
        return None

    # Validasi urutan swing: untuk BUY, swing high harus sebelum swing low
    # (harga naik dulu → turun → kita beli saat pullback)
    # Untuk SELL, swing low harus sebelum swing high
    # (harga turun dulu → naik → kita jual saat pullback)
    if direction == Direction.BUY and high_idx >= low_idx:
        # Swing high setelah swing low = tidak ada pullback yang valid untuk BUY
        # Cari swing yang urutan nya benar
        pass  # tetap lanjut, hitung saja

    fib = FibLevels(swing_high, swing_low, direction)
    fib.log_levels(symbol, digits)
    return fib


def check_fib_confluence(
    price: float,
    bars: List[RateBar],
    direction: Direction,
    point: float,
    digits: int,
    symbol: str,
) -> tuple[bool, Optional[float], Optional[FibLevels]]:
    """
    Cek apakah harga berada di level Fibonacci yang valid.

    Return:
        (in_confluence, fib_tp, fib_levels)
        - in_confluence: True jika harga di level Fib
        - fib_tp: harga TP dari Fib extension (None jika tidak ada)
        - fib_levels: object FibLevels untuk referensi
    """
    fib = get_fib_levels(bars, direction, point, digits, symbol)
    if fib is None:
        return True, None, None  # Fib disabled atau data kurang → skip filter

    hit = fib.nearest_entry_level(price, point)
    if hit is None:
        # Log level terdekat untuk debugging
        buffer = settings.fib_confluence_buffer * point
        closest = min(
            fib.retracements.items(),
            key=lambda x: abs(price - x[1])
        )
        dist = int(abs(price - closest[1]) / point)
        logger.info(
            f"[NO TRADE][{symbol}] Tidak di level Fibonacci "
            f"(price={price:.{digits}f}, "
            f"nearest Fib {closest[0]*100:.1f}%={closest[1]:.{digits}f}, "
            f"jarak={dist} pts, buffer={int(buffer/point)} pts)"
        )
        return False, None, fib

    level, fib_price = hit
    fib_tp = fib.best_tp_extension(price, point)

    logger.info(
        f"[FIB][{symbol}] Konfluensi! {direction.value} di Fib {level*100:.1f}% "
        f"= {fib_price:.{digits}f} (price={price:.{digits}f})"
        + (f" → FibTP={fib_tp:.{digits}f}" if fib_tp else "")
    )
    return True, fib_tp, fib
