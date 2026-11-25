from __future__ import annotations
from datetime import date
import pandas as pd
from .pricing import black_scholes, years_to_expiry
from .scenarios import build_spot_grid, future_iv, future_date

def calculate_strategy(legs, spots: dict[str, float], valuation_date: date,
                       low_pct=-5, high_pct=5, step_pct=0.5, days_forward=0,
                       iv_mode="Unchanged", iv_change=0.0, risk_free_rate=0.065, dividends=None):
    dividends = dividends or {}
    grid = build_spot_grid(low_pct, high_pct, step_pct)
    fdate = future_date(valuation_date, days_forward)
    for leg in legs:
        leg.validate()
        if leg.underlying not in spots: raise ValueError(f"Missing spot for {leg.underlying}")
    rows = []
    for move in grid:
        total = 0.0
        for idx, leg in enumerate(legs, start=1):
            future_spot = float(spots[leg.underlying]) * (1.0 + move / 100.0)
            q = float(dividends.get(leg.underlying, 0.0))
            tf = years_to_expiry(leg.expiry, fdate)
            ivf = future_iv(leg.iv, iv_mode, iv_change)
            value = black_scholes(future_spot, leg.strike, tf, ivf, risk_free_rate, q, leg.option_type).price
            pnl = leg.side * (value - leg.entry_price) * leg.quantity * leg.multiplier
            total += pnl
            rows.append({"move_pct": move, "leg": idx, "underlying": leg.underlying, "option_type": leg.option_type,
                         "position": leg.position, "strike": leg.strike, "future_spot": future_spot,
                         "future_iv": ivf, "future_value": value, "pnl": pnl, "fyers_symbol": leg.fyers_symbol,
                         "future_date": fdate})
        rows.append({"move_pct": move, "leg": "TOTAL", "underlying": "COMBINED", "option_type": "", "position": "",
                     "strike": 0, "future_spot": 0, "future_iv": 0, "future_value": 0, "pnl": total,
                     "fyers_symbol": "", "future_date": fdate})
    detail = pd.DataFrame(rows)
    return detail[detail["leg"] == "TOTAL"][["move_pct", "pnl"]].reset_index(drop=True), detail

def current_leg_analytics(legs, spots, valuation_date, risk_free_rate=0.065, dividends=None):
    dividends = dividends or {}
    rows = []
    for i, leg in enumerate(legs, 1):
        spot = float(spots[leg.underlying]); q = float(dividends.get(leg.underlying, 0.0))
        t = years_to_expiry(leg.expiry, valuation_date)
        val = black_scholes(spot, leg.strike, t, leg.iv, risk_free_rate, q, leg.option_type)
        mult = leg.quantity * leg.multiplier * leg.side
        rows.append({"Leg": i, "Underlying": leg.underlying, "Type": leg.option_type, "Position": leg.position,
                     "Strike": leg.strike, "Qty": leg.quantity, "Entry": leg.entry_price, "Theo": val.price,
                     "Delta": val.greeks.delta * mult, "Gamma": val.greeks.gamma * mult,
                     "Theta/yr": val.greeks.theta * mult, "Vega": val.greeks.vega * mult, "Rho": val.greeks.rho * mult,
                     "IV %": leg.iv * 100, "FYERS Symbol": leg.fyers_symbol})
    return pd.DataFrame(rows)
