import os, base64, hashlib, re
from datetime import date
from collections import defaultdict
import requests, pyotp

try:
    import streamlit as st
except Exception:
    st = None

try:
    from fyers_apiv3 import fyersModel
except Exception:
    fyersModel = None

CLIENT_ID = os.environ.get("FYERS_CLIENT_ID", "YOUR_APP_ID-100")
SECRET_KEY = os.environ.get("FYERS_SECRET_KEY", "YOUR_SECRET_KEY")
TOKEN_FILE = "access_token.txt"

UNDERLYING_SYM = {
    "SENSEX":"BSE:SENSEX-INDEX", "BANKEX":"BSE:BANKEX-INDEX",
    "NIFTY":"NSE:NIFTY50-INDEX", "BANKNIFTY":"NSE:NIFTYBANK-INDEX",
    "FINNIFTY":"NSE:FINNIFTY-INDEX", "MIDCPNIFTY":"NSE:MIDCPNIFTY-INDEX",
}
MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]


def get_secret(key):
    try:
        if st is not None and key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.environ.get(key, "")


def b64(value):
    return base64.b64encode(str(value).encode()).decode()


def generate_token(client_id, secret_key, username, pin, totp_key):
    """Same TOTP/PIN authentication flow used by the supplied working project."""
    redirect_uri = "http://127.0.0.1:8080/"
    try:
        s = requests.Session()
        r1 = s.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
                    json={"fy_id": b64(username), "app_id":"2"}, timeout=10)
        if r1.status_code == 429:
            return None, "Rate limited by Fyers (429). Wait and retry."
        r1d = r1.json()
        if r1d.get("s") != "ok": return None, f"Step 1 failed: {r1d}"
        r2 = s.post("https://api-t2.fyers.in/vagator/v2/verify_otp",
                    json={"request_key":r1d["request_key"], "otp":pyotp.TOTP(totp_key).now()}, timeout=10)
        r2d = r2.json()
        if r2d.get("s") != "ok": return None, f"Step 2 failed: {r2d}"
        r3 = s.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2",
                    json={"request_key":r2d["request_key"], "identity_type":"pin", "identifier":b64(pin)}, timeout=10)
        r3d = r3.json()
        if r3d.get("s") != "ok": return None, f"Step 3 failed: {r3d}"
        app_id = client_id.split("-")[0]
        r4 = s.post("https://api-t1.fyers.in/api/v3/token", json={
            "fyers_id":username,"app_id":app_id,"redirect_uri":redirect_uri,
            "appType":"100","code_challenge":"","state":"sample","scope":"",
            "nonce":"","response_type":"code","create_cookie":True},
            headers={"Authorization":f"Bearer {r3d['data']['access_token']}"}, timeout=10)
        r4d = r4.json()
        # FYERS may return HTTP 308 here.  That is not a failure: the
        # response contains the authorization URL and auth_code.
        auth_code = None
        if isinstance(r4d, dict):
            data = r4d.get("data") or {}
            auth_code = (
                data.get("authorization_code")
                or data.get("auth_code")
                or r4d.get("authorization_code")
                or r4d.get("auth_code")
            )
            if not auth_code:
                # Current response uses capitalized `Url`.
                url = (data.get("url") or data.get("Url")
                       or r4d.get("url") or r4d.get("Url") or "")
                from urllib.parse import urlparse, parse_qs
                try:
                    auth_code = parse_qs(urlparse(url).query).get("auth_code", [None])[0]
                except Exception:
                    auth_code = None
                if not auth_code:
                    m = re.search(r"[?&]auth_code=([^&]+)", str(url))
                    if m:
                        auth_code = m.group(1)
        if not auth_code:
            return None, f"Token step failed: {r4d}"
        # FYERS appIdHash uses the FULL API/app ID, including the -100 suffix.
        # The token endpoint above uses the base app id, but hash validation does not.
        full_app_id = str(client_id).strip()
        checksum = hashlib.sha256(f"{full_app_id}:{secret_key}".encode()).hexdigest()
        r5 = s.post("https://api-t1.fyers.in/api/v3/validate-authcode",
                    json={"grant_type":"authorization_code","appIdHash":checksum,"code":auth_code}, timeout=10)
        r5d = r5.json()
        if r5d.get("s") == "ok":
            return r5d.get("access_token") or r5d.get("data",{}).get("access_token"), None

        # Keep the SDK fallback used by the supplied working dashboard.
        # This is useful if FYERS changes the direct validation response.
        if fyersModel is not None:
            try:
                session = fyersModel.SessionModel(
                    client_id=client_id,
                    secret_key=secret_key,
                    redirect_uri=redirect_uri,
                    response_type="code",
                    grant_type="authorization_code",
                )
                session.set_token(auth_code)
                sdk_resp = session.generate_token()
                sdk_token = (sdk_resp or {}).get("access_token")
                if sdk_token:
                    return sdk_token, None
            except Exception:
                pass
        return None, f"Auth-code validation failed: {r5d}"
    except Exception as e:
        return None, str(e)


