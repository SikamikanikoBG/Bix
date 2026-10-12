"""Thread-safe publish/subscribe bus between the device parts.

Recorder, presence, LEDs, the web UI and the face all talk through events,
so any one of them can be missing or fail without taking the others down.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class Bus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[Callable[[Event], None]] = []
        self._queues: list[queue.Queue[Event]] = []

    def publish(self, type: str, **data: Any) -> None:
        ev = Event(type, data)
        with self._lock:
            handlers = list(self._handlers)
            queues = list(self._queues)
        for h in handlers:
            try:
                h(ev)
            except Exception:
                log.exception("event handler failed for %s", type)
        for q in queues:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass  # slow consumer (e.g. a stalled browser tab): drop

    def on(self, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._handlers.append(handler)

    def subscribe(self, maxsize: int = 500) -> queue.Queue[Event]:
        q: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)
