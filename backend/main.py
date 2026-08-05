#!/usr/bin/env python3
"""
Entry point with crash recovery.

The service runs 24/7 under systemd; the scheduler idles until phase1_time,
so there is nothing to launch by hand in the morning. An unhandled exception
restarts the application with backoff instead of leaving the box silent.

    python backend/main.py [--config PATH] [--credentials PATH] [--once]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.api.server import serve                      # noqa: E402
from backend.app import Application                       # noqa: E402
from backend.config.loader import ConfigError             # noqa: E402

MAX_CRASHES = 5
BASE_BACKOFF_S = 10

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    _stop = True
    logging.getLogger("main").info("signal %s received — shutting down", signum)


def build_logger(level: str, data_dir: str) -> logging.Logger:
    log_dir = Path(data_dir).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-9s | %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    fh = logging.FileHandler(log_dir / "engine.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    return logging.getLogger("engine")


class BufferHandler(logging.Handler):
    """Keeps recent log lines in memory for GET /logs."""

    def __init__(self, sink: list, limit: int = 2000):
        super().__init__()
        self.sink, self.limit = sink, limit

    def emit(self, record):
        self.sink.append({
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
            "ts": time.strftime("%H:%M:%S"),
        })
        if len(self.sink) > self.limit:
            del self.sink[:-self.limit]


def run_once(args) -> None:
    app = Application(config_path=args.config, credentials_path=args.credentials)
    log = build_logger(app.cfg.system.log_level, app.cfg.system.data_dir)
    logging.getLogger().addHandler(BufferHandler(app.logs))
    app.log = log
    app.scheduler.log = log

    log.info("=" * 62)
    log.info("  TG/TL FIRST-TICK  |  mode=%s  |  api=%s:%s",
             app.cfg.trading_mode.mode, app.cfg.api.host, app.cfg.api.port)
    log.info("=" * 62)

    serve(app)                                    # API up first, so status is visible
    log.info("API listening on http://%s:%s/api/v1",
             app.cfg.api.host, app.cfg.api.port)

    try:
        app.run()
    finally:
        app.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="TG/TL First-Tick trading system")
    parser.add_argument("--config", default="config/config.json")
    parser.add_argument("--credentials", default="config/credentials.json")
    parser.add_argument("--once", action="store_true",
                        help="do not restart after a crash")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    crashes = 0
    while not _stop:
        try:
            run_once(args)
            return 0
        except ConfigError as exc:
            print(f"CONFIG ERROR\n{exc}", file=sys.stderr)
            return 2                              # never retry a bad config
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            crashes += 1
            logging.getLogger("main").exception("crashed (%d/%d): %s",
                                                crashes, MAX_CRASHES, exc)
            if args.once or crashes >= MAX_CRASHES or _stop:
                return 1
            delay = min(BASE_BACKOFF_S * crashes, 60)
            logging.getLogger("main").info("restarting in %ss", delay)
            time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
