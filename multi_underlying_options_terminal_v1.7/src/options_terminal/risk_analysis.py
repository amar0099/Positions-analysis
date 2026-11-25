from __future__ import annotations

from datetime import date
import numpy as np
import pandas as pd

from .pricing import black_scholes


def _expiry_date(value):
    text = str(value).strip()
    parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        raise ValueError(f"Invalid expiry date: {value}")
    return parsed.date()


def position_expiry_risk(legs: pd.DataFrame) -> pd.DataFrame:
    """Standalone single-leg expiry bounds, using quantity in units (not lots).

    This is deliberately per leg. It does not net offsets across a multi-leg
    strategy because the source position file has no reliable strategy-group ID.
    """
    rows = []
    for _, leg in legs.iterrows():
        qty = abs(float(leg["Quantity"]))
        premium = max(float(leg["EntryPrice"]), 0.0)
        strike = float(leg["Strike"])
        kind = str(leg["OptionType"]).upper()
        side = str(leg["Position"]).upper()
        if kind not in {"CE", "PE"} or side not in {"BUY", "SELL"}:
            raise ValueError("OptionType must be CE/PE and Position must be BUY/SELL")

        if side == "BUY":
            max_loss = premium * qty
            max_profit = np.inf if kind == "CE" else max((strike - premium) * qty, 0.0)
        else:
            max_profit = premium * qty
            if kind == "CE":
                max_loss = np.inf
            else:
                max_loss = max((strike - premium) * qty, 0.0)

        rows.append({
            "Underlying": leg.get("Underlying", ""),
            "Expiry": leg.get("Expiry", ""),
            "OptionType": kind,
            "Position": side,
            "Strike": strike,
            "Quantity": qty,
            "EntryPrice": premium,
            "MaxLoss": max_loss,
            "MaxLossLabel": "Unlimited" if np.isinf(max_loss) else max_loss,
            "MaxProfit": max_profit,
            "MaxProfitLabel": "Unlimited" if np.isinf(max_profit) else max_profit,
        })
    return pd.DataFrame(rows)


def stress_test_portfolio(
    legs: pd.DataFrame,
    shocks: list[dict],
    valuation_date: date,
    rate: float = 0.0,
    dividend: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reprice each option for named spot/IV/time shocks.

    Each shock can define a common spot_pct or an underlying-specific
    spot_moves mapping, plus an additive iv_points change and days_forward.
    P&L is measured against the imported entry premium, consistent with the
    terminal's scenario P&L convention.
    """
    portfolio_rows = []
    leg_rows = []
    for shock in shocks:
        name = str(shock["name"])
        common_move = float(shock.get("spot_pct", 0.0))
        spot_moves = shock.get("spot_moves", {}) or {}
        iv_points = float(shock.get("iv_points", 0.0))
        days_forward = int(shock.get("days_forward", 0))
        if days_forward < 0:
            raise ValueError("days_forward cannot be negative")

        total = 0.0
        for idx, leg in legs.iterrows():
            underlying = str(leg["Underlying"])
            move = float(spot_moves.get(underlying, common_move))
            spot = float(leg["Spot"]) * (1.0 + move / 100.0)
            if spot <= 0:
                raise ValueError(f"Shock {name} makes {underlying} spot non-positive")
            expiry = _expiry_date(leg["Expiry"])
            remaining_days = max((expiry - valuation_date).days - days_forward, 0)
            t_years = remaining_days / 365.0
            iv = max(float(leg["IV"]) + iv_points / 100.0, 1e-6)
            option_type = str(leg["OptionType"]).upper()
            side = 1.0 if str(leg["Position"]).upper() == "BUY" else -1.0
            quantity = abs(float(leg["Quantity"]))
            entry = float(leg["EntryPrice"])
            future_price = black_scholes(
                spot, float(leg["Strike"]), t_years, iv, rate, dividend, option_type
            ).price
            pnl = (future_price - entry) * quantity * side
            total += pnl
            leg_rows.append({
                "Scenario": name,
                "Underlying": underlying,
                "Expiry": leg.get("Expiry", ""),
                "OptionType": option_type,
                "Position": str(leg["Position"]).upper(),
                "Strike": float(leg["Strike"]),
                "Quantity": quantity,
                "SpotShockPct": move,
                "IVShockPoints": iv_points,
                "DaysForward": days_forward,
                "ModelPrice": future_price,
                "PnL": pnl,
            })
        portfolio_rows.append({
            "Scenario": name,
            "PortfolioPnL": total,
            "SpotShockPct": common_move,
            "IVShockPoints": iv_points,
            "DaysForward": days_forward,
        })
    return pd.DataFrame(portfolio_rows), pd.DataFrame(leg_rows)
