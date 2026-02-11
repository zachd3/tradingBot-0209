from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from bot.okx_client import OKXClient


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


async def run(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    client = OKXClient(
        api_key=cfg["okx"]["api_key"],
        api_secret=cfg["okx"]["api_secret"],
        passphrase=cfg["okx"]["passphrase"],
        simulated=bool(cfg["okx"].get("simulated", True)),
    )

    inst = cfg["bot"]["instrument_id"]
    pos = await client.positions(inst)
    out = {
        "instrument_id": inst,
        "simulated": bool(cfg["okx"].get("simulated", True)),
        "positions": {"long": {"open": False, "sz": 0.0, "upl": 0.0}, "short": {"open": False, "sz": 0.0, "upl": 0.0}},
    }

    for p in pos.get("data", []):
        side = p.get("posSide")
        if side in ("long", "short"):
            sz = abs(_to_float(p.get("pos"), 0.0))
            out["positions"][side] = {"open": sz > 0.0, "sz": sz, "upl": _to_float(p.get("upl"), 0.0)}

    state_path = Path(cfg.get("bot", {}).get("state_file", "state/hedge_state.json"))
    if state_path.exists():
        out["state"] = json.loads(state_path.read_text())

    print(json.dumps(out, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    asyncio.run(run(Path(args.config)))


if __name__ == "__main__":
    main()
