from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class TelegramNotifier:
    token: str
    chat_id: str
    enabled: bool = True

    async def send(self, text: str) -> None:
        if not self.enabled or not self.token or not self.chat_id:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
