import json, os
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fyers_adapter import (
    get_fyers_client, build_symbol, build_symbol_candidates, fetch_quotes,
    fetch_expiry_map, fetch_option_chain, option_chain_rows, UNDERLYING_SYM
)
from pricing import bs_greeks, iv_from_quote_or_ltp, implied_vol, year_fraction, expiry_datetime
from src.options_terminal.risk_analysis import position_expiry_risk, stress_test_portfolio

st.set_page_config(page_title="Options Terminal | Portfolio Intelligence", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

# Institutional-style terminal theme. All analytics below continue to use the
# existing pricing, market-data and scenario engines.
st.markdown("""
<style>
:root { --ink:#e8edf5; --muted:#93a4b8; --panel:#111c2b; --line:#26364a; }
.stApp { background: #08111e; color: var(--ink); }
[data-testid="stHeader"] { background: rgba(8,17,30,.92); }
[data-testid="stSidebar"] { background: #0d1928; border-right: 1px solid #24344a; }
.block-container { padding-top: 1.25rem; padding-bottom: 2rem; max-width: 1600px; }
h1,h2,h3 { letter-spacing: -.025em; }
[data-testid="stMetric"] { background: linear-gradient(145deg,#142338,#101b2a); border:1px solid #293c54; padding:14px 16px; border-radius:12px; }
[data-testid="stMetricLabel"] { color:#9fb0c5; }
[data-testid="stMetricValue"] { color:#edf4ff; }
[data-testid="stMetricDelta"] { font-size:.82rem; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-color:#26384e; }
.stButton button, .stDownloadButton button { border-radius:8px; font-weight:600; }
[data-testid="stDataFrame"] { border:1px solid #26384e; border-radius:10px; overflow:hidden; }
hr { border-color:#26384e; }
.terminal-eyebrow { color:#79a9e8; text-transform:uppercase; letter-spacing:.16em; font-size:.72rem; font-weight:700; }
.terminal-title { font-size:1.8rem; font-weight:750; letter-spacing:-.04em; color:#f1f6ff; margin:0; }
.terminal-subtitle { color:#93a4b8; font-size:.88rem; margin-top:.25rem; }
.status-pill { display:inline-block; padding:5px 10px; border:1px solid #275d4d; background:#102c29; color:#67d6a7; border-radius:20px; font-size:.72rem; font-weight:700; }
</style>
""", unsafe_allow_html=True)
head_a, head_b = st.columns([4,1])
with head_a:
    st.markdown('<div class="terminal-eyebrow">Portfolio intelligence / derivatives</div><div class="terminal-title">OPTIONS TERMINAL</div><div class="terminal-subtitle">Multi-underlying position analysis · FYERS market data · scenario risk</div>', unsafe_allow_html=True)
with head_b:
    st.markdown('<div style="text-align:right;padding-top:16px"><span class="status-pill">● LIVE ANALYTICS</span></div>', unsafe_allow_html=True)
st.divider()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _norm_col(c):
    return " ".join(str(c).strip().lower().replace("_", " ").replace("-", " ").split())


def _find_col(columns, aliases, required=True):
    norm = {_norm_col(c): c for c in columns}
    for a in aliases:
        if _norm_col(a) in norm:
            return norm[_norm_col(a)]
    for c in columns:
        nc = _norm_col(c)
        for a in aliases:
            na = _norm_col(a)
            if na and (na in nc or nc in na):
                return c
    if required:
        raise ValueError("Could not identify required column. Tried: " + ", ".join(aliases))
    return None


def _num_series(s):
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.replace("₹", "", regex=False).str.strip(),
        errors="coerce",
    )


def _parse_date_value(x):
    if pd.isna(x):
        return pd.NaT
    v = str(x).strip()
    for dayfirst in (True, False):
        d = pd.to_datetime(v, errors="coerce", dayfirst=dayfirst)
        if pd.notna(d):
            return d
    return pd.NaT


def _safe_float(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _quote_ltp(v):
    if not isinstance(v, dict):
        return np.nan
    for k in ("lp", "ltp", "last_price"):
        x = _safe_float(v.get(k))
        if np.isfinite(x):
            return x
    return np.nan


def _quote_field(v, *keys):
    if not isinstance(v, dict):
        return np.nan
    for k in keys:
        x = _safe_float(v.get(k))
        if np.isfinite(x):
            return x
    return np.nan


def _greek_from_payload(payload, name):
    g = payload.get("greeks") if isinstance(payload, dict) else None
    if isinstance(g, dict):
        aliases = {
            "delta": ["delta"], "gamma": ["gamma"], "theta": ["theta"],
            "vega": ["vega"], "rho": ["rho"], "iv": ["iv", "implied_volatility"],
        }
        for k in aliases.get(name, [name]):
            x = _safe_float(g.get(k))
            if np.isfinite(x):
                return x / 100.0 if name == "iv" and x > 3 else x
    return np.nan


def _expiry_kind_map_from_fyers(fyers, underlyings):
    maps = {}
    for u in underlyings:
        ex = "BSE" if u in ("SENSEX", "BANKEX") else "NSE"
        maps[u] = fetch_expiry_map(ex, u)
    return maps


def _position_frame_from_csv(raw):
    COL = {
        "underlying": _find_col(raw.columns, ["Underlying", "Underlying Name", "Underlying Symbol"]),
        "expiry": _find_col(raw.columns, ["Expiry Date", "Expiry", "ExpiryDate"]),
        "option_type": _find_col(raw.columns, ["Scrip Type", "Option Type", "OptionType", "Instrument Type"]),
        "strike": _find_col(raw.columns, ["Strike Price", "Strike", "StrikePrice"]),
        # Net Position is the sole source of truth for current open quantity.
        "net_position": _find_col(raw.columns, ["Net Position"]),
        "broker_mtm": _find_col(raw.columns, ["MTM"], required=False),
        "bep": _find_col(raw.columns, ["BEP", "Break Even Price", "Average Price", "Avg Price", "Entry Price", "EntryPrice"]),
        "ltp": _find_col(raw.columns, ["LTP", "Last Traded Price"], required=False),
        "iv": _find_col(raw.columns, ["IV", "Implied Volatility"], required=False),
        "scrip_name": _find_col(raw.columns, ["Scrip Name", "Symbol", "Trading Symbol"], required=False),
        "exchange": _find_col(raw.columns, ["Exchange"], required=False),
        "lot_size": _find_col(raw.columns, ["Lot Size", "LotSize", "Market Lot", "Contract Lot"], required=False),
    }
    df = pd.DataFrame(index=raw.index)
    df["Underlying"] = raw[COL["underlying"]].astype(str).str.strip().str.upper()
    df["Expiry"] = raw[COL["expiry"]].map(_parse_date_value).dt.strftime("%d-%b-%Y")
    df["OptionType"] = raw[COL["option_type"]].astype(str).str.strip().str.upper().replace({"C": "CE", "P": "PE"})
    df["Strike"] = _num_series(raw[COL["strike"]])
    df["NetPosition"] = _num_series(raw[COL["net_position"]])
    # Broker-reported MTM includes both open and squared-off rows in the supplied export.
    df["BrokerMTM"] = _num_series(raw[COL["broker_mtm"]]) if COL["broker_mtm"] else np.nan
    df["EntryPrice"] = _num_series(raw[COL["bep"]])
    if COL["ltp"]:
        df["SourceLTP"] = _num_series(raw[COL["ltp"]])
    if COL["iv"]:
        df["SourceIV"] = _num_series(raw[COL["iv"]])
    if COL["scrip_name"]:
        df["SourceScripName"] = raw[COL["scrip_name"]].astype(str).str.strip()
    df["Exchange"] = raw[COL["exchange"]].astype(str).str.strip().str.upper() if COL["exchange"] else ""
    blank_ex = df["Exchange"].isin(["", "NAN", "NONE"])
    df.loc[blank_ex, "Exchange"] = df.loc[blank_ex, "Underlying"].map(
        lambda x: "BSE" if x in ("SENSEX", "BANKEX") else "NSE"
    )
    default_lots = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20, "BANKEX": 30, "FINNIFTY": 60, "MIDCPNIFTY": 50}
    df["LotSize"] = _num_series(raw[COL["lot_size"]]) if COL["lot_size"] else df["Underlying"].map(default_lots)
    df["LotSize"] = pd.to_numeric(df["LotSize"], errors="coerce")
    df = df[df["NetPosition"].notna()].copy()
    df["IsOpen"] = df["NetPosition"] != 0
    df["Position"] = np.where(df["NetPosition"] > 0, "BUY", "SELL")
    df["Quantity"] = df["NetPosition"].abs()
    df["Lots"] = (df["Quantity"] / df["LotSize"]).where(df["LotSize"] > 0)
    df["Multiplier"] = 1.0
    # Keep all broker rows for the overall P&L view. Closed rows may have
    # zero quantity and broker MTM; they must not enter live risk calculations.
    df = df[df["Expiry"].notna() & df["Strike"].notna()].copy()
    return df.reset_index(drop=True), COL


