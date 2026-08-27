"""POST /api/analyze — the contract the frontend and every caller depends on."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from conftest import xff

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def live(client, monkeypatch, analysis):
    """A fully configured app with both network boundaries stubbed."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0, GLOBAL_RATE_LIMIT_REQUESTS=0)
    monkeypatch.setattr(module, "call_grok", AsyncMock(return_value=analysis))
    monkeypatch.setattr(
        module, "_fetch_transcript_sync", lambda vid: "Rome fell in 476 AD. " * 10
    )
    return module, c


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [{}, {"url": ""}, {"title": "   "}, {"url": None, "title": None}],
)
def test_missing_input_is_400(live, body):
    _, c = live
    r = c.post("/api/analyze", json=body)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)


def test_short_title_is_400(live):
    _, c = live
    assert c.post("/api/analyze", json={"title": "ab"}).status_code == 400


@pytest.mark.parametrize("title", ["​​​​​", "‍‍‍", "  ​  "])
def test_zero_width_title_is_rejected(live, title):
    """Counting code points let a visually empty title through, and it was then
    billed as an analysis of nothing."""
    _, c = live
    assert c.post("/api/analyze", json={"title": title}).status_code == 400


@pytest.mark.parametrize("title", ["A B", "abc", "1917", "日本の歴史"])
def test_short_but_real_titles_are_accepted(live, title):
    """Only genuinely invisible characters are discounted. A space occupies
    space, so 'A B' is a three-character title."""
    _, c = live
    assert c.post("/api/analyze", json={"title": title}).status_code == 200


@pytest.mark.parametrize(
    "title",
    ["Café Müller", "العصور الوسطى", "Rome 🏛️ falls"],
)
def test_unicode_titles_survive_intact(live, title):
    """Trimming model output must not mangle accents, RTL text or emoji."""
    _, c = live
    body = c.post("/api/analyze", json={"title": title}).json()
    assert body["video_title"] == title


def test_bare_video_id_as_title_is_rejected(live):
    """The server accepts a bare 11-char id as a *URL*. Letting the same string
    through the title branch produced an invented analysis of a meaningless
    string, badged as a completed check."""
    module, c = live
    r = c.post("/api/analyze", json={"title": "dQw4w9WgXcQ"})
    assert r.status_code == 400
    assert "full link" in r.json()["detail"]
    module.call_grok.assert_not_awaited()


def test_ordinary_title_still_works(live):
    _, c = live
    r = c.post("/api/analyze", json={"title": "The Fall of the Roman Empire"})
    assert r.status_code == 200


def test_over_length_title_returns_a_readable_error(live):
    """Pydantic's detail is a LIST. The frontend must be able to render it, so
    the shape is part of the contract and is asserted here."""
    _, c = live
    r = c.post("/api/analyze", json={"title": "a" * 301})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, list)
    assert any("msg" in item for item in detail)


def test_malformed_url_is_400(live):
    _, c = live
    r = c.post("/api/analyze", json={"url": "https://vimeo.com/1234"})
    assert r.status_code == 400


def test_get_is_not_allowed(live):
    _, c = live
    assert c.get("/api/analyze").status_code == 405


# --------------------------------------------------------------------------
# Response shape
# --------------------------------------------------------------------------
def test_url_analysis_reports_transcript_basis(live):
    _, c = live
    body = c.post("/api/analyze", json={"url": URL}).json()
    assert body["basis"] == "transcript"
    assert body["video_id"] == "dQw4w9WgXcQ"


def test_title_analysis_reports_title_basis(live):
    """The page must be able to tell an analysis that read the video from one
    that only guessed from its title. They are otherwise identical."""
    _, c = live
    body = c.post("/api/analyze", json={"title": "The Fall of Rome"}).json()
    assert body["basis"] == "title"
    assert body["video_id"] is None


def test_off_list_sources_are_dropped(live):
    _, c = live
    body = c.post("/api/analyze", json={"url": URL}).json()
    assert body["sources_used"] == ["cambridge.org", "jstor.org"]
    assert body["claims"][0]["sources"] == ["cambridge.org"]  # wikipedia.org filtered


