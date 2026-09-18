"""
Configuration for the trend notifier.

One engine, several instruments. Everything instrument-specific lives here as a
declarative Instrument entry -- code, timezone, trading sessions, calendar
market and EMA/poll knobs -- so the engine itself stays generic. Add or remove
an instrument by editing INSTRUMENTS; touch nothing else.

Secrets (Telegram bot token and chat ids) are read from the environment, so no
credentials live in source. Set them via a local .env file (see .env.example)
or real environment variables.
"""

import os
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from futu import TradeDateMarket

load_dotenv()

# ----------------------------------------------------------------------------
# TELEGRAM  (from environment -- never hard-code credentials)
# ----------------------------------------------------------------------------
# TELEGRAM_BOT_TOKEN : the token from @BotFather
# TELEGRAM_CHAT_IDS  : comma-separated chat ids to broadcast to. A private chat
#                      is a positive id; a group/supergroup is a NEGATIVE id
#                      (e.g. -1001234567890). Use get_chat_id.py to find a group's id.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]

# ----------------------------------------------------------------------------
# FUTU GATEWAY
# ----------------------------------------------------------------------------
FUTU_HOST = os.environ.get("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.environ.get("FUTU_PORT", "11111"))

# ----------------------------------------------------------------------------
# SHARED ENGINE KNOBS (apply to every instrument unless overridden)
# ----------------------------------------------------------------------------
CLOSE_GRACE_MINUTES = 2       # keep polling N min past the bell so the final
                              # candle of a session is fetched before we sleep
IDLE_DISCONNECT_SECONDS = 300 # if next session is further away than this, drop
                              # the socket while we wait (OpenD relogins daily)
MAX_CONSECUTIVE_ERRORS = 6    # ~1 min of failed fetches -> rebuild connection
LOOKAHEAD_DAYS = 12           # enough to clear a Lunar New Year / Easter break

HK_TZ = ZoneInfo("Asia/Hong_Kong")
NY_TZ = ZoneInfo("America/New_York")


# ----------------------------------------------------------------------------
# INSTRUMENT MODEL
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Session:
    """
    One continuous trading window as local wall-clock times.

    If `end` <= `start` the window crosses midnight into the next calendar day
    (e.g. the HSI-futures night session 17:15 -> 03:00).

    `phase` gates the window on half-days: on a MORNING half-day only 'morning'
    windows run; the afternoon and night windows are dropped. 'full' always runs
    on any trading day (used by markets with a single continuous session).
    """
    start: dt.time
    end: dt.time
    phase: str = "full"       # 'morning' | 'afternoon' | 'night' | 'full'


@dataclass(frozen=True)
class Instrument:
    name: str                 # display name (shown in the alert + logs)
    code: str                 # Futu code, e.g. 'HK.HSImain', 'US.SPY'
    tz: ZoneInfo              # timezone the sessions are expressed in
    sessions: tuple           # tuple[Session, ...]
    calendar_market: str      # TradeDateMarket.* for the holiday calendar
    fast: int = 5             # fast EMA span
    slow: int = 20            # slow EMA span
    poll_seconds: int = 10    # heartbeat: how often we re-check for a new candle
    kline_count: int = 100    # candles pulled per check (must exceed `slow`)
    timeframe: str = "5m"     # label only; the engine trades the 5m K-line


# ----------------------------------------------------------------------------
# THE INSTRUMENTS
# ----------------------------------------------------------------------------
INSTRUMENTS = [
    # HSI futures, main-contract continuous. Day + night sessions.
    # HK futures hours: 09:15-12:00, 13:00-16:30, night 17:15-03:00 (T+1).
    Instrument(
        name="HSI Futures",
        code="HK.HSImain",
        tz=HK_TZ,
        sessions=(
            Session(dt.time(9, 15), dt.time(12, 0), "morning"),
            Session(dt.time(13, 0), dt.time(16, 30), "afternoon"),
            Session(dt.time(17, 15), dt.time(3, 0), "night"),   # crosses midnight
        ),
        calendar_market=TradeDateMarket.HK,
    ),

    # S&P 500 via SPY (Futu can't stream the US index itself; SPY tracks it).
    # US regular session 09:30-16:00 ET.
    Instrument(
        name="SPY (S&P 500)",
        code="US.SPY",
        tz=NY_TZ,
        sessions=(Session(dt.time(9, 30), dt.time(16, 0), "full"),),
        calendar_market=TradeDateMarket.US,
    ),

    # Nasdaq 100 via QQQ.
    Instrument(
        name="QQQ (Nasdaq 100)",
        code="US.QQQ",
        tz=NY_TZ,
        sessions=(Session(dt.time(9, 30), dt.time(16, 0), "full"),),
        calendar_market=TradeDateMarket.US,
    ),
]
