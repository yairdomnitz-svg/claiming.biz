# Claimifi.biz — Historical Fact-Checker

**Domain:** claimifi.biz

An AI-powered site that fact-checks historical claims made in YouTube videos, using Grok (xAI)
constrained to a fixed list of academic, archival, and fact-checking domains.

## Files (all at repository root)

```
main.py                FastAPI backend; serves both pages and the API
index.html             Landing page (/)
app.html               Analyzer (/app)
styles.css             Shared stylesheet
app.js                 Shared analyzer logic
favicon.svg            Tab icon
apple-touch-icon.png   iOS home-screen icon
og-image.png           1200x630 social preview
google*.html           Search Console verification file
requirements.txt       Pinned dependencies
Procfile               Start command fallback
.python-version        Pins the Python version for the Railway build
.env.example           Template for local .env
```

There is no build step: the CSS and JS are served as-is. Both pages reference
them as `/styles.css?v=<hash>`, where the hash is derived from the file contents
at startup, so a deploy invalidates a visitor's cached copy automatically.

## Railway deployment

The service builds with **Railpack**, which auto-detects Python from `requirements.txt`.
No Dockerfile and no `railway.toml` are needed (Config-as-Code is deprecated).

### 1. Variables

| Variable | Required | Notes |
| --- | --- | --- |
| `XAI_API_KEY` | **Yes** | From https://console.x.ai. Without it `/api/analyze` returns `503` with `"reason": "no_api_key"` and the page says so. There is no demo mode: a fact-checker must never show invented verdicts. |
| `GROK_MODEL` | No | Defaults to `grok-4`. |
| `ALLOWED_ORIGINS` | No | Comma-separated. Defaults to `SITE_URL` and its `www.` form — **not** `*`, because `/api/analyze` is unauthenticated and costs money per call. `*` alone is accepted; `*` mixed with explicit origins is refused at startup. |
| `SITE_URL` | No | Canonical origin for `robots.txt` and `sitemap.xml`. Defaults to `https://claimifi.biz` — **set this on any other deploy** or the sitemap advertises the wrong host. |
| `TRANSCRIPT_TIMEOUT` | No | Total budget for one transcript fetch, in seconds. Default `45`. |
| `TRANSCRIPT_HTTP_TIMEOUT` | No | Ceiling on any single YouTube call, in seconds. Default `10`. The real bound is `TRANSCRIPT_TIMEOUT`: one fetch issues several calls and the thread cannot be cancelled once started, so the session tracks a wall-clock deadline across all of them. |
| `TRANSCRIPT_WORKERS` | No | Concurrent transcript fetches allowed in flight. Default `8`. Beyond this, `/api/analyze` returns 503 rather than starting work nobody is waiting for. |
| `GLOBAL_RATE_LIMIT_REQUESTS` | No | Analyses per window across *all* callers, so many IPs cannot together bypass the per-IP limit. Default `300`. `0` disables. |
| `GLOBAL_RATE_LIMIT_WINDOW` | No | Window for the global limit, in seconds. Default `3600`. |
| `MAX_RATE_BUCKETS` | No | Hard cap on rate-limit buckets held in memory. Default `20000`. |
| `GROK_TIMEOUT` | No | Seconds to wait on xAI. Default `120`. |
| `GROK_MAX_TOKENS` | No | Output budget per analysis. Default `8000`. A reply cut off here returns 502 rather than being reported as malformed. |
| `GROK_TEMPERATURE` | No | Default `0.2`. |
| `MAX_TRANSCRIPT_CHARS` | No | Transcript characters sent to Grok. Default `100000`. |
| `XAI_BASE_URL` | No | Default `https://api.x.ai/v1`. |
| `LOG_LEVEL` | No | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. Default `INFO`. An unrecognised value logs a warning and falls back instead of failing to boot. |
| `RATE_LIMIT_REQUESTS` | No | Analyses per IP per window. Default `10`. `0` disables. |
| `RATE_LIMIT_WINDOW` | No | Window in seconds. Default `600`. |
| `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` | See below | Residential proxy for transcript fetching. |
| `WEBSHARE_RETRIES` | No | Retries when an exit node is blocked; each one rotates to a fresh IP. Default `2`. Webshare's own default is 10, which can occupy a worker thread for two minutes. |
| `WEBSHARE_IP_LOCATIONS` | No | Country codes (`nl,de,gb`) to pin the exit pool nearer the deploy region. Empty uses the full pool. |
| `PROXY_URL` | See below | Alternative to Webshare: any `http://user:pass@host:port`. |

