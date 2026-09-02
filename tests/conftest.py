"""Shared fixtures.

Two rules this suite enforces on itself:

1. No outbound network. Ever. The app talks to YouTube and to xAI, and a test
   that accidentally reaches either is slow, flaky, and costs money.
2. No leakage from the developer's `.env`. main.py reads every tunable at import
   time, so a real XAI_API_KEY on disk would silently change what the
   "unconfigured" tests exercise.
"""

from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Every environment variable main.py reads. Cleared before each reload so a test
# only sees what it asked for.
KNOWN_ENV_VARS = (
    "XAI_API_KEY",
    "XAI_BASE_URL",
    "GROK_MODEL",
    "GROK_TIMEOUT",
    "GROK_MAX_TOKENS",
    "GROK_TEMPERATURE",
    "MAX_TRANSCRIPT_CHARS",
    "TRANSCRIPT_TIMEOUT",
    "TRANSCRIPT_HTTP_TIMEOUT",
    "TRANSCRIPT_WORKERS",
    "RATE_LIMIT_REQUESTS",
    "RATE_LIMIT_WINDOW",
    "GLOBAL_RATE_LIMIT_REQUESTS",
    "GLOBAL_RATE_LIMIT_WINDOW",
    "MAX_RATE_BUCKETS",
    "TRUSTED_PROXY_HOPS",
    "ANALYSIS_ENABLED",
    "DAILY_BUDGET_USD",
    "ALLOWED_ORIGINS",
    "SITE_URL",
    "LOG_LEVEL",
    "WEBSHARE_PROXY_USERNAME",
    "WEBSHARE_PROXY_PASSWORD",
    "WEBSHARE_RETRIES",
    "WEBSHARE_IP_LOCATIONS",
    "PROXY_URL",
    "PORT",
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_loopback: permit real sockets to 127.0.0.1 (fake servers)"
    )


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Fail loudly on any outbound connection instead of hanging on a timeout.

    Loopback is always permitted: asyncio builds its self-pipe with
    socket.socketpair(), which connects to 127.0.0.1, and TestClient needs an
    event loop. Nothing this app talks to lives on loopback, so allowing it
    costs nothing — the tests that use a real local server just need the
    transport-level blocks lifted as well.
    """
    allow_loopback = request.node.get_closest_marker("allow_loopback") is not None
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(fn):
        def wrapper(self, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else address
            if str(host) in _LOOPBACK:
                return fn(self, address, *args, **kwargs)
            raise RuntimeError(f"outbound network call in tests: {address!r}")

        return wrapper

    monkeypatch.setattr(socket.socket, "connect", guard(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(real_connect_ex))

    # Name resolution is its own escape hatch: a test that resolves youtube.com
    # has already left the machine, and on some stacks the connect that follows
    # is issued by a path that does not go through socket.socket.connect.
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is None or str(host) in _LOOPBACK or str(host) == "0.0.0.0":
            return real_getaddrinfo(host, *args, **kwargs)
        raise RuntimeError(f"DNS lookup in tests: {host!r}")

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    real_create_connection = socket.create_connection

    def guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if str(host) in _LOOPBACK:
            return real_create_connection(address, *args, **kwargs)
        raise RuntimeError(f"outbound network call in tests: {address!r}")

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)

    if not allow_loopback:
        # Belt and braces: anything that reaches a transport without an explicit
        # stub should fail immediately rather than wait out a 120s timeout.
        def boom(*args, **kwargs):
            raise RuntimeError("un-stubbed HTTP call in tests")

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", boom)
        monkeypatch.setattr(
            "httpx.AsyncHTTPTransport.handle_async_request",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("un-stubbed HTTP call in tests")),
        )
    yield


@pytest.fixture
def fresh_main(monkeypatch):
    """Reimport main.py under a chosen environment.

    Reloading is what makes this work at all: main.py resolves its configuration
    at module scope, and the reload also clears the lru_caches on _asset_version,
    _render_page and _build_proxy_config, plus the rate-limit state.
    """

    def _load(**env):
        import dotenv

        # A developer's real .env would otherwise override everything below.
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
        for key in KNOWN_ENV_VARS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))

        if "main" in sys.modules:
            return importlib.reload(sys.modules["main"])
        import main

        return main

    return _load


@pytest.fixture
def client(fresh_main):
    """TestClient factory. Runs the lifespan, so the pool and client exist."""
    from fastapi.testclient import TestClient

    created = []

    def _make(**env):
        module = fresh_main(**env)
        ctx = TestClient(module.app)
        ctx.__enter__()
        created.append(ctx)
        return module, ctx

    yield _make
    for ctx in created:
        ctx.__exit__(None, None, None)


@pytest.fixture
def analysis():
    """A well-formed Grok reply: one on-list source, one off-list."""
    return {
        "claims": [
            {
                "claim": "Rome fell in 476 AD.",
                "verdict": "Unsupported",
                "explanation": "476 marks a deposition, not a collapse.",
                "sources": ["cambridge.org", "wikipedia.org"],
            }
        ],
        "overall_assessment": "Broadly reliable, with one common oversimplification.",
        "sources_used": ["cambridge.org", "jstor.org"],
    }


def xff(addr: str) -> dict:
    """Headers that exercise the real rightmost-hop logic.

    The left hop is deliberately junk: it is what a caller can forge, and the
    rate limiter must ignore it.
    """
    return {"x-forwarded-for": f"9.9.9.9, {addr}"}
