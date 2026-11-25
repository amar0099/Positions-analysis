import pandas as pd
from src.options_terminal.csv_positions import parse_positions

def test_parse_positions():
    df=pd.DataFrame([{"Underlying":"NIFTY","Expiry":"2026-10-13","OptionType":"CE","Position":"BUY","Strike":25000,
                      "Quantity":65,"EntryPrice":185.5,"FYERSSymbol":"NSE:TEST","IV":18,"Multiplier":65}])
    legs,normalized=parse_positions(df)
    assert len(legs)==1 and legs[0].iv==.18 and normalized.iloc[0]["FYERS Symbol"]=="NSE:TEST"
