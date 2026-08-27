"""Rate limiting: the only thing standing between /api/analyze and a bill."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from conftest import xff

TITLE = {"title": "The Fall of the Roman Empire"}
URL = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}


def _stub_grok(module, monkeypatch, analysis):
    monkeypatch.setattr(module, "call_grok", AsyncMock(return_value=analysis))


def test_ipv6_addresses_in_one_64_share_a_bucket(client, monkeypatch, analysis):
    """A residential IPv6 customer holds a whole /64 and can pick any address
    in it, so keying on the exact address handed out unlimited free quotas."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=3, RATE_LIMIT_WINDOW=600)
    _stub_grok(module, monkeypatch, analysis)

    codes = [
        c.post("/api/analyze", json=TITLE, headers=xff(f"2001:db8::{i}")).status_code
        for i in range(1, 6)
    ]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


def test_different_64_blocks_are_separate(client, monkeypatch, analysis):
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=1, RATE_LIMIT_WINDOW=600)
    _stub_grok(module, monkeypatch, analysis)

    assert c.post("/api/analyze", json=TITLE, headers=xff("2001:db8::1")).status_code == 200
    assert c.post("/api/analyze", json=TITLE, headers=xff("2001:db9::1")).status_code == 200


def test_forged_left_hops_are_ignored(client, monkeypatch, analysis):
    """Rotating the leftmost X-Forwarded-For entry must not mint new quotas."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW=600)
    _stub_grok(module, monkeypatch, analysis)

    codes = []
    for i in range(4):
        codes.append(
            c.post(
                "/api/analyze",
                json=TITLE,
                headers={"x-forwarded-for": f"1.2.3.{i}, 203.0.113.7"},
            ).status_code
        )
    assert codes == [200, 200, 429, 429]


def test_global_ceiling_applies_across_distinct_ips(client, monkeypatch, analysis):
    """Many IPs must not add up to unlimited spend."""
    module, c = client(
        XAI_API_KEY="k",
        RATE_LIMIT_REQUESTS=100,
        GLOBAL_RATE_LIMIT_REQUESTS=3,
        GLOBAL_RATE_LIMIT_WINDOW=3600,
    )
    _stub_grok(module, monkeypatch, analysis)

    codes = [
        c.post("/api/analyze", json=TITLE, headers=xff(f"203.0.113.{i}")).status_code
        for i in range(1, 6)
    ]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [503, 503]
    assert "budget" in c.post("/api/analyze", json=TITLE, headers=xff("203.0.113.9")).json()["detail"]


def _fail_transcript_with(module, monkeypatch, status, detail="nope"):
    from fastapi import HTTPException

    def boom(video_id):
        raise HTTPException(status_code=status, detail=detail)

    monkeypatch.setattr(module, "_fetch_transcript_sync", boom)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_server_side_transcript_failures_refund_the_slot(client, monkeypatch, analysis, status):
    """An IP block or a timeout is the service failing, not the visitor
    spending. Charging for it burned all ten slots and locked the visitor out of
    the title-only fallback the FAQ points them to."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW=600)
    _stub_grok(module, monkeypatch, analysis)
    _fail_transcript_with(module, monkeypatch, status)

    for _ in range(4):
        assert c.post("/api/analyze", json=URL, headers=xff("198.51.100.4")).status_code == status

    assert c.post("/api/analyze", json=TITLE, headers=xff("198.51.100.4")).status_code == 200


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_caller_chosen_transcript_failures_still_cost_a_slot(client, monkeypatch, analysis, status):
    """This is what keeps the transcript path metered at all.

    The caller picks the video, so if these refunded, an attacker could pick one
    that always fails and loop forever: every attempt still spends metered proxy
    bandwidth and a worker thread, while neither counter ever moves.
    """
    module, c = client(
        XAI_API_KEY="k", RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW=600, GLOBAL_RATE_LIMIT_REQUESTS=0
    )
    _stub_grok(module, monkeypatch, analysis)
    _fail_transcript_with(module, monkeypatch, status)

    codes = [
        c.post("/api/analyze", json=URL, headers=xff("198.51.100.6")).status_code
        for _ in range(4)
    ]
    assert codes == [status, status, 429, 429]