def _position_frame_from_json(obj):
    rows = obj.get("positions", obj) if isinstance(obj, dict) else obj
    df = pd.DataFrame(rows)
    required = ["Underlying", "Expiry", "OptionType", "Strike", "Quantity", "Position", "EntryPrice"]
    missing = [x for x in required if x not in df.columns]
    if missing:
        raise ValueError("Saved strategy is missing: " + ", ".join(missing))
    if "NetPosition" not in df.columns:
        df["NetPosition"] = np.where(df["Position"].eq("BUY"), df["Quantity"], -df["Quantity"])
    if "LotSize" not in df.columns:
        defaults = {"NIFTY":65,"BANKNIFTY":30,"SENSEX":20,"BANKEX":30,"FINNIFTY":60,"MIDCPNIFTY":50}
        df["LotSize"] = df["Underlying"].map(defaults)
    df["Lots"] = df["Quantity"].abs() / pd.to_numeric(df["LotSize"], errors="coerce")
    df["Exchange"] = df.get("Exchange", df["Underlying"].map(lambda x: "BSE" if x in ("SENSEX","BANKEX") else "NSE"))
    df["Multiplier"] = 1.0
    return df


# -----------------------------------------------------------------------------
# Input: CSV or saved strategy
# -----------------------------------------------------------------------------
uploaded = st.file_uploader("Upload broker positions CSV", type=["csv"])
saved_strategy_input = st.file_uploader("Or load a saved strategy JSON", type=["json"], key="strategy_top")
if uploaded is None and saved_strategy_input is None:
    st.markdown("### Connect your position book")
    st.info("Import an open-position CSV from your broker or load a saved strategy. Live analytics become available after the FYERS quote refresh.")
    st.stop()

COL = {}
try:
    if saved_strategy_input is not None:
        obj = json.load(saved_strategy_input)
        df = _position_frame_from_json(obj)
        st.success(f"Loaded saved strategy: {len(df)} open positions.")
    else:
        raw = pd.read_csv(uploaded)
        all_positions, COL = _position_frame_from_csv(raw)
        df = all_positions[all_positions["IsOpen"] & all_positions["EntryPrice"].notna()].copy()
except Exception as e:
    st.error(f"Position import failed: {e}")
    st.stop()

if uploaded is not None and "all_positions" not in locals():
    all_positions = df.copy()
if "all_positions" not in locals():
    all_positions = df.copy()
if "IsOpen" not in all_positions.columns:
    all_positions["IsOpen"] = pd.to_numeric(all_positions.get("Quantity", 0), errors="coerce").fillna(0) != 0
if "BrokerMTM" not in all_positions.columns:
    all_positions["BrokerMTM"] = np.nan
if df.empty:
    st.warning("No open positions found. Overall broker MTM is still available; live Greeks and stress testing require open positions.")
    overall_broker_mtm = float(all_positions["BrokerMTM"].fillna(0).sum()) if "BrokerMTM" in all_positions else 0.0
    st.metric("Portfolio MTM · Overall", f"₹{overall_broker_mtm:,.2f}")
    st.dataframe(all_positions[[c for c in ["Underlying","Expiry","OptionType","Strike","NetPosition","BrokerMTM"] if c in all_positions.columns]], use_container_width=True, hide_index=True)
    st.stop()

# -----------------------------------------------------------------------------
# Pricing/session controls are placed beside live position analytics.
# -----------------------------------------------------------------------------
rf_col, q_col, refresh_col = st.columns([1, 1, 1])
with rf_col:
    rf = st.number_input("Risk-free rate (%)", value=6.50, step=0.25, key="pricing_rf") / 100
with q_col:
    q = st.number_input("Dividend / yield (%)", value=0.00, step=0.25, key="pricing_q") / 100
with refresh_col:
    if st.button("Refresh FYERS token", key="refresh_fyers_token"):
        try:
            Path("access_token.txt").unlink()
        except FileNotFoundError:
            pass
        st.rerun()

with st.expander("CSV column mapping", expanded=False):
    st.dataframe(pd.DataFrame([{"Terminal field":k, "CSV column":v or "Not present"} for k,v in COL.items()]), use_container_width=True, hide_index=True)

# -----------------------------------------------------------------------------
# Resolve FYERS symbols and live market data
# -----------------------------------------------------------------------------
if "resolved_positions" not in st.session_state:
    st.session_state["resolved_positions"] = None
if "quotes" not in st.session_state:
    st.session_state["quotes"] = None
if "chain_cache" not in st.session_state:
    st.session_state["chain_cache"] = {}

