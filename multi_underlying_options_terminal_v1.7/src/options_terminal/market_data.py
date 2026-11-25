from __future__ import annotations
from .pricing import implied_volatility, years_to_expiry

def enrich_legs_from_quotes(legs, quote_map, spots, valuation_date, risk_free_rate=0.065):
    for leg in legs:
        q=quote_map.get(leg.fyers_symbol)
        if not q: raise ValueError(f"No quote returned for requested contract: {leg.fyers_symbol}")
        live_price=next((float(q[k]) for k in ("lp","ltp","last_price") if q.get(k) is not None), None)
        if live_price is None: raise ValueError(f"No LTP in quote for {leg.fyers_symbol}")
        leg.entry_price=live_price
        t=years_to_expiry(leg.expiry, valuation_date)
        market_iv=q.get("iv")
        if market_iv is not None:
            market_iv=float(market_iv); market_iv=market_iv/100 if market_iv>5 else market_iv
        else:
            market_iv=implied_volatility(live_price,float(spots[leg.underlying]),leg.strike,t,rate=risk_free_rate,option_type=leg.option_type)
        if market_iv is not None and market_iv>0: leg.iv=market_iv
    return legs
