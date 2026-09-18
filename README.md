# Index Trend Notifier

Real-time EMA(5) / EMA(20) trend-reversal alerts for **HSI futures, SPY and QQQ**, pushed to
Telegram. One config-driven engine, one [FutuOpenD](https://openapi.futunn.com/) gateway, one
thread per instrument.

An alert fires on the *exact* 5-minute candle where the fast EMA crosses the slow EMA, and never
again until the opposite cross occurs — right-side confirmation of a trend change, not a
prediction (see [Disclaimer](#disclaimer)).

## Features

- **Multi-instrument, one process** — HSI futures (incl. the overnight session), SPY and QQQ run
  concurrently, each on its own thread, connection and schedule, off a single gateway.
- **Closed-bar signals only** — the still-forming live candle is dropped, so a cross can't
  "un-happen" when the current bar wiggles back over the line. Every alert is de-duplicated to
  once per candle.
- **Session- & calendar-aware** — uses the exchange trading calendar per market (HK / US) so
  holidays and half-days are handled; sleeps through lunch, overnight and weekends, drops idle
  sockets and rebuilds stale connections. The HSI night session that crosses midnight
  (17:00 → 03:00) is handled explicitly.
- **Config over code** — add or remove an instrument by editing one declarative entry; the engine
  never changes.
- **No secrets in source** — the Telegram token and chat ids are read from the environment.

## Instruments

| Name | Code | Sessions (local) |
|---|---|---|
| HSI Futures | `HK.HSImain` | 09:15–12:00, 13:00–16:30, night 17:00–03:00 |
| SPY (S&P 500) | `US.SPY` | 09:30–16:00 ET |
| QQQ (Nasdaq 100) | `US.QQQ` | 09:30–16:00 ET |

> The provider does not stream US stock indices directly, so **SPY** and **QQQ** are used as ETF
> proxies for the S&P 500 and Nasdaq 100 — they track the indices closely during regular hours.

## How it works

Two clocks. A short **heartbeat** (default 10s) re-checks for a new candle; the **signal** is only
evaluated when a new *closed* 5-minute bar appears — so an alert lands within ~10s of the candle
close, exactly once per bar. On a cross, a formatted message (🟢 bullish / 🔴 bearish) is broadcast
to every configured Telegram chat.

## Requirements

- Python 3.10+
- A running, logged-in **FutuOpenD** gateway (default `127.0.0.1:11111`) with market data enabled
  for the instruments you want. *Note:* the provider cannot stream US **index** candles, which is
  why the ETF proxies above are used.
- A Telegram bot ([@BotFather](https://t.me/BotFather)) and the chat id(s) to post to.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your bot token and chat id(s)
```

To post into a **group**: add your bot to the group, send any command in it (e.g.
`/id@your_bot_username`), then run `python get_chat_id.py` and copy the group's **negative** id
into `TELEGRAM_CHAT_IDS`.

## Usage

```bash
python run.py                 # all instruments
python run.py hsi             # only instruments whose name/code matches
python run.py --dry           # log alerts instead of sending (no Telegram, no credentials needed)
```

## Configuration

All instrument-specific settings live in `config.py` as `Instrument` entries — `code`, timezone,
`sessions` (with midnight-crossing and half-day `phase` handling), `calendar_market`, `fast`/`slow`
EMA spans, and `poll_seconds`. Shared knobs (grace period, idle-disconnect, error threshold,
look-ahead) are constants at the top of the same file. Credentials come from the environment
(`.env`): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS` (comma-separated), and optional `FUTU_HOST` /
`FUTU_PORT`.

## Project layout

- `config.py` — instruments + shared knobs (the file you normally edit)
- `engine.py` — the generic `NotifierEngine` (EMA / cross / de-dup / session / connection)
- `run.py` — entry point; one thread per instrument
- `get_chat_id.py` — helper to find a Telegram group's chat id
- `probe_feasibility.py` — one-off check of which instruments your data feed can reach

## Disclaimer

This is a **notifier**, not trading advice. An EMA crossover is right-side *confirmation* that a
short-term trend has already turned — it is not predictive. In back-testing, an EMA(5)/EMA(20)
5-minute crossover shows no directional edge on its own after costs. Use it for awareness, not as a
standalone entry signal. Provided as-is under the MIT License; you are responsible for your own
trading decisions.
