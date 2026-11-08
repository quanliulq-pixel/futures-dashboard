import os
import threading
import time

os.environ.setdefault("SKIP_BG_WARMUP", "1")

import pytest

import bg_tasks


@pytest.fixture(autouse=True)
def reset():
    bg_tasks._reset_for_tests(max_tasks=2)
    yield
    bg_tasks._reset_for_tests(max_tasks=2)


def _wait(pred, t=2.0):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_single_flight_coalesces_rapid_repeats():
    gate = threading.Event()
    calls = []

    def job():
        calls.append(1)
        gate.wait(2)

    started = [bg_tasks.submit("k", job, min_interval=0) for _ in range(50)]
    assert started.count(True) == 1
    gate.set()
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    assert len(calls) == 1


def test_concurrency_ceiling_and_nonblocking_admission():
    gate = threading.Event()
    assert bg_tasks.submit("a", lambda: gate.wait(2), min_interval=0)
    assert bg_tasks.submit("b", lambda: gate.wait(2), min_interval=0)
    t0 = time.monotonic()
    assert bg_tasks.submit("c", lambda: None, min_interval=0) is False
    assert time.monotonic() - t0 < 0.05  # caller never waits for a slot
    assert len(bg_tasks.snapshot()["running"]) == 2
    gate.set()
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    assert bg_tasks.submit("c", lambda: None, min_interval=0)


def test_exception_releases_slot():
    def boom():
        raise ValueError("x")

    assert bg_tasks.submit("e", boom, min_interval=0)
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    assert bg_tasks.submit("f", lambda: None, min_interval=0)
    assert bg_tasks.submit("g", lambda: None, min_interval=0)


def test_min_interval_blocks_restart():
    assert bg_tasks.submit("m", lambda: None, min_interval=60)
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    assert bg_tasks.submit("m", lambda: None, min_interval=60) is False


@pytest.mark.parametrize("msg", ["HTTP Error 429: Too Many Requests", "HTTP Error 401: Invalid Crumb"])
def test_rate_limit_triggers_provider_cooldown(msg):
    def limited():
        raise RuntimeError(msg)

    assert bg_tasks.submit("y1", limited, min_interval=0, provider="yahoo")
    assert _wait(lambda: bg_tasks.in_cooldown("yahoo"))
    assert bg_tasks.submit("y2", lambda: None, min_interval=0, provider="yahoo") is False
    assert bg_tasks.submit("other", lambda: None, min_interval=0, provider="sina")


def test_plain_error_no_cooldown():
    assert bg_tasks.submit("p", lambda: (_ for _ in ()).throw(ValueError("bad")), min_interval=0)
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    assert not bg_tasks.in_cooldown("yahoo")


def test_force_skips_interval_but_not_inflight_capacity_or_cooldown():
    assert bg_tasks.submit("f1", lambda: None, min_interval=600)
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    assert bg_tasks.try_start("f1", lambda: None, min_interval=600) == "throttled"
    gate = threading.Event()
    assert bg_tasks.try_start("f1", lambda: gate.wait(2), min_interval=600, force=True) == "started"
    assert bg_tasks.try_start("f1", lambda: None, force=True) == "running"
    assert bg_tasks.try_start("f2", lambda: gate.wait(2), force=True) == "started"
    assert bg_tasks.try_start("f3", lambda: None, force=True) == "busy"
    gate.set()
    assert _wait(lambda: not bg_tasks.snapshot()["running"])
    bg_tasks.note_rate_limited("yahoo", 60)
    assert bg_tasks.try_start("f3", lambda: None, force=True) == "cooldown"


def test_yfinance_log_hook_only_definite_rate_limits():
    import logging

    bg_tasks.install_yfinance_watch()
    logging.getLogger("yfinance.data").error('HTTP Error 401: {"description":"Invalid Crumb"}')
    assert not bg_tasks.in_cooldown("yahoo")  # intermediate auth retry may succeed
    logging.getLogger("yfinance.data").error("HTTP Error 429: Too Many Requests")
    assert bg_tasks.in_cooldown("yahoo")


def test_final_propagated_401_still_cools_down():
    def fail():
        raise RuntimeError('HTTP Error 401: Invalid Crumb')

    assert bg_tasks.submit("auth", fail, min_interval=0)
    assert _wait(lambda: bg_tasks.in_cooldown("yahoo"))


def test_yfinance_ratelimit_exception_class_detected():
    from yfinance.exceptions import YFRateLimitError

    assert bg_tasks.is_rate_limit_error(YFRateLimitError())
    assert not bg_tasks.is_rate_limit_error(ValueError("price 401.5 out of range"))
