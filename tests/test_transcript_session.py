"""_TimeoutSession — the only bound that actually holds on a transcript fetch.

asyncio.wait_for cancels the *await*, not the worker thread. Once a fetch starts
it runs to completion no matter what the caller does, so its own internal budget
is what decides how long a thread stays occupied.

These tests use a real socket, but only on 127.0.0.1 (see the allow_loopback
marker and the no_network fixture).
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import requests


@pytest.fixture
def black_hole():
    """A listening socket that accepts connections and never answers."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    accepted = []
    stop = threading.Event()

    def loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (socket.timeout, OSError):
                continue
            accepted.append(conn)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}/"
    stop.set()
    thread.join(timeout=2)
    for conn in accepted:
        try:
            conn.close()
        except OSError:
            pass
    srv.close()


@pytest.mark.allow_loopback
def test_a_single_call_is_bounded_by_the_per_call_cap(fresh_main, black_hole):
    main = fresh_main()
    session = main._TimeoutSession(budget=30.0, per_call_cap=1.0)
    start = time.monotonic()
    with pytest.raises(requests.exceptions.Timeout):
        session.get(black_hole)
    assert time.monotonic() - start < 3.0
    session.close()


@pytest.mark.allow_loopback
def test_the_budget_bounds_the_whole_fetch_not_each_call(fresh_main, black_hole):
    """One fetch issues several HTTP calls, and WEBSHARE_RETRIES is consumed
    twice by the library — as a mounted urllib3 Retry adapter *and* as its own
    re-fetch loop. A per-call timeout therefore multiplies out to many times the
    timeout the caller is waiting on."""
    main = fresh_main()
    session = main._TimeoutSession(budget=1.5, per_call_cap=10.0)
    start = time.monotonic()
    for _ in range(6):
        try:
            session.get(black_hole)
        except requests.exceptions.RequestException:
            pass
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"budget did not hold: {elapsed:.1f}s"
    session.close()


@pytest.mark.allow_loopback
def test_calls_after_the_deadline_fail_immediately(fresh_main, black_hole):
    main = fresh_main()
    session = main._TimeoutSession(budget=0.0, per_call_cap=10.0)
    start = time.monotonic()
    with pytest.raises(main.TranscriptDeadlineExceeded):
        session.get(black_hole)
    assert time.monotonic() - start < 0.5
    session.close()


def test_an_explicit_timeout_is_respected(fresh_main):
    """kwargs.setdefault-style handling must not clobber a caller's own value."""
    main = fresh_main()
    session = main._TimeoutSession(budget=30.0, per_call_cap=10.0)
    captured = {}

    class Recorder(requests.Session):
        def request(self, *args, **kwargs):
            captured.update(kwargs)
            raise requests.exceptions.ConnectionError("stop here")

    session.__class__ = type("Probe", (main._TimeoutSession, Recorder), {})
    with pytest.raises(requests.exceptions.ConnectionError):
        session.request("GET", "http://127.0.0.1:1/", timeout=(2, 3))
    assert captured["timeout"] == (2, 3)
    session.close()


def test_timeout_is_a_connect_read_tuple(fresh_main):
    """A scalar timeout is applied to each socket operation separately, so it
    does not bound the call as a whole."""
    main = fresh_main()
    session = main._TimeoutSession(budget=30.0, per_call_cap=8.0)
    captured = {}

    class Recorder(requests.Session):
        def request(self, *args, **kwargs):
            captured.update(kwargs)
            raise requests.exceptions.ConnectionError("stop here")

    session.__class__ = type("Probe", (main._TimeoutSession, Recorder), {})
    with pytest.raises(requests.exceptions.ConnectionError):
        session.request("GET", "http://127.0.0.1:1/")
    assert isinstance(captured["timeout"], tuple)
    connect, read = captured["timeout"]
    assert read <= 8.0 and connect <= read
    session.close()


def test_proxy_config_is_built_once(fresh_main):
    """It used to run on every fetch, re-importing the proxy module and
    re-logging 'no proxy configured' once per request."""
    main = fresh_main(WEBSHARE_PROXY_USERNAME="u", WEBSHARE_PROXY_PASSWORD="p")
    first = main._build_proxy_config()
    assert first is main._build_proxy_config()
    assert main._build_proxy_config.cache_info().hits >= 1


def test_no_proxy_configured_returns_none(fresh_main):
    assert fresh_main()._build_proxy_config() is None


def test_generic_proxy_url_is_used(fresh_main):
    main = fresh_main(PROXY_URL="http://user:pass@proxy.example:8080")
    config = main._build_proxy_config()
    assert config is not None
    assert type(config).__name__ == "GenericProxyConfig"
