"""
Performance Tracker — rekam setiap trade dan generate laporan mingguan
Data disimpan di data/trade_log.json dan data/daily_stats.json
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import List, Optional
from loguru import logger

DATA_DIR = Path(__file__).parent.parent / "data"
TRADE_LOG_FILE = DATA_DIR / "trade_log.json"
DAILY_STATS_FILE = DATA_DIR / "daily_stats.json"

WIB = timezone(timedelta(hours=7))
_lock = threading.Lock()


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> list | dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return [] if "trade" in path.name else {}


def _save_json(path: Path, data) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


# ── Trade Recording ───────────────────────────────────────────────────────────

def record_trade(
    symbol: str,
    direction: str,
    lot: float,
    entry_price: float,
    exit_price: float,
    sl: float,
    tp: float,
    profit: float,
    comment: str = "",
) -> None:
    """Dipanggil saat posisi ditutup."""
    now_wib = datetime.now(WIB)
    trade = {
        "date": now_wib.strftime("%Y-%m-%d"),
        "time": now_wib.strftime("%H:%M:%S"),
        "symbol": symbol,
        "direction": direction,
        "lot": lot,
        "entry": entry_price,
        "exit": exit_price,
        "sl": sl,
        "tp": tp,
        "profit": round(profit, 2),
        "result": "WIN" if profit >= 0 else "LOSS",
        "comment": comment,
    }

    with _lock:
        trades = _load_json(TRADE_LOG_FILE)
        trades.append(trade)
        _save_json(TRADE_LOG_FILE, trades)

    _update_daily_stats(trade)
    logger.info(
        f"[PERF] Trade recorded: {direction} {symbol} "
        f"profit={profit:+.2f} ({trade['result']})"
    )


def _update_daily_stats(trade: dict) -> None:
    today = trade["date"]
    with _lock:
        stats = _load_json(DAILY_STATS_FILE)
        if today not in stats:
            stats[today] = {
                "date": today,
                "trades": 0, "wins": 0, "losses": 0,
                "gross_profit": 0.0, "gross_loss": 0.0,
                "net_pnl": 0.0, "max_win": 0.0, "max_loss": 0.0,
            }
        d = stats[today]
        d["trades"] += 1
        d["net_pnl"] = round(d["net_pnl"] + trade["profit"], 2)
        if trade["profit"] >= 0:
            d["wins"] += 1
            d["gross_profit"] = round(d["gross_profit"] + trade["profit"], 2)
            d["max_win"] = max(d["max_win"], trade["profit"])
        else:
            d["losses"] += 1
            d["gross_loss"] = round(d["gross_loss"] + trade["profit"], 2)
            d["max_loss"] = min(d["max_loss"], trade["profit"])
        _save_json(DAILY_STATS_FILE, stats)


# ── Report Generation ─────────────────────────────────────────────────────────

def generate_weekly_report(weeks_back: int = 1) -> dict:
    """Generate laporan 7 hari terakhir."""
    now = datetime.now(WIB)
    start = (now - timedelta(days=7 * weeks_back)).date()
    end = now.date()

    with _lock:
        all_stats: dict = _load_json(DAILY_STATS_FILE)
        all_trades: list = _load_json(TRADE_LOG_FILE)

    # Filter ke rentang tanggal
    daily = {
        k: v for k, v in all_stats.items()
        if start <= date.fromisoformat(k) <= end
    }
    trades = [
        t for t in all_trades
        if start <= date.fromisoformat(t["date"]) <= end
    ]

    if not trades:
        return {"error": "Belum ada data trade dalam periode ini"}

    # Aggregate
    total_trades = sum(d["trades"] for d in daily.values())
    total_wins = sum(d["wins"] for d in daily.values())
    total_losses = sum(d["losses"] for d in daily.values())
    net_pnl = sum(d["net_pnl"] for d in daily.values())
    gross_profit = sum(d["gross_profit"] for d in daily.values())
    gross_loss = sum(d["gross_loss"] for d in daily.values())
    win_rate = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0

    # Profit factor
    pf = round(gross_profit / abs(gross_loss), 2) if gross_loss != 0 else 0

    # Max drawdown (daily)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for d in sorted(daily.values(), key=lambda x: x["date"]):
        cumulative += d["net_pnl"]
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    # Consecutive losses max
    max_consec_loss = 0
    cur_consec = 0
    for t in sorted(trades, key=lambda x: x["date"] + x["time"]):
        if t["result"] == "LOSS":
            cur_consec += 1
            max_consec_loss = max(max_consec_loss, cur_consec)
        else:
            cur_consec = 0

    # Verdict
    verdict, verdict_reason = _assess_stability(
        win_rate, pf, max_dd, net_pnl, total_trades, max_consec_loss
    )

    return {
        "period": f"{start} s/d {end}",
        "generated_at": now.strftime("%Y-%m-%d %H:%M WIB"),
        "summary": {
            "total_trades": total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate_pct": win_rate,
            "net_pnl": round(net_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": pf,
            "max_drawdown": round(max_dd, 2),
            "max_consecutive_losses": max_consec_loss,
        },
        "daily": sorted(daily.values(), key=lambda x: x["date"]),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }


def _assess_stability(
    win_rate: float,
    profit_factor: float,
    max_dd: float,
    net_pnl: float,
    total_trades: int,
    max_consec_loss: int,
) -> tuple[str, str]:
    issues = []
    passed = []

    if total_trades < 20:
        issues.append(f"Terlalu sedikit trade ({total_trades} < 20 minimal)")
    else:
        passed.append(f"Sample cukup ({total_trades} trades)")

    if win_rate >= 50:
        passed.append(f"Win rate {win_rate}% ✓")
    else:
        issues.append(f"Win rate rendah ({win_rate}% < 50%)")

    if profit_factor >= 1.5:
        passed.append(f"Profit factor {profit_factor} ✓")
    elif profit_factor >= 1.0:
        issues.append(f"Profit factor cukup tapi lemah ({profit_factor}, target ≥1.5)")
    else:
        issues.append(f"Profit factor buruk ({profit_factor} < 1.0) — bot rugi")

    if net_pnl > 0:
        passed.append(f"Net PnL positif +{net_pnl:.2f} ✓")
    else:
        issues.append(f"Net PnL negatif ({net_pnl:.2f})")

    if max_consec_loss <= 3:
        passed.append(f"Max consecutive loss {max_consec_loss} ✓")
    else:
        issues.append(f"Consecutive loss tinggi ({max_consec_loss}x berturut-turut)")

    if not issues:
        verdict = "✅ SIAP DIGUNAKAN"
        reason = " | ".join(passed)
    elif len(issues) <= 1 and net_pnl > 0:
        verdict = "⚠️ PERLU OBSERVASI LEBIH LANJUT"
        reason = f"Lulus: {', '.join(passed)} | Perhatikan: {', '.join(issues)}"
    else:
        verdict = "❌ BELUM SIAP — PERLU PERBAIKAN"
        reason = f"Masalah: {' | '.join(issues)}"

    return verdict, reason


def get_today_summary() -> dict:
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    with _lock:
        stats = _load_json(DAILY_STATS_FILE)
    d = stats.get(today, {})
    if not d:
        return {"date": today, "message": "Belum ada trade hari ini"}
    total = d["trades"]
    win_rate = round(d["wins"] / total * 100, 1) if total > 0 else 0
    return {
        "date": today,
        "trades": total,
        "wins": d["wins"],
        "losses": d["losses"],
        "win_rate_pct": win_rate,
        "net_pnl": d["net_pnl"],
        "max_win": d["max_win"],
        "max_loss": d["max_loss"],
    }
