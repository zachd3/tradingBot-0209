from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from rich.console import Console

from bot.models import Instrument, Position
from bot.okx_client import OKXClient
from bot.sizing import compute_contract_size

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

    async def _fetch_positions(self) -> dict[PosSide, Position | None]:
        inst = self._bot()["instrument_id"]
        data = await self.client.positions(inst)
        out: dict[PosSide, Position | None] = {"long": None, "short": None}
        for p in data.get("data", []):
            side = p.get("posSide")
            if side in ("long", "short"):
                out[side] = Position.model_validate(p)
        return out

    async def _fetch_instrument(self) -> Instrument:
        inst = self._bot()["instrument_id"]
        data = await self.client.instruments("SWAP", inst)
        items = data.get("data", [])
        if not items:
            raise RuntimeError(f"Instrument not found: {inst}")
        return Instrument.model_validate(items[0])

    async def _fetch_last_px(self) -> float:
        inst = self._bot()["instrument_id"]
        data = await self.client.tickers("SWAP", inst)
        items = data.get("data", [])
        if not items:
            raise RuntimeError(f"Ticker not found: {inst}")
        return _to_float(items[0].get("last"), 0.0)

    def _is_open(self, p: Position | None) -> bool:
        if p is None:
            return False
        return _to_float(p.pos, 0.0) > 0

    async def _open_side_market(self, side: PosSide, sz: str) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "isolated")
        console.print(f"Opening {side} market sz={sz} on {inst} ({td_mode})")

        # For SWAP in long/short mode: side is buy/sell, posSide is long/short
        order_side = "buy" if side == "long" else "sell"
        payload = dict(
            instId=inst,
            tdMode=td_mode,
            side=order_side,
            ordType="market",
            sz=sz,
            posSide=side,
        )
        if bool(self._bot().get("dry_run", True)):
            console.print(f"[yellow]DRY RUN order payload:[/] {payload}")
            return
        await self.client.place_order(**payload)

    async def _open_both_sides_if_needed(self) -> None:
        inst = self._bot()["instrument_id"]
        notional = float(self._bot().get("notional_usdt", 20))

        instrument = await self._fetch_instrument()
        last_px = await self._fetch_last_px()
        sr = compute_contract_size(instrument=instrument, last_px=last_px, target_notional=notional)
        console.print(
            f"Sizing: target_notional={notional} last={last_px} ctVal={instrument.ctVal} -> sz={sr.sz} (~{sr.approx_notional:.2f} USDT)"
        )

        pos = await self._fetch_positions()
        if not self._is_open(pos["long"]):
            await self._open_side_market("long", sr.sz)
        if not self._is_open(pos["short"]):
            await self._open_side_market("short", sr.sz)

    async def _close_side(self, side: PosSide) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "cross")
        console.print(f"Closing {side} position on {inst} ({td_mode})")
        if bool(self._bot().get("dry_run", True)):
            console.print(f"[yellow]DRY RUN close-position:[/] instId={inst} posSide={side} mgnMode={td_mode}")
            return
        await self.client.close_position(inst_id=inst, mgn_mode=td_mode, pos_side=side)

    async def tick(self) -> None:
        if self.cfg.get("risk", {}).get("kill_switch"):
            console.print("[red]KILL SWITCH enabled — not trading.[/]")
            return

        if not self.client.simulated:
            raise RuntimeError("Refusing to run with simulated=false. Set okx.simulated=true for demo.")

        if bool(self._bot().get("dry_run", True)):
            console.print("[yellow]DRY RUN enabled — will not place orders.[/]")

        await self._ensure_long_short_mode()

        pos = await self._fetch_positions()
        upnl = {
            "long": _to_float(pos["long"].upl if pos["long"] else 0.0, 0.0),
            "short": _to_float(pos["short"].upl if pos["short"] else 0.0, 0.0),
        }
        tp = float(self._bot().get("take_profit_usdt", 0.3))
        recovery = float(self._bot().get("recovery_usdt", -0.05))

        console.print(
            f"uPnL long={upnl['long']:.4f} short={upnl['short']:.4f} | open long={self._is_open(pos['long'])} short={self._is_open(pos['short'])}"
        )

        # Ensure both sides are open (initial / after re-open).
        if not self._is_open(pos["long"]) or not self._is_open(pos["short"]):
            # Recovery gating: only re-open the missing side when the remaining side recovered.
            if not self._is_open(pos["long"]) and self._is_open(pos["short"]):
                if upnl["short"] >= recovery:
                    await self._open_both_sides_if_needed()
                return
            if not self._is_open(pos["short"]) and self._is_open(pos["long"]):
                if upnl["long"] >= recovery:
                    await self._open_both_sides_if_needed()
                return

            # If both are closed/flat, open both.
            await self._open_both_sides_if_needed()
            return

        # Take profit on the winning side.
        if upnl["long"] >= tp:
            await self._close_side("long")
            return
        if upnl["short"] >= tp:
            await self._close_side("short")
            return
