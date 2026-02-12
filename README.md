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
cd tradingBot-0209
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

## Telegram alerts (optional)
In `config.yaml`:
```yaml
telegram:
  enabled: true
  bot_token: "<YOUR_BOT_TOKEN>"
  chat_id: "<YOUR_CHAT_ID>"
```

## Quick status dashboard
```bash
source .venv/bin/activate
python -m bot.status --config config.yaml
```

## Safety notes
- Start with demo trading only.
- Current default is leverage=10 with conservative per-side notional; tune only after validating fills and fee behavior.
- `take_profit_usdt` is fee-aware (bot applies a fee floor using `taker_fee_rate`, `fee_buffer_mult`, and `min_edge_usdt`).
- Entry/re-entry is blocked automatically when spread/volatility exceeds configured limits.
- Bot persists cycle state in `state/hedge_state.json` for restart-safe behavior.
- Optional Telegram alerts can be enabled in `config.yaml`.
- Bot now retries transient API/network errors and keeps running.
- `status_push_seconds` controls periodic status updates to Telegram.
- Add API keys in `config.yaml` (do not commit).
