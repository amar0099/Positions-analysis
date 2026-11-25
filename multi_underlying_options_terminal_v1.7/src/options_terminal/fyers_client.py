from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any

@dataclass
class FyersMarketData:
    client_id: str
    access_token: str
    quote_batch_size: int = 50

    @classmethod
    def from_env(cls):
        client_id = os.getenv("FYERS_CLIENT_ID", "")
        token = os.getenv("FYERS_ACCESS_TOKEN", "")
        if not client_id or not token:
            raise RuntimeError("Set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN in .env")
        return cls(client_id, token)

    def _client(self):
        from fyers_apiv3 import fyersModel
        return fyersModel.FyersModel(client_id=self.client_id, token=self.access_token, is_async=False, log_path="")

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        symbols = list(dict.fromkeys(s.strip() for s in symbols if s and s.strip()))
        out = {}
        for i in range(0, len(symbols), self.quote_batch_size):
            batch = symbols[i:i+self.quote_batch_size]
            response = self._client().quotes({"symbols": ",".join(batch)})
            if response.get("s") != "ok":
                raise RuntimeError(f"FYERS quotes error: {response}")
            for item in response.get("d", []):
                name = item.get("n") or item.get("symbol") or item.get("v", {}).get("symbol")
                if name:
                    out[name] = item.get("v", {})
        return out

    @staticmethod
    def spot_from_quote_map(quote_map: dict[str, dict[str, Any]], symbol: str) -> float:
        values = quote_map.get(symbol, {})
        for key in ("lp", "ltp", "last_price"):
            if values.get(key) is not None: return float(values[key])
        raise RuntimeError(f"Could not find LTP for {symbol}: {values}")
