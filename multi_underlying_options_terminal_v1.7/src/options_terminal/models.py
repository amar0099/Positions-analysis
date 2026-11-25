from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime

SUPPORTED_UNDERLYINGS = {"NIFTY", "SENSEX"}

@dataclass
class OptionLeg:
    underlying: str
    option_type: str
    position: str
    strike: float
    quantity: float
    entry_price: float
    expiry: date
    iv: float
    multiplier: float
    fyers_symbol: str = ""
    label: str = ""

    @property
    def side(self) -> int:
        return 1 if self.position.upper() == "BUY" else -1

    def validate(self) -> None:
        self.underlying = self.underlying.upper().strip()
        self.option_type = self.option_type.upper().strip()
        self.position = self.position.upper().strip()
        if self.underlying not in SUPPORTED_UNDERLYINGS:
            raise ValueError(f"Unsupported underlying: {self.underlying}")
        if self.option_type not in {"CE", "PE"}:
            raise ValueError(f"Option type must be CE or PE: {self.option_type}")
        if self.position not in {"BUY", "SELL"}:
            raise ValueError(f"Position must be BUY or SELL: {self.position}")
        if self.strike <= 0 or self.quantity <= 0 or self.multiplier <= 0:
            raise ValueError("Strike, quantity and multiplier must be positive")
        if self.entry_price < 0:
            raise ValueError("Entry price cannot be negative")
        if not 0 <= self.iv <= 5:
            raise ValueError("IV must be supplied as a decimal, e.g. 0.16 for 16%")
        if not self.fyers_symbol:
            raise ValueError("FYERS Symbol is required for live market-data mode")


def parse_expiry(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
