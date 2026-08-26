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
| `XAI_API_KEY` | **Yes** | From https://console.x.ai. Without it the site serves demo results only. |
| `GROK_MODEL` | No | Defaults to `grok-4`. |
| `ALLOWED_ORIGINS` | No | Defaults to `*`. Set to `https://claimifi.biz` to lock down the API. |
| `SITE_URL` | No | Canonical origin for `robots.txt` and `sitemap.xml`. Defaults to `https://claimifi.biz` — **set this on any other deploy** or the sitemap advertises the wrong host. |
| `TRANSCRIPT_HTTP_TIMEOUT` | No | Per-request bound on YouTube calls, in seconds. Default `12`. |
| `RATE_LIMIT_REQUESTS` | No | Analyses per IP per window. Default `10`. `0` disables. |
| `RATE_LIMIT_WINDOW` | No | Window in seconds. Default `600`. |
| `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` | See below | Residential proxy for transcript fetching. |
| `PROXY_URL` | See below | Alternative to Webshare: any `http://user:pass@host:port`. |

Do **not** set `PORT` yourself — Railway injects it and must match the domain's target port.

### 2. Settings → Deploy

- **Custom Start Command:**
  ```
  uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 120
  ```
  `--timeout-keep-alive 120` keeps long analyses from being cut off. Rate limiting
  reads the rightmost `X-Forwarded-For` hop — the address Railway itself observed —
  because anything further left is supplied by the caller and can be forged.
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

Set `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` (Webshare's residential tier is
what `youtube-transcript-api` supports natively) or `PROXY_URL` for any other provider. Until
one is configured, URL analysis returns a 502 explaining the block, while title-only analysis
still works because it never touches YouTube.

`GET /health` reports `transcript_proxy_configured` so you can confirm it took effect.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Landing page |
| `GET /app` | Analyzer; accepts `?q=` to prefill the input |
| `GET /health` | Liveness + configuration status |
| `GET /api/config` | Tells the frontend whether live analysis is available |
| `POST /api/analyze` | `{"url": "..."}` or `{"title": "..."}` |
| `GET /robots.txt`, `/sitemap.xml` | SEO |

The interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled.

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in XAI_API_KEY
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000