def test_a_caller_cannot_loop_failing_fetches_past_the_global_budget(client, monkeypatch, analysis):
    """The global ceiling is the backstop when the per-IP limit is generous."""
    module, c = client(
        XAI_API_KEY="k",
        RATE_LIMIT_REQUESTS=100,
        GLOBAL_RATE_LIMIT_REQUESTS=3,
        GLOBAL_RATE_LIMIT_WINDOW=3600,
    )
    _stub_grok(module, monkeypatch, analysis)
    _fail_transcript_with(module, monkeypatch, 404)

    codes = [
        c.post("/api/analyze", json=URL, headers=xff(f"198.51.100.{i}")).status_code
        for i in range(10, 20)
    ]
    assert codes.count(503) >= 5, f"global budget never engaged: {codes}"


def test_successful_analysis_still_consumes_a_slot(client, monkeypatch, analysis):
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW=600)
    _stub_grok(module, monkeypatch, analysis)
    monkeypatch.setattr(module, "_fetch_transcript_sync", lambda vid: "word " * 50)

    codes = [
        c.post("/api/analyze", json=URL, headers=xff("198.51.100.9")).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


def test_invalid_input_does_not_consume_a_slot(client, monkeypatch, analysis):
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=1, RATE_LIMIT_WINDOW=600)
    _stub_grok(module, monkeypatch, analysis)

    assert c.post("/api/analyze", json={"url": "not a link"}, headers=xff("198.51.100.5")).status_code == 400
    assert c.post("/api/analyze", json=TITLE, headers=xff("198.51.100.5")).status_code == 200


def test_bucket_sweep_actually_collects(client, monkeypatch, analysis):
    """The old sweep could only drop buckets older than a full window, but the
    caller had just written into the only bucket it touched — so a flood of
    one-shot addresses was never collected and the dict grew unboundedly."""
    module, c = client(
        XAI_API_KEY="k", RATE_LIMIT_REQUESTS=5, RATE_LIMIT_WINDOW=600, MAX_RATE_BUCKETS=1000
    )
    _stub_grok(module, monkeypatch, analysis)

    for i in range(1500):
        module._rate_buckets[f"filler-{i}"] = module.deque([module.time.monotonic()])

    c.post("/api/analyze", json=TITLE, headers=xff("203.0.113.200"))
    assert len(module._rate_buckets) <= 1000