st.subheader("Position book · imported trades")
strategy_payload = {"version": "0.4.0", "created": datetime.now().isoformat(), "positions": df.to_dict(orient="records")}
st.download_button(
    "Save open-position strategy JSON", data=json.dumps(strategy_payload, indent=2, default=str),
    file_name="strategy.json", mime="application/json", key="save_strategy_json"
)
view_cols = ["Underlying","Expiry","OptionType","Position","Strike","Quantity","Lots","LotSize","EntryPrice"]
book_all, book_open = st.tabs(["Overall Positions", "Open Positions Only"])
with book_all:
    show_cols = [c for c in view_cols + ["NetPosition", "BrokerMTM"] if c in all_positions.columns]
    with st.expander(f"View all position strikes ({len(all_positions)} rows)", expanded=False):
        st.dataframe(all_positions[show_cols], use_container_width=True, hide_index=True)
with book_open:
    with st.expander(f"View open position strikes ({len(df)} rows)", expanded=False):
        st.dataframe(df[[c for c in view_cols if c in df.columns]], use_container_width=True, hide_index=True)

st.caption("Market-data controls")
if st.button("Fetch / Refresh FYERS Market Data", type="primary"):
    try:
        fyers = get_fyers_client()
        expiry_maps = _expiry_kind_map_from_fyers(fyers, list(df.Underlying.unique()))
        df2 = df.copy()
        def candidates_for_row(rr):
            d = pd.to_datetime(rr.Expiry, format="%d-%b-%Y").date().isoformat()
            info = expiry_maps.get(rr.Underlying, {}).get(d)
            if info:
                return build_symbol_candidates(rr.Exchange, rr.Underlying, info["code"], rr.OptionType, int(rr.Strike), expiry_kind=info["kind"])
            return build_symbol_candidates(rr.Exchange, rr.Underlying, rr.Expiry, rr.OptionType, int(rr.Strike))
        df2["SymbolCandidates"] = df2.apply(candidates_for_row, axis=1)
        df2["Symbol"] = df2["SymbolCandidates"].map(lambda x:x[0])
        symbols=[]
        for c in df2.SymbolCandidates: symbols.extend(c)
        symbols += [UNDERLYING_SYM[u] for u in df2.Underlying.unique() if u in UNDERLYING_SYM]
        quotes = fetch_quotes(fyers, list(dict.fromkeys(symbols)))
        resolved=[]
        for _, rr in df2.iterrows():
            resolved.append(next((c for c in rr.SymbolCandidates if c in quotes), rr.Symbol))
        df2["Symbol"] = resolved
        st.session_state["resolved_positions"] = df2
        st.session_state["quotes"] = quotes
        st.session_state["last_refresh"] = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        st.session_state["chain_cache"] = {}
        st.success(f"FYERS market data refreshed: {len(quotes)} symbols returned.")
    except Exception as e:
        st.error(f"FYERS data error: {e}")

if st.session_state.get("resolved_positions") is not None:
    df = st.session_state["resolved_positions"].copy()
quotes = st.session_state.get("quotes")
if not quotes:
    st.warning("Market data is not loaded yet. Use **Fetch / Refresh FYERS Market Data** above to populate live quotes, Greeks and scenario analysis.")
    st.stop()

spots={}
for u in df.Underlying.unique():
    sym=UNDERLYING_SYM.get(u)
    if not sym or sym not in quotes:
        st.error(f"No live underlying quote for {u} ({sym}).")
        st.stop()
    spots[u]=_quote_ltp(quotes[sym])

# -----------------------------------------------------------------------------
# Build live leg analytics. Greeks are taken from payload when available;
# otherwise they are calculated from live LTP-implied IV.
# -----------------------------------------------------------------------------
legs=[]
for _,r in df.iterrows():
    S=spots[r.Underlying]; T=year_fraction(r.Expiry); kind=r.OptionType; v=quotes.get(r.Symbol,{})
    iv=iv_from_quote_or_ltp(v,S,r.Strike,T,rf,q,kind,r.get("EntryIV"))
    if not np.isfinite(iv): iv=0.20
    g=bs_greeks(S,r.Strike,T,rf,q,iv,kind)
    signed=1 if r.Position=="BUY" else -1
    mult=float(r.Quantity)
    payload_greeks={k:_greek_from_payload(v,k) for k in ["delta","gamma","theta","vega","rho","iv"]}
    # Do not use incomplete broker Greek payloads individually; use the model
    # when any requested Greek is missing.
    leg={**r.to_dict(), "Spot":S, "T":T, "LiveLTP":_quote_ltp(v), "Bid":_quote_field(v,"bid"), "Ask":_quote_field(v,"ask"),
         "IV":payload_greeks["iv"] if np.isfinite(payload_greeks["iv"]) else iv,
         "TheoValue":g.price, "Delta":payload_greeks["delta"] if np.isfinite(payload_greeks["delta"]) else g.delta,
         "Gamma":payload_greeks["gamma"] if np.isfinite(payload_greeks["gamma"]) else g.gamma,
         "Theta":payload_greeks["theta"] if np.isfinite(payload_greeks["theta"]) else g.theta,
         "Vega":payload_greeks["vega"] if np.isfinite(payload_greeks["vega"]) else g.vega,
         "Rho":payload_greeks["rho"] if np.isfinite(payload_greeks["rho"]) else g.rho,
         "SignedMultiplier":signed*mult}
    legs.append(leg)
legs=pd.DataFrame(legs)

# Portfolio-level snapshot: MTM uses broker entry price versus live option LTP.
# Margin is intentionally not estimated because the source project has no
# broker margin endpoint/position-margin field.
legs["SignedMTM"] = (legs["LiveLTP"] - legs["EntryPrice"]) * legs["SignedMultiplier"]
legs["Signed_Delta"] = legs["Delta"] * legs["SignedMultiplier"]
legs["Signed_Gamma"] = legs["Gamma"] * legs["SignedMultiplier"]
legs["Signed_Theta"] = legs["Theta"] * legs["SignedMultiplier"]
legs["Signed_Vega"] = legs["Vega"] * legs["SignedMultiplier"]
legs["Signed_Rho"] = legs["Rho"] * legs["SignedMultiplier"]
live_open_mtm = float(legs["SignedMTM"].sum(skipna=True))
open_broker_mtm = float(all_positions.loc[all_positions["IsOpen"], "BrokerMTM"].fillna(0).sum()) if "BrokerMTM" in all_positions else live_open_mtm
portfolio_mtm = float(all_positions["BrokerMTM"].fillna(0).sum()) if "BrokerMTM" in all_positions else open_broker_mtm
gross_contract_value = float((legs["LiveLTP"].fillna(0).abs() * legs["Quantity"]).sum())
net_delta = float(legs["Signed_Delta"].sum())
net_gamma = float(legs["Signed_Gamma"].sum())
net_theta = float(legs["Signed_Theta"].sum())
net_vega = float(legs["Signed_Vega"].sum())
net_rho = float(legs["Signed_Rho"].sum())

