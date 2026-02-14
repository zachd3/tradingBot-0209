from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from statistics import pstdev
from typing import Any, Literal

from rich.console import Console

from bot.notifier import TelegramNotifier
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
    _pos_mode_ready: bool = False
    _leverage_ready: bool = False
    _preflight_done: bool = False
    _state: dict[str, Any] = field(default_factory=dict)
    _notifier: TelegramNotifier | None = None

    def __post_init__(self) -> None:
        self._load_state()
        tg = self.cfg.get("telegram", {})
        token = str(tg.get("bot_token", "")).strip()
        chat_id = str(tg.get("chat_id", "")).strip()
        enabled = bool(tg.get("enabled", False))
        if enabled and token and chat_id:
            self._notifier = TelegramNotifier(token=token, chat_id=chat_id, enabled=True)

    def _bot(self) -> dict[str, Any]:
        return self.cfg["bot"]

    def _risk(self) -> dict[str, Any]:
        return self.cfg.get("risk", {})

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _state_path(self) -> Path:
        p = self._bot().get("state_file", "state/hedge_state.json")
        return Path(p)

    def _load_state(self) -> None:
        p = self._state_path()
        if p.exists():
            self._state = json.loads(p.read_text())
        else:
            self._state = {
                "today": self._today(),
                "today_realized_usdt": 0.0,
                "lifetime_realized_usdt": 0.0,
                "closed_legs": 0,
                "halted_today": False,
                "cycle_id": 0,
                "last_action": "",
                "last_action_ts": 0.0,
                "missing_side": "",
            }

        # Backward-compatible defaults for old state files.
        self._state.setdefault("today", self._today())
        self._state.setdefault("today_realized_usdt", 0.0)
        self._state.setdefault("lifetime_realized_usdt", 0.0)
        self._state.setdefault("closed_legs", 0)

        # Migration heuristic: if old state had only today_realized, seed lifetime with it once.
        if float(self._state.get("lifetime_realized_usdt", 0.0)) == 0.0 and float(self._state.get("today_realized_usdt", 0.0)) != 0.0:
            self._state["lifetime_realized_usdt"] = float(self._state.get("today_realized_usdt", 0.0))
        self._state.setdefault("halted_today", False)
        self._state.setdefault("cycle_id", 0)
        self._state.setdefault("last_action", "")
        self._state.setdefault("last_action_ts", 0.0)
        self._state.setdefault("missing_side", "")
        self._save_state()

    def _save_state(self) -> None:
        p = self._state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._state, indent=2))

    async def _notify(self, text: str) -> None:
        prefix = str(self.cfg.get("telegram", {}).get("alert_prefix", "[hedge-bot]"))
        msg = f"{prefix} {text}"
        console.print(f"[cyan]{msg}[/]")
        if self._notifier:
            try:
                await self._notifier.send(msg)
            except Exception as e:
                console.print(f"[yellow]telegram alert failed:[/] {e}")

    async def _maybe_status_push(self, long_open: bool, short_open: bool, long_upl: float, short_upl: float, tp: float, recovery: float) -> None:
        interval = int(self._bot().get("status_push_seconds", 600))
        if interval <= 0:
            return
        now = time.time()
        last = float(self._state.get("last_status_push_ts", 0.0))
        if now - last < interval:
            return
        self._state["last_status_push_ts"] = now
        self._save_state()
        reason = str(self._state.get("last_decision_reason", "n/a"))
        await self._notify(
            f"Status | decision={reason} | long(open={long_open}, upl={long_upl:.4f}) short(open={short_open}, upl={short_upl:.4f}) "
            f"tp={tp:.4f} recovery={recovery:.4f} cycle={self._state.get('cycle_id',0)} "
            f"today={self._state.get('today_realized_usdt',0.0):.4f} total={self._state.get('lifetime_realized_usdt',0.0):.4f}"
        )

    async def _decision_log_once(self, reason: str) -> None:
        prev = str(self._state.get("last_decision_reason", ""))
        if reason == prev:
            return
        self._state["last_decision_reason"] = reason
        self._save_state()
        await self._notify(f"Decision: hold ({reason})")

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if self._state.get("today") != today:
            self._state["today"] = today
            self._state["today_realized_usdt"] = 0.0
            self._state["halted_today"] = False
            self._save_state()

    def _can_act_now(self) -> bool:
        cooldown = float(self._bot().get("min_action_interval_seconds", 3))
        return (time.time() - float(self._state.get("last_action_ts", 0.0))) >= cooldown

    def _mark_action(self, action: str) -> None:
        self._state["last_action"] = action
        self._state["last_action_ts"] = time.time()
        self._save_state()

    async def _ensure_long_short_mode(self) -> None:
        if self._pos_mode_ready:
            return
        try:
            await self.client.set_position_mode("long_short_mode")
            self._pos_mode_ready = True
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
            await self._notify(f"Leverage set to {lever}x ({td_mode})")
        except Exception as e:
            console.print(f"[yellow]set leverage skipped/failed:[/] {e}")

    async def _preflight_check(self) -> None:
        if self._preflight_done:
            return

        inst = self._bot()["instrument_id"]
        cfg = await self.client.account_config()
        row = (cfg.get("data") or [{}])[0]
        acct_lv = str(row.get("acctLv", ""))
        pos_mode = str(row.get("posMode", ""))
        perm = str(row.get("perm", ""))

        if "trade" not in perm:
            raise RuntimeError("API key missing trade permission (perm does not include 'trade').")

        # acctLv=1 is Simple mode; perpetual futures orders are rejected with 51010.
        if acct_lv == "1":
            raise RuntimeError(
                "OKX account is in Simple mode (acctLv=1). Please switch to Single-currency or Multi-currency margin mode in OKX app/web."
            )

        if pos_mode != "long_short_mode":
            await self.client.set_position_mode("long_short_mode")
        self._pos_mode_ready = True

        # quick instrument sanity check
        ins = await self.client.instruments("SWAP", inst_id=inst)
        if not ins.get("data"):
            raise RuntimeError(f"Instrument not available for trading: {inst}")

        self._preflight_done = True
        await self._notify(f"Preflight OK | acctLv={acct_lv} posMode=long_short_mode")

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

        raw_contracts = notional / (ct_val * px)
        contracts = (raw_contracts / lot_sz).to_integral_value(rounding=ROUND_DOWN) * lot_sz
        if contracts < min_sz:
            contracts = min_sz

        return format(contracts.normalize(), "f")

    async def _market_filter_ok(self) -> tuple[bool, str]:
        inst = self._bot()["instrument_id"]

        ticker = await self.client.ticker(inst)
        t = ticker.get("data", [])[0]
        bid = _to_float(t.get("bidPx"), 0.0)
        ask = _to_float(t.get("askPx"), 0.0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 9999.0

        max_spread_bps = float(self._bot().get("max_spread_bps", 10.0))
        if spread_bps > max_spread_bps:
            return False, f"spread too wide {spread_bps:.2f}bps > {max_spread_bps:.2f}bps"

        candles = await self.client.candles(inst, bar=str(self._bot().get("vol_bar", "1m")), limit=int(self._bot().get("vol_lookback", 30)))
        rows = candles.get("data", [])
        closes = [_to_float(r[4], 0.0) for r in rows if len(r) >= 5]
        closes = [c for c in closes if c > 0]
        if len(closes) >= 8:
            rets = []
            for i in range(len(closes) - 1):
                r = math.log(closes[i] / closes[i + 1])
                rets.append(r)
            vol_bps = pstdev(rets) * 10000 if rets else 0.0
            max_vol_bps = float(self._bot().get("max_vol_bps", 35.0))
            if vol_bps > max_vol_bps:
                return False, f"vol too high {vol_bps:.2f}bps > {max_vol_bps:.2f}bps"

        return True, "ok"

    async def _open_side(self, side: PosSide, reason: str) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "isolated")
        side_for_order = "buy" if side == "long" else "sell"
        sz = await self._target_contracts()

        await self.client.place_order(
            instId=inst,
            tdMode=td_mode,
            side=side_for_order,
            posSide=side,
            ordType="market",
            sz=sz,
        )
        self._mark_action(f"open_{side}")
        await self._notify(f"Opened {side} | {inst} sz={sz} | why: {reason}")

    async def _close_side(self, side: PosSide, est_upl: float, reason: str) -> None:
        inst = self._bot()["instrument_id"]
        td_mode = self._bot().get("td_mode", "isolated")
        await self.client.close_position(inst_id=inst, mgn_mode=td_mode, pos_side=side)

        self._state["today_realized_usdt"] = float(self._state.get("today_realized_usdt", 0.0)) + est_upl
        self._state["lifetime_realized_usdt"] = float(self._state.get("lifetime_realized_usdt", 0.0)) + est_upl
        self._state["closed_legs"] = int(self._state.get("closed_legs", 0)) + 1
        self._state["missing_side"] = side
        self._mark_action(f"close_{side}")
        self._save_state()
        await self._notify(
            f"Closed {side} TP hit | leg={est_upl:.4f} USDT | total={self._state['lifetime_realized_usdt']:.4f} USDT "
            f"| today={self._state['today_realized_usdt']:.4f} USDT | closed_legs={self._state['closed_legs']} | why: {reason}"
        )

    def _effective_tp_usdt(self) -> float:
        bot = self._bot()
        configured = float(bot.get("take_profit_usdt", 0.6))
        notional = float(bot.get("notional_usdt", 25.0))
        taker_fee = float(bot.get("taker_fee_rate", 0.0005))
        fee_buffer_mult = float(bot.get("fee_buffer_mult", 2.2))
        min_edge = float(bot.get("min_edge_usdt", 0.15))
        fee_floor = notional * taker_fee * fee_buffer_mult + min_edge
        return max(configured, fee_floor)

    def _daily_stop_hit(self) -> bool:
        max_loss = float(self._risk().get("max_daily_loss_usdt", 8.0))
        realized = float(self._state.get("today_realized_usdt", 0.0))
        return realized <= -abs(max_loss)

    async def tick(self) -> None:
        self._roll_day_if_needed()

        if self.cfg.get("risk", {}).get("kill_switch"):
            console.print("[red]KILL SWITCH enabled — not trading.[/]")
            return

        if bool(self._state.get("halted_today", False)):
            console.print("[red]Daily stop already hit; halted for today.[/]")
            return

        if self._daily_stop_hit():
            self._state["halted_today"] = True
            self._save_state()
            await self._notify("Daily realized loss limit reached. Trading halted for today.")
            return

        await self._preflight_check()
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
        await self._maybe_status_push(long_open, short_open, long_upl, short_upl, tp, recovery)

        if not self._can_act_now():
            return

        # First run / restart recovery
        if not long_open and not short_open:
            ok, reason = await self._market_filter_ok()
            if not ok:
                console.print(f"[yellow]entry blocked:[/] {reason}")
                await self._decision_log_once(f"entry blocked: {reason}")
                return
            base_reason = "entry allowed: spread/vol filters passed and no open positions"
            await self._open_side("long", reason=base_reason)
            await self._open_side("short", reason=base_reason)
            self._state["cycle_id"] = int(self._state.get("cycle_id", 0)) + 1
            self._state["missing_side"] = ""
            self._save_state()
            await self._notify(f"Cycle #{self._state['cycle_id']} started (both sides open) | why: neutral hedge entry after filters passed")
            return

        # Keep hedged structure: if one side missing and recovery reached on opposite side, re-open missing side.
        if not long_open and short_open and short_upl >= recovery:
            ok, reason = await self._market_filter_ok()
            if not ok:
                console.print(f"[yellow]re-entry blocked:[/] {reason}")
                await self._decision_log_once(f"re-entry blocked: {reason}")
                return
            await self._open_side("long", reason=f"re-entry: short upl {short_upl:.4f} >= recovery {recovery:.4f}")
            self._state["missing_side"] = ""
            self._state["cycle_id"] = int(self._state.get("cycle_id", 0)) + 1
            self._save_state()
            await self._notify(
                f"Re-opened long after recovery. Cycle #{self._state['cycle_id']} | why: short recovery condition met"
            )
            return

        if not short_open and long_open and long_upl >= recovery:
            ok, reason = await self._market_filter_ok()
            if not ok:
                console.print(f"[yellow]re-entry blocked:[/] {reason}")
                await self._decision_log_once(f"re-entry blocked: {reason}")
                return
            await self._open_side("short", reason=f"re-entry: long upl {long_upl:.4f} >= recovery {recovery:.4f}")
            self._state["missing_side"] = ""
            self._state["cycle_id"] = int(self._state.get("cycle_id", 0)) + 1
            self._save_state()
            await self._notify(
                f"Re-opened short after recovery. Cycle #{self._state['cycle_id']} | why: long recovery condition met"
            )
            return

        # Take profit on whichever open side reaches threshold.
        if long_open and long_upl >= tp:
            await self._close_side("long", est_upl=long_upl, reason=f"TP reached: long upl {long_upl:.4f} >= {tp:.4f}")
            return

        if short_open and short_upl >= tp:
            await self._close_side("short", est_upl=short_upl, reason=f"TP reached: short upl {short_upl:.4f} >= {tp:.4f}")
            return

        await self._decision_log_once("waiting for recovery/TP")
