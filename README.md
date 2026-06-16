# Polybot — MT5 SR Zone Trading Bot

Trading bot berbasis Python yang terhubung ke MetaTrader 5 via HTTP bridge.
Strategy: Support/Resistance zone detection pada multi-timeframe.

## Arsitektur

```
MT5 (EA)  ──POST /ea/rates──►  Python Bridge Server  ──►  Strategy (SR Zone)
          ◄──GET /ea/command──                        ◄──  Risk Management
```

## Setup

### 1. Python

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env sesuai kebutuhan
python main.py
```

Server berjalan di `http://0.0.0.0:47302`

### 2. MetaTrader 5

1. Copy `PolybotBridgeEA.mq5` ke folder `MQL5/Experts/`
2. Compile di MetaEditor
3. Tambahkan URL bridge ke **Tools → Options → Expert Advisors → Allow WebRequest**:
   - `http://127.0.0.1:47302`
4. Pasang EA di chart symbol yang diinginkan (XAUUSD, BTCUSD, dst)
5. Set parameter:
   - `InpServerURL`: `http://127.0.0.1:47302`
   - `InpTF`: timeframe untuk SR (default M15)
   - `InpBars`: jumlah bar yang dikirim (default 300)

## Endpoints Bridge Server

| Method | Path | Deskripsi |
|--------|------|-----------|
| POST | `/ea/rates` | EA push data setiap tick |
| GET | `/ea/command` | EA poll perintah trade |
| POST | `/ea/symbol-meta` | EA registrasi symbol |
| GET | `/account` | Info akun |
| GET | `/tick?symbol=X` | Harga terkini |
| GET | `/symbol-meta?symbol=X` | Meta symbol |
| GET | `/positions-count?symbol=X` | Jumlah posisi per symbol |
| GET | `/positions-count-total` | Total posisi |
| GET | `/today-pnl` | PnL hari ini |
| GET | `/today-loss-count` | Jumlah loss hari ini |
| GET | `/health` | Status server |

## Konfigurasi (.env)

| Variable | Default | Keterangan |
|----------|---------|------------|
| `BRIDGE_PORT` | 47302 | Port HTTP server |
| `SYMBOLS` | XAUUSD,BTCUSD | Symbol yang ditrade |
| `LOT_SIZE` | 0.02 | Volume per trade |
| `MAX_DAILY_LOSS` | 100.0 | Batas loss harian (USD) |
| `MAX_POSITIONS_PER_SYMBOL` | 3 | Max posisi per symbol |
| `MAX_TOTAL_POSITIONS` | 10 | Max total posisi |
| `SR_TIMEFRAME` | M15 | Timeframe SR zone |
| `SR_BARS` | 300 | Jumlah bar analisis |
| `SR_ZONE_THRESHOLD_POINTS` | 30 | Toleransi entry ke zone |
| `SR_ZONE_MERGE_POINTS` | 50 | Jarak merge zone |
| `MAX_SPREAD_POINTS` | 50 | Max spread diizinkan |

## Log Output Contoh

```
[NO TRADE][BTCUSD] Price not in SR zone (dir=SELL, price=77451.420, zone=[77464.050, 77494.050], below zone by 1263 points)
[SIGNAL][XAUUSD] dir=BUY price=3345.100 zone=[3342.000, 3344.500] sl=3340.000 tp=3352.000
[BOT][XAUUSD] Queued BUY lot=0.02 sl=3340.0 tp=3352.0
```
