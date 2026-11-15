"""Bounded per-session sequence buffers for streaming inference (FR-6, PRD 6.2).

* Keyed by session; a session's window never mixes flows from another session.
* A buffer resets when its session has been idle longer than ``ttl_seconds`` (wall clock) or
  when a flow arrives with an event time earlier than the last one (out-of-order -> new run).
* At most ``max_sessions`` buffers are kept; the least recently used is evicted.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass
class _Buffer:
    rows: deque[np.ndarray]
    starts: deque[datetime]
    last_event_time: datetime
    last_seen: float
    sanitized: deque[int] = field(default_factory=deque)


@dataclass(frozen=True)
class WindowReady:
    rows: np.ndarray  # (L, F)
    start: datetime
    end: datetime
    sanitized: int


class SessionBuffers:
    def __init__(
        self,
        length: int,
        *,
        ttl_seconds: float,
        max_sessions: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.length = length
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._buffers: OrderedDict[str, _Buffer] = OrderedDict()
        self._lock = threading.Lock()
        self.resets = 0
        self.evictions = 0

    def __len__(self) -> int:
        return len(self._buffers)

    def _new(self, row: np.ndarray, when: datetime, now: float, sanitized: int) -> _Buffer:
        return _Buffer(
            rows=deque([row], maxlen=self.length),
            starts=deque([when], maxlen=self.length),
            last_event_time=when,
            last_seen=now,
            sanitized=deque([sanitized], maxlen=self.length),
        )

    def push(
        self, session: str, row: np.ndarray, when: datetime, *, sanitized: int = 0
    ) -> tuple[int, WindowReady | None]:
        """Append one prepared row; return (buffered count, window if full)."""
        now = self._clock()
        with self._lock:
            buffer = self._buffers.get(session)
            stale = buffer is not None and (
                now - buffer.last_seen > self.ttl or when < buffer.last_event_time
            )
            if buffer is None or stale:
                if stale:
                    self.resets += 1
                buffer = self._new(row, when, now, sanitized)
                self._buffers[session] = buffer
            else:
                buffer.rows.append(row)
                buffer.starts.append(when)
                buffer.sanitized.append(sanitized)
                buffer.last_event_time = when
                buffer.last_seen = now
            self._buffers.move_to_end(session)
            while len(self._buffers) > self.max_sessions:
                self._buffers.popitem(last=False)
                self.evictions += 1
            count = len(buffer.rows)
            if count < self.length:
                return count, None
            return count, WindowReady(
                rows=np.stack(list(buffer.rows)),
                start=buffer.starts[0],
                end=buffer.starts[-1],
                sanitized=int(sum(buffer.sanitized)),
            )

    def expire(self) -> int:
        """Drop idle sessions; returns how many were removed."""
        now = self._clock()
        with self._lock:
            stale = [k for k, b in self._buffers.items() if now - b.last_seen > self.ttl]
            for key in stale:
                del self._buffers[key]
            return len(stale)
