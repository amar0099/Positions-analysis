from src.options_terminal.pricing import black_scholes, implied_volatility

def test_call_price_positive():
    x=black_scholes(100,100,30/365,.20,.06,0,"CE")
    assert x.price>0 and x.greeks.gamma>0

def test_put_call_symmetry_basic():
    c=black_scholes(100,100,30/365,.20,0,0,"CE").price
    p=black_scholes(100,100,30/365,.20,0,0,"PE").price
    assert abs(c-p)<.1

def test_iv_roundtrip():
    p=black_scholes(100,100,45/365,.27,.06,0,"CE").price
    iv=implied_volatility(p,100,100,45/365,.06,0,"CE")
    assert iv is not None and abs(iv-.27)<1e-4
