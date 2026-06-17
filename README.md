# Polybot — Automated Gold Trading Bot

Bot trading otomatis untuk XAUUSDm (Gold) menggunakan MetaTrader 5 via HTTP Bridge EA.

---

## Arsitektur

```
MetaTrader 5 (EA)
     │  HTTP POST /ea/rates (setiap tick)
     ▼
FastAPI Bridge Server (Python)  ←→  Dashboard HTML
     │
     ├── Strategy (EMA50 Band)
     ├── Risk Manager
     ├── Trade Manager (Session)
     └── Performance Tracker
```

- **EA (MQL5)** — mengirim data tick + bars ke Python, menerima perintah order
- **Python (FastAPI)** — analisa, keputusan entry, manajemen risiko
- **Dashboard** — monitoring realtime via browser

---

## Strategi: EMA50 High/Low Band

### Konsep
- **Band Atas** = EMA50 dari candle High
- **Band Bawah** = EMA50 dari candle Low
- Harga bergerak di antara band = zona netral

### Deteksi Trend (Majority Vote 5 Candle)
- ≥ 3 dari 5 candle terakhir close **di atas band atas** → Uptrend (BUY)
- ≥ 3 dari 5 candle terakhir close **di bawah band bawah** → Downtrend (SELL)

### Entry Signal (2 Setup per Arah)

**BUY:**
| Setup | Kondisi |
|---|---|
| Breakout | `close > EMA_High` + candle bullish |
| Pullback | `low <= EMA_High` + `close >= EMA_Low` + bullish |

**SELL:**
| Setup | Kondisi |
|---|---|
| Breakout | `close < EMA_Low` + candle bearish |
| Pullback | `high >= EMA_High` + `close <= EMA_High` + bearish |

### SL / TP
- **SL** = ujung wick + buffer (`max(ATR × 0.3, 30 pts)`)
- **TP** = SL distance × `MIN_RR_RATIO` (default 2.0 → RR 1:2)
- Breakeven matematika: win rate 34% sudah profit dengan RR 1:2

---

## Filter & Proteksi

| Filter | Nilai Default | Keterangan |
|---|---|---|
| Spread | ≤ 50 pts | Block entry jika spread lebar (Asia session) |
| Session | Auto (London + NY) | 14:00–24:00 WIB aktif, Asia diblok |
| Daily Loss | 3% dari balance | Hard stop dinamis berdasarkan saldo saat ini |
| Cooldown | 2 loss → 60 menit | Pause setelah 2 loss berturut-turut |
| Entry Interval | 60 detik | Jeda minimum antar entry per symbol |
| Max Positions | 1 per symbol | Tidak boleh double entry arah sama |
| Trailing SL | 150 pts | Otomatis geser SL mengikuti profit |
| Break Even | 80 pts profit | Geser SL ke harga entry + 5 pts |
| Partial Close | 120 pts profit | Tutup 50% posisi untuk amankan profit |
| EOD Close | 23:50 UTC | Tutup semua posisi sebelum hari berganti |

---

## Sesi Trading

| Sesi | Jam WIB | Spread | Status |
|---|---|---|---|
| Asia | 00:00–08:00 | 200–400 pts | ❌ Diblok otomatis |
| Pre-London | 08:00–14:00 | 100–280 pts | ❌ Spread masih lebar |
| **London** | **14:00–20:00** | **20–40 pts** | **✅ Terbaik** |
| **New York** | **20:00–24:00** | **20–40 pts** | **✅ Bagus** |

Mode **AUTO** — bot otomatis mengikuti sesi yang sedang berjalan.

---

## Konfigurasi (.env)

```env
# Trading
SYMBOLS=XAUUSDm
LOT_SIZE=0.01
MAX_POSITIONS_PER_SYMBOL=1
MAX_TOTAL_POSITIONS=1

# Risk
MAX_SPREAD_POINTS=50
MIN_RR_RATIO=2.0
DAILY_LOSS_PERCENT=3.0
ENTRY_INTERVAL_SECONDS=60

# EMA Band
EMA_SLOW=50

# Session
SESSION_FILTER_ENABLED=true
SESSION_AUTO=true

# Cooldown
LOSS_COOLDOWN_ENABLED=true
LOSS_COOLDOWN_TRIGGER=2
LOSS_COOLDOWN_MINUTES=60

# Trailing / BE / Partial
TRAILING_SL_ENABLED=true
TRAILING_SL_POINTS=150
BREAK_EVEN_ENABLED=true
BREAK_EVEN_TRIGGER_POINTS=80
PARTIAL_CLOSE_ENABLED=true
PARTIAL_CLOSE_TRIGGER_POINTS=120

# EOD
EOD_CLOSE_ENABLED=true
EOD_HOUR=23
EOD_MINUTE=50
```

