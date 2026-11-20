"""Cooperative controls at durable work boundaries, never inside an HTTP stream."""

import threading
import time

local = threading.local()


class TaskCancelled(BaseException):
    """Bypass retry handlers; the owning job still runs its cleanup blocks."""


def checkpoint():
    control = getattr(local, "checkpoint", None)
    if control:
        control()


def active_time():
    return time.monotonic() - getattr(local, "paused_seconds", 0)
