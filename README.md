# okx-hedge-bot

Two-sided hedge cycle bot for OKX perpetuals (demo/mock trading first).

## What it does
- Opens **both long + short** on the same perp (OKX long/short mode).
- When one side reaches a **profit target**, it closes that profitable side.
- Keeps the losing side open and waits for a **recovery threshold**, then re-opens the closed side.
- Repeats in cycles.

## Status
WIP scaffold.

## Setup (local)
```bash
cd okx-hedge-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

## Run (demo trading)
```bash
source .venv/bin/activate
python -m bot.main --config config.yaml
```

## Safety notes
- Start with demo trading only.
- Use small notional and leverage=1 until validated.
- Add API keys in `config.yaml` (do not commit).
