from __future__ import annotations
import pandas as pd
from .models import OptionLeg, parse_expiry

REQUIRED_COLUMNS = ["Underlying", "Expiry", "OptionType", "Position", "Strike", "Quantity", "EntryPrice", "FYERSSymbol"]
ALIASES = {"underlying":"Underlying","symbol":"FYERSSymbol","fyers_symbol":"FYERSSymbol","expiry":"Expiry","expiry_date":"Expiry",
"optiontype":"OptionType","type":"OptionType","option_type":"OptionType","position":"Position","side":"Position","strike":"Strike",
"quantity":"Quantity","qty":"Quantity","entryprice":"EntryPrice","entry_price":"EntryPrice","averageprice":"EntryPrice","avgprice":"EntryPrice",
"avg_price":"EntryPrice","multiplier":"Multiplier","iv":"IV","iv%":"IV","iv_pct":"IV"}

def normalize_columns(df):
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower().replace(" ", "").replace("-", "_")
        rename[col] = ALIASES.get(key, str(col).strip())
    return df.rename(columns=rename)

def parse_positions(df, default_multipliers=None, default_iv=0.20):
    default_multipliers = default_multipliers or {"NIFTY":65.0,"SENSEX":20.0}
    df = normalize_columns(df.copy())
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing: raise ValueError("Missing required CSV columns: " + ", ".join(missing))
    if "Multiplier" not in df.columns: df["Multiplier"] = df["Underlying"].astype(str).str.upper().map(default_multipliers)
    if "IV" not in df.columns: df["IV"] = default_iv
    legs=[]; errors=[]; clean=[]
    for idx,row in df.iterrows():
        try:
            iv=float(row["IV"]); iv=iv/100 if iv>5 else iv
            leg=OptionLeg(underlying=str(row["Underlying"]).strip().upper(), option_type=str(row["OptionType"]).strip().upper(),
                          position=str(row["Position"]).strip().upper(), strike=float(row["Strike"]), quantity=float(row["Quantity"]),
                          entry_price=float(row["EntryPrice"]), expiry=parse_expiry(row["Expiry"]), iv=iv,
                          multiplier=float(row["Multiplier"]), fyers_symbol=str(row["FYERSSymbol"]).strip(), label=str(row.get("Label","")))
            leg.validate(); legs.append(leg)
            clean.append({"row":idx+2,"FYERS Symbol":leg.fyers_symbol,"Underlying":leg.underlying,"Expiry":leg.expiry.isoformat(),
                          "OptionType":leg.option_type,"Position":leg.position,"Strike":leg.strike,"Quantity":leg.quantity,
                          "EntryPrice":leg.entry_price,"IV %":leg.iv*100,"Multiplier":leg.multiplier})
        except Exception as exc: errors.append(f"CSV row {idx+2}: {exc}")
    if errors: raise ValueError("\n".join(errors))
    return legs,pd.DataFrame(clean)
