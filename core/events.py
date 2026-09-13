"""Event bus between the agent and the dashboard.

The agent runs synchronously in a worker thread; the dashboard listens on a
WebSocket in the event loop. This is the piece in between: publishing is
thread-safe and never blocks, and a slow browser gets its oldest events dropped
rather than stalling a decision.

A replay buffer is kept so a dashboard opened halfway through a demo still
shows the run that already happened.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set


class EventBus:
    def __init__(self, *, history: int = 300, queue_size: int = 200) -> None:
        self._history: Deque[Dict[str, Any]] = deque(maxlen=history)
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()
        self._queue_size = queue_size
        self._seq = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at startup so worker threads know where to deliver."""
        self._loop = loop

    # -- publishing ----------------------------------------------------------

    def publish(self, topic: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "topic": topic,
                "at": datetime.now(timezone.utc).isoformat(),
                "data": _jsonable(payload),
            }
            self._history.append(event)
            subscribers = list(self._subscribers)
            loop = self._loop

        for queue in subscribers:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(_offer, queue, event)
            else:
                _offer(queue, event)
        return event

    # -- subscribing ---------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def replay(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history)[-limit:]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


def _offer(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
    """Put without blocking; drop the oldest event if the consumer is behind."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(event)
        except Exception:  # noqa: BLE001 - dropping an event beats blocking
            pass


def _jsonable(value: Any) -> Any:
    """Make a payload safe for json.dumps without losing the useful parts."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return str(value)