st.markdown("### Portfolio overview")
st.caption(f"Live snapshot · {st.session_state.get('last_refresh', 'Quote refresh pending')} · {len(legs)} open legs · {len(all_positions)} total broker rows")
st.metric("Portfolio MTM · Overall", f"₹{portfolio_mtm:,.2f}", help="Sum of broker-reported MTM across all imported rows, including rows with Net Position = 0.")
overall_tab, open_tab = st.tabs(["Overall Positions", "Open Positions Only"])
with overall_tab:
    a1, a2 = st.columns(2)
    a1.metric("Portfolio MTM", f"₹{portfolio_mtm:,.2f}")
    a2.metric("Rows included", f"{len(all_positions):,}")
    st.caption("Overall MTM uses the broker MTM column for both open and closed rows. Closed rows are not treated as live exposure.")
with open_tab:
    a1, a2 = st.columns(2)
    a1.metric("Open-position broker MTM", f"₹{open_broker_mtm:,.2f}")
    a2.metric("Open legs", f"{len(legs):,}")
k1,k2,k3,k4 = st.columns(4)
k1.metric("Open Live MTM (model)", f"₹{live_open_mtm:,.2f}", help="Indicative live LTP versus entry premium for open positions only.")
k2.metric("Gross option value", f"₹{gross_contract_value:,.0f}", help="Sum of absolute live option premium value × quantity; not margin or notional underlying exposure.")
k3.metric("Net portfolio delta", f"{net_delta:,.2f}")
k4.metric("Net portfolio theta", f"{net_theta:,.2f}")
g1,g2,g3 = st.columns(3)
g1.metric("Net gamma", f"{net_gamma:,.4f}")
g2.metric("Net vega", f"{net_vega:,.2f}")
g3.metric("Net rho", f"{net_rho:,.2f}")

underlying_pnl = legs.groupby("Underlying", as_index=False).agg(MTM=("SignedMTM","sum"), Legs=("Underlying","size"))
if not underlying_pnl.empty:
    pcol, tcol = st.columns([1.2,1])
    with pcol:
        pnl_fig = go.Figure(go.Bar(x=underlying_pnl["Underlying"], y=underlying_pnl["MTM"],
                                   marker_color=["#21b783" if x >= 0 else "#ef6474" for x in underlying_pnl["MTM"]]))
        pnl_fig.update_layout(title="MTM contribution by underlying", height=285, margin=dict(l=10,r=10,t=45,b=10),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              yaxis_title="P&L (₹)", xaxis_title="")
        st.plotly_chart(pnl_fig, use_container_width=True)
    with tcol:
        st.markdown("#### Underlying breakdown")
        st.dataframe(underlying_pnl.style.format({"MTM":"₹{:,.2f}"}), use_container_width=True, hide_index=True)
# Broker MTM attribution: all rows versus current open positions.
if "BrokerMTM" in all_positions.columns:
    all_positions["BrokerMTM"] = pd.to_numeric(all_positions["BrokerMTM"], errors="coerce").fillna(0)
    overall_by_underlying = all_positions.groupby("Underlying", as_index=False).agg(BrokerMTM=("BrokerMTM", "sum"), Rows=("Underlying", "size"))
    open_by_underlying = all_positions[all_positions["IsOpen"]].groupby("Underlying", as_index=False).agg(BrokerMTM=("BrokerMTM", "sum"), Rows=("Underlying", "size"))
    p_all, p_open = st.tabs(["Overall Positions · Broker MTM", "Open Positions Only · Broker MTM"])
    with p_all:
        st.dataframe(overall_by_underlying.style.format({"BrokerMTM":"₹{:,.2f}"}), use_container_width=True, hide_index=True)
    with p_open:
        st.dataframe(open_by_underlying.style.format({"BrokerMTM":"₹{:,.2f}"}), use_container_width=True, hide_index=True)

st.divider()

with st.expander("Live position analytics · Open Positions Only", expanded=False):
    leg_cols=["Underlying","Expiry","OptionType","Position","Symbol","Strike","Quantity","Lots","EntryPrice","Spot","LiveLTP","Bid","Ask","IV","TheoValue","Delta","Gamma","Theta","Vega","Rho"]
    st.dataframe(legs[leg_cols].style.format({"Spot":"{:.2f}","LiveLTP":"{:.2f}","Bid":"{:.2f}","Ask":"{:.2f}","IV":"{:.2%}","TheoValue":"{:.2f}","Delta":"{:.4f}","Gamma":"{:.6f}","Theta":"{:.4f}","Vega":"{:.4f}","Rho":"{:.4f}","EntryPrice":"{:.2f}"}), use_container_width=True, hide_index=True)

# -----------------------------------------------------------------------------
# Option-chain + IV surface layer
# -----------------------------------------------------------------------------
with st.expander("FYERS option chain / IV surface", expanded=False):
    st.caption("FYERS' option-chain endpoint provides contract LTP, bid/ask, OI, volume and expiry metadata. Where a Greek/IV field is not returned, the terminal calculates IV from LTP and derives Black-Scholes Greeks. FYERS provides LTP, bid/ask, OI, volume and options-chain analytics through its market-data APIs.")
    if st.button("Load option chains for open-position expiries"):
        try:
            fyers=get_fyers_client()
            cache={}
            for u in df.Underlying.unique():
                ex="BSE" if u in ("SENSEX","BANKEX") else "NSE"
                em=fetch_expiry_map(ex,u)
                for exp in sorted(df.loc[df.Underlying.eq(u),"Expiry"].unique()):
                    d=pd.to_datetime(exp,format="%d-%b-%Y").date().isoformat()
                    info=em.get(d)
                    if not info:
                        continue
                    resp=fetch_option_chain(
                        fyers,u,exchange=ex,strikecount=chain_strikes,
                        expiry_timestamp=info.get("timestamp"),greeks=True
                    )
                    rows=option_chain_rows(resp)
                    cache[(u,exp)]={"rows":rows,"kind":info["kind"],"code":info["code"]}
            st.session_state["chain_cache"]=cache
            st.success(f"Loaded {len(cache)} underlying/expiry chain snapshots.")
        except Exception as e:
            st.error(f"Option-chain load failed: {e}")
    cache=st.session_state.get("chain_cache",{})
    if cache:
        chain_rows=[]
        for (u,exp),payload in cache.items():
            spot=spots[u]
            T=year_fraction(exp)
            for x in payload["rows"]:
                strike=_safe_float(x.get("strike_price")); ltp=_safe_float(x.get("ltp"))
                if not np.isfinite(strike) or strike<=0 or x.get("option_type") not in ("CE","PE"): continue
                iv=iv_from_quote_or_ltp(x,spot,strike,T,rf,q,x.get("option_type"))
                if np.isfinite(iv):
                    chain_rows.append({"Underlying":u,"Expiry":exp,"Type":x.get("option_type"),"Strike":strike,"LTP":ltp,"Bid":_safe_float(x.get("bid")),"Ask":_safe_float(x.get("ask")),"IV":iv,"OI":_safe_float(x.get("oi")),"Volume":_safe_float(x.get("volume")),"Symbol":x.get("symbol")})
        chain_df=pd.DataFrame(chain_rows)
        if not chain_df.empty:
            st.dataframe(chain_df.sort_values(["Underlying","Expiry","Strike","Type"]),use_container_width=True,hide_index=True)
            figiv=go.Figure()
            for (u,exp,typ),gdf in chain_df.groupby(["Underlying","Expiry","Type"]):
                gdf=gdf.sort_values("Strike")
                figiv.add_trace(go.Scatter(x=gdf.Strike,y=gdf.IV*100,mode="lines+markers",name=f"{u} {exp} {typ}"))
            figiv.update_layout(title="Current IV surface / skew slices",xaxis_title="Strike",yaxis_title="IV (%)",height=520)
            st.plotly_chart(figiv,use_container_width=True)
            st.session_state["chain_df"]=chain_df

