"""
Feasibility probe for the multi-index trend notifier.

Answers ONE question per instrument: can this Futu account pull real-time 5m
candles for it? For each candidate code it subscribes to K_5M, pulls the last
few candles, and reports ret / row count / latest candle time / staleness.

Run with FutuOpenD running and logged in on 127.0.0.1:11111:
    python probe_feasibility.py

Nothing here trades or alerts -- it's read-only reconnaissance.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from futu import (
    OpenQuoteContext, SubType, KLType, AuType, RET_OK,
)

HOST, PORT = "127.0.0.1", 11111

# Candidate codes to test. Futu's futures/index codes differ from cash HK.800000,
# so we try the likely ones and report which actually resolve.
CANDIDATES = [
    # --- what the current notifier already uses (control) ---
    ("HSI cash (control)",      "HK.800000",   ZoneInfo("Asia/Hong_Kong")),
    # --- HSI futures: main-contract continuous + a specific month fallback ---
    ("HSI futures main",        "HK.HSImain",  ZoneInfo("Asia/Hong_Kong")),
    ("Mini-HSI futures main",   "HK.MHImain",  ZoneInfo("Asia/Hong_Kong")),
    # --- US indices (cash) ---
    ("S&P 500 (SPX)",           "US.SPX",      ZoneInfo("America/New_York")),
    ("Nasdaq 100 (NDX)",        "US.NDX",      ZoneInfo("America/New_York")),
    ("Nasdaq Composite (IXIC)", "US.IXIC",     ZoneInfo("America/New_York")),
]


def probe_one(ctx, label, code, tz):
    line = f"{label:<26} {code:<12}"
    ret, err = ctx.subscribe([code], [SubType.K_5M], is_first_push=False)
    if ret != RET_OK:
        print(f"  [FAIL] {line}  subscribe -> {err}")
        return
    ret, data = ctx.get_cur_kline(code, 5, KLType.K_5M, AuType.QFQ)
    if ret != RET_OK:
        print(f"  [FAIL] {line}  get_cur_kline -> {data}")
        return
    if data is None or data.empty:
        print(f"  [WARN] {line}  subscribed but no candles returned")
        return
    last = str(data["time_key"].iloc[-1])
    try:
        last_dt = dt.datetime.fromisoformat(last).replace(tzinfo=tz)
        age_min = (dt.datetime.now(tz) - last_dt).total_seconds() / 60
        age = f"{age_min:,.0f} min old"
    except Exception:
        age = "age n/a"
    print(f"  [OK]   {line}  rows={len(data)}  last={last}  ({age})")


def main():
    print(f"Connecting to FutuOpenD {HOST}:{PORT} ...")
    try:
        ctx = OpenQuoteContext(host=HOST, port=PORT)
    except Exception as e:
        print(f"Could not connect -- is FutuOpenD running and logged in? {e}")
        return

    # Global state tells us login + market data package status.
    ret, gs = ctx.get_global_state()
    if ret == RET_OK:
        print(f"OpenD state: {gs}\n")
    print("Probing 5-minute candle access per instrument:\n")
    for label, code, tz in CANDIDATES:
        try:
            probe_one(ctx, label, code, tz)
        except Exception as e:
            print(f"  [ERR]  {label:<26} {code:<12}  {e}")

    print("\nInterpretation:")
    print("  [OK] with a fresh 'last' during that market's hours -> real-time, buildable.")
    print("  [OK] but always ~15+ min old                        -> delayed feed, notifier of limited use.")
    print("  [FAIL] subscribe/kline                              -> no quote right for that instrument.")
    ctx.close()


if __name__ == "__main__":
    main()
