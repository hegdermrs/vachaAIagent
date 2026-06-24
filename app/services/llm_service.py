"""LLM service — DeepSeek-powered field extraction and relevance scoring.

DeepSeek exposes an OpenAI-compatible chat API, so we talk to it directly with
httpx (already a dependency). Every function fails soft: if no API key is set,
the feature is disabled, and if a request errors it returns None so callers fall
back to the regex / keyword logic. The app keeps working with or without AI.
"""
import json
import logging

import httpx

from app.config import settings_cache, DEEPSEEK_BASE_URL, decrypt_value

logger = logging.getLogger("varshini.llm")

_TIMEOUT = httpx.Timeout(60.0)

OPPORTUNITY_TYPES = [
    "residency", "exhibition", "grant", "fellowship",
    "commission", "competition", "open_call", "prize",
]


def _api_key() -> str:
    return (decrypt_value(settings_cache.get("deepseek_api_key", "")) or "").strip()


def _model() -> str:
    return (settings_cache.get("deepseek_model") or "deepseek-v4-pro").strip()


def is_enabled() -> bool:
    """True when AI is switched on in settings and an API key is configured."""
    if settings_cache.get("ai_enabled", "true") != "true":
        return False
    return bool(_api_key())


async def _chat_json(system: str, user: str, max_tokens: int = 1000) -> dict | None:
    """Call DeepSeek chat completions in JSON mode; return parsed dict or None."""
    key = _api_key()
    if not key:
        return None

    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning(f"DeepSeek request failed: {e}")
        return None

    if not content:
        logger.warning("DeepSeek returned empty content")
        return None
    # Strip ```json fences if the model added them
    if content.startswith("```"):
        content = content.strip("`")
        content = content[4:] if content.lower().startswith("json") else content
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"DeepSeek JSON parse failed: {e}")
        return None


_ANALYZE_SYSTEM = (
    "You analyse scraped web/social text about art and creative opportunities "
    "(open calls, residencies, grants, exhibitions, competitions, fellowships, "
    "commissions, prizes). You extract structured fields AND judge how relevant "
    "the opportunity is to a specific artist. Be accurate, never invent details, "
    "and respond ONLY with a single JSON object."
)


async def analyze(item: dict, profile: dict) -> dict | None:
    """Extract fields AND score relevance in one call. Returns dict or None.

    The returned dict contains the extracted fields plus "is_opportunity",
    "score" (0..1) and "reasoning".
    """
    raw = item.get("raw_data") or {}
    full_text = raw.get("full_text", "") if isinstance(raw, dict) else ""
    text = (full_text or item.get("description") or "")[:5000]

    mediums = ", ".join(profile.get("mediums", []))
    themes = ", ".join(profile.get("themes", []))
    bio = (profile.get("bio") or "")[:1000]

    user = f"""ARTIST PROFILE (score relevance for this artist)
Name: {profile.get('name', '')}
Bio: {bio}
Mediums: {mediums}
Themes: {themes}

OPPORTUNITY SOURCE
URL: {item.get('source_url', '')}
TITLE: {item.get('title', '')}
TEXT:
{text}

Return a JSON object with EXACTLY these keys (use null when genuinely unknown):
- "is_opportunity": boolean — true only if this is a real opportunity an artist can apply to (not a news article, index/listing page, or unrelated content)
- "opportunity_type": one of {OPPORTUNITY_TYPES} or null
- "title": clean concise title (max 140 chars)
- "organization": host organisation / institution, or null
- "deadline": application deadline as ISO date "YYYY-MM-DD" if a specific date is present, else null
- "location": location or geographic eligibility (e.g. "UK", "London", "International", "Online"), or null
- "eligibility": short summary of who can apply, or null
- "uk_eligible": boolean — true if a UK-based artist could apply. UK, international, worldwide, Europe-wide, and online/remote calls all count as true. Mark false ONLY if it is restricted to a specific non-UK country/region (e.g. "US artists only", "must reside in Germany")
- "fee": application/entry fee as short text (e.g. "£25", "Free"), or null
- "medium": comma-separated mediums/disciplines, or null
- "summary": one short plain-English sentence
- "score": number 0..1 — relevance for THIS artist. Weigh medium/theme fit most, then location/eligibility (artist is UK-based: UK, international and online/remote are good; non-UK region-locked calls are poor), then quality
- "reasoning": one short sentence explaining the score"""

    result = await _chat_json(_ANALYZE_SYSTEM, user, max_tokens=900)
    if not result:
        return None
    # Normalise score
    try:
        result["score"] = max(0.0, min(float(result.get("score")), 1.0))
    except (TypeError, ValueError):
        result["score"] = None
    return result
