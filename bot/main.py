from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import os

import yaml
from rich.console import Console

from bot.okx_client import OKXClient
from bot.strategy import HedgeCycleStrategy

console = Console()


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


async def run(config_path: Path) -> None:
    cfg = load_config(config_path)

    api_key = cfg["okx"].get("api_key") or os.getenv("OKX_API_KEY") or ""
    api_secret = cfg["okx"].get("api_secret") or os.getenv("OKX_API_SECRET") or ""
    passphrase = cfg["okx"].get("passphrase") or os.getenv("OKX_PASSPHRASE") or ""

    client = OKXClient(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        simulated=bool(cfg["okx"].get("simulated", True)),
    )

    if not client.api_key or not client.api_secret or not client.passphrase:
        raise SystemExit(
            "Missing OKX credentials. Set OKX_API_KEY/OKX_API_SECRET/OKX_PASSPHRASE env vars or fill config.yaml (not committed)."
        )

    strat = HedgeCycleStrategy(client=client, cfg=cfg)

    poll = int(cfg["bot"].get("poll_seconds", 10))
    console.print(f"Running hedge-cycle bot (poll={poll}s, simulated={client.simulated})")

    while True:
        await strat.tick()
        await asyncio.sleep(poll)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    asyncio.run(run(Path(args.config)))


if __name__ == "__main__":
    main()
