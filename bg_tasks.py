"""Bounded, de-duplicated background refreshes.

Why: on Render, several endpoints serve a seed snapshot and start a full
upstream rebuild in a background thread. Previously each client poll spawned
a fresh thread with no de-duplication, so bursts or slow/limited upstreams
could pile up many concurrent scans in one gunicorn worker.

Guarantees provided here:
- single-flight per key: a key already running is not started again;
- global concurrency ceiling (MAX_BG_TASKS); admission is non-blocking, so a
  Flask request thread never waits for a slot (extra work is simply skipped);
- min interval per key, and a provider cooldown after rate-limit/auth errors
  (HTTP 401/429 style messages), during which tasks for that provider are
  skipped and callers keep serving the cached/seed payload.

Limitations: Python cannot kill a running thread. A task stuck in upstream I/O
keeps its slot until the underlying library call returns; that bound comes
from the per-request network timeouts of requests/yfinance, not from here.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

from net_limits import env_num

MAX_BG_TASKS = int(env_num("MAX_BG_TASKS", 2, 1, 8))
DEFAULT_MIN_INTERVAL = env_num("BG_MIN_INTERVAL_SEC", 120.0, 0.0, 3600.0)
RATE_LIMIT_COOLDOWN = env_num("BG_RATE_LIMIT_COOLDOWN_SEC", 600.0, 30.0, 3600.0)

_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_BG_TASKS)
_running: set[str] = set()
_last_start: dict[str, float] = {}
_cooldown_until: dict[str, float] = {}

_RATE_LIMIT_MARKERS = ("http error 429", "429 client error", "too many requests",
                       "rate limit", "rate-limit", "http error 401", "401 client error",
                       "invalid crumb", "yfratelimiterror")


def is_rate_limit_error(exc: BaseException) -> bool:
    try:
        from yfinance.exceptions import YFRateLimitError

        if isinstance(exc, YFRateLimitError):
            return True
    except Exception:  # noqa: BLE001
        pass
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _RATE_LIMIT_MARKERS)


def in_cooldown(provider: str | None, now: float | None = None) -> bool:
    if not provider:
        return False
    now = time.monotonic() if now is None else now
    with _lock:
        return _cooldown_until.get(provider, 0.0) > now


def note_rate_limited(provider: str, seconds: float | None = None) -> None:
    until = time.monotonic() + (RATE_LIMIT_COOLDOWN if seconds is None else seconds)
    with _lock:
        _cooldown_until[provider] = max(_cooldown_until.get(provider, 0.0), until)
    logger.warning("provider %s rate-limited; background refresh paused", provider)


def try_start(key: str, fn: Callable[[], object], *, provider: str | None = "yahoo",
              min_interval: float | None = None, force: bool = False) -> str:
    """Try to start fn in the background without ever blocking the caller.

    Returns one of: "started", "running" (same key already in flight),
    "cooldown" (provider paused after rate-limit), "throttled" (key ran
    within min_interval), "busy" (all slots in use). ``force`` only skips
    the min-interval check; it never bypasses in-flight, cooldown or capacity.
    """
    interval = DEFAULT_MIN_INTERVAL if min_interval is None else min_interval
    now = time.monotonic()
    with _lock:
        if key in _running:
            return "running"
        if provider and _cooldown_until.get(provider, 0.0) > now:
            return "cooldown"
        if not force and now - _last_start.get(key, -1e18) < interval:
            return "throttled"
        slots = _slots
        if not slots.acquire(blocking=False):
            return "busy"
        _running.add(key)
        _last_start[key] = now

    def _run() -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            if provider and is_rate_limit_error(exc):
                note_rate_limited(provider)
            logger.exception("background task %s failed", key)
        finally:
            with _lock:
                _running.discard(key)
            slots.release()

    try:
        threading.Thread(target=_run, name=f"bg:{key}", daemon=True).start()
    except Exception:
        with _lock:
            _running.discard(key)
        slots.release()
        raise
    return "started"


def submit(key: str, fn: Callable[[], object], **kwargs) -> bool:
    return try_start(key, fn, **kwargs) == "started"


def refresh_meta(status: str) -> dict:
    """Additive response metadata describing a requested background refresh."""
    return {"status": status, "queued": status in ("started", "running")}


class _YahooRateLimitLogHandler(logging.Handler):
    """yfinance swallows many HTTP errors and only logs them. Observe its
    logger so definite 429 / rate-limit messages also start a cooldown."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage().lower()
        except Exception:  # noqa: BLE001
            return
        # Only definite rate-limit signals. 401/Invalid Crumb is often retried
        # internally and may succeed, so it only counts when it propagates as
        # the task's final exception (see is_rate_limit_error).
        if any(m in msg for m in ("too many requests", "rate limited", "http error 429",
                                   "429 client error", "yfratelimiterror")):
            note_rate_limited("yahoo")


_handler_installed = False


def install_yfinance_watch() -> None:
    global _handler_installed
    if _handler_installed:
        return
    logging.getLogger("yfinance").addHandler(_YahooRateLimitLogHandler(level=logging.WARNING))
    _handler_installed = True


def snapshot() -> dict:
    now = time.monotonic()
    with _lock:
        return {
            "max": MAX_BG_TASKS,
            "running": sorted(_running),
            "cooldown": {k: round(v - now) for k, v in _cooldown_until.items() if v > now},
        }


def _reset_for_tests(max_tasks: int | None = None) -> None:
    global _slots, MAX_BG_TASKS
    with _lock:
        if max_tasks is not None:
            MAX_BG_TASKS = max_tasks
        _slots = threading.BoundedSemaphore(MAX_BG_TASKS)
        _running.clear()
        _last_start.clear()
        _cooldown_until.clear()
