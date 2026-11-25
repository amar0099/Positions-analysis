from __future__ import annotations
from datetime import date, timedelta
import numpy as np

def build_spot_grid(low_pct: float, high_pct: float, step_pct: float):
    if step_pct <= 0:
        raise ValueError("Step must be positive")
    if low_pct >= high_pct:
        raise ValueError("Low movement must be less than high movement")
    n = int(np.floor((high_pct - low_pct) / step_pct + 1e-9))
    values = [low_pct + i * step_pct for i in range(n + 1)]
    if values[-1] < high_pct - 1e-9:
        values.append(high_pct)
    return np.array(values, dtype=float)

def future_iv(base_iv: float, mode: str, change: float) -> float:
    if mode == "Unchanged": result = base_iv
    elif mode == "Absolute points": result = base_iv + change / 100.0
    elif mode == "Relative %": result = base_iv * (1.0 + change / 100.0)
    else: raise ValueError(f"Unknown IV scenario: {mode}")
    return max(result, 1e-6)

def future_date(valuation_date: date, days_forward: int) -> date:
    if days_forward < 0: raise ValueError("Days forward cannot be negative")
    return valuation_date + timedelta(days=days_forward)
