from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
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

    _contract_sz: str | None = None
    _leverage_ready: bool = False

    def _bot(self) -> dict[str, Any]:
        return self.cfg["bot"]

    async def _ensure_long_short_mode(self) -> None:
        # Best-effort; OKX may reject if already set.
        try:
            await self.client.set_position_mode("long_short_mode")
        except Exception as e:
            console.print(f"[yellow]pos mode set skipped/failed:[/] {e}")

    async def _ensure_leverage(self) -> None:
        if self._leverage_ready:
            return
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "isolated")
        lever = float(self._bot().get("leverage", 10))
        try:
            if td_mode == "isolated":
                await self.client.set_leverage(inst_id=inst, lever=lever, mgn_mode=td_mode, pos_side="long")
                await self.client.set_leverage(inst_id=inst, lever=lever, mgn_mode=td_mode, pos_side="short")
            else:
                await self.client.set_leverage(inst_id=inst, lever=lever, mgn_mode=td_mode)
            self._leverage_ready = True
            console.print(f"Leverage set: {lever}x ({td_mode})")
        except Exception as e:
            console.print(f"[yellow]set leverage skipped/failed:[/] {e}")

    async def _fetch_positions(self) -> dict[PosSide, dict[str, float | bool]]:
        inst = self._bot()["instrument_id"]
        data = await self.client.positions(inst)
        out: dict[PosSide, dict[str, float | bool]] = {
            "long": {"open": False, "sz": 0.0, "upl": 0.0},
            "short": {"open": False, "sz": 0.0, "upl": 0.0},
        }

        for p in data.get("data", []):
            side = p.get("posSide")
            if side in ("long", "short"):
                sz = abs(_to_float(p.get("pos"), 0.0))
                upl = _to_float(p.get("upl"), 0.0)
                out[side] = {"open": sz > 0.0, "sz": sz, "upl": upl}
        return out

    async def _load_contract_size(self) -> Decimal:
        if self._contract_sz is not None:
            return Decimal(self._contract_sz)

        inst = self._bot()["instrument_id"]
        data = await self.client.instruments("SWAP", inst_id=inst)
        rows = data.get("data", [])
        if not rows:
            raise RuntimeError(f"No instrument metadata found for {inst}")

        ct_val = rows[0].get("ctVal")
        if ct_val is None:
            raise RuntimeError(f"ctVal missing for instrument {inst}")

        self._contract_sz = str(ct_val)
        return Decimal(self._contract_sz)

    async def _latest_price(self) -> Decimal:
        inst = self._bot()["instrument_id"]
        t = await self.client.ticker(inst)
        rows = t.get("data", [])
        if not rows:
            raise RuntimeError(f"No ticker data for {inst}")
        last = rows[0].get("last")
        if not last:
            raise RuntimeError(f"Ticker last price missing for {inst}")
        return Decimal(str(last))

    async def _target_contracts(self) -> str:
        inst = self._bot()["instrument_id"]
        notional = Decimal(str(self._bot().get("notional_usdt", 25)))

        meta = await self.client.instruments("SWAP", inst_id=inst)
        m = meta.get("data", [])[0]
        lot_sz = Decimal(str(m.get("lotSz", "1")))
        min_sz = Decimal(str(m.get("minSz", lot_sz)))
        ct_val = await self._load_contract_size()
        px = await self._latest_price()

        # For linear USDT swaps: notional ~= contracts * ctVal * price
        raw_contracts = notional / (ct_val * px)
        contracts = (raw_contracts / lot_sz).to_integral_value(rounding=ROUND_DOWN) * lot_sz
        if contracts < min_sz:
            contracts = min_sz

        s = format(contracts.normalize(), "f")
        return s

    async def _open_side(self, side: PosSide) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "isolated")
        side_for_order = "buy" if side == "long" else "sell"
        sz = await self._target_contracts()

        console.print(f"Open {side}: {inst} sz={sz} ({td_mode})")
        await self.client.place_order(
            instId=inst,
            tdMode=td_mode,
            side=side_for_order,
            posSide=side,
            ordType="market",
            sz=sz,
        )

    async def _close_side(self, side: PosSide) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "isolated")
        console.print(f"Closing {side} position on {inst} ({td_mode})")
        await self.client.close_position(inst_id=inst, mgn_mode=td_mode, pos_side=side)

    def _effective_tp_usdt(self) -> float:
        bot = self._bot()
        # A practical default for taker-on-both-legs behavior.
        # One side close per cycle event, plus buffer to avoid fee churn.
        configured = float(bot.get("take_profit_usdt", 0.6))
        notional = float(bot.get("notional_usdt", 25.0))
        taker_fee = float(bot.get("taker_fee_rate", 0.0005))
        fee_buffer_mult = float(bot.get("fee_buffer_mult", 2.2))
        min_edge = float(bot.get("min_edge_usdt", 0.15))
        fee_floor = notional * taker_fee * fee_buffer_mult + min_edge
        return max(configured, fee_floor)

    async def tick(self) -> None:
        if self.cfg.get("risk", {}).get("kill_switch"):
            console.print("[red]KILL SWITCH enabled — not trading.[/]")
            return

        await self._ensure_long_short_mode()
        await self._ensure_leverage()

        pos = await self._fetch_positions()
        long_open = bool(pos["long"]["open"])
        short_open = bool(pos["short"]["open"])
        long_upl = float(pos["long"]["upl"])
        short_upl = float(pos["short"]["upl"])

        tp = self._effective_tp_usdt()
        recovery = float(self._bot().get("recovery_usdt", 0.10))

        console.print(
            f"state long(open={long_open}, upl={long_upl:.4f}) short(open={short_open}, upl={short_upl:.4f}) | tp={tp:.4f} recovery={recovery:.4f}"
        )

        # First run / restart recovery
        if not long_open and not short_open:
            await self._open_side("long")
            await self._open_side("short")
            return

        # Keep hedged structure: if one side missing and recovery reached on opposite side, re-open missing side.
        if not long_open and short_open and short_upl >= recovery:
            await self._open_side("long")
            return
        if not short_open and long_open and long_upl >= recovery:
            await self._open_side("short")
            return

        # Take profit on whichever open side reaches threshold.
        if long_open and long_upl >= tp:
            await self._close_side("long")
            return
        if short_open and short_upl >= tp:
            await self._close_side("short")
            return
