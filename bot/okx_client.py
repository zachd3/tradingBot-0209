from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx


@dataclass
class OKXClient:
    api_key: str
    api_secret: str
    passphrase: str
    simulated: bool = True
    base_url: str = "https://www.okx.com"

    def _timestamp(self) -> str:
        # OKX expects ISO8601 like 2020-12-08T09:08:57.715Z
        # We'll use milliseconds UTC.
        ms = int(time.time() * 1000)
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + f".{ms % 1000:03d}Z"

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        msg = f"{ts}{method.upper()}{path}{body}".encode()
        secret = self.api_secret.encode()
        sig = hmac.new(secret, msg, hashlib.sha256).digest()
        return base64.b64encode(sig).decode()

    def _headers(self, ts: str, method: str, path: str, body: str) -> dict[str, str]:
        h = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.simulated:
            # OKX demo trading
            h["x-simulated-trading"] = "1"
        return h

    async def request(self, method: str, path: str, params: Optional[dict[str, Any]] = None, json_body: Any = None) -> dict[str, Any]:
        query = ""
        if params:
            query = "?" + urlencode({k: v for k, v in params.items() if v is not None})

        path_with_query = path + query
        url = self.base_url + path_with_query
        body_str = "" if json_body is None else json.dumps(json_body, separators=(",", ":"))
        ts = self._timestamp()
        headers = self._headers(ts, method, path_with_query, body_str)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, url, content=body_str if body_str else None, headers=headers)

        data = resp.json()
        if resp.status_code >= 400 or data.get("code") not in ("0", 0, None):
            raise RuntimeError(f"OKX error: status={resp.status_code} body={data}")
        return data

    # ---- Convenience wrappers ----

    async def public_request(self, method: str, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        query = ""
        if params:
            query = "?" + urlencode({k: v for k, v in params.items() if v is not None})
        url = self.base_url + path + query

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, url)

        data = resp.json()
        if resp.status_code >= 400 or data.get("code") not in ("0", 0, None):
            raise RuntimeError(f"OKX public error: status={resp.status_code} body={data}")
        return data

    async def account_config(self) -> dict[str, Any]:
        return await self.request("GET", "/api/v5/account/config")

    async def set_position_mode(self, pos_mode: str) -> dict[str, Any]:
        # pos_mode: "long_short_mode" or "net_mode"
        return await self.request("POST", "/api/v5/account/set-position-mode", json_body={"posMode": pos_mode})

    async def set_leverage(self, inst_id: str, lever: float, mgn_mode: str, pos_side: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"instId": inst_id, "lever": str(lever), "mgnMode": mgn_mode}
        if pos_side:
            payload["posSide"] = pos_side
        return await self.request("POST", "/api/v5/account/set-leverage", json_body=payload)

    async def positions(self, inst_id: str) -> dict[str, Any]:
        return await self.request("GET", "/api/v5/account/positions", params={"instId": inst_id})

    async def instruments(self, inst_type: str, inst_id: Optional[str] = None) -> dict[str, Any]:
        return await self.public_request("GET", "/api/v5/public/instruments", params={"instType": inst_type, "instId": inst_id})

    async def ticker(self, inst_id: str) -> dict[str, Any]:
        return await self.public_request("GET", "/api/v5/market/ticker", params={"instId": inst_id})

    async def candles(self, inst_id: str, bar: str = "1m", limit: int = 30) -> dict[str, Any]:
        return await self.public_request(
            "GET",
            "/api/v5/market/candles",
            params={"instId": inst_id, "bar": bar, "limit": limit},
        )

    async def place_order(self, **payload: Any) -> dict[str, Any]:
        return await self.request("POST", "/api/v5/trade/order", json_body=payload)

    async def close_position(self, inst_id: str, mgn_mode: str, pos_side: str) -> dict[str, Any]:
        # pos_side: long|short
        return await self.request(
            "POST",
            "/api/v5/trade/close-position",
            json_body={"instId": inst_id, "mgnMode": mgn_mode, "posSide": pos_side},
        )