---

## File Penting

```
polybot/
├── main.py                    # Entry point FastAPI
├── src/
│   ├── strategy.py            # EMA50 band + entry logic
│   ├── risk.py                # Spread, daily loss, cooldown, max positions
│   ├── trade_manager.py       # Session control, trailing SL, BE, partial close
│   ├── bot.py                 # Orchestrator: terima tick → analyze → order
│   ├── bridge_server.py       # FastAPI endpoints
│   ├── config.py              # Settings dari .env
│   ├── performance_tracker.py # Log & laporan trade
│   └── types.py               # Dataclass: RateBar, TradeSignal, dll
├── static/
│   └── dashboard.html         # Dashboard monitoring (browser)
├── PolybotBridgeEA_VPS.mq5   # EA untuk VPS/live
├── PolybotBridgeEA.mq5        # EA untuk lokal/dev
└── deploy/
    ├── deploy.sh              # git pull + pip install + restart service
    └── reset_data.sh          # Backup + reset data trade
```

---

## Dashboard

Buka browser ke `http://<VPS_IP>:47302`

| Tab | Isi |
|---|---|
| Live | Status akun, posisi, analisa pasar (band, ATR, spread) |
| Laporan | Win rate, P&L mingguan, drawdown |
| Trade Log | Riwayat semua trade |
| Sesi | Toggle auto/manual sesi, rekomendasi sesi |
| Probabilitas | Countdown sesi, peluang setup per sesi, statistik aktual |
| Filter | News calendar, DXY status |
| Terminal | Log realtime SSE |

---

## Deploy ke VPS

```bash
# Clone & setup pertama kali
git clone <repo> /var/www/mypolybot
cd /var/www/mypolybot
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # edit sesuai kebutuhan

# Update rutin
bash deploy/deploy.sh

# Reset data trade
bash deploy/reset_data.sh
```

---

## EA MetaTrader 5

1. Copy `PolybotBridgeEA_VPS.mq5` ke folder `Experts/` MT5
2. Compile di MetaEditor
3. Attach ke chart **XAUUSDm M15**
4. Set `BridgeURL = http://127.0.0.1:47302` (jika bot di VPS yang sama dengan MT5)
5. Pastikan **Allow WebRequest** di MT5 Settings → Expert Advisors

EA mengirim:
- Bars M15 (100 candle) + tick realtime → `/ea/rates`
- Poll perintah order → `/ea/command`
- Draw band EMA50 di chart (garis continuous OBJ_TREND)

---

## Probabilitas & Ekspektasi

Dengan RR 1:2:
- Break-even win rate = **34%** (1 dari 3 trade profit = tidak rugi)
- Target realistis = **38–42% win rate**
- London session: ~55% kemungkinan ada setup per hari

---

## Changelog

| Tanggal | Update |
|---|---|
| 2026-06-17 | Strategi awal SR Zone diganti ke EMA50 High/Low Band |
| 2026-06-17 | Entry: 2 setup (breakout + pullback) per arah BUY/SELL |
| 2026-06-17 | Trend detection: majority vote 5 candle (bukan 1 candle) |
| 2026-06-17 | Daily loss: dinamis 3% dari balance (bukan nilai static) |
| 2026-06-17 | Session control dashboard: auto/manual toggle + 4 preset sesi |
| 2026-06-17 | Tab Probabilitas: countdown sesi, peluang per sesi, statistik bot |
| 2026-06-17 | Spread log throttle: max 1 warning per 60 detik per symbol |
| 2026-06-17 | Config: `extra=ignore` agar .env lama tidak crash |
| 2026-06-17 | Deploy scripts: `deploy.sh` + `reset_data.sh` |
