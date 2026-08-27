"""call_grok: how a real xAI response — good or bad — becomes an answer.

The transport is stubbed at httpx level, so the whole of call_grok runs: the
payload build, the status branches, the choice extraction and the JSON parse.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

ANALYSIS = {
    "claims": [{"claim": "Rome fell in 476.", "verdict": "Mixed", "sources": ["jstor.org"]}],
    "overall_assessment": "Mostly sound.",
    "sources_used": ["jstor.org"],
}


@pytest.fixture
def grok(fresh_main, monkeypatch):
    """Run call_grok against a canned HTTP response."""
    import asyncio

    def _run(response, **env):
        main = fresh_main(XAI_API_KEY="test-key", **env)
        sent = {}

        async def post(url, **kwargs):
            sent["url"] = url
            sent["json"] = kwargs.get("json")
            sent["headers"] = kwargs.get("headers")
            if isinstance(response, Exception):
                raise response
            return response

        main._grok_client = type("Stub", (), {"post": staticmethod(post)})()
        return main, sent, asyncio.get_event_loop_policy().new_event_loop()

    return _run


def _call(main, loop, transcript="Rome fell in 476 AD. " * 10):
    try:
        return loop.run_until_complete(main.call_grok(transcript, video_context="A video"))
    finally:
        loop.close()


def _reply(content, finish_reason="stop", status=200):
    return httpx.Response(
        status,
        json={"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]},
    )


# --------------------------------------------------------------------------
# Happy path and request shape
# --------------------------------------------------------------------------
def test_a_well_formed_reply_is_parsed(grok):
    main, sent, loop = grok(_reply(json.dumps(ANALYSIS)))
    assert _call(main, loop) == ANALYSIS
    assert sent["url"].endswith("/chat/completions")
    assert sent["headers"]["Authorization"] == "Bearer test-key"


def test_the_transcript_is_truncated_to_the_configured_cap(grok):
    main, sent, loop = grok(_reply(json.dumps(ANALYSIS)), MAX_TRANSCRIPT_CHARS=100)
    _call(main, loop, transcript="x" * 5000)
    user_message = sent["json"]["messages"][1]["content"]
    assert "x" * 100 in user_message
    assert "x" * 101 not in user_message


def test_json_mode_is_requested(grok):
    main, sent, loop = grok(_reply(json.dumps(ANALYSIS)))
    _call(main, loop)
    assert sent["json"]["response_format"] == {"type": "json_object"}


def test_the_system_prompt_marks_the_transcript_as_untrusted(grok):
    main, sent, loop = grok(_reply(json.dumps(ANALYSIS)))
    _call(main, loop)
    system = sent["json"]["messages"][0]["content"]
    assert "untrusted" in system.lower()


# --------------------------------------------------------------------------
# Failure surfaces
# --------------------------------------------------------------------------
def test_truncation_at_max_tokens_is_reported_as_such(grok):
    """A reply cut off at max_tokens is not malformed in any way the caller can
    fix by retrying, and reporting it as 'malformed' hid a cap only the operator
    can raise."""
    main, _, loop = grok(_reply('{"claims": [{"claim": "x"', finish_reason="length"))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 502
    assert "GROK_MAX_TOKENS" in exc.value.detail


def test_a_rejected_key_is_502_not_503(grok):
    """503 means 'no key configured' — the one state the page may treat as
    non-live. A key that exists but was rejected is a server fault."""
    main, _, loop = grok(httpx.Response(401, json={"error": "invalid"}))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 502


def test_upstream_rate_limiting_is_passed_through(grok):
    main, _, loop = grok(httpx.Response(429, json={"error": "slow down"}))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 429


def test_other_upstream_errors_do_not_leak_the_body(grok):
    main, _, loop = grok(httpx.Response(500, text="internal detail: api-key-abc123 failed"))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 502
    assert "api-key-abc123" not in exc.value.detail


def test_an_empty_reply_is_502(grok):
    main, _, loop = grok(_reply(""))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 502
    assert "empty" in exc.value.detail.lower()


def test_an_unexpected_payload_shape_is_502(grok):
    main, _, loop = grok(httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 502


def test_a_timeout_is_504(grok):
    main, _, loop = grok(httpx.ConnectTimeout("too slow"))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 504


def test_a_transport_error_is_502(grok):
    main, _, loop = grok(httpx.ConnectError("refused"))
    with pytest.raises(HTTPException) as exc:
        _call(main, loop)
    assert exc.value.status_code == 502
    assert "ConnectError" in exc.value.detail


def test_a_missing_key_is_503_with_a_reason(fresh_main):
    import asyncio

    main = fresh_main()
    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        with pytest.raises(HTTPException) as exc:
            loop.run_until_complete(main.call_grok("some transcript"))
    finally:
        loop.close()
    assert exc.value.status_code == 503
    assert getattr(exc.value, "reason", None) == "no_api_key"


# --------------------------------------------------------------------------
# Client lifetime
# --------------------------------------------------------------------------
def test_one_client_is_shared_across_requests(client, monkeypatch, analysis):
    """A fresh AsyncClient per request builds an ssl.SSLContext synchronously on
    the event loop, stalling every other in-flight request."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0)
    seen = []

    async def post(url, **kwargs):
        seen.append(id(module._grok_client))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ANALYSIS)}}]},
        )

    monkeypatch.setattr(module._grok_client, "post", post)
    for _ in range(3):
        assert c.post("/api/analyze", json={"title": "The Fall of Rome"}).status_code == 200
    assert len(set(seen)) == 1


def test_requests_before_startup_are_rejected_cleanly(fresh_main):
    """_grok_client is None outside the lifespan; that must be a 503, not an
    AttributeError surfacing as a 500."""
    import asyncio

    main = fresh_main(XAI_API_KEY="k")
    main._grok_client = None
    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        with pytest.raises(HTTPException) as exc:
            loop.run_until_complete(main.call_grok("transcript text here"))
    finally:
        loop.close()
    assert exc.value.status_code == 503