def get_shared_token(force_refresh=False):
    vals = {k:get_secret(k) for k in ["FYERS_CLIENT_ID","FYERS_SECRET_KEY","FYERS_USERNAME","FYERS_PIN","FYERS_TOTP_KEY"]}
    missing = [k for k,v in vals.items() if not v]
    if missing: return None, "Missing credentials: " + ", ".join(missing)
    if not force_refresh:
        try:
            with open(TOKEN_FILE) as f:
                token=f.read().strip()
            if token: return token, None
        except FileNotFoundError: pass
    token, err = generate_token(vals["FYERS_CLIENT_ID"],vals["FYERS_SECRET_KEY"],vals["FYERS_USERNAME"],vals["FYERS_PIN"],vals["FYERS_TOTP_KEY"])
    if token:
        try:
            with open(TOKEN_FILE,"w") as f: f.write(token)
        except Exception: pass
    return token, err


def get_fyers_client(force_refresh=False):
    if fyersModel is None: raise RuntimeError("fyers-apiv3 is not installed. Run pip install -r requirements.txt")
    token, err = get_shared_token(force_refresh)
    if not token: raise RuntimeError(err)
    cid = get_secret("FYERS_CLIENT_ID") or CLIENT_ID
    return fyersModel.FyersModel(client_id=cid, token=token, log_path="")