def test_sources_used_is_not_fabricated(client, monkeypatch):
    """Reporting six trusted domains the model never cited is fabricated
    provenance on a product whose premise is that verdicts are sourced."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0)
    monkeypatch.setattr(
        module,
        "call_grok",
        AsyncMock(
            return_value={
                "claims": [],
                "overall_assessment": "Nothing checkable.",
                "sources_used": ["wikipedia.org", "history.com"],
            }
        ),
    )
    body = c.post("/api/analyze", json={"title": "Some video"}).json()
    assert body["sources_used"] == []


def test_missing_api_key_is_503_with_a_machine_readable_reason(client):
    """The frontend treats this one 503 differently from every other 503, so it
    needs a marker that a WAF or load balancer cannot accidentally produce."""
    _, c = client(RATE_LIMIT_REQUESTS=0)
    r = c.post("/api/analyze", json={"title": "The Fall of Rome"})
    assert r.status_code == 503
    assert r.json()["reason"] == "no_api_key"


def test_rejected_api_key_is_502_not_503(client, monkeypatch, respx_free_transport=None):
    """A key that exists but was rejected is a server fault. Returning 503 made
    it indistinguishable from 'no key configured', which the frontend is
    allowed to treat as an expected, non-error state."""
    import httpx

    module, c = client(XAI_API_KEY="bad-key", RATE_LIMIT_REQUESTS=0)

    async def unauthorized(*args, **kwargs):
        return httpx.Response(401, json={"error": "invalid api key"})

    monkeypatch.setattr(module._grok_client, "post", unauthorized)
    r = c.post("/api/analyze", json={"title": "The Fall of Rome"})
    assert r.status_code == 502
    assert "reason" not in r.json()


# --------------------------------------------------------------------------
# Transcript failure surfaces
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error_name,expected",
    [
        ("IpBlocked", 502),
        ("RequestBlocked", 502),
        ("TranscriptsDisabled", 404),
        ("NoTranscriptFound", 404),
        ("VideoUnavailable", 404),
        ("AgeRestricted", 403),
        ("PoTokenRequired", 403),
    ],
)
def test_transcript_errors_map_to_useful_statuses(live, monkeypatch, error_name, expected):
    module, c = live
    from youtube_transcript_api import _errors as yt_errors

    cls = getattr(yt_errors, error_name)

    def raise_it(video_id):
        if error_name == "NoTranscriptFound":
            exc = cls(video_id, ["en"], [])
        else:
            exc = cls(video_id)
        raise module._classify_transcript_error(exc)

    monkeypatch.setattr(module, "_fetch_transcript_sync", raise_it)
    assert c.post("/api/analyze", json={"url": URL}).status_code == expected


def test_video_id_cannot_steer_its_own_error_classification(live, monkeypatch):
    """Classification used to sniff str(exc), which embeds the caller-supplied
    id via the watch URL — so an id containing 'blocked' rewrote the answer."""
    module, c = live
    from youtube_transcript_api._errors import VideoUnavailable

    def raise_it(video_id):
        raise module._classify_transcript_error(VideoUnavailable("xxblockedxx"))

    monkeypatch.setattr(module, "_fetch_transcript_sync", raise_it)
    r = c.post("/api/analyze", json={"url": URL})
    assert r.status_code == 404  # not the 502 an IP block would give


def test_library_internals_are_not_shown_to_visitors(live, monkeypatch):
    """The library's messages are written for the developer who installed it,
    complete with a 'please open a GitHub issue' line."""
    module, c = live

    def raise_it(video_id):
        raise module._classify_transcript_error(RuntimeError("open a GitHub issue at https://github.com/..."))

    monkeypatch.setattr(module, "_fetch_transcript_sync", raise_it)
    detail = c.post("/api/analyze", json={"url": URL}).json()["detail"]
    assert "github" not in detail.lower()


def test_proxy_hint_is_omitted_when_a_proxy_is_configured(client, monkeypatch):
    """Telling the operator to set env vars they already set is noise that hides
    the real cause (usually the wrong Webshare product)."""
    module, c = client(
        XAI_API_KEY="k",
        RATE_LIMIT_REQUESTS=0,
        WEBSHARE_PROXY_USERNAME="u",
        WEBSHARE_PROXY_PASSWORD="p",
    )
    from youtube_transcript_api._errors import IpBlocked

    def raise_it(video_id):
        raise module._classify_transcript_error(IpBlocked(video_id))

    monkeypatch.setattr(module, "_fetch_transcript_sync", raise_it)
    detail = c.post("/api/analyze", json={"url": URL}).json()["detail"]
    assert "WEBSHARE_PROXY_USERNAME" not in detail

    module2, c2 = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0)
    monkeypatch.setattr(module2, "_fetch_transcript_sync", raise_it)
    detail2 = c2.post("/api/analyze", json={"url": URL}).json()["detail"]
    assert "WEBSHARE_PROXY_USERNAME" in detail2


def test_empty_caption_track_is_422(live, monkeypatch):
    module, c = live
    monkeypatch.setattr(module, "_fetch_transcript_sync", lambda vid: "")
    assert c.post("/api/analyze", json={"url": URL}).status_code == 422


def test_short_transcript_is_422(live, monkeypatch):
    module, c = live
    monkeypatch.setattr(module, "_fetch_transcript_sync", lambda vid: "hi")
    assert c.post("/api/analyze", json={"url": URL}).status_code == 422


def test_transcript_timeout_is_504(client, monkeypatch, analysis):
    import time

    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0, TRANSCRIPT_TIMEOUT=0.3)
    monkeypatch.setattr(module, "call_grok", AsyncMock(return_value=analysis))
    monkeypatch.setattr(module, "_fetch_transcript_sync", lambda vid: time.sleep(3) or "x")
    assert c.post("/api/analyze", json={"url": URL}).status_code == 504


def test_saturated_transcript_pool_rejects_rather_than_queueing(client, monkeypatch, analysis):
    """asyncio.wait_for cancels the await, not the thread, so work that nobody
    is waiting for keeps running. Without a bounded pool, load simply grew
    threads until the process could not start another."""
    import threading
    import time

    module, c = client(
        XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0, TRANSCRIPT_WORKERS=1, TRANSCRIPT_TIMEOUT=5
    )
    monkeypatch.setattr(module, "call_grok", AsyncMock(return_value=analysis))
    monkeypatch.setattr(module, "_fetch_transcript_sync", lambda vid: time.sleep(1.5) or "word " * 50)

    results = []

    def fire():
        r = c.post("/api/analyze", json={"url": URL})
        results.append((r.status_code, r.headers.get("retry-after"), r.json().get("detail", "")))

    threads = [threading.Thread(target=fire) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    codes = [r[0] for r in results]
    # Both halves matter: a permanently broken pool would also produce 503s, so
    # the success proves the limiter is shedding load rather than refusing it.
    assert 503 in codes, f"expected back-pressure, got {results}"
    assert 200 in codes, f"expected work to get through, got {results}"

    shed = next(r for r in results if r[0] == 503)
    assert shed[1] == "30", "the 503 must tell the caller when to come back"
    assert "in flight" in shed[2]

    # And the pool recovers: once the in-flight work drains, the next request
    # is served rather than shed.
    time.sleep(2)
    assert c.post("/api/analyze", json={"url": URL}).status_code == 200
