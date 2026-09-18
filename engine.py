"""
Trend-notifier engine (instrument-agnostic).

One NotifierEngine drives one Instrument: it connects to FutuOpenD, polls the
5-minute K-line during that instrument's trading sessions, and fires a Telegram
alert on the exact candle where EMA(fast) crosses EMA(slow).

Right-side confirmation only -- no prediction. An alert fires once per trend
leg, on the bar where the cross completes between two *closed* candles, and
never again until the opposite cross occurs. Multiple instruments each run their
own engine on their own thread (see run.py), so their sessions/sleeps are fully
independent.
"""

import time
import datetime as dt

import pandas as pd
import requests
from futu import (
    OpenQuoteContext, SubType, KLType, AuType, RET_OK,
    TradeDateType,
)

import config as cfg


# ----------------------------------------------------------------------------
# NOTIFICATION
# ----------------------------------------------------------------------------
def send_telegram(message: str) -> bool:
    """Broadcast one message to every chat id in cfg.CHAT_IDS (DMs and/or groups)."""
    url = f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/sendMessage"
    all_ok = True
    for chat_id in cfg.CHAT_IDS:
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            r = requests.post(url, data=payload, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[telegram] send to {chat_id} failed: {e}")
            all_ok = False
    return all_ok


# ----------------------------------------------------------------------------
# ENGINE
# ----------------------------------------------------------------------------
class NotifierEngine:
    def __init__(self, instrument: "cfg.Instrument"):
        self.inst = instrument
        self._ctx = None
        self._last_alert_key = None       # (candle_ts, direction) already sent
        self._last_seen_candle = None     # newest candle we've evaluated
        self._errors = 0
        self._calendar_cache = {}         # date -> list[(open_dt, close_dt)]

    # -- small logging helper so interleaved thread output stays readable ----
    def _log(self, msg: str):
        line = f"[{self.inst.name}] {msg}"
        try:
            print(line)
        except UnicodeEncodeError:
            # Windows consoles default to cp1252 and choke on the alert emoji.
            # Logging must never crash the engine -- fall back to ASCII.
            print(line.encode("ascii", "replace").decode("ascii"))

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.inst.tz)

    # ------------------------------------------------------------------ EMAs
    def _add_emas(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=self.inst.fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.inst.slow, adjust=False).mean()
        return df

    def _detect_cross(self, df: pd.DataFrame):
        """
        ('BULLISH'|'BEARISH', row) if the last *closed* candle is the crossover
        candle, else (None, None). The live (still-forming) bar is dropped so a
        cross can't 'un-happen' when the current bar wiggles back over the line.
        """
        df = df.iloc[:-1]                          # drop the unclosed candle
        if len(df) < self.inst.slow + 2:
            return None, None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if pd.isna(prev["ema_slow"]) or pd.isna(curr["ema_slow"]):
            return None, None
        if prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]:
            return "BULLISH", curr
        if prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]:
            return "BEARISH", curr
        return None, None

    def _format_alert(self, direction: str, price: float, ts) -> str:
        icon = "🟢" if direction == "BULLISH" else "🔴"
        cross = "above" if direction == "BULLISH" else "below"
        return (
            f"{icon} *{direction} REVERSAL CONFIRMED*\n"
            f"*Instrument:* {self.inst.name} (`{self.inst.code}`)\n"
            f"*Timeframe:* {self.inst.timeframe}\n"
            f"*Price:* {price:,.2f}\n"
            f"*Signal:* EMA{self.inst.fast} crossed {cross} EMA{self.inst.slow}\n"
            f"*Candle close:* {pd.Timestamp(ts):%Y-%m-%d %H:%M}"
        )

    def _check_and_alert(self, df: pd.DataFrame, notify: bool = True):
        df = self._add_emas(df)
        direction, candle = self._detect_cross(df)
        if direction is None:
            return None
        key = (candle.name, direction)
        if key == self._last_alert_key:            # same candle -> skip
            return None
        self._last_alert_key = key
        msg = self._format_alert(direction, float(candle["close"]), candle.name)
        self._log(msg.replace("*", "").replace("\n", " | "))
        if notify:
            send_telegram(msg)
        return direction

    # ------------------------------------------------------- DATA / CALENDAR
    @staticmethod
    def _normalize(data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        df["time_key"] = pd.to_datetime(df["time_key"])
        df = df.set_index("time_key").sort_index()
        return df[["open", "high", "low", "close", "volume"]]

    def _sessions_for_daytype(self, day_type):
        """Which of the instrument's sessions run given a WHOLE/MORNING/AFTERNOON day."""
        out = []
        for s in self.inst.sessions:
            if day_type == TradeDateType.WHOLE:
                out.append(s)
            elif day_type == TradeDateType.MORNING and s.phase in ("morning", "full"):
                out.append(s)
            elif day_type == TradeDateType.AFTERNOON and s.phase in ("afternoon", "full"):
                out.append(s)
        return out

    def _concrete_windows(self, day: dt.date, day_type):
        """Build (open_dt, close_dt) windows for one calendar day, midnight-aware."""
        grace = dt.timedelta(minutes=cfg.CLOSE_GRACE_MINUTES)
        out = []
        for s in self._sessions_for_daytype(day_type):
            start_dt = dt.datetime.combine(day, s.start, self.inst.tz)
            end_day = day + dt.timedelta(days=1) if s.end <= s.start else day
            end_dt = dt.datetime.combine(end_day, s.end, self.inst.tz) + grace
            out.append((start_dt, end_dt))
        return sorted(out)

    def _trading_windows(self, day: dt.date):
        """Windows for one calendar day; [] = non-trading. Cached per day."""
        if day in self._calendar_cache:
            return self._calendar_cache[day]

        day_type = None
        try:
            ret, data = self._ctx.request_trading_days(
                self.inst.calendar_market, start=day.isoformat(), end=day.isoformat()
            )
            if ret == RET_OK:
                if not data:
                    self._calendar_cache[day] = []      # confirmed non-trading day
                    return []
                day_type = data[0].get("trade_date_type", TradeDateType.WHOLE)
        except Exception as e:
            self._log(f"[cal] trading-day lookup failed for {day}: {e}")

        if day_type is None:
            # Calendar unavailable -> assume a normal weekday, do NOT cache, so
            # we retry rather than lock in a guess for the rest of the run.
            if day.weekday() >= 5:
                return []
            self._log(f"[cal] falling back to weekday assumption for {day}")
            return self._concrete_windows(day, TradeDateType.WHOLE)

        windows = self._concrete_windows(day, day_type)
        self._calendar_cache[day] = windows
        if len(self._calendar_cache) > 60:              # keep the cache bounded
            for stale in sorted(self._calendar_cache)[:30]:
                self._calendar_cache.pop(stale, None)
        return windows

    def _session_status(self, now: dt.datetime):
        """
        -> (in_session: bool, boundary: datetime | None)

        In session: boundary is when polling should stop. Out of session:
        boundary is the next open. Starts one day back so a night session that
        began yesterday and runs past midnight is seen as still open.
        """
        for offset in range(-1, cfg.LOOKAHEAD_DAYS):
            day = now.date() + dt.timedelta(days=offset)
            for start, end in self._trading_windows(day):
                if start <= now <= end:
                    return True, end
                if now < start:
                    return False, start
        return False, None

    # ------------------------------------------------------------ CONNECTION
    def _open_feed(self):
        ctx = OpenQuoteContext(host=cfg.FUTU_HOST, port=cfg.FUTU_PORT)
        ret, err = ctx.subscribe([self.inst.code], [SubType.K_5M], is_first_push=False)
        if ret != RET_OK:
            ctx.close()
            raise RuntimeError(f"subscribe failed: {err}")
        self._log(f"[futu] connected | subscribed {self.inst.code} K_5M")
        return ctx

    def _close_feed(self):
        if self._ctx is None:
            return
        try:
            self._ctx.unsubscribe([self.inst.code], [SubType.K_5M])
        except Exception:
            pass
        try:
            self._ctx.close()
            self._log("[futu] connection closed")
        except Exception as e:
            self._log(f"[futu] close error (ignored): {e}")
        self._ctx = None

    def _sleep_until(self, target, label: str):
        if target is None:
            self._log("[wait] no upcoming session found -- rechecking in 1h")
            remaining = 3600.0
        else:
            remaining = (target - self.now()).total_seconds()
            self._log(f"[wait] closed -- sleeping {remaining/60:.0f} min until {label} "
                      f"{target:%Y-%m-%d %H:%M}")
        while remaining > 0:
            time.sleep(min(remaining, 60))
            remaining = (target - self.now()).total_seconds() if target else remaining - 60

    # ------------------------------------------------------------------ RUN
    def run(self, notify: bool = True):
        try:
            self._ctx = self._open_feed()          # needed even while closed (calendar)

            while True:
                now = self.now()
                in_session, boundary = self._session_status(now)

                # ---------------- market closed ----------------
                if not in_session:
                    idle = (boundary - now).total_seconds() if boundary else 3600
                    if idle > cfg.IDLE_DISCONNECT_SECONDS:
                        self._close_feed()
                    self._sleep_until(boundary, "next session")
                    if self._ctx is None:
                        try:
                            self._ctx = self._open_feed()
                            self._errors = 0
                        except Exception as e:
                            self._log(f"[futu] reconnect at session start failed: {e}")
                            time.sleep(30)
                    continue

                # ---------------- market open ----------------
                try:
                    if self._ctx is None:
                        self._ctx = self._open_feed()
                        self._errors = 0

                    ret, data = self._ctx.get_cur_kline(
                        self.inst.code, self.inst.kline_count, KLType.K_5M, AuType.QFQ
                    )
                    if ret != RET_OK:
                        raise RuntimeError(f"get_cur_kline error: {data}")
                    if data is None or data.empty:
                        raise RuntimeError("get_cur_kline returned an empty frame")

                    df = self._normalize(data)
                    self._errors = 0

                    newest = df.index[-1]
                    if newest != self._last_seen_candle:
                        self._last_seen_candle = newest
                        self._check_and_alert(df, notify=notify)

                except Exception as e:
                    self._errors += 1
                    self._log(f"[futu] fetch error {self._errors}/{cfg.MAX_CONSECUTIVE_ERRORS}: {e}")
                    if self._errors >= cfg.MAX_CONSECUTIVE_ERRORS:
                        self._log("[futu] too many failures -- rebuilding connection")
                        self._close_feed()
                        time.sleep(5)
                        try:
                            self._ctx = self._open_feed()
                            self._errors = 0
                        except Exception as reconnect_err:
                            self._log(f"[futu] reconnect failed: {reconnect_err}")
                            time.sleep(30)

                time.sleep(self.inst.poll_seconds)

        except KeyboardInterrupt:
            self._log("[futu] stopped by user")
        finally:
            self._close_feed()
