from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from hnids.serving.buffers import SessionBuffers

T0 = datetime(2017, 7, 7, 9, 0)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _row(v: float) -> np.ndarray:
    return np.full(3, v, dtype=np.float32)


def test_window_emitted_when_full_and_slides() -> None:
    buffers = SessionBuffers(3, ttl_seconds=60, max_sessions=10)
    assert buffers.push("s", _row(0), T0)[1] is None
    assert buffers.push("s", _row(1), T0 + timedelta(seconds=1))[1] is None
    count, ready = buffers.push("s", _row(2), T0 + timedelta(seconds=2), sanitized=1)
    assert count == 3 and ready is not None
    assert ready.rows[:, 0].tolist() == [0, 1, 2]
    assert ready.sanitized == 1
    _, slid = buffers.push("s", _row(3), T0 + timedelta(seconds=3))
    assert slid is not None and slid.rows[:, 0].tolist() == [1, 2, 3]
    assert slid.sanitized == 1  # row 2 (sanitized) is still inside the window
    _, later = buffers.push("s", _row(4), T0 + timedelta(seconds=4))
    _, last = buffers.push("s", _row(5), T0 + timedelta(seconds=5))
    assert later is not None and last is not None
    assert last.sanitized == 0  # it has now slid out


def test_ttl_expiry_resets_session() -> None:
    clock = FakeClock()
    buffers = SessionBuffers(3, ttl_seconds=10, max_sessions=10, clock=clock)
    buffers.push("s", _row(0), T0)
    clock.now = 11
    assert buffers.push("s", _row(1), T0 + timedelta(seconds=1))[0] == 1
    assert buffers.resets == 1


def test_lru_eviction_bounds_memory() -> None:
    buffers = SessionBuffers(3, ttl_seconds=60, max_sessions=2)
    for name in ("a", "b", "c"):
        buffers.push(name, _row(0), T0)
    assert len(buffers) == 2
    assert buffers.evictions == 1
    assert buffers.push("a", _row(1), T0)[0] == 1  # "a" was evicted, starts fresh


def test_expire_drops_idle_sessions() -> None:
    clock = FakeClock()
    buffers = SessionBuffers(3, ttl_seconds=5, max_sessions=10, clock=clock)
    buffers.push("a", _row(0), T0)
    clock.now = 3
    buffers.push("b", _row(0), T0)
    clock.now = 7
    assert buffers.expire() == 1
    assert len(buffers) == 1
