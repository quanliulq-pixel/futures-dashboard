"""Explicit network timeouts for Yahoo (yfinance) calls.

yfinance 1.x exposes a public ``timeout`` argument on ``yf.download`` and
``Ticker.history`` (seconds, passed to each HTTP request). We pass it
explicitly everywhere instead of relying on library defaults.
``Ticker.option_chain`` / ``Ticker.options`` expose no timeout argument; they
use yfinance's internal per-request default (30s) and are only called from
admitted background tasks (see bg_tasks), never from request handlers on Render.
"""
from __future__ import annotations

import math
import os


def env_num(name: str, default: float, lo: float, hi: float) -> float:
    """Finite env number clamped to [lo, hi]; malformed/NaN/inf -> default."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(val):
        return default
    return min(hi, max(lo, val))


YF_TIMEOUT_SEC = env_num("YF_TIMEOUT_SEC", 10.0, 1.0, 60.0)


def yf_download(*args, **kwargs):
    import yfinance as yf

    kwargs.setdefault("timeout", YF_TIMEOUT_SEC)
    return yf.download(*args, **kwargs)


def yf_history(ticker, **kwargs):
    kwargs.setdefault("timeout", YF_TIMEOUT_SEC)
    return ticker.history(**kwargs)