# -----------------------------------------------------------------------------
# Scenario engine, including scenario-dependent IV surface interpolation.
# Derive the first expiry from the imported open-position book; never rely on
# an undeclared UI variable. If every expiry is already past, use the nearest
# available expiry so the scenario view remains renderable.
_expiry_dates = pd.to_datetime(df["Expiry"], format="%d-%b-%Y", errors="coerce").dropna().dt.date
_future_expiries = [d for d in _expiry_dates if d >= date.today()]
first_upcoming_expiry = min(_future_expiries) if _future_expiries else (
    min(_expiry_dates) if not _expiry_dates.empty else date.today()
)
max_days_forward = 365

st.markdown("#### Scenario controls")
sc1, sc2 = st.columns(2)
with sc1:
    spot_move = st.slider("Spot movement range (%)", -20.0, 20.0, (-10.0, 10.0), 0.5, key="scenario_spot_range")
    points = st.slider("Scenario points", 21, 201, 81, 10, help="Number of spot-movement points on the scenario curve.", key="scenario_points")
    days_fwd = st.number_input("Days forward", min_value=0, max_value=int(max_days_forward), value=0, step=1, key="scenario_days_forward")
with sc2:
    iv_mode = st.selectbox("IV scenario", ["Unchanged", "Lower", "Higher", "Custom relative %", "Custom IV points", "Use IV surface"], key="scenario_iv_mode")
    if iv_mode == "Lower": iv_shift = -10.0
    elif iv_mode == "Higher": iv_shift = 10.0
    elif iv_mode == "Custom relative %": iv_shift = st.number_input("IV relative change (%)", value=0.0, step=1.0, key="scenario_iv_relative")
    elif iv_mode == "Custom IV points": iv_shift = st.number_input("IV change (vol points)", value=0.0, step=0.5, key="scenario_iv_points")
    else: iv_shift = 0.0
    chain_strikes = st.slider("Option-chain strikes around ATM", 5, 50, 25, 5, key="scenario_chain_strikes")
st.caption(f"First upcoming expiry: **{first_upcoming_expiry:%d-%b-%Y}** · Days-forward limit: **{max_days_forward} days**")

# -----------------------------------------------------------------------------
def surface_iv(chain_df, underlying, expiry, strike, scenario_spot, fallback):
    if chain_df is None or chain_df.empty:
        return fallback
    g=chain_df[(chain_df.Underlying==underlying)&(chain_df.Expiry==expiry)].copy()
    if g.empty: return fallback
    # Use both CE/PE observations to construct a strike/IV slice. FYERS notes
    # that IV can be represented at strike/expiry level in the option chain.
    g=g[np.isfinite(g.IV)&(g.IV>0)&np.isfinite(g.Strike)].copy()
    if g.empty: return fallback
    g["Moneyness"] = g.Strike / float(spots[underlying])
    g=g.groupby("Moneyness",as_index=False)["IV"].mean().sort_values("Moneyness")
    target=float(strike)/max(float(scenario_spot),1e-9)
    if len(g)==1: return float(g.IV.iloc[0])
    return float(np.interp(target,g.Moneyness,g.IV,left=g.IV.iloc[0],right=g.IV.iloc[-1]))

moves=np.linspace(spot_move[0],spot_move[1],points)
scenario_date=date.today()+pd.Timedelta(days=int(days_fwd))
chain_df=st.session_state.get("chain_df")
rows=[]
for m in moves:
    total=0.0
    row={"MovePct":float(m)}
    for i,l in legs.iterrows():
        S0=float(l["Spot"]); S=S0*(1+m/100.0)
        expiry_dt=expiry_datetime(l["Expiry"])
        T=max((expiry_dt-scenario_date).days/365.0,0.0)
        base_iv=float(l["IV"])
        if iv_mode=="Use IV surface":
            iv=surface_iv(chain_df,l["Underlying"],l["Expiry"],float(l["Strike"]),S,base_iv)
        elif iv_mode=="Custom IV points":
            iv=max(base_iv+iv_shift/100.0,0.0001)
        else:
            iv=max(base_iv*(1+iv_shift/100.0),0.0001)
        p=bs_greeks(S,float(l["Strike"]),T,rf,q,iv,l["OptionType"]).price
        pnl=(p-float(l["EntryPrice"]))*float(l["SignedMultiplier"])
        total+=pnl
        row[f"Leg {i+1}"]=pnl
    row["Portfolio P&L"]=total
    rows.append(row)
sc=pd.DataFrame(rows)

st.subheader("Portfolio scenario analysis · Open Positions Only")
st.caption(f"Scenario points = {points}. Spot range = {spot_move[0]:.1f}% to {spot_move[1]:.1f}%. Scenario date = {scenario_date:%d-%b-%Y}. First expiry limit = {first_upcoming_expiry:%d-%b-%Y}.")
fig=go.Figure()
fig.add_trace(go.Scatter(x=sc.MovePct,y=sc["Portfolio P&L"],mode="lines",name="Portfolio P&L",line={"width":3}))
fig.add_hline(y=0,line_dash="dot"); fig.add_vline(x=0,line_dash="dot")
fig.update_layout(title="Combined portfolio P&L across common spot shocks",
                  xaxis_title="Common move from each underlying spot (%)", yaxis_title="P&L (₹)",
                  height=470, margin=dict(l=20,r=20,t=55,b=20),
                  paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                  font=dict(color="#c8d5e5"), hovermode="x unified")
fig.update_traces(line=dict(color="#62a5ff", width=3), fill="tozeroy", fillcolor="rgba(98,165,255,.08)")

