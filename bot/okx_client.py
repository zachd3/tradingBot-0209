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

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Any = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        body_str = "" if json_body is None else json.dumps(json_body, separators=(",", ":"))

        # OKX signature must include the exact request path, including query string.
        request_path = path
        if params:
            # Use stable ordering
            query = urlencode(sorted(((k, str(v)) for k, v in params.items())), doseq=True)
            request_path = f"{path}?{query}"

        ts = self._timestamp()
        headers = self._headers(ts, method, request_path, body_str)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, url, params=params, content=body_str if body_str else None, headers=headers)

        data = resp.json()
        if resp.status_code >= 400 or data.get("code") not in ("0", 0, None):
            raise RuntimeError(f"OKX error: status={resp.status_code} body={data}")
        return data

    # ---- Convenience wrappers ----

    async def account_config(self) -> dict[str, Any]:
        return await self.request("GET", "/api/v5/account/config")

    async def set_position_mode(self, pos_mode: str) -> dict[str, Any]:
        # pos_mode: "long_short_mode" or "net_mode"
        return await self.request("POST", "/api/v5/account/set-position-mode", json_body={"posMode": pos_mode})

    async def positions(self, inst_id: str) -> dict[str, Any]:
        return await self.request("GET", "/api/v5/account/positions", params={"instId": inst_id})

    async def instruments(self, inst_type: str, inst_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return await self.request("GET", "/api/v5/public/instruments", params=params)

    async def tickers(self, inst_type: str, inst_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return await self.request("GET", "/api/v5/market/tickers", params=params)

    async def place_order(self, **payload: Any) -> dict[str, Any]:
        return await self.request("POST", "/api/v5/trade/order", json_body=payload)

    async def close_position(self, inst_id: str, mgn_mode: str, pos_side: str) -> dict[str, Any]:
        # pos_side: long|short
        return await self.request(
            "POST",
            "/api/v5/trade/close-position",
            json_body={"instId": inst_id, "mgnMode": mgn_mode, "posSide": pos_side},
        )
