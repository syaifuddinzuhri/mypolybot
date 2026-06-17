from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeSignal(BaseModel):
    symbol: str
    direction: Direction
    lot: float
    sl: float
    tp: float
    comment: str = ""


class RateBar(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class TickData(BaseModel):
    symbol: str
    bid: float
    ask: float
    time: int


class SymbolMeta(BaseModel):
    symbol: str
    digits: int
    point: float
    contract_size: float
    spread: int


class AccountInfo(BaseModel):
    login: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    profit: float
    currency: str
    broker: str = ""
    server: str = ""
    leverage: int = 0
    account_type: str = ""
    margin_level: float = 0.0


class Position(BaseModel):
    ticket: int
    symbol: str
    type: str
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    comment: str


class SRZone(BaseModel):
    low: float
    high: float
    strength: int
    zone_type: str  # "support" | "resistance"


class EACommand(BaseModel):
    action: str  # "BUY" | "SELL" | "CLOSE" | "CLOSE_ALL"
    symbol: str
    lot: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    ticket: Optional[int] = None
    comment: Optional[str] = ""


class EARatesPayload(BaseModel):
    symbol: str
    timeframe: str
    bars: List[RateBar]
    tick: TickData
    meta: SymbolMeta
    account: AccountInfo
    positions: List[Position]
