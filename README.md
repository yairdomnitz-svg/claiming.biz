# Claimifi.biz — Historical Fact-Checker

**Domain:** claimifi.biz

A modern AI-powered website that fact-checks historical claims made in YouTube videos.  
Powered by Grok (xAI).

## Files (all at repository root)

```
├── main.py              ← FastAPI backend + serves the website
├── requirements.txt
├── index.html           ← Frontend
├── .env.example
└── README.md
```

## Deploy on Railway (exact steps)

1. Create a **new empty repository** on GitHub (or clear the old one).
2. Upload **these extracted files** (NOT the zip) to the root of the repository:
   - main.py
   - requirements.txt
   - index.html
   - .env.example
   - README.md
3. Go to https://railway.app → New Project → Deploy from GitHub repo → select this repository.
4. In the service **Variables** tab, add:
   - `XAI_API_KEY` = your key from https://console.x.ai
5. In **Settings**:
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Generate a domain under Networking, then add your custom domain `claimifi.biz`.

That is all. Railway will detect Python automatically because `requirements.txt` and `main.py` are at the root.

## Local run

```bash
pip install -r requirements.txt
export XAI_API_KEY="your-key"
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000
