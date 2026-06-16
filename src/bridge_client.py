"""
HTTP client for querying the bridge server from external tools/scripts.
"""
import httpx
from typing import Optional
from .config import settings


BASE_URL = f"http://127.0.0.1:{settings.bridge_port}"


def _get(path: str, params: dict = None) -> dict:
    with httpx.Client(timeout=5) as client:
        resp = client.get(f"{BASE_URL}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


def get_account() -> dict:
    return _get("/account")


def get_tick(symbol: str) -> dict:
    return _get("/tick", {"symbol": symbol})


def get_symbol_meta(symbol: str) -> dict:
    return _get("/symbol-meta", {"symbol": symbol})


def get_positions_count(symbol: str) -> dict:
    return _get("/positions-count", {"symbol": symbol})


def get_positions_count_total() -> dict:
    return _get("/positions-count-total")


def get_today_pnl() -> dict:
    return _get("/today-pnl")


def get_today_loss_count() -> dict:
    return _get("/today-loss-count")


def get_rates(symbol: str, timeframe: str, bars: int = 300) -> dict:
    return _get("/rates", {"symbol": symbol, "timeframe": timeframe, "bars": bars})


def health() -> dict:
    return _get("/health")