st.plotly_chart(fig,use_container_width=True)
c1,c2,c3,c4=st.columns(4)
current_row=sc.iloc[(sc.MovePct.abs()).argmin()]
c1.metric("Current scenario P&L",f"{current_row['Portfolio P&L']:,.2f}")
c2.metric("Worst scenario",f"{sc['Portfolio P&L'].min():,.2f}")
c3.metric("Best scenario",f"{sc['Portfolio P&L'].max():,.2f}")
c4.metric("Scenario date",f"{scenario_date:%d-%b}")
with st.expander("Scenario matrix · view all scenario strikes",expanded=False):
    st.dataframe(sc.style.format({c:"{:.2f}" for c in sc.columns if c!="MovePct"}).format({"MovePct":"{:.2f}%"}),use_container_width=True,hide_index=True)

    # -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Multi-scenario stress testing and per-leg expiry loss bounds
# -----------------------------------------------------------------------------
st.divider()
st.subheader("Stress testing · Open Positions Only")
st.caption("Model repricing of every open leg under joint market shocks and isolated underlying shocks. P&L is measured against imported entry premium; this is a scenario estimate, not a guaranteed liquidation value.")
st.markdown("#### Stress test controls")
stress_c1, stress_c2, stress_c3 = st.columns(3)
with stress_c1:
    stress_spot_pct = st.number_input("Base spot shock (%)", min_value=0.5, max_value=50.0, value=5.0, step=0.5, key="stress_spot_shock")
with stress_c2:
    stress_iv_points = st.number_input("Base IV shock (vol points)", min_value=0.0, max_value=100.0, value=10.0, step=1.0, key="stress_iv_shock")
with stress_c3:
    stress_days_forward = st.number_input("Stress horizon (days)", min_value=0, max_value=365, value=1, step=1, key="stress_horizon")

stress_shocks = [
    {"name": "Flat / time decay", "spot_pct": 0.0, "iv_points": 0.0, "days_forward": int(stress_days_forward)},
    {"name": "Mild sell-off", "spot_pct": -min(stress_spot_pct, 95.0), "iv_points": stress_iv_points / 2, "days_forward": int(stress_days_forward)},
    {"name": "Sharp sell-off", "spot_pct": -min(2 * stress_spot_pct, 95.0), "iv_points": stress_iv_points, "days_forward": int(stress_days_forward)},
    {"name": "Crash / volatility spike", "spot_pct": -min(3 * stress_spot_pct, 95.0), "iv_points": 2 * stress_iv_points, "days_forward": int(stress_days_forward)},
    {"name": "Mild rally", "spot_pct": stress_spot_pct, "iv_points": -stress_iv_points / 2, "days_forward": int(stress_days_forward)},
    {"name": "Sharp rally", "spot_pct": 2 * stress_spot_pct, "iv_points": -stress_iv_points, "days_forward": int(stress_days_forward)},
]
for underlying in sorted(legs["Underlying"].dropna().unique()):
    stress_shocks.extend([
        {"name": f"{underlying} only -{stress_spot_pct:g}%", "spot_moves": {underlying: -stress_spot_pct}, "iv_points": stress_iv_points / 2, "days_forward": int(stress_days_forward)},
        {"name": f"{underlying} only +{stress_spot_pct:g}%", "spot_moves": {underlying: stress_spot_pct}, "iv_points": -stress_iv_points / 2, "days_forward": int(stress_days_forward)},
    ])

