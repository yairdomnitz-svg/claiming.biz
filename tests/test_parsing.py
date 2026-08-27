"""Parsing and normalising whatever the model actually returns."""

from __future__ import annotations

import pytest


@pytest.fixture
def main(fresh_main):
    return fresh_main()


# --------------------------------------------------------------------------
# _extract_json_object
# --------------------------------------------------------------------------
def test_plain_object(main):
    assert main._extract_json_object('{"claims": [], "overall_assessment": "x"}') == {
        "claims": [],
        "overall_assessment": "x",
    }


def test_markdown_fences_are_stripped(main):
    assert main._extract_json_object('```json\n{"claims": []}\n```') == {"claims": []}


def test_object_wrapped_in_prose(main):
    assert main._extract_json_object('Sure! {"claims": []} hope that helps') == {"claims": []}


def test_two_objects_picks_the_analysis(main):
    """The greedy span from the first '{' to the last '}' is invalid JSON as
    soon as the reply holds two objects, so the whole reply was rejected."""
    assert main._extract_json_object('{"a": 1} and {"claims": []}') == {"claims": []}


def test_a_reasoning_preamble_does_not_shadow_the_analysis(main):
    """Returning the first balanced object would hand back the preamble, and the
    caller would see a successful analysis with zero claims."""
    raw = '{"reasoning": "let me think"} {"claims": [{"claim": "x"}], "overall_assessment": "y"}'
    parsed = main._extract_json_object(raw)
    assert parsed["overall_assessment"] == "y"
    assert len(parsed["claims"]) == 1


def test_an_object_with_neither_key_is_still_returned(main):
    """Preference, not a requirement: an unrecognised shape must not become a
    502 when it is the only thing the model sent."""
    assert main._extract_json_object('{"a": 1}') == {"a": 1}


def test_trailing_junk_after_the_payload(main):
    parsed = main._extract_json_object('{"claims": [{"claim": "a"}]} trailing {"junk": 1}')
    assert parsed == {"claims": [{"claim": "a"}]}


def test_bare_array_is_read_as_the_claims_list(main):
    """The greedy scan used to pull one arbitrary element out of an array and
    return it as the whole analysis."""
    parsed = main._extract_json_object('[{"claim": "a", "verdict": "Mixed"}]')
    assert parsed == {"claims": [{"claim": "a", "verdict": "Mixed"}]}


def test_braces_inside_strings_do_not_break_the_scan(main):
    parsed = main._extract_json_object('{"overall_assessment": "a } b", "claims": []}')
    assert parsed["overall_assessment"] == "a } b"


def test_unparseable_reply_is_502(main):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        main._extract_json_object("I could not do that.")
    assert exc.value.status_code == 502


# --------------------------------------------------------------------------
# _filter_sources
# --------------------------------------------------------------------------
def test_only_vetted_domains_survive(main):
    assert main._filter_sources(["jstor.org", "wikipedia.org", "evil.tld"]) == ["jstor.org"]


def test_www_prefix_and_case_are_normalised(main):
    assert main._filter_sources(["WWW.LOC.GOV", "loc.gov"]) == ["loc.gov"]


def test_non_list_input_is_empty(main):
    assert main._filter_sources("jstor.org") == []
    assert main._filter_sources(None) == []


def test_limit_is_respected(main):
    assert len(main._filter_sources(main.TRUSTED_SOURCES, limit=3)) == 3


# --------------------------------------------------------------------------
# _normalize_claims
# --------------------------------------------------------------------------
def test_unknown_verdict_falls_back(main):
    claims = main._normalize_claims(
        {"claims": [{"claim": "x", "verdict": "Probably true", "explanation": "e"}]}
    )
    assert claims[0].verdict == "Insufficient Evidence"


def test_verdict_case_is_repaired(main):
    claims = main._normalize_claims({"claims": [{"claim": "x", "verdict": "supported"}]})
    assert claims[0].verdict == "Supported"


def test_claims_without_text_are_dropped(main):
    claims = main._normalize_claims(
        {"claims": [{"claim": "", "verdict": "Mixed"}, {"claim": "ok", "verdict": "Mixed"}]}
    )
    assert len(claims) == 1


def test_non_dict_entries_are_skipped(main):
    claims = main._normalize_claims({"claims": ["nope", 42, {"claim": "ok"}]})
    assert len(claims) == 1


def test_claim_text_is_bounded(main):
    claims = main._normalize_claims({"claims": [{"claim": "x" * 5000, "verdict": "Mixed"}]})
    assert len(claims[0].claim) <= main.MAX_CLAIM_CHARS


def test_claim_count_is_bounded(main):
    payload = {"claims": [{"claim": f"c{i}", "verdict": "Mixed"} for i in range(200)]}
    assert len(main._normalize_claims(payload)) == main.MAX_CLAIMS


def test_control_characters_are_stripped(main):
    claims = main._normalize_claims({"claims": [{"claim": "a\x00b\x07c", "verdict": "Mixed"}]})
    assert "\x00" not in claims[0].claim


def test_missing_claims_key(main):
    assert main._normalize_claims({}) == []
    assert main._normalize_claims({"claims": "nope"}) == []
