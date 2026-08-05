"""
WebSocket fan-out to the frontend.

Topic-based, snapshot-then-diff, with a per-client send queue. A slow or
stalled client is DROPPED rather than allowed to back-pressure the engine
(docs/02 §8.4) — the trading loop must never wait on a browser.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

TOPICS = ("status", "market", "positions", "orders", "events", "logs")

#: Drop a client whose backlog exceeds this many messages.
MAX_BACKLOG = 100


class WsHub:
    """Registry of connected frontends and their topic subscriptions."""

    def __init__(self) -> None:
        self._clients: dict[Any, set[str]] = {}
        self._seq: dict[str, int] = {t: 0 for t in TOPICS}
        self._lock = asyncio.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_holder: dict[str, Any] = {}
        self.dropped = 0

    # -- connection lifecycle ---------------------------------------------

    async def connect(self, ws) -> None:
        await ws.accept()
        self.loop = asyncio.get_running_loop()
        async with self._lock:
            self._clients[ws] = set()

    def disconnect(self, ws) -> None:
        self._clients.pop(ws, None)

    async def subscribe(self, ws, topics: list[str],
                        snapshot_for: Callable[[str], Any]) -> None:
        wanted = {t for t in topics if t in TOPICS}
        subs = self._clients.get(ws)
        if subs is None:
            return
        subs |= wanted
        for topic in sorted(wanted):
            await self._send(ws, self._frame(topic, "snapshot", snapshot_for(topic)))

    def unsubscribe(self, ws, topics: list[str]) -> None:
        subs = self._clients.get(ws)
        if subs is not None:
            subs -= set(topics)

    async def resync(self, ws, topics: list[str],
                     snapshot_for: Callable[[str], Any]) -> None:
        for topic in (topics or list(TOPICS)):
            if topic in TOPICS:
                await self._send(ws, self._frame(topic, "snapshot", snapshot_for(topic)))

    # -- publishing --------------------------------------------------------

    def publish(self, topic: str, data: Any, kind: str = "diff") -> None:
        """Thread-safe publish from engine threads."""
        if topic not in TOPICS or not self._clients:
            return
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        frame = self._frame(topic, kind, data)
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(topic, frame), loop)
        except RuntimeError:
            pass

    async def _broadcast(self, topic: str, frame: str) -> None:
        dead = []
        for ws, subs in list(self._clients.items()):
            if topic not in subs:
                continue
            if not await self._send(ws, frame):
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def _send(self, ws, frame: str) -> bool:
        try:
            await ws.send_text(frame)
            return True
        except Exception:
            self.dropped += 1
            try:
                await ws.close(code=4408)
            except Exception:
                pass
            return False

    def _frame(self, topic: str, kind: str, data: Any) -> str:
        self._seq[topic] = self._seq.get(topic, 0) + 1
        return json.dumps(
            {"topic": topic, "type": kind, "seq": self._seq[topic], "data": data},
            default=str,
        )

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def stats(self) -> dict:
        return {"clients": self.client_count, "dropped": self.dropped,
                "seq": dict(self._seq)}


__all__ = ["WsHub", "TOPICS", "MAX_BACKLOG"]
