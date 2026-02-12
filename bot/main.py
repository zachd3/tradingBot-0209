from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml
from rich.console import Console

from bot.okx_client import OKXClient
from bot.strategy import HedgeCycleStrategy

console = Console()


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


async def run(config_path: Path) -> None:
    cfg = load_config(config_path)

    client = OKXClient(
        api_key=cfg["okx"]["api_key"],
        api_secret=cfg["okx"]["api_secret"],
        passphrase=cfg["okx"]["passphrase"],
        simulated=bool(cfg["okx"].get("simulated", True)),
    )

    strat = HedgeCycleStrategy(client=client, cfg=cfg)

    poll = int(cfg["bot"].get("poll_seconds", 10))
    err_sleep = int(cfg["bot"].get("error_retry_seconds", 8))
    console.print(f"Running hedge-cycle bot (poll={poll}s, simulated={client.simulated})")

    while True:
        try:
            await strat.tick()
            await asyncio.sleep(poll)
        except Exception as e:
            console.print(f"[red]tick error:[/] {e}")
            try:
                await strat._notify(f"⚠️ Runtime error: {e}. Retrying in {err_sleep}s")
            except Exception:
                pass
            await asyncio.sleep(err_sleep)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    asyncio.run(run(Path(args.config)))


if __name__ == "__main__":
    main()
