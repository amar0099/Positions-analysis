from dataclasses import dataclass
from datetime import datetime, date
import math
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq

@dataclass
class Greeks:
    price: float; delta: float; gamma: float; theta: float; vega: float; rho: float; iv: float

def _d1d2(S,K,T,r,q,sigma):
    vs=sigma*math.sqrt(max(T,1e-12)); d1=(math.log(S/K)+(r-q+0.5*sigma*sigma)*T)/vs; return d1,d1-vs

def bs_price(S,K,T,r,q,sigma,kind):
    S=max(float(S),1e-12); K=max(float(K),1e-12); T=max(float(T),0.0); sigma=max(float(sigma),1e-8)
    if T<=1e-10:
        return max(S-K,0.0) if kind=="CE" else max(K-S,0.0)
    d1,d2=_d1d2(S,K,T,r,q,sigma); disc_r=math.exp(-r*T); disc_q=math.exp(-q*T)
    if kind=="CE": return S*disc_q*norm.cdf(d1)-K*disc_r*norm.cdf(d2)
    return K*disc_r*norm.cdf(-d2)-S*disc_q*norm.cdf(-d1)

def bs_greeks(S,K,T,r,q,sigma,kind):
    p=bs_price(S,K,T,r,q,sigma,kind)
    if T<=1e-10:
        intrinsic=max(S-K,0) if kind=="CE" else max(K-S,0); d=1.0 if (kind=="CE" and S>K) else (-1.0 if kind=="PE" and S<K else 0.0)
        return Greeks(p,d,0,0,0,0,sigma)
    d1,d2=_d1d2(S,K,T,r,q,sigma); pdf=norm.pdf(d1); disc_r=math.exp(-r*T); disc_q=math.exp(-q*T)
    delta=disc_q*norm.cdf(d1) if kind=="CE" else disc_q*(norm.cdf(d1)-1)
    gamma=disc_q*pdf/(S*sigma*math.sqrt(T))
    vega=S*disc_q*pdf*math.sqrt(T)/100.0
    theta_annual=(-S*disc_q*pdf*sigma/(2*math.sqrt(T)) - r*K*disc_r*norm.cdf(d2) + q*S*disc_q*norm.cdf(d1)) if kind=="CE" else (-S*disc_q*pdf*sigma/(2*math.sqrt(T)) + r*K*disc_r*norm.cdf(-d2) - q*S*disc_q*norm.cdf(-d1))
    theta=theta_annual/365.0
    rho=(K*T*disc_r*norm.cdf(d2)/100.0) if kind=="CE" else (-K*T*disc_r*norm.cdf(-d2)/100.0)
    return Greeks(p,delta,gamma,theta,vega,rho,sigma)

def implied_vol(market,S,K,T,r,q,kind):
    if market is None or not np.isfinite(market) or market<=0 or S<=0 or K<=0 or T<=0: return np.nan
    intrinsic=max(S-K,0) if kind=="CE" else max(K-S,0)
    lower=intrinsic*math.exp(-q*T) if kind=="CE" else intrinsic*math.exp(-r*T)
    if market < max(lower-1e-8,0): return np.nan
    f=lambda x: bs_price(S,K,T,r,q,x,kind)-market
    try: return brentq(f,1e-5,5.0,maxiter=100)
    except Exception: return np.nan

def expiry_datetime(expiry_code):
    e=str(expiry_code).upper().strip()
    # Accept the CSV/display date formats used by the terminal as well as
    # FYERS internal weekly/monthly codes.
    for fmt in ("%d-%b-%Y", "%d/%b/%Y", "%d %b %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try: return datetime.strptime(e,fmt).date()
        except ValueError: pass
    if __import__('re').fullmatch(r"\d{2}[A-Z]{3}", e):
        # Monthly code: use the last calendar day of that month.
        y=int(e[:2])+2000; m=datetime.strptime(e[2:],"%b").month
        import calendar
        return date(y,m,calendar.monthrange(y,m)[1])
    return datetime.strptime(e,"%y%m%d").date()

def year_fraction(expiry_code, valuation_date=None):
    d=valuation_date or date.today(); ed=expiry_datetime(expiry_code); days=max((ed-d).days,0); return days/365.0

def iv_from_quote_or_ltp(v,S,K,T,r,q,kind,entry_iv=None):
    for key in ("iv","implied_volatility","impliedVolatility"):
        if v.get(key) is not None:
            x=float(v[key]); return x/100 if x>3 else x
    if entry_iv is not None: return float(entry_iv)/100 if float(entry_iv)>3 else float(entry_iv)
    ltp=v.get("lp") or v.get("ltp") or v.get("last_price")
    return implied_vol(float(ltp),S,K,T,r,q,kind) if ltp is not None else np.nan