Do **not** set `PORT` yourself — Railway injects it and must match the domain's target port.

### 2. Settings → Deploy

- **Custom Start Command:**
  ```
  uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 120
  ```
  `--timeout-keep-alive 120` keeps long analyses from being cut off. Rate limiting
  reads the rightmost `X-Forwarded-For` hop — the address Railway itself observed —
  because anything further left is supplied by the caller and can be forged. IPv6
  addresses are bucketed by `/64`, since a residential customer holds the whole block.
- **Healthcheck Path:** `/health`
- **Serverless:** leave disabled. An analysis can run for a minute or more; cold starts on
  top of that push requests past the client timeout.

### 3. Networking

Add `claimifi.biz` as a custom domain and point DNS at the CNAME Railway shows under
*Show DNS records*. The domain stays in "Waiting for DNS update" until that record
resolves. Use *Generate Domain* to get a `*.up.railway.app` URL for testing in the meantime.

### The transcript problem (read this)

YouTube blocks requests from datacenter IP ranges, which includes all of Railway. Transcript
fetching works on your laptop and then fails in production with an IP-block error. There is no
code-side workaround; requests have to leave from a residential IP.

**Setting up Webshare:**

1. Create an account at https://www.webshare.io
2. Buy a **Residential** package. Not "Proxy Server", not "Static Residential" —
   `youtube-transcript-api` only supports the rotating residential tier, and the
   other two will not work.
3. Copy the **Proxy Username** and **Proxy Password** from
   https://dashboard.webshare.io/proxy/settings — these are proxy credentials,
   not your account login.
4. Add them as `WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD` in the
   Railway **Variables** tab, then click **Deploy** to apply.

The username is combined with the location filter and a `-rotate` suffix
automatically, so a fresh exit IP is used per request. `PROXY_URL` is available
for any other provider. Until one is configured, URL analysis returns a 502
explaining the block, while title-only analysis is unaffected because it never
touches YouTube.

`GET /health` reports `transcript_proxy_configured` and `transcript_proxy_kind` so you can confirm it took effect.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Landing page |
| `GET /app` | Analyzer; accepts `?q=` to prefill the input |
| `GET /health` | Liveness + configuration status |
| `GET /api/config` | Tells the frontend whether live analysis is available |
| `POST /api/analyze` | `{"url": "..."}` (max 2000 chars) or `{"title": "..."}` (3–300 chars) |
| `GET /robots.txt`, `/sitemap.xml` | SEO |

`POST /api/analyze` failure codes: `400` malformed input, or a bare video ID sent as a
title; `403` age-restricted video; `404` no captions / video unavailable; `422` empty or
too-short transcript, or a pydantic validation error (whose `detail` is a **list**, not a
string); `429` per-IP rate limit; `503` no `XAI_API_KEY` (carrying `"reason": "no_api_key"`),
global budget reached, or too many fetches in flight; `502` upstream failure; `504` timeout.

Only `502`, `503` and `504` refund the caller's rate-limit slot. A `404` for a captionless
video is an answer about the video the caller chose, and charging for it is what keeps the
transcript path metered at all.

The response carries `basis: "transcript" | "title"`. A `title` analysis never read the
video, and the page marks it as such — do not present the two identically.

The interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled.

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in XAI_API_KEY
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000
