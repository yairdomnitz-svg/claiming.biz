# Claimifi.biz — Historical Fact-Checker

**Domain:** claimifi.biz

An AI-powered site that fact-checks historical claims made in YouTube videos, using Grok (xAI)
constrained to a fixed list of academic, archival, and fact-checking domains.

## Files (all at repository root)

```
main.py            FastAPI backend, also serves the frontend
index.html         Frontend (single page, no build step)
requirements.txt   Pinned dependencies
Procfile           Start command fallback
.python-version    Pins the Python version for the Railway build
.env.example       Template for local .env
```

## Railway deployment

The service builds with **Railpack**, which auto-detects Python from `requirements.txt`.
No Dockerfile and no `railway.toml` are needed (Config-as-Code is deprecated).

### 1. Variables

| Variable | Required | Notes |
| --- | --- | --- |
| `XAI_API_KEY` | **Yes** | From https://console.x.ai. Without it the site serves demo results only. |
| `GROK_MODEL` | No | Defaults to `grok-4`. |
| `ALLOWED_ORIGINS` | No | Defaults to `*`. Set to `https://claimifi.biz` to lock down the API. |
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
  `--proxy-headers` is what makes per-IP rate limiting see the real visitor instead of
  Railway's edge. `--timeout-keep-alive 120` keeps long analyses from being cut off.
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
| `GET /` | The frontend |
| `GET /health` | Liveness + configuration status |
| `GET /api/config` | Tells the frontend whether live analysis is available |
| `POST /api/analyze` | `{"url": "..."} ` or `{"title": "..."}` |

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in XAI_API_KEY
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000
