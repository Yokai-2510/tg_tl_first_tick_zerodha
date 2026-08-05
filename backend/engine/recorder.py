"""
Tick recorder — queue to disk, on its own thread.

Contract with the feed (BUILD_SPEC R1 / §11):
  * `put()` is called from the websocket callback and MUST never block.
    The queue is unbounded; the recorder never applies back-pressure.
  * All serialisation, compression and disk I/O happen on this thread.

Output: NDJSON (optionally zstd), one file per hour, in
`<data_dir>/<date>/ticks/`. Market ticks and lifecycle events share the same
chronological stream, discriminated by the `t` field, so one file tells the
whole story of a session.
"""

from __future__ import annotations

import json
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from ..core.enums import DiskFullPolicy, RecordKind
from ..core.timeutil import epoch_us, now_ist, to_epoch_us

try:                                            # optional; falls back to plain NDJSON
    import zstandard as zstd
except ImportError:                             # pragma: no cover
    zstd = None

try:
    import orjson

    def _dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)
except ImportError:                             # pragma: no cover
    def _dumps(obj: Any) -> bytes:
        return json.dumps(obj, separators=(",", ":"), default=str).encode()


def tick_to_record(
    tick: dict, *, recv_ns: int, recv_us: int, batch_seq: int, batch_size: int,
    symbol: str | None = None, depth_levels: int = 5,
) -> dict:
    """Flatten one Kite tick into a record.

    `exchange_timestamp` and `last_trade_time` arrive as naive datetimes, not
    epochs — `to_epoch_us` is the single conversion point.
    """
    ohlc = tick.get("ohlc") or {}
    rec: dict[str, Any] = {
        "t": RecordKind.TICK,
        "recv_ns": recv_ns,
        "recv_us": recv_us,
        "batch_seq": batch_seq,
        "batch_size": batch_size,
        "token": tick.get("instrument_token"),
        "ltp": tick.get("last_price"),
        "exch_ts": to_epoch_us(tick.get("exchange_timestamp")),
        "ltt": to_epoch_us(tick.get("last_trade_time")),
        "ltq": tick.get("last_traded_quantity"),
        "atp": tick.get("average_traded_price"),
        "vol": tick.get("volume_traded"),
        "tbq": tick.get("total_buy_quantity"),
        "tsq": tick.get("total_sell_quantity"),
        "oi": tick.get("oi"),
        "oi_hi": tick.get("oi_day_high"),
        "oi_lo": tick.get("oi_day_low"),
    }
    if symbol:
        rec["sym"] = symbol
    if ohlc:
        rec["ohlc"] = {"o": ohlc.get("open"), "h": ohlc.get("high"),
                       "l": ohlc.get("low"), "c": ohlc.get("close")}

    depth = tick.get("depth")
    if depth and depth_levels > 0:
        rec["depth"] = {
            "b": [[d.get("price"), d.get("quantity"), d.get("orders")]
                  for d in (depth.get("buy") or ())[:depth_levels]],
            "s": [[d.get("price"), d.get("quantity"), d.get("orders")]
                  for d in (depth.get("sell") or ())[:depth_levels]],
        }
    return {k: v for k, v in rec.items() if v is not None}


