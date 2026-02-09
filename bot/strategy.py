from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from rich.console import Console

from bot.okx_client import OKXClient

console = Console()
PosSide = Literal["long", "short"]


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


@dataclass
class HedgeCycleStrategy:
    client: OKXClient
    cfg: dict[str, Any]

    def _bot(self) -> dict[str, Any]:
        return self.cfg["bot"]

    async def _ensure_long_short_mode(self) -> None:
        # Best-effort; OKX may reject if already set.
        try:
            await self.client.set_position_mode("long_short_mode")
        except Exception as e:
            console.print(f"[yellow]pos mode set skipped/failed:[/] {e}")

    async def _fetch_upnl(self) -> dict[PosSide, float]:
        inst = self._bot()["instrument_id"]
        data = await self.client.positions(inst)
        ups: dict[PosSide, float] = {"long": 0.0, "short": 0.0}
        for p in data.get("data", []):
            side = p.get("posSide")
            if side in ("long", "short"):
                ups[side] = _to_float(p.get("upl"), 0.0)  # upl: unrealized PnL
        return ups

    async def _open_both_sides_if_needed(self) -> None:
        # Placeholder: open market orders sized by notional.
        # We’ll refine after Zach confirms sizing rules + contract specs.
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "cross")
        notional = float(self._bot().get("notional_usdt", 20))

        # TODO: OKX requires sz in contract units, not notional. We need to query instrument details
        # and compute size. For now, we do nothing until sizing is implemented.
        console.print(
            f"[yellow]TODO:[/] Implement sizing + open both sides (inst={inst}, tdMode={td_mode}, notional~{notional} USDT)"
        )

    async def _close_side(self, side: PosSide) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "cross")
        console.print(f"Closing {side} position on {inst} ({td_mode})")
        await self.client.close_position(inst_id=inst, mgn_mode=td_mode, pos_side=side)

    async def tick(self) -> None:
        if self.cfg.get("risk", {}).get("kill_switch"):
            console.print("[red]KILL SWITCH enabled — not trading.[/]")
            return

        await self._ensure_long_short_mode()

        upnl = await self._fetch_upnl()
        tp = float(self._bot().get("take_profit_usdt", 0.3))
        recovery = float(self._bot().get("recovery_usdt", -0.05))

        console.print(f"uPnL long={upnl['long']:.4f} short={upnl['short']:.4f}")

        # First run: if no positions, open both.
        if abs(upnl["long"]) < 1e-9 and abs(upnl["short"]) < 1e-9:
            await self._open_both_sides_if_needed()
            return

        # Take profit on the winning side.
        if upnl["long"] >= tp:
            await self._close_side("long")
            return
        if upnl["short"] >= tp:
            await self._close_side("short")
            return

        # Recovery rule placeholder:
        # If one side is flat (0) and the other recovered to >= threshold, re-open the missing side.
        # We'll implement after we track actual position sizes and whether a side is open.
        if abs(upnl["long"]) < 1e-9 and upnl["short"] >= recovery:
            console.print("[yellow]TODO:[/] Re-open long side (recovery reached)")
            return
        if abs(upnl["short"]) < 1e-9 and upnl["long"] >= recovery:
            console.print("[yellow]TODO:[/] Re-open short side (recovery reached)")
            return
