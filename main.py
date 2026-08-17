"""
Claimifi.biz Backend
-------------------
Historical fact-checker for YouTube videos powered by Grok (xAI).

Run:
  cd backend
  python -m venv venv && source venv/bin/activate
  pip install -r requirements.txt
  export XAI_API_KEY="your-key"
  uvicorn main:app --reload --port 8000

Then open http://localhost:8000
"""

import os
import re
import json
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

load_dotenv()

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Claimifi.biz",
    description="Historical fact-checking powered by Grok (xAI)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent

XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4")

TRUSTED_SOURCES = [
    "historians.org", "oah.org", "history.ac.uk", "iamhist.net",
    "jstor.org", "muse.jhu.edu", "archives.gov", "docsteach.org",
    "loc.gov", "britishpathe.com", "aparchive.com", "americanarchive.org",
    "iwm.org.uk", "bfi.org.uk", "dp.la", "archive.org",
    "academic.oup.com", "cambridge.org", "hup.harvard.edu", "yalebooks.yale.edu",
    "press.princeton.edu", "press.uchicago.edu", "ucpress.edu", "cup.columbia.edu",
    "taylorandfrancis.com", "routledge.com", "brill.com", "onlinelibrary.wiley.com",
    "iupress.org", "factcheck.org", "politifact.com", "snopes.com",
]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None


class Claim(BaseModel):
    claim: str
    verdict: str
    explanation: str
    sources: List[str] = []


class AnalyzeResponse(BaseModel):
    video_title: Optional[str] = None
    video_id: Optional[str] = None
    transcript_preview: Optional[str] = None
    claims: List[Claim]
    overall_assessment: str
    sources_used: List[str]
    note: str = "Analysis powered by Grok. Always cross-check with primary sources."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_transcript(video_id: str) -> str:
    """Fetch transcript. Tries modern API first, then older classmethod."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="youtube-transcript-api is not installed. Run: pip install youtube-transcript-api",
        )

    # Modern style (v1.x)
    try:
        ytt = YouTubeTranscriptApi()
        fetched = ytt.fetch(video_id, languages=["en", "en-US", "en-GB"])
        text = " ".join(snippet.text for snippet in fetched)
        if text.strip():
            return text
    except Exception:
        pass

    # Older style fallback
    try:
        entries = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-US", "en-GB"]
        )
        text = " ".join(entry["text"] for entry in entries)
        if text.strip():
            return text
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"No usable transcript found for this video. ({type(e).__name__}: {e})",
        )

    raise HTTPException(status_code=400, detail="Transcript was empty.")


def build_system_prompt() -> str:
    sources = ", ".join(TRUSTED_SOURCES)
    return f"""You are Claimifi.biz, a rigorous historical fact-checker focused on global and ancient history.

You may only base your analysis on knowledge consistent with these trusted domains:
{sources}

Instructions:
1. Extract the main historical claims from the transcript (people, dates, events, causes, outcomes, numbers, interpretations).
2. For every distinct claim give one of these verdicts:
   - "Supported"
   - "Mixed"
   - "Unsupported"
   - "Insufficient Evidence"
3. Write a short, precise explanation for each verdict.
4. List only domains from the trusted list in the sources field.
5. Stay neutral and educational. Do not moralize.
6. Return ONLY valid JSON with this exact shape (no markdown fences):

{{
  "claims": [
    {{
      "claim": "the claim text",
      "verdict": "Supported | Mixed | Unsupported | Insufficient Evidence",
      "explanation": "2-4 sentence explanation",
      "sources": ["domain1.com", "domain2.org"]
    }}
  ],
  "overall_assessment": "2-4 sentence summary of the video's historical reliability",
  "sources_used": ["list of trusted domains used"]
}}
"""


async def call_grok(transcript: str, video_context: str = "") -> dict:
    if not XAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="XAI_API_KEY is not set. Get a key at https://console.x.ai and export it.",
        )

    truncated = transcript[:100000]
    user_content = (
        f"Analyze the historical claims in this YouTube video.\n\n"
        f"Context: {video_context}\n\n"
        f"Transcript:\n\"\"\"\n{truncated}\n\"\"\"\n\n"
        f"Return the JSON analysis now."
    )

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 4000,
    }

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{XAI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Grok API returned {resp.status_code}: {resp.text[:400]}",
        )

    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            return json.loads(match.group(0))
        raise HTTPException(
            status_code=502,
            detail="Grok returned invalid JSON. First 300 chars: " + raw[:300],
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"error": "index.html not found. Expected in the same folder as main.py."}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "grok_key_configured": bool(XAI_API_KEY),
        "model": GROK_MODEL,
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    if not req.url and not req.title:
        raise HTTPException(status_code=400, detail="Provide either 'url' or 'title'.")

    video_id = None
    transcript = ""
    video_title = req.title or "Unknown title"

    if req.url:
        video_id = extract_video_id(req.url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Could not extract a valid YouTube video ID.")
        transcript = get_transcript(video_id)
        video_title = f"YouTube video ({video_id})"
    else:
        transcript = f"[No transcript – analyzing from title only: {req.title}]"

    if len(transcript.strip()) < 30:
        raise HTTPException(status_code=400, detail="Transcript too short to analyze.")

    analysis = await call_grok(transcript, video_context=video_title)

    claims = []
    for c in analysis.get("claims", []):
        claims.append(
            Claim(
                claim=c.get("claim", ""),
                verdict=c.get("verdict", "Insufficient Evidence"),
                explanation=c.get("explanation", ""),
                sources=c.get("sources", []),
            )
        )

    return AnalyzeResponse(
        video_title=video_title,
        video_id=video_id,
        transcript_preview=(transcript[:350] + "...") if len(transcript) > 350 else transcript,
        claims=claims,
        overall_assessment=analysis.get("overall_assessment", "No overall assessment returned."),
        sources_used=analysis.get("sources_used", TRUSTED_SOURCES[:6]),
        note="Analysis powered by Grok (xAI). Educational tool only – always verify with primary sources.",
    )
