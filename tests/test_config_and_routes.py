"""Configuration parsing, CORS, static routes, and doc/code agreement."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# LOG_LEVEL — the one env var read before `app` exists
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        ("", "INFO"),
        ("   ", "INFO"),
        ("verbose", "INFO"),
        ("20", "INFO"),
        ("nonsense", "INFO"),
        ("debug", "DEBUG"),
        (" DEBUG ", "DEBUG"),
        ("warn", "WARNING"),  # a real alias; must keep resolving
        ("warning", "WARNING"),
        ("ERROR", "ERROR"),
    ],
)
def test_log_level_never_crashes_the_import(fresh_main, value, expected):
    """LOG_LEVEL is read at module scope, before `app` exists, and was the one
    env var with no validation. os.getenv returns "" rather than the default for
    a set-but-blank variable, and basicConfig(level="") raises — so a stray
    variable meant the container simply failed to boot with no route to say so.

    The level is asserted through the resolver rather than the root logger:
    logging.basicConfig is a no-op once handlers exist, and pytest installs its
    own before main.py is ever imported.
    """
    import logging

    module = fresh_main(LOG_LEVEL=value)
    assert module is not None  # the import itself must survive
    assert logging.getLevelName(module._env_log_level()) == expected


# --------------------------------------------------------------------------
# Numeric env parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["", "  ", "abc", "1.5.2"])
def test_bad_int_falls_back(fresh_main, value):
    module = fresh_main(RATE_LIMIT_REQUESTS=value)
    assert module.RATE_LIMIT_REQUESTS == 10


def test_transcript_workers_is_at_least_one(fresh_main):
    assert fresh_main(TRANSCRIPT_WORKERS="0").TRANSCRIPT_WORKERS == 1
    assert fresh_main(TRANSCRIPT_WORKERS="-5").TRANSCRIPT_WORKERS == 1


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------
def test_default_origins_are_not_a_wildcard(fresh_main):
    """/api/analyze is unauthenticated and costs money per call. A wildcard lets
    any page spend that budget from its visitors' browsers, each arriving from a
    different residential IP with its own fresh quota."""
    module = fresh_main(SITE_URL="https://claimifi.biz")
    assert "*" not in module.ALLOWED_ORIGINS
    assert "https://claimifi.biz" in module.ALLOWED_ORIGINS
    assert "https://www.claimifi.biz" in module.ALLOWED_ORIGINS


def test_wildcard_mixed_with_explicit_origins_is_refused(fresh_main):
    """Starlette derives allow_all_origins from '*' in the list, so a mixed list
    reflects any Origin — while the credentials flag would also switch on."""
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        fresh_main(ALLOWED_ORIGINS="*,https://claimifi.biz")


def test_explicit_wildcard_is_still_allowed(fresh_main):
    module = fresh_main(ALLOWED_ORIGINS="*")
    assert module.ALLOWED_ORIGINS == ["*"]


def test_unlisted_origin_gets_no_cors_header(client):
    _, c = client(SITE_URL="https://claimifi.biz")
    r = c.options(
        "/api/analyze",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_listed_origin_is_allowed(client):
    _, c = client(SITE_URL="https://claimifi.biz")
    r = c.options(
        "/api/analyze",
        headers={
            "Origin": "https://claimifi.biz",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "https://claimifi.biz"


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
def test_health_reports_configuration(client):
    _, c = client(XAI_API_KEY="k", ANALYSIS_ENABLED=1, WEBSHARE_PROXY_USERNAME="u", WEBSHARE_PROXY_PASSWORD="p")
    body = c.get("/health").json()
    assert body["status"] == "ok"
    assert body["grok_key_configured"] is True
    assert body["transcript_proxy_kind"] == "webshare"


def test_config_endpoint_advertises_liveness(client):
    _, c = client()
    assert c.get("/api/config").json()["live"] is False
    _, c2 = client(XAI_API_KEY="k", ANALYSIS_ENABLED=1)
    assert c2.get("/api/config").json()["live"] is True


@pytest.mark.parametrize(
    "path,ctype",
    [
        ("/", "text/html"),
        ("/app", "text/html"),
        ("/styles.css", "text/css"),
        ("/app.js", "application/javascript"),
        ("/favicon.svg", "image/svg+xml"),
        ("/og-image.png", "image/png"),
        ("/robots.txt", "text/plain"),
        ("/sitemap.xml", "application/xml"),
    ],
)
def test_public_routes_serve(client, path, ctype):
    _, c = client()
    r = c.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(ctype)


@pytest.mark.parametrize(
    "path",
    ["/main.py", "/.env", "/nope", "/STYLES.CSS", "/styles.css.", "/%2e%2e/main.py"],
)
def test_catch_all_serves_only_the_whitelist(client, path):
    _, c = client()
    assert c.get(path).status_code == 404


def test_static_assets_carry_cache_headers(client):
    _, c = client()
    assert c.get("/styles.css").headers["cache-control"] == "no-cache, must-revalidate"
    assert c.get("/og-image.png").headers["cache-control"] == "public, max-age=86400"
    assert c.get("/favicon.ico").headers["cache-control"] == "public, max-age=86400"


def test_pages_carry_a_content_hash_so_a_deploy_busts_caches(client):
    """Computed independently of _asset_version: asking the function under test
    what it expects would pass even if it returned a constant."""
    import hashlib

    _, c = client()
    digest = hashlib.sha256()
    for name in ("styles.css", "app.js"):
        digest.update((REPO / name).read_bytes())
    expected = digest.hexdigest()[:10]

    for path in ("/", "/app"):
        html = c.get(path).text
        assert "__ASSET_V__" not in html
        assert f"/styles.css?v={expected}" in html
        assert f"/app.js?v={expected}" in html


def test_the_hash_changes_when_an_asset_changes(fresh_main, tmp_path, monkeypatch):
    """A constant would satisfy the test above; this is what proves it tracks
    the file contents, which is the whole point of the cache-buster."""
    module = fresh_main()
    before = module._asset_version()

    shadow = tmp_path / "site"
    shadow.mkdir()
    for name in ("styles.css", "app.js", "index.html", "app.html"):
        (shadow / name).write_bytes((REPO / name).read_bytes())
    (shadow / "app.js").write_text("/* changed */\n", encoding="utf-8")

    monkeypatch.setattr(module, "FRONTEND_DIR", shadow)
    module._asset_version.cache_clear()
    try:
        assert module._asset_version() != before
    finally:
        module._asset_version.cache_clear()


def test_sitemap_and_robots_follow_site_url(client):
    _, c = client(SITE_URL="https://preview.up.railway.app/")
    assert "https://preview.up.railway.app/sitemap.xml" in c.get("/robots.txt").text
    assert "<loc>https://preview.up.railway.app/app</loc>" in c.get("/sitemap.xml").text


def test_robots_does_not_block_the_deep_links_the_site_advertises(client):
    """'Disallow: /app?' blocked exactly the ?q= URLs the landing page links to,
    while the sitemap listed /app as canonical."""
    _, c = client()
    assert "/app?" not in c.get("/robots.txt").text


def test_api_docs_stay_disabled(client):
    _, c = client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


# --------------------------------------------------------------------------
# Documentation must match the code
# --------------------------------------------------------------------------
def _readme_default(var: str) -> str:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith(f"| `{var}`"))
    match = re.search(r"Default `([^`]+)`", row)
    assert match, f"README row for {var} states no default: {row}"
    return match.group(1)


@pytest.mark.parametrize(
    "var,attr",
    [
        ("TRANSCRIPT_TIMEOUT", "TRANSCRIPT_TIMEOUT"),
        ("TRANSCRIPT_HTTP_TIMEOUT", "TRANSCRIPT_HTTP_TIMEOUT"),
        ("TRANSCRIPT_WORKERS", "TRANSCRIPT_WORKERS"),
        ("GLOBAL_RATE_LIMIT_REQUESTS", "GLOBAL_RATE_LIMIT_REQUESTS"),
        ("GLOBAL_RATE_LIMIT_WINDOW", "GLOBAL_RATE_LIMIT_WINDOW"),
    ],
)
def test_readme_documents_the_real_default(fresh_main, var, attr):
    module = fresh_main()
    assert float(_readme_default(var)) == float(getattr(module, attr))


def test_env_example_matches_the_code_defaults(fresh_main):
    module = fresh_main()
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    for var, value in [
        ("TRANSCRIPT_TIMEOUT", module.TRANSCRIPT_TIMEOUT),
        ("TRANSCRIPT_HTTP_TIMEOUT", module.TRANSCRIPT_HTTP_TIMEOUT),
        ("TRANSCRIPT_WORKERS", module.TRANSCRIPT_WORKERS),
        ("RATE_LIMIT_REQUESTS", module.RATE_LIMIT_REQUESTS),
    ]:
        match = re.search(rf"^#\s*{var}=(\S+)", text, re.M)
        assert match, f"{var} missing from .env.example"
        assert float(match.group(1)) == float(value), var


def test_every_env_var_the_code_reads_is_documented():
    """A knob nobody can find is a knob that does not exist."""
    source = (REPO / "main.py").read_text(encoding="utf-8")
    read = set(re.findall(r'os\.getenv\(\s*"([A-Z_0-9]+)"', source))
    read |= set(re.findall(r'_env_(?:int|float|bool)\(\s*"([A-Z_0-9]+)"', source))
    read.discard("PORT")  # injected by the platform; documented as "do not set"

    docs = (REPO / "README.md").read_text(encoding="utf-8") + (
        REPO / ".env.example"
    ).read_text(encoding="utf-8")
    undocumented = sorted(v for v in read if v not in docs)
    assert not undocumented, f"undocumented env vars: {undocumented}"


def test_trusted_source_count_matches_every_place_it_is_claimed(fresh_main):
    module = fresh_main()
    count = len(module.TRUSTED_SOURCES)
    assert count == len(set(module.TRUSTED_SOURCES)), "duplicate trusted domain"

    index = (REPO / "index.html").read_text(encoding="utf-8")
    listed = set(re.findall(r"<li>([a-z0-9.\-]+\.[a-z]{2,})</li>", index))
    assert listed == set(module.TRUSTED_SOURCES), (
        f"page lists {len(listed)} domains, code has {count}; "
        f"only on page: {sorted(listed - set(module.TRUSTED_SOURCES))}; "
        f"only in code: {sorted(set(module.TRUSTED_SOURCES) - listed)}"
    )

    for page in ("index.html", "app.html"):
        text = (REPO / page).read_text(encoding="utf-8")
        for claimed in re.findall(r"(\d+)\s+(?:vetted|trusted)\s+(?:sources|domains)", text):
            assert int(claimed) == count, f"{page} claims {claimed} sources, code has {count}"


# --- Security headers and compression --------------------------------------


def test_security_headers_on_every_kind_of_response(client):
    """Pages, static assets, JSON and errors all get them.

    The middleware wraps everything else, so an error raised deep inside a route
    must still come back hardened.
    """
    _, c = client()
    for path in ("/", "/app", "/styles.css", "/health", "/api/config", "/nope"):
        headers = c.get(path).headers
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["x-frame-options"] == "DENY", path
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin", path
        assert "content-security-policy" in headers, path


def test_csp_allows_what_the_pages_actually_load(client):
    """Google Fonts and inline style attributes are both real dependencies."""
    _, c = client()
    csp = c.get("/").headers["content-security-policy"]
    assert "https://fonts.googleapis.com" in csp
    assert "https://fonts.gstatic.com" in csp
    assert "'unsafe-inline'" in csp.split("style-src")[1].split(";")[0]


def test_csp_does_not_allow_inline_script(client):
    """The analyzer renders model output through innerHTML. If esc() ever slips,
    this is the header that stops the result from executing."""
    _, c = client()
    csp = c.get("/").headers["content-security-policy"]
    script_src = csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


def test_hsts_only_when_the_request_arrived_over_https(client):
    """Claiming HTTPS from a plain-HTTP dev server is a promise this cannot keep."""
    _, c = client()
    assert "strict-transport-security" not in c.get("/health").headers
    forwarded = c.get("/health", headers={"x-forwarded-proto": "https"})
    assert "max-age=" in forwarded.headers["strict-transport-security"]


def test_pages_are_compressed_for_clients_that_ask(client):
    """index.html is ~23 KB and was going out raw on every visit."""
    _, c = client()
    resp = c.get("/", headers={"accept-encoding": "gzip"})
    assert resp.headers.get("content-encoding") == "gzip"
    # TestClient decodes transparently, so the page itself is still intact.
    assert "Claimifi" in resp.text


def test_compression_is_skipped_when_not_accepted(client):
    _, c = client()
    resp = c.get("/", headers={"accept-encoding": "identity"})
    assert "content-encoding" not in resp.headers
