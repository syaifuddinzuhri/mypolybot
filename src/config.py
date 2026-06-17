from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    bridge_host: str = "0.0.0.0"
    bridge_port: int = 47302

    symbols: str = "XAUUSD,BTCUSD"
    lot_size: float = 0.02
    daily_loss_enabled: bool = True    # enable/disable daily loss hard stop
    daily_loss_percent: float = 3.0   # hard stop dinamis: X% dari balance
    max_positions_per_symbol: int = 3
    max_total_positions: int = 10

    sr_timeframe: str = "M15"
    sr_bars: int = 300
    sr_zone_threshold_points: int = 30
    sr_zone_merge_points: int = 50

    risk_percent: float = 1.0
    max_spread_points: int = 50
    min_rr_ratio: float = 1.5   # TP minimal 1.5x jarak SL

    # Trend Filter
    ema_fast: int = 20
    ema_slow: int = 50
    min_candle_body_points: int = 30   # abaikan doji/candle kecil

    # Entry Interval — jeda minimum antar entry per symbol
    entry_interval_seconds: int = 300  # default 5 menit

    # Pyramid Entry
    pyramid_enabled: bool = True
    pyramid_max_levels: int = 2          # max berapa kali tambah posisi
    pyramid_trigger_points: int = 200    # profit (points) sebelum boleh pyramid
    pyramid_lot_multiplier: float = 0.5  # lot pyramid = lot awal x multiplier
    pyramid_min_lot: float = 0.01        # lot minimum pyramid

    # Trailing SL
    trailing_sl_enabled: bool = True
    trailing_sl_points: int = 150        # jarak SL dari harga saat ini
    trailing_sl_step_points: int = 10    # minimum gerak sebelum update SL

    # Break Even
    break_even_enabled: bool = True
    break_even_trigger_points: int = 100  # profit (points) sebelum geser SL ke entry
    break_even_buffer_points: int = 5     # SL = entry + buffer

    # Partial Close
    partial_close_enabled: bool = True
    partial_close_trigger_points: int = 150  # profit (points) untuk trigger partial close
    partial_close_ratio: float = 0.5         # tutup 50% posisi

    # Close End of Day
    eod_close_enabled: bool = True
    eod_hour: int = 23          # jam tutup (server time)
    eod_minute: int = 50        # menit tutup

    # Loss Cooldown
    loss_cooldown_enabled: bool = True
    loss_cooldown_trigger: int = 2        # jumlah loss berturut-turut sebelum cooldown
    loss_cooldown_minutes: int = 15       # durasi cooldown (menit)

    # Fibonacci
    fib_enabled: bool = True
    fib_swing_lookback: int = 50        # bar untuk cari swing high/low
    fib_entry_levels: str = "0.382,0.5,0.618"   # level entry yang valid
    fib_tp_extension: str = "1.272,1.618"        # level TP extension
    fib_confluence_buffer: int = 150    # toleransi harga ke level Fib (points)

    # Session Filter
    session_filter_enabled: bool = True
    session_open_hour: int = 14
    session_close_hour: int = 20
    session_auto: bool = True   # otomatis pilih sesi berdasarkan jam WIB sekarang

    # News Filter — Economic Calendar (ForexFactory)
    news_filter_enabled: bool = True
    news_blackout_before_min: int = 15  # pause N menit sebelum news
    news_blackout_after_min: int = 15   # pause N menit setelah news

    # DXY Filter — kekuatan USD vs arah Gold
    dxy_filter_enabled: bool = True
    dxy_refresh_seconds: int = 300      # refresh DXY tiap 5 menit
    dxy_trend_threshold: float = 0.15   # selisih DXY vs EMA untuk dianggap "trending"

    # SL Buffer
    sl_atr_multiplier: float = 2.0   # buffer = ATR × multiplier
    sl_min_points: int = 4000        # minimum buffer dalam points (×point = nilai $)

    # Multi-TP SL Management
    multi_tp_enabled: bool = True
    tp1_rr: float = 1.0   # TP1 di 1:1 → geser SL ke Break Even
    tp2_rr: float = 2.0   # TP2 di 1:2 → geser SL ke +1x SL (lock profit)

    # Telegram Notification
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def symbol_list(self) -> List[str]:
        return [s.strip() for s in self.symbols.split(",")]


settings = Settings()
