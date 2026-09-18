"""
Entry point: run the trend notifier for every configured instrument.

Each instrument gets its own engine on its own thread, with its own FutuOpenD
connection and its own session schedule -- so HSI futures (incl. night session),
SPY and QQQ all run concurrently and independently off one gateway.

    python run.py                 # run all instruments in config.INSTRUMENTS
    python run.py spy qqq         # run only instruments whose name matches
    python run.py --dry           # run all, but log alerts instead of sending

Requires FutuOpenD running and logged in, and TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_IDS set in the environment (see .env.example).
"""

import sys
import threading

# Show the alert emoji in the console where the terminal supports it (Windows
# defaults to cp1252). Harmless if it fails -- engine._log falls back to ASCII.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config as cfg
from engine import NotifierEngine


def select_instruments(argv):
    filters = [a.lower() for a in argv if not a.startswith("-")]
    if not filters:
        return list(cfg.INSTRUMENTS)
    picked = [i for i in cfg.INSTRUMENTS
              if any(f in i.name.lower() or f in i.code.lower() for f in filters)]
    return picked


def main():
    dry = "--dry" in sys.argv
    notify = not dry

    if notify and (not cfg.BOT_TOKEN or not cfg.CHAT_IDS):
        print("Missing Telegram config. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS "
              "(see .env.example), or run with --dry to log alerts instead of sending.")
        return

    instruments = select_instruments(sys.argv[1:])
    if not instruments:
        print("No instruments matched. Available:")
        for i in cfg.INSTRUMENTS:
            print(f"  - {i.name}  ({i.code})")
        return

    print("Starting trend notifier")
    print(f"  gateway : {cfg.FUTU_HOST}:{cfg.FUTU_PORT}")
    print(f"  alerts  : {'Telegram' if notify else 'DRY-RUN (log only)'}")
    for i in instruments:
        print(f"  - {i.name:<18} {i.code:<12} EMA{i.fast}/{i.slow}  poll {i.poll_seconds}s")
    print()

    threads = []
    for inst in instruments:
        engine = NotifierEngine(inst)
        t = threading.Thread(target=engine.run, kwargs={"notify": notify},
                             name=inst.name, daemon=True)
        t.start()
        threads.append(t)

    # Keep the main thread alive so Ctrl-C reaches the daemon threads.
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        print("\nStopping (Ctrl-C) -- engines will close their connections.")


if __name__ == "__main__":
    main()
