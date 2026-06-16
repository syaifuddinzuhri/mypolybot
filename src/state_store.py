"""
Persist bot state ke file JSON agar survive restart.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger

STATE_FILE = Path(__file__).parent.parent / "data" / "state.json"


def _ensure_dir():
    STATE_FILE.parent.mkdir(exist_ok=True)


def save(state: dict) -> None:
    _ensure_dir()
    payload = {
        "today_pnl": state.get("today_pnl", 0.0),
        "today_loss_count": state.get("today_loss_count", 0),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    # Simpan cooldown state dari risk module
    from .risk import _cooldown
    cooldown_serializable = {}
    for symbol, data in _cooldown.items():
        cooldown_serializable[symbol] = {
            "consecutive_losses": data["consecutive_losses"],
            "cooldown_until": data["cooldown_until"].isoformat() if data["cooldown_until"] else None,
        }
    payload["cooldown"] = cooldown_serializable

    STATE_FILE.write_text(json.dumps(payload, indent=2))
    logger.debug(f"[STATE] Saved → {STATE_FILE}")


def load() -> dict:
    """Load state dari file. Return dict kosong jika file tidak ada."""
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text())

        # Validasi: cek apakah data dari hari yang sama
        saved_at_str = raw.get("saved_at")
        if saved_at_str:
            saved_at = datetime.fromisoformat(saved_at_str)
            now = datetime.now(timezone.utc)
            if saved_at.date() < now.date():
                logger.info("[STATE] State dari hari kemarin, reset today_pnl & loss_count")
                raw["today_pnl"] = 0.0
                raw["today_loss_count"] = 0
                raw["cooldown"] = {}

        return raw
    except Exception as e:
        logger.warning(f"[STATE] Gagal load state: {e}")
        return {}


def restore_cooldown(saved: dict) -> None:
    """Restore cooldown state ke risk module."""
    from .risk import _cooldown
    cooldown_data = saved.get("cooldown", {})
    for symbol, data in cooldown_data.items():
        until = None
        if data.get("cooldown_until"):
            until = datetime.fromisoformat(data["cooldown_until"])
            # Jika cooldown sudah expired, jangan restore
            if until < datetime.now(timezone.utc):
                until = None
                data["consecutive_losses"] = 0
        _cooldown[symbol] = {
            "consecutive_losses": data["consecutive_losses"],
            "cooldown_until": until,
        }
        if until:
            logger.warning(
                f"[STATE] Restored cooldown {symbol}: "
                f"{data['consecutive_losses']} losses, "
                f"aktif sampai {until.strftime('%H:%M:%S')} UTC"
            )
