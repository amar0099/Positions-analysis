# Multi-Underlying Options Payoff & Scenario Terminal v0.4.0

Streamlit options analytics terminal for mixed-underlying NIFTY/SENSEX portfolios.

## What is implemented

- Broker CSV auto-mapping from header names.
- Open positions only: `Net Position != 0`.
- `Net Position` is actual quantity/units, not lots.
- BUY/SELL from the sign of Net Position.
- `BEP` is the entry price.
- Lots are display-only: `abs(Net Position) / Lot Size`.
- Automatic weekly/monthly FYERS symbol resolution using FYERS expiry data.
- FYERS authentication/token flow from the supplied reference project.
- FYERS Quotes API batching.
- Live NIFTY/SENSEX spot and option quotes.
- Option-chain retrieval for open-position expiries.
- IV solved from live option LTP when IV is not directly returned.
- Greeks from FYERS payload when available, otherwise Black-Scholes Greeks.
- Spot scenario engine across mixed underlyings.
- Days-forward repricing using each leg's actual expiry.
- Days-forward maximum capped at the first upcoming expiry in the open-position book.
- IV scenarios: unchanged, lower, higher, custom relative %, custom IV points.
- IV-surface mode using strike/moneyness interpolation from loaded option-chain data.
- Payoff chart and scenario matrix.
- Portfolio Greek aggregation.
- Risk Monitor with underlying, expiry and CE/PE concentration views.
- Configurable warning thresholds for net Delta, Gamma, Theta and Vega.
- Configurable scenario-loss warning against the currently displayed scenario range.
- Risk alerts for concentration and breached Greek/scenario thresholds.
- Clear separation between gross option premium value and broker margin/notional exposure.
- Strategy JSON save/load.
- Historical validation upload with MAE/RMSE.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## FYERS secrets

Local `.streamlit/secrets.toml` or Streamlit Cloud secrets:

```toml
FYERS_CLIENT_ID = "YOUR_CLIENT_ID-100"
FYERS_SECRET_KEY = "YOUR_SECRET_KEY"
FYERS_USERNAME = "YOUR_FYERS_USER_ID"
FYERS_PIN = "YOUR_PIN"
FYERS_TOTP_KEY = "YOUR_TOTP_SECRET"
```

Do not put credentials in the CSV, JSON strategy file, or source code.

## Historical validation CSV

Required columns (aliases are supported):

- Spot
- Strike
- Expiry
- OptionType
- MarketPrice / LTP

Optional:

- IV

The validator calculates theoretical price and reports absolute error, MAE and RMSE.

## Risk Monitor notes

The Risk Monitor uses gross live option premium value (`abs(LTP) × quantity`) as its concentration proxy. It is not SPAN/Exposure margin, maximum loss, or underlying notional exposure. Greek and scenario thresholds are user-configurable alert levels, not exchange or broker limits. This version does not calculate portfolio VaR, liquidity/slippage risk, or intraday peak exposure. Refresh FYERS quotes before relying on the displayed risk figures.


## Version 1.2 — local stress testing and expiry risk

The dashboard now includes:
- Joint market stress scenarios: flat/time decay, sell-offs, crash/volatility spike, and rallies.
- Isolated underlying shocks, so NIFTY and SENSEX can be stressed independently.
- Editable base spot shock, IV shock (volatility points), and forward-day assumptions.
- Scenario-level portfolio P&L and a drill-down showing each leg's contribution.
- Standalone position-level expiry maximum loss and maximum profit.

Risk interpretation:
- Stress results are Black-Scholes model estimates using imported positions, current quote-derived spot/IV, and the configured shocks. They are not guaranteed exit prices.
- Long-option maximum loss is the entry premium paid times quantity.
- A short call has theoretically unlimited standalone expiry loss.
- A short put has a bounded standalone expiry loss.
- Individual leg limits do not net the protection of other legs. Combined strategy max loss requires reliable strategy grouping and is not inferred from rows that are not explicitly grouped.
- These calculations do not require broker margin data and do not calculate SPAN/Exposure margin.


## Portfolio MTM and position views (v1.3 update)

- Broker CSV import uses **Net Position** as the only source for current position quantity. It does not fall back to Net Position CF, Net Qty, Net Quantity, or generic Quantity.
- CSV rows with Net Position = 0 are retained in the Overall Positions book. Non-zero rows feed the Open Positions Only live analytics.
- The common Portfolio MTM is the sum of the broker CSV **MTM** column across all imported rows. Open and closed MTM subtotals are also shown.
- Forward scenario analysis, live Greeks, stress testing, and live exposure are calculated from open positions only. Closed positions have no current live exposure and are not assigned artificial Greeks.
- The Overall Positions and Open Positions Only tabs are provided for portfolio P&L, position book, broker MTM attribution, scenario, stress, Greeks, and risk-monitor sections. Overall tabs show the all-row broker P&L context; forward risk calculations remain in the open-only tab.
- Active FYERS credentials and access tokens are intentionally not included in this distributable ZIP. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and enter your own credentials; obtain a fresh token in the app.