stress_portfolio, stress_legs = stress_test_portfolio(legs, stress_shocks, date.today(), rf, q)
stress_portfolio = stress_portfolio.sort_values("PortfolioPnL")
worst_stress, best_stress = stress_portfolio.iloc[0], stress_portfolio.iloc[-1]
sm1, sm2, sm3 = st.columns(3)
sm1.metric("Worst stress P&L", f"₹{worst_stress.PortfolioPnL:,.0f}", help=f"Scenario: {worst_stress.Scenario}")
sm2.metric("Best stress P&L", f"₹{best_stress.PortfolioPnL:,.0f}", help=f"Scenario: {best_stress.Scenario}")
sm3.metric("Stress scenarios", f"{len(stress_portfolio)}")
stress_fig = go.Figure(go.Bar(
    x=stress_portfolio["PortfolioPnL"], y=stress_portfolio["Scenario"], orientation="h",
    marker_color=["#ef6474" if x < 0 else "#21b783" for x in stress_portfolio["PortfolioPnL"]],
))
stress_fig.update_layout(title="Portfolio P&L under defined market shocks",
    xaxis_title="Estimated P&L (₹)", yaxis_title="", height=max(360, 42 * len(stress_portfolio)),
    margin=dict(l=15, r=15, t=50, b=15), paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#c8d5e5"))
st.plotly_chart(stress_fig, use_container_width=True)
with st.expander("Stress scenario results", expanded=True):
    st.dataframe(stress_portfolio.style.format({
        "PortfolioPnL": "₹{:,.2f}", "SpotShockPct": "{:+.1f}%",
        "IVShockPoints": "{:+.1f}", "DaysForward": "{:.0f}",
    }), use_container_width=True, hide_index=True)
stress_choice = st.selectbox("Inspect leg-level impact", stress_legs["Scenario"].drop_duplicates().tolist())
selected_stress_legs = stress_legs[stress_legs["Scenario"].eq(stress_choice)].copy()
with st.expander(f"View leg-level strike impact ({len(selected_stress_legs)} rows)", expanded=False):
    st.dataframe(selected_stress_legs[[
        "Underlying", "Expiry", "OptionType", "Position", "Strike", "Quantity",
        "SpotShockPct", "IVShockPoints", "ModelPrice", "PnL"
    ]].style.format({
        "Strike": "{:,.2f}", "Quantity": "{:,.0f}", "SpotShockPct": "{:+.1f}%",
        "IVShockPoints": "{:+.1f}", "ModelPrice": "{:,.2f}", "PnL": "₹{:,.2f}",
    }), use_container_width=True, hide_index=True)

st.subheader("Position-level maximum loss · expiry bounds")
st.caption("Each imported row is analysed as a standalone option leg. Long options have premium-at-risk; short calls have theoretically unlimited loss; short puts have a bounded expiry loss. Offsets between legs are intentionally not netted because the import does not contain reliable strategy-group identifiers.")
expiry_risk = position_expiry_risk(legs)
risk_display = expiry_risk[[
    "Underlying", "Expiry", "OptionType", "Position", "Strike", "Quantity",
    "EntryPrice", "MaxLossLabel", "MaxProfitLabel"
]].rename(columns={"MaxLossLabel": "Standalone Max Loss", "MaxProfitLabel": "Standalone Max Profit"})
with st.expander(f"View expiry risk by strike ({len(risk_display)} rows)", expanded=False):
    st.dataframe(risk_display.style.format({
        "Strike": "{:,.2f}", "Quantity": "{:,.0f}", "EntryPrice": "₹{:,.2f}",
        "Standalone Max Loss": lambda x: "Unlimited" if isinstance(x, str) else f"₹{x:,.2f}",
        "Standalone Max Profit": lambda x: "Unlimited" if isinstance(x, str) else f"₹{x:,.2f}",
    }), use_container_width=True, hide_index=True)
bounded_losses = expiry_risk.loc[np.isfinite(pd.to_numeric(expiry_risk["MaxLoss"], errors="coerce")), "MaxLoss"]
unlimited_loss_legs = int(np.isinf(expiry_risk["MaxLoss"]).sum())
ml1, ml2, ml3 = st.columns(3)
ml1.metric("Bounded standalone max loss", f"₹{bounded_losses.sum():,.0f}")
ml2.metric("Unlimited-loss legs", unlimited_loss_legs)
ml3.metric("Analysed positions", len(expiry_risk))
if unlimited_loss_legs:
    st.warning(f"{unlimited_loss_legs} short-call position(s) have theoretically unlimited standalone expiry loss. A defined-risk spread may cap the combined strategy loss, but the terminal will only calculate that after positions are grouped into strategies.")
else:
    st.info("All imported legs have a finite standalone expiry loss under the standard option payoff model.")

# Portfolio Greeks and leg contribution charts
# -----------------------------------------------------------------------------
st.subheader("Portfolio Greeks & sensitivities · Open Positions Only")
for col in ["Delta","Gamma","Theta","Vega","Rho"]:
    legs[f"Signed_{col}"]=legs[col]*legs["SignedMultiplier"]
greek_totals={c:float(legs[f"Signed_{c}"].sum()) for c in ["Delta","Gamma","Theta","Vega","Rho"]}
cols=st.columns(5)
for c,(name,val) in zip(cols,greek_totals.items()): c.metric(name,f"{val:,.4f}")

figg=go.Figure(go.Bar(x=list(greek_totals.keys()),y=list(greek_totals.values())))
figg.update_layout(title="Net portfolio Greeks",xaxis_title="Greek",yaxis_title="Signed exposure",height=340,
                   paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#c8d5e5"))
st.plotly_chart(figg,use_container_width=True)

# -----------------------------------------------------------------------------
# Risk monitor: concentration, expiry exposure, limits and actionable alerts
# -----------------------------------------------------------------------------
st.divider()
st.subheader("Risk monitor · Open Positions Only")
st.caption("Concentration is measured using gross live option premium value (LTP × quantity), not underlying notional or exchange margin. Greek limits are user-configurable warning thresholds.")
st.markdown("#### Risk monitor thresholds")
risk_c1, risk_c2, risk_c3 = st.columns(3)
with risk_c1:
    risk_delta_limit = st.number_input("|Net delta| warning", min_value=0.0, value=5000.0, step=500.0, key="risk_delta_limit")
    risk_gamma_limit = st.number_input("|Net gamma| warning", min_value=0.0, value=500.0, step=50.0, key="risk_gamma_limit")
with risk_c2:
    risk_theta_limit = st.number_input("|Net theta| warning", min_value=0.0, value=10000.0, step=500.0, key="risk_theta_limit")
    risk_vega_limit = st.number_input("|Net vega| warning", min_value=0.0, value=10000.0, step=500.0, key="risk_vega_limit")
with risk_c3:
    concentration_limit = st.slider("Concentration warning (%)", 25, 100, 60, 5, key="risk_concentration_limit")
    scenario_loss_limit = st.number_input("Scenario loss warning (₹)", min_value=0.0, value=100000.0, step=10000.0, key="risk_scenario_loss_limit")

# Premium-value exposure is a transparent proxy available from the imported
# positions and live quotes. It must not be confused with margin or max loss.
risk_legs = legs.copy()
risk_legs["GrossPremiumValue"] = risk_legs["LiveLTP"].abs() * risk_legs["Quantity"]
risk_legs["ExpiryDate"] = pd.to_datetime(risk_legs["Expiry"], format="%d-%b-%Y", errors="coerce")
total_premium_value = float(risk_legs["GrossPremiumValue"].sum())

def _risk_group(group_col, label):
    out = risk_legs.groupby(group_col, dropna=False).agg(
        Legs=("Quantity", "size"),
        GrossPremiumValue=("GrossPremiumValue", "sum"),
        NetMTM=("SignedMTM", "sum"),
        NetDelta=("Signed_Delta", "sum"),
        NetGamma=("Signed_Gamma", "sum"),
        NetTheta=("Signed_Theta", "sum"),
        NetVega=("Signed_Vega", "sum"),
    ).reset_index().rename(columns={group_col: label})
    out["PremiumSharePct"] = np.where(total_premium_value > 0, out["GrossPremiumValue"] / total_premium_value * 100, 0.0)
    return out.sort_values("GrossPremiumValue", ascending=False)

by_underlying = _risk_group("Underlying", "Underlying")
by_expiry = _risk_group("Expiry", "Expiry")
by_option_type = _risk_group("OptionType", "OptionType")

alerts = []
def _add_alert(level, title, detail):
    alerts.append({"Level": level, "Risk": title, "Detail": detail})

if total_premium_value <= 0:
    _add_alert("INFO", "Premium exposure unavailable", "No valid live option LTP values are available to calculate concentration.")
else:
    top_u = by_underlying.iloc[0]
    if float(top_u.PremiumSharePct) >= concentration_limit:
        _add_alert("WARNING", "Underlying concentration", f"{top_u.Underlying} represents {top_u.PremiumSharePct:.1f}% of gross option premium value (threshold {concentration_limit}%).")
    top_e = by_expiry.iloc[0]
    if float(top_e.PremiumSharePct) >= concentration_limit:
        _add_alert("WARNING", "Expiry concentration", f"{top_e.Expiry} represents {top_e.PremiumSharePct:.1f}% of gross option premium value (threshold {concentration_limit}%).")

for greek_name, greek_value, greek_limit in [
    ("Delta", net_delta, risk_delta_limit), ("Gamma", net_gamma, risk_gamma_limit),
    ("Theta", net_theta, risk_theta_limit), ("Vega", net_vega, risk_vega_limit)]:
    if greek_limit > 0 and abs(greek_value) >= greek_limit:
        _add_alert("WARNING", f"{greek_name} limit breached", f"Absolute net {greek_name.lower()} is {abs(greek_value):,.4f}; configured warning is {greek_limit:,.4f}.")

worst_scenario_pnl = float(sc["Portfolio P&L"].min()) if not sc.empty else np.nan
if np.isfinite(worst_scenario_pnl) and scenario_loss_limit > 0 and worst_scenario_pnl <= -scenario_loss_limit:
    _add_alert("CRITICAL", "Scenario loss threshold breached", f"Worst displayed scenario is ₹{worst_scenario_pnl:,.2f}; configured loss warning is ₹{scenario_loss_limit:,.2f}.")

if not alerts:
    _add_alert("INFO", "No configured risk alerts", "No configured threshold is currently breached. This does not mean the portfolio is risk-free.")

critical_count = sum(a["Level"] == "CRITICAL" for a in alerts)
warning_count = sum(a["Level"] == "WARNING" for a in alerts)
info_count = sum(a["Level"] == "INFO" for a in alerts)
a1, a2, a3, a4 = st.columns(4)
a1.metric("Critical alerts", critical_count)
a2.metric("Warnings", warning_count)
a3.metric("Underlying buckets", len(by_underlying))
a4.metric("Expiry buckets", len(by_expiry))

for alert in alerts:
    if alert["Level"] == "CRITICAL":
        st.error(f"**{alert['Risk']}** — {alert['Detail']}")
    elif alert["Level"] == "WARNING":
        st.warning(f"**{alert['Risk']}** — {alert['Detail']}")
    else:
        st.info(f"**{alert['Risk']}** — {alert['Detail']}")

rc1, rc2 = st.columns(2)
with rc1:
    st.markdown("#### Underlying concentration")
    st.dataframe(by_underlying[["Underlying", "Legs", "GrossPremiumValue", "PremiumSharePct", "NetMTM", "NetDelta", "NetGamma", "NetTheta", "NetVega"]].style.format({"GrossPremiumValue":"₹{:,.0f}","PremiumSharePct":"{:.1f}%","NetMTM":"₹{:,.2f}","NetDelta":"{:,.2f}","NetGamma":"{:,.4f}","NetTheta":"{:,.2f}","NetVega":"{:,.2f}"}), use_container_width=True, hide_index=True)
with rc2:
    st.markdown("#### Expiry-wise exposure")
    st.dataframe(by_expiry[["Expiry", "Legs", "GrossPremiumValue", "PremiumSharePct", "NetMTM", "NetDelta", "NetGamma", "NetTheta", "NetVega"]].style.format({"GrossPremiumValue":"₹{:,.0f}","PremiumSharePct":"{:.1f}%","NetMTM":"₹{:,.2f}","NetDelta":"{:,.2f}","NetGamma":"{:,.4f}","NetTheta":"{:,.2f}","NetVega":"{:,.2f}"}), use_container_width=True, hide_index=True)

rc3, rc4 = st.columns(2)
with rc3:
    st.markdown("#### Premium value by underlying")
    fig_u = go.Figure(go.Bar(x=by_underlying["Underlying"], y=by_underlying["GrossPremiumValue"], text=by_underlying["PremiumSharePct"].map(lambda x: f"{x:.1f}%"), textposition="auto"))
    fig_u.update_layout(height=300, yaxis_title="Gross option premium value (₹)", margin=dict(l=10,r=10,t=25,b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#c8d5e5"))
    st.plotly_chart(fig_u, use_container_width=True)
with rc4:
    st.markdown("#### Premium value by option type")
    fig_t = go.Figure(go.Pie(labels=by_option_type["OptionType"], values=by_option_type["GrossPremiumValue"], hole=.58))
    fig_t.update_layout(height=300, margin=dict(l=10,r=10,t=25,b=10), paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#c8d5e5"))
    st.plotly_chart(fig_t, use_container_width=True)

st.markdown("#### Risk policy and limitations")
st.caption("Alerts are based on the currently imported open positions, latest successful FYERS quote snapshot, model/broker Greeks and the scenario range configured above in the risk/scenario sections. Premium value is not margin, max loss, or underlying notional. This version does not calculate SPAN/Exposure margin, portfolio VaR, liquidity/slippage risk, or intraday peak exposure. Refresh quotes before relying on the dashboard; stale quotes can make every risk figure stale.")

# -----------------------------------------------------------------------------
# Historical validation
# -----------------------------------------------------------------------------
st.subheader("Model validation")
st.caption("Upload a historical option dataset with Spot, Strike, Expiry, OptionType and MarketPrice. IV is optional; if absent, the model solves IV from the market price before comparing the theoretical value.")
hist_file=st.file_uploader("Historical validation CSV",type=["csv"],key="historical_validation")
if hist_file is not None:
    try:
        hraw=pd.read_csv(hist_file)
        hcols={
            "spot":_find_col(hraw.columns,["Spot","Underlying LTP","Underlying Spot"]),
            "strike":_find_col(hraw.columns,["Strike","Strike Price"]),
            "expiry":_find_col(hraw.columns,["Expiry","Expiry Date"]),
            "kind":_find_col(hraw.columns,["OptionType","Option Type","Scrip Type"]),
            "market":_find_col(hraw.columns,["MarketPrice","Market Price","LTP","Option LTP"]),
            "iv":_find_col(hraw.columns,["IV","Implied Volatility"],required=False),
        }
        vals=[]
        for _,rr in hraw.iterrows():
            S=_safe_float(rr[hcols["spot"]]); K=_safe_float(rr[hcols["strike"]]); M=_safe_float(rr[hcols["market"]]); kind=str(rr[hcols["kind"]]).upper().replace("C","CE").replace("P","PE")
            exp=_parse_date_value(rr[hcols["expiry"]]);
            if not np.isfinite(S) or not np.isfinite(K) or not np.isfinite(M) or pd.isna(exp): continue
            expstr=exp.strftime("%d-%b-%Y"); T=year_fraction(expstr)
            giv_iv=_safe_float(rr[hcols["iv"]]) if hcols["iv"] else np.nan
            iv=giv_iv/100 if np.isfinite(giv_iv) and giv_iv>3 else giv_iv
            if not np.isfinite(iv): iv=implied_vol(M,S,K,T,rf,q,kind)
            if not np.isfinite(iv): continue
            model=bs_greeks(S,K,T,rf,q,iv,kind).price
            vals.append({"Spot":S,"Strike":K,"Expiry":expstr,"OptionType":kind,"MarketPrice":M,"ModelPrice":model,"Error":model-M,"AbsError":abs(model-M),"IV":iv})
        vdf=pd.DataFrame(vals)
        if vdf.empty: st.warning("No valid rows could be modelled.")
        else:
            st.dataframe(vdf,use_container_width=True,hide_index=True)
            a,b,c=st.columns(3); a.metric("Rows",len(vdf)); b.metric("MAE",f"{vdf.AbsError.mean():.4f}"); c.metric("RMSE",f"{np.sqrt(np.mean(vdf.Error**2)):.4f}")
    except Exception as e:
        st.error(f"Historical validation failed: {e}")

st.subheader("Market reference")
st.dataframe(pd.DataFrame([{"Underlying":u,"FYERS Symbol":UNDERLYING_SYM[u],"Live Spot":spots[u]} for u in spots]),use_container_width=True,hide_index=True)
st.caption("The common scenario axis is percentage movement from each underlying's own live spot. Quantity is the broker Net Position in units; Lots is display-only and is never multiplied into P&L.")