def test_rate_limit_message_reads_correctly_for_short_windows(client, monkeypatch, analysis):
    """A 60s window used to render as 'per 1 minutes'."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=1, RATE_LIMIT_WINDOW=60)
    _stub_grok(module, monkeypatch, analysis)

    c.post("/api/analyze", json=TITLE, headers=xff("203.0.113.30"))
    detail = c.post("/api/analyze", json=TITLE, headers=xff("203.0.113.30")).json()["detail"]
    assert "1 minutes" not in detail
    assert "60 seconds" in detail


def test_rate_limit_can_be_disabled(client, monkeypatch, analysis):
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=0, GLOBAL_RATE_LIMIT_REQUESTS=0)
    _stub_grok(module, monkeypatch, analysis)

    for _ in range(5):
        assert c.post("/api/analyze", json=TITLE, headers=xff("203.0.113.40")).status_code == 200


@pytest.mark.parametrize("addr", ["not-an-ip", "", "  ", "1.2.3.4.5"])
def test_unparseable_address_shares_one_bucket(client, addr):
    module, _ = client(RATE_LIMIT_REQUESTS=1)
    assert module._bucket_key(addr) == "unparsed"


def test_ipv4_mapped_ipv6_keeps_callers_distinct(client):
    """An IPv4 client reaches a dual-stack listener as ::ffff:a.b.c.d. Masked to
    /64 that is '::' for every IPv4 caller on earth, so one of them could
    exhaust the quota for all the others."""
    module, _ = client(RATE_LIMIT_REQUESTS=1)
    assert module._bucket_key("::ffff:1.2.3.4") == "1.2.3.4"
    assert module._bucket_key("::ffff:5.6.7.8") == "5.6.7.8"
    assert module._bucket_key("::ffff:1.2.3.4") != module._bucket_key("::ffff:5.6.7.8")


@pytest.mark.parametrize(
    "addr,expected",
    [("1.2.3.4:5678", "1.2.3.4"), ("[2001:db8::1]:443", "2001:db8::")],
)
def test_port_suffixed_addresses_are_still_identities(client, addr, expected):
    """Some proxies append the source port. Treating those as unparseable would
    drop every one of those callers into a single shared bucket."""
    module, _ = client(RATE_LIMIT_REQUESTS=1)
    assert module._bucket_key(addr) == expected


def test_the_window_expires(client, monkeypatch, analysis):
    """Only the hard size cap was asserted before; a limiter that never released
    a slot would have passed every one of these tests."""
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW=60)
    _stub_grok(module, monkeypatch, analysis)

    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    head = xff("203.0.113.55")
    assert c.post("/api/analyze", json=TITLE, headers=head).status_code == 200
    assert c.post("/api/analyze", json=TITLE, headers=head).status_code == 200
    assert c.post("/api/analyze", json=TITLE, headers=head).status_code == 429

    now[0] += 61  # the whole window has passed
    assert c.post("/api/analyze", json=TITLE, headers=head).status_code == 200


def test_the_window_slides_rather_than_resetting(client, monkeypatch, analysis):
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW=60)
    _stub_grok(module, monkeypatch, analysis)

    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    head = xff("203.0.113.56")

    c.post("/api/analyze", json=TITLE, headers=head)      # t=1000
    now[0] += 30
    c.post("/api/analyze", json=TITLE, headers=head)      # t=1030
    now[0] += 31                                          # t=1061: the first expired
    assert c.post("/api/analyze", json=TITLE, headers=head).status_code == 200
    # ...but the second has not, so the very next one is refused.
    assert c.post("/api/analyze", json=TITLE, headers=head).status_code == 429


def test_the_sweep_evicts_the_coldest_buckets_first(client, monkeypatch, analysis):
    """OrderedDict order is what makes the cap meaningful: evicting a hot bucket
    would hand its owner a fresh quota."""
    module, c = client(
        XAI_API_KEY="k", RATE_LIMIT_REQUESTS=5, RATE_LIMIT_WINDOW=600, MAX_RATE_BUCKETS=1000
    )
    _stub_grok(module, monkeypatch, analysis)

    head = xff("203.0.113.99")
    c.post("/api/analyze", json=TITLE, headers=head)  # the hot bucket, touched last
    for i in range(1500):
        module._rate_buckets[f"cold-{i}"] = module.deque([module.time.monotonic()])
        module._rate_buckets.move_to_end(f"cold-{i}")
    module._rate_buckets.move_to_end("203.0.113.99")   # hottest of all

    module._sweep_buckets(module.time.monotonic())
    assert len(module._rate_buckets) <= 1000
    assert "203.0.113.99" in module._rate_buckets, "the active caller was evicted"


def test_expired_buckets_are_collected_by_the_sweep(client, monkeypatch, analysis):
    module, c = client(XAI_API_KEY="k", RATE_LIMIT_REQUESTS=5, RATE_LIMIT_WINDOW=60)
    _stub_grok(module, monkeypatch, analysis)

    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    for i in range(50):
        module._rate_buckets[f"stale-{i}"] = module.deque([now[0]])

    now[0] += 120  # every one of them is now older than the window
    module._sweep_buckets(now[0])
    assert module._rate_buckets == {}
