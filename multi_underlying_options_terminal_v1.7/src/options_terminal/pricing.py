from __future__ import annotations
from dataclasses import dataclass
from math import exp, log, pi, sqrt
from scipy.stats import norm

TRADING_DAYS = 365.0

@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

@dataclass(frozen=True)
class OptionValue:
    price: float
    greeks: Greeks


def _expiry_value(spot, strike, option_type):
    return max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)


def black_scholes(spot: float, strike: float, t_years: float, vol: float,
                  rate: float = 0.0, dividend: float = 0.0,
                  option_type: str = "CE") -> OptionValue:
    option_type = option_type.upper()
    if option_type not in {"CE", "PE"}:
        raise ValueError("option_type must be CE or PE")
    if spot <= 0 or strike <= 0:
        raise ValueError("Spot and strike must be positive")
    if t_years <= 0 or vol <= 0:
        price = _expiry_value(spot, strike, option_type)
        delta = (1.0 if spot > strike else 0.0) if option_type == "CE" else (-1.0 if spot < strike else 0.0)
        return OptionValue(price, Greeks(delta, 0.0, 0.0, 0.0, 0.0))
    sqrt_t = sqrt(t_years)
    d1 = (log(spot / strike) + (rate - dividend + 0.5 * vol**2) * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    df_q = exp(-dividend * t_years)
    df_r = exp(-rate * t_years)
    pdf = exp(-0.5 * d1 * d1) / sqrt(2 * pi)
    if option_type == "CE":
        price = spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2)
        delta = df_q * norm.cdf(d1)
        theta = (-(spot * df_q * pdf * vol) / (2 * sqrt_t) - rate * strike * df_r * norm.cdf(d2) + dividend * spot * df_q * norm.cdf(d1))
        rho = strike * t_years * df_r * norm.cdf(d2)
    else:
        price = strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1)
        delta = df_q * (norm.cdf(d1) - 1)
        theta = (-(spot * df_q * pdf * vol) / (2 * sqrt_t) + rate * strike * df_r * norm.cdf(-d2) - dividend * spot * df_q * norm.cdf(-d1))
        rho = -strike * t_years * df_r * norm.cdf(-d2)
    gamma = df_q * pdf / (spot * vol * sqrt_t)
    vega = spot * df_q * pdf * sqrt_t
    return OptionValue(float(price), Greeks(float(delta), float(gamma), float(theta), float(vega), float(rho)))


def years_to_expiry(expiry, valuation_date):
    return max((expiry - valuation_date).days, 0) / TRADING_DAYS


def implied_volatility(market_price: float, spot: float, strike: float, t_years: float,
                       rate: float = 0.0, dividend: float = 0.0, option_type: str = "CE",
                       low: float = 1e-6, high: float = 5.0, iterations: int = 100) -> float | None:
    if market_price <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return None
    intrinsic = _expiry_value(spot, strike, option_type)
    if market_price < intrinsic - 1e-8:
        return None
    p_low = black_scholes(spot, strike, t_years, low, rate, dividend, option_type).price
    p_high = black_scholes(spot, strike, t_years, high, rate, dividend, option_type).price
    if market_price < p_low - 1e-8 or market_price > p_high + 1e-8:
        return None
    lo, hi = low, high
    for _ in range(iterations):
        mid = (lo + hi) / 2
        p = black_scholes(spot, strike, t_years, mid, rate, dividend, option_type).price
        if p < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