class Recorder:
    """Background writer. Start once; `put()` from anywhere."""

    _SENTINEL = object()

    def __init__(
        self,
        data_dir: Path | str,
        *,
        enabled: bool = True,
        compression: str = "zstd",
        depth_levels: int = 5,
        flush_interval_ms: int = 500,
        max_disk_mb: int = 20_000,
        on_disk_full: str = DiskFullPolicy.STOP_RECORDING,
        symbol_lookup: dict[int, str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.compression = compression if zstd is not None else "none"
        self.depth_levels = depth_levels
        self.flush_interval = max(0.05, flush_interval_ms / 1000.0)
        self.max_disk_mb = max_disk_mb
        self.on_disk_full = on_disk_full
        self.symbol_lookup = symbol_lookup or {}

        self._dir = Path(data_dir) / now_ist().strftime("%Y-%m-%d") / "ticks"
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._fh: Any = None
        self._writer: Any = None
        self._hour: str | None = None

        self.batch_seq = 0
        self.ticks_written = 0
        self.events_written = 0
        self.bytes_written = 0
        self.dropped = 0
        self.disk_full = False

    # -- producer side (hot path) ------------------------------------------

    def put(self, ticks: list[dict], recv_ns: int) -> None:
        """Enqueue a tick batch. O(1); never blocks; safe from the WS thread."""
        if self.enabled and not self.disk_full:
            self._q.put((ticks, recv_ns, epoch_us()))

    def event(self, kind: str, payload: dict | None = None) -> None:
        """Record a lifecycle event into the same chronological stream."""
        if self.enabled:
            self._q.put(({"t": kind, "recv_us": epoch_us(), **(payload or {})},
                         None, None))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="Recorder", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        if not self._running:
            return
        self._running = False
        self._q.put(self._SENTINEL)
        if self._thread:
            self._thread.join(timeout=timeout)
        self._close()

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "queue_depth": self._q.qsize(),
            "ticks": self.ticks_written,
            "events": self.events_written,
            "bytes": self.bytes_written,
            "dropped": self.dropped,
            "batches": self.batch_seq,
            "disk_full": self.disk_full,
            "dir": str(self._dir),
            "compression": self.compression,
        }

    # -- consumer thread ---------------------------------------------------

    def _loop(self) -> None:
        last_flush = time.monotonic()
        while True:
            try:
                item = self._q.get(timeout=self.flush_interval)
            except queue.Empty:
                self._flush()
                last_flush = time.monotonic()
                if not self._running:
                    break
                continue

            if item is self._SENTINEL:
                break

            try:
                self._write_item(item)
            except Exception:
                self.dropped += 1               # never let one bad record kill the thread

            if time.monotonic() - last_flush >= self.flush_interval:
                self._flush()
                self._check_disk()
                last_flush = time.monotonic()

        self._flush()
        self._close()

    def _write_item(self, item) -> None:
        payload, recv_ns, recv_us = item
        self._rotate_if_needed()

        if recv_ns is None:                      # lifecycle event
            self._emit(payload)
            self.events_written += 1
            return

        self.batch_seq += 1
        size = len(payload)
        for tick in payload:
            self._emit(tick_to_record(
                tick, recv_ns=recv_ns, recv_us=recv_us,
                batch_seq=self.batch_seq, batch_size=size,
                symbol=self.symbol_lookup.get(tick.get("instrument_token")),
                depth_levels=self.depth_levels,
            ))
            self.ticks_written += 1

    def _emit(self, record: dict) -> None:
        line = _dumps(record) + b"\n"
        (self._writer or self._fh).write(line)
        self.bytes_written += len(line)

    # -- files -------------------------------------------------------------

    def _rotate_if_needed(self) -> None:
        hour = now_ist().strftime("%H")
        if hour == self._hour and self._fh is not None:
            return
        self._close()
        suffix = ".ndjson.zst" if self.compression == "zstd" else ".ndjson"
        path = self._dir / f"{hour}{suffix}"
        self._fh = open(path, "ab")
        self._writer = (zstd.ZstdCompressor(level=3).stream_writer(self._fh)
                        if self.compression == "zstd" else None)
        self._hour = hour

    def _flush(self) -> None:
        try:
            if self._writer is not None:
                self._writer.flush()
            if self._fh is not None:
                self._fh.flush()
        except (OSError, ValueError):
            pass

    def _close(self) -> None:
        try:
            if self._writer is not None:
                self._writer.close()
        except Exception:
            pass
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass
        self._writer = self._fh = self._hour = None

    def _check_disk(self) -> None:
        try:
            free_mb = shutil.disk_usage(self._dir).free / (1024 * 1024)
        except OSError:
            return
        if free_mb >= 500:
            return
        self.disk_full = True
        self.event(RecordKind.FEED_GAP, {"reason": "DISK_FULL",
                                         "free_mb": round(free_mb, 1)})
        if self.on_disk_full == DiskFullPolicy.STOP_RECORDING:
            self.enabled = False                 # keep trading, stop recording


def prune_old_sessions(data_dir: Path | str, retention_days: int) -> list[str]:
    """Delete session directories older than `retention_days`. Returns removed names."""
    if retention_days <= 0:
        return []
    root = Path(data_dir)
    if not root.is_dir():
        return []
    cutoff = now_ist().date()
    removed: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or len(child.name) != 10:
            continue
        try:
            y, m, d = child.name.split("-")
            age = (cutoff - type(cutoff)(int(y), int(m), int(d))).days
        except (ValueError, TypeError):
            continue
        if age > retention_days:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed


__all__ = ["Recorder", "tick_to_record", "prune_old_sessions"]