def normalize_expiry_code(expiry):
    """Normalize CSV/user expiry input to FYERS internal expiry codes.

    Accepted examples:
      13-Oct-2026 -> 261013
      13-OCT-2026 -> 261013
      2026-10-13  -> 261013
      261013      -> 261013
      26OCT       -> 26OCT (monthly)
    """
    e = str(expiry).strip().upper()
    if not e or e == "NAN":
        raise ValueError("Expiry is empty")
    for fmt in ("%d-%b-%Y", "%d/%b/%Y", "%d %b %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(e, fmt).date()
            return d.strftime("%y%m%d")
        except ValueError:
            pass
    # Already an FYERS-style weekly code or monthly code.
    if re.fullmatch(r"\d{6}", e) or re.fullmatch(r"\d{2}[A-Z]{3}", e):
        return e
    raise ValueError(f"Unsupported expiry format: {expiry!r}")


def weekly_symbol_candidates(exchange, underlying, expiry, option_type, strike):
    ot = "CE" if str(option_type).upper() in ("C","CE") else "PE"
    e=normalize_expiry_code(expiry)
    if not re.fullmatch(r"\d{6}", e):
        raise ValueError(f"Weekly expiry requires a date/YYMMDD code, got {expiry!r}")
    yy,mm,dd=e[:2],e[2:4],e[4:6]
    ml={"01":"J","02":"F","03":"M","04":"A","05":"M","06":"J","07":"J","08":"A","09":"S","10":"O","11":"N","12":"D"}[mm]
    # Current FYERS weekly format first; legacy numeric format retained as fallback.
    return list(dict.fromkeys([
        f"{exchange}:{underlying}{yy}{ml}{dd}{strike}{ot}",
        f"{exchange}:{underlying}{yy}{mm}{dd}{strike}{ot}",
    ]))


def build_symbol_candidates(exchange, underlying, expiry, option_type, strike, expiry_kind=None):
    """Return FYERS-compatible candidate symbols for one exact contract.

    expiry_kind may be ``M`` for monthly or ``W`` for weekly.  When a
    full calendar date is supplied, callers should pass the kind obtained
    from FYERS expiryData because the date alone does not tell us whether
    the contract is the monthly expiry.
    """
    ot = "CE" if str(option_type).upper() in ("C","CE") else "PE"
    e=str(expiry).strip().upper()

    # Explicit monthly code such as 26OCT. FYERS monthly symbols use
    # YYMON, e.g. NSE:NIFTY19NOV11500PE.
    if re.fullmatch(r"\d{2}[A-Z]{3}", e):
        return [f"{exchange}:{underlying}{e}{int(strike)}{ot}"]

    if expiry_kind and str(expiry_kind).upper().startswith("M"):
        # Convert a human date to YYMON.
        from datetime import datetime as _dt
        parsed = None
        for fmt in ("%d-%b-%Y", "%d/%b/%Y", "%d %b %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                parsed = _dt.strptime(e, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            if re.fullmatch(r"\d{6}", e):
                parsed = _dt.strptime(e, "%y%m%d")
            else:
                raise ValueError(f"Unsupported monthly expiry format: {expiry!r}")
        code = parsed.strftime("%y%b").upper()
        return [f"{exchange}:{underlying}{code}{int(strike)}{ot}"]

    # Full date / YYMMDD is treated as a weekly expiry.
    return weekly_symbol_candidates(exchange,underlying,e,ot,int(strike))


def build_symbol(exchange, underlying, expiry, option_type, strike):
    return build_symbol_candidates(exchange, underlying, expiry, option_type, strike)[0]

def fetch_expiries(exchange, underlying):
    """Fetch expiryData only; this does not fetch the full option chain."""
    fyers=get_fyers_client()
    sym=UNDERLYING_SYM.get(str(underlying).upper(),f"{exchange}:{underlying}-INDEX")
    resp=fyers.optionchain(data={"symbol":sym,"strikecount":1,"timestamp":""})
    if not resp or resp.get("s")!="ok": raise RuntimeError(f"optionchain failed: {resp}")
    parsed=[]
    for x in resp.get("data",{}).get("expiryData",[]):
        try:
            dd,mm,yyyy=map(int,x.get("date","").split("-"))
            parsed.append((yy:=yyyy%100,mm,dd,MONTHS[mm-1],(x.get("expiry_flag") or "").upper()))
        except Exception: pass
    by=defaultdict(list)
    for yy,mm,dd,mon,flag in parsed: by[(yy,mm)].append(dd)
    out={}
    for yy,mm,dd,mon,flag in parsed:
        monthly = flag=="M" if flag in ("M","W") else dd==max(by[(yy,mm)])
        code=f"{yy:02d}{mon}" if monthly else f"{yy:02d}{mm:02d}{dd:02d}"
        out[f"{dd:02d} {mon} {yy:02d} ({'M' if monthly else 'W'})"]=code
    return out


def fetch_expiry_map(exchange, underlying):
    """Return FYERS expiry information keyed by calendar date.

    Values are ``{"code": ..., "kind": "M"|"W"}``.  This is the
    authoritative way to distinguish a monthly expiry from a weekly expiry
    when the CSV contains a human-readable date.
    """
    fyers=get_fyers_client()
    sym=UNDERLYING_SYM.get(str(underlying).upper(),f"{exchange}:{underlying}-INDEX")
    resp=fyers.optionchain(data={"symbol":sym,"strikecount":1,"timestamp":""})
    if not resp or resp.get("s")!="ok":
        raise RuntimeError(f"optionchain failed for {underlying}: {resp}")
    out={}
    for x in resp.get("data",{}).get("expiryData",[]):
        raw=str(x.get("date","")).strip()
        flag=(x.get("expiry_flag") or "").upper()
        try:
            from datetime import datetime as _dt
            d=_dt.strptime(raw,"%d-%m-%Y").date()
        except Exception:
            continue
        kind=flag if flag in ("M","W") else None
        if kind is None:
            # Fallback only when FYERS does not provide expiry_flag.
            same_month=[
                y for y in resp.get("data",{}).get("expiryData",[])
                if str(y.get("date","")).endswith(f"-{d.month:02d}-{d.year}")
            ]
            kind="M" if raw==max([str(y.get("date","")) for y in same_month] or [raw]) else "W"
        code=d.strftime("%y%b").upper() if kind=="M" else d.strftime("%y%m%d")
        out[d.isoformat()]={"code":code,"kind":kind,"timestamp":x.get("expiry")}
    return out


def _quote_batches(symbols, batch_size=50):
    for i in range(0,len(symbols),batch_size): yield symbols[i:i+batch_size]


def fetch_quotes(fyers, symbols):
    """Fetch requested symbols, batching at FYERS' documented Quotes limit."""
    rows=[]
    for batch in _quote_batches(list(dict.fromkeys(symbols)),50):
        resp=fyers.quotes(data={"symbols":",".join(batch)})
        if not resp or resp.get("s") not in ("ok","partial_success"):
            raise RuntimeError(f"Quotes failed: {resp}")
        rows.extend(resp.get("d",[]))
    out={}
    for item in rows:
        sym=item.get("v",{}).get("symbol") or item.get("symbol")
        if sym: out[sym]=item.get("v",item)
    return out


def spot_quote(fyers, symbol):
    q=fetch_quotes(fyers,[symbol])
    if symbol not in q: raise RuntimeError(f"No quote returned for {symbol}")
    v=q[symbol]
    ltp=v.get("lp") or v.get("last_price") or v.get("ltp")
    return float(ltp), v


def fetch_option_chain(fyers, underlying, exchange=None, strikecount=25, expiry_timestamp=None, greeks=True):
    """Fetch one FYERS option-chain snapshot.

    FYERS' option-chain response supplies contract-level LTP/bid/ask/OI/volume,
    expiry metadata and symbols. Some SDK/API versions also return a nested
    ``greeks`` object. We preserve that object when present; the terminal can
    calculate Greeks from IV when it is absent.
    """
    u=str(underlying).upper()
    ex=exchange or ("BSE" if u in ("SENSEX","BANKEX") else "NSE")
    sym=UNDERLYING_SYM.get(u,f"{ex}:{u}-INDEX")
    data={"symbol":sym,"strikecount":int(max(1,min(50,strikecount))),"timestamp":""}
    # Recent FYERS versions accept greeks=1. Older versions may reject it;
    # retry without it so the rest of the chain remains usable.
    if greeks:
        data["greeks"]="1"
    if expiry_timestamp not in (None, ""):
        data["timestamp"]=str(expiry_timestamp)
    try:
        resp=fyers.optionchain(data=data)
    except Exception:
        if "greeks" in data:
            data.pop("greeks",None)
            resp=fyers.optionchain(data=data)
        else:
            raise
    if not resp or resp.get("s")!="ok":
        if "greeks" in data:
            data.pop("greeks",None)
            resp=fyers.optionchain(data=data)
        if not resp or resp.get("s")!="ok":
            raise RuntimeError(f"Option chain failed for {u}: {resp}")
    return resp


def expiry_records(fyers, underlying, exchange=None):
    """Return FYERS expiryData records with date, timestamp and M/W flag."""
    resp=fetch_option_chain(fyers,underlying,exchange=exchange,strikecount=1,greeks=False)
    records=[]
    for x in resp.get("data",{}).get("expiryData",[]) or []:
        raw=str(x.get("date","")).strip()
        try:
            from datetime import datetime as _dt
            d=_dt.strptime(raw,"%d-%m-%Y").date()
        except Exception:
            continue
        records.append({"date":d,"date_text":raw,"timestamp":x.get("expiry"),"kind":str(x.get("expiry_flag") or "").upper()})
    return records


def option_chain_rows(resp):
    """Flatten the optionsChain array while retaining raw fields."""
    rows=[]
    for item in (resp or {}).get("data",{}).get("optionsChain",[]) or []:
        r=dict(item)
        # Normalise the most common alternate spellings without destroying raw data.
        if "symbol" not in r and r.get("ex_symbol"): r["symbol"]=r.get("ex_symbol")
        r["option_type"]=str(r.get("option_type") or "").upper()
        try: r["strike_price"]=float(r.get("strike_price"))
        except Exception: r["strike_price"]=None
        rows.append(r)
    return rows
