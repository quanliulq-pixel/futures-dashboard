# -*- coding: utf-8 -*-
"""App settings. Instrument lists live in universe.py (full CN + NA)."""

# Refresh interval for the browser (seconds)
QUOTE_REFRESH_SEC = 30
# Server-side cache TTL
QUOTE_CACHE_TTL = 20
HISTORY_CACHE_TTL = 600

# Analysis cache
ANALYSIS_CACHE_TTL = 900

# Max parallel workers for history/quotes
MAX_WORKERS_HISTORY = 8
MAX_WORKERS_QUOTES = 10
