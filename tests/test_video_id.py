"""extract_video_id — the one place a typo turns into someone else's video."""

from __future__ import annotations

import pytest

VALID = "dQw4w9WgXcQ"


@pytest.fixture(scope="module")
def extract():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import main

    return main.extract_video_id


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VALID}",
        f"https://youtube.com/watch?v={VALID}",
        f"https://m.youtube.com/watch?v={VALID}",
        f"https://music.youtube.com/watch?v={VALID}",
        f"https://youtu.be/{VALID}",
        f"https://youtu.be/{VALID}?si=AbCdEfGhIjKl",
        f"https://www.youtube.com/embed/{VALID}",
        f"https://www.youtube-nocookie.com/embed/{VALID}",
        f"https://www.youtube.com/shorts/{VALID}",
        f"https://www.youtube.com/live/{VALID}",
        f"https://www.youtube.com/v/{VALID}",
        f"https://www.youtube.com/watch?app=desktop&v={VALID}",
        f"https://www.youtube.com/watch?feature=share&list=PLx&v={VALID}",
        VALID,
    ],
)
def test_accepts_real_video_urls(extract, url):
    assert extract(url) == VALID


@pytest.mark.parametrize("word", ["Renaissance", "Reformation", "Charlemagne", "abcdefghijk"])
def test_eleven_letter_words_are_titles_not_video_ids(extract, word):
    """"Renaissance" is exactly 11 letters. Treating every 11-character token as
    an id sent common one-word history topics down the transcript path, where
    they 404 - and blocked the title-only feature the FAQ points users to."""
    assert len(word) == 11
    assert extract(word) is None


@pytest.mark.parametrize("ident", ["dQw4w9WgXcQ", "a_bcdefghij", "abcdefghi-j", "12345678901"])
def test_id_shaped_bare_tokens_are_still_accepted(extract, ident):
    """A real id almost always carries a digit, "-" or "_"."""
    assert extract(ident) == ident


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "The Fall of the Roman Empire",
        "https://www.youtube.com/playlist?list=PLabcdefghij",
        "https://www.youtube.com/@SomeHistoryChannel",
        "https://vimeo.com/123456789",
        "Why 1453 mattered",
    ],
)
def test_rejects_non_video_input(extract, text):
    assert extract(text) is None


def test_over_long_token_is_not_truncated(extract):
    """A 16-char token used to match its own first 11 characters.

    That silently redirected a mistyped link to a different, real video and
    analysed it as if the user had asked for it.
    """
    assert extract(f"https://www.youtube.com/watch?v={VALID}AAAAA") is None
    assert extract(f"https://youtu.be/{VALID}extra12") is None
    assert extract(f"https://www.youtube.com/shorts/{VALID}xyz") is None


def test_videoseries_is_not_a_video_id(extract):
    """YouTube's reserved playlist-embed segment is exactly 11 characters."""
    assert extract("https://www.youtube.com/embed/videoseries?list=PLabcdef") is None


def test_bare_id_must_be_the_whole_string(extract):
    assert extract(f"a {VALID} b") is None
    assert extract(f"{VALID}x") is None
