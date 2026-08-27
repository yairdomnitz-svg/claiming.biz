"""Source-level guards over app.js / app.html / styles.css.

There is no JS test runner here, and adding one would mean adding a build step
to a repo that deliberately has none. These are regression locks, not behaviour
tests: they catch a revert of a decision, not a logic error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_JS = (REPO / "app.js").read_text(encoding="utf-8")
APP_HTML = (REPO / "app.html").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "index.html").read_text(encoding="utf-8")
STYLES = (REPO / "styles.css").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The page must never invent an analysis
# --------------------------------------------------------------------------
def test_no_fabricated_sample_analysis_remains():
    """A fact-checker rendering invented claims into its results panel is the
    worst failure this product has. The fallback fired on any dropped
    connection and on every 503, including a revoked API key."""
    assert "demoData" not in APP_JS
    assert "The video presents a single chronological sequence" not in APP_JS


def test_network_failure_reports_an_error():
    catch = APP_JS[APP_JS.index(".catch(function (e)") :]
    assert "renderError" in catch
    assert "renderAnalysis" not in catch.split(".then(cleanup")[0]


def test_only_the_no_api_key_503_switches_to_demo_mode():
    """503 is emitted by load balancers and WAFs too. Treating all of them as
    'no key configured' let an infrastructure fault look like a normal state."""
    assert "err.reason === 'no_api_key'" in APP_JS


# --------------------------------------------------------------------------
# Error rendering
# --------------------------------------------------------------------------
def test_pydantic_list_details_are_rendered():
    """FastAPI returns `detail` as a list for anything pydantic rejects, so
    assuming a string turned every length violation into 'error (422)'."""
    assert "function detailText" in APP_JS
    assert "Array.isArray(detail)" in APP_JS


def test_input_has_a_length_cap():
    assert re.search(r'id="videoInput"[^>]*maxlength="\d+"', APP_HTML)


# --------------------------------------------------------------------------
# Client/server agreement on what a video ID is
# --------------------------------------------------------------------------
def test_bare_video_id_is_routed_as_a_url():
    """The server accepts a bare 11-char id as a URL. Sending it as a title
    produced an invented analysis of a meaningless string."""
    assert "function looksLikeVideo" in APP_JS
    assert "(?=[a-zA-Z0-9_-]{11}$)" in APP_JS
    body = APP_JS[APP_JS.index("function run()") :]
    assert "looksLikeVideo(q) ? { url: q } : { title: q }" in body


def test_title_only_results_are_visibly_marked():
    """An analysis that never read the video must not look identical to one
    that did."""
    assert "data.basis === 'title'" in APP_JS
    assert "Title only" in APP_JS
    assert "No transcript was read" in APP_JS


# --------------------------------------------------------------------------
# Accessibility
# --------------------------------------------------------------------------
def test_elapsed_counter_is_not_announced():
    """It sits inside the results panel and ticks once a second for up to three
    minutes — as a live region that is continuous speech over everything else."""
    line = next(l for l in APP_JS.splitlines() if 'id="elapsed"' in l and "div" in l)
    assert 'aria-hidden="true"' in line


def test_results_panel_is_not_a_live_region():
    results = re.search(r'<div id="results"[^>]*>', APP_HTML).group(0)
    assert "aria-live" not in results
    assert 'aria-busy' in results


def test_a_dedicated_status_line_exists():
    assert 'id="srStatus"' in APP_HTML
    assert "function announce" in APP_JS


def test_skip_link_target_is_focusable():
    for html in (APP_HTML, INDEX_HTML):
        assert '<main id="main" tabindex="-1">' in html


def test_sample_output_label_is_not_hidden_from_screen_readers():
    """'Sample analysis' is the only thing marking that block as fabricated, and
    aria-hidden hid it from exactly the users who cannot see the frame."""
    bar = re.search(r'<div class="sample-bar"[^>]*>', INDEX_HTML).group(0)
    assert "aria-hidden" not in bar


def test_input_keeps_a_visible_focus_indicator():
    assert "#videoInput:focus-visible" in STYLES


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
@pytest.mark.parametrize("selector", [".claim-text", ".claim-why", ".panel-body p"])
def test_long_tokens_wrap_instead_of_being_clipped(selector):
    """.panel clips overflow and body hides horizontal scroll, so an unbroken
    token — a 300-char pasted title needs no model at all — is unreadable and
    unreachable."""
    rule = re.search(re.escape(selector) + r"\s*\{[^}]*\}", STYLES)
    assert rule, f"{selector} rule missing"
    assert "overflow-wrap" in rule.group(0)


def test_every_class_the_script_emits_is_styled():
    """An unstyled class rendered at runtime is a visibly broken results panel,
    and nothing else in this repo would catch it."""
    emitted = set()
    # Only literal class="..." segments; the scan stops at the first quote or
    # concatenation so an interpolated variable name is never mistaken for one.
    for value in re.findall(r"""class="([a-z0-9 _-]*)""", APP_JS):
        emitted.update(value.split())
    for name in re.findall(r"classList\.add\('([^']+)'\)", APP_JS):
        emitted.add(name)
    # Interpolated through the `pill` variable, so they never appear inside a
    # class="..." literal for the scan above to find.
    emitted.update({"busy", "done", "error", "demo"})

    styled = set(re.findall(r"\.([a-zA-Z][\w-]*)", STYLES))
    missing = sorted(c for c in emitted if c not in styled)
    assert not missing, f"unstyled classes rendered at runtime: {missing}"


# --------------------------------------------------------------------------
# Comments must not describe behaviour the code does not have
# --------------------------------------------------------------------------
def test_deep_link_comment_matches_the_code():
    """The comment claimed ?q= 'runs immediately'; it only prefills — and it
    should, because a link must not be able to spend an API call on load."""
    comment = APP_JS[APP_JS.index("// Deep link:") : APP_JS.index("// Deep link:") + 400]
    assert "does not run" in comment
    tail = APP_JS[APP_JS.index("var q0 = new URLSearchParams") :]
    assert "run()" not in tail
