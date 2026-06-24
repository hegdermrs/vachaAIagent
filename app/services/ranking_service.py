"""Ranking service — keyword-based relevance scoring against artist profile."""
import json
import re
import logging
from app.database import async_session
from app.models.artist import ArtistProfile
from sqlalchemy import select

logger = logging.getLogger("varshini.ranking")

# Scoring weights
WEIGHTS = {
    "medium": 0.30,
    "theme": 0.25,
    "location": 0.20,
    "type_bonus": 0.15,
    "deadline": 0.10,
}

# Location scores — UK is priority
LOCATION_SCORES = {
    "uk": 1.0,
    "united kingdom": 1.0,
    "london": 1.0,
    "england": 1.0,
    "scotland": 1.0,
    "wales": 1.0,
    "northern ireland": 1.0,
    "international": 0.8,
    "worldwide": 0.8,
    "global": 0.8,
    "virtual": 0.7,
    "online": 0.7,
    "remote": 0.7,
    "europe": 0.5,
    "european": 0.5,
}

# Artist mediums and themes — loaded from DB on first use, cached
_profile_cache: dict | None = None


async def _load_profile() -> dict:
    global _profile_cache
    if _profile_cache is not None:
        return _profile_cache

    async with async_session() as session:
        result = await session.execute(select(ArtistProfile).limit(1))
        profile = result.scalar_one_or_none()

    if profile is None:
        _profile_cache = {
            "name": "",
            "bio": "",
            "mediums": [],
            "themes": [],
        }
        return _profile_cache

    _profile_cache = {
        "name": profile.name or "",
        "bio": profile.bio or "",
        "mediums": _parse_json_list(profile.mediums),
        "themes": _parse_json_list(profile.themes),
    }
    return _profile_cache


async def load_profile() -> dict:
    """Public accessor for the cached artist profile."""
    return await _load_profile()


def invalidate_profile_cache() -> None:
    """Clear the cached profile so the next ranking reloads it (call after edits)."""
    global _profile_cache
    _profile_cache = None


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [item.strip().lower() for item in data]
    except (json.JSONDecodeError, TypeError):
        return []


async def rank_opportunities(items: list[dict]) -> list[dict]:
    """Ensure every item has a relevance score, then sort.

    AI scoring (when enabled) is applied earlier during enrichment; here we only
    fill in the keyword-overlap score for any item the AI didn't score.
    """
    profile = await _load_profile()

    for item in items:
        if item.get("relevance_score") is None:
            item["relevance_score"] = _score_one(item, profile)

    items.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    return items


def _score_one(item: dict, profile: dict) -> float:
    text = f"{item.get('title', '')} {item.get('description', '')} {item.get('organization', '')}".lower()

    medium_score = _match_keywords(text, profile.get("mediums", []))
    theme_score = _match_keywords(text, profile.get("themes", []))
    loc_score = _score_location(item.get("location", ""))
    type_bonus = _score_opportunity_type(item.get("opportunity_type", ""))
    deadline_score = _score_deadline(item.get("deadline", ""))

    total = (
        medium_score * WEIGHTS["medium"]
        + theme_score * WEIGHTS["theme"]
        + loc_score * WEIGHTS["location"]
        + type_bonus * WEIGHTS["type_bonus"]
        + deadline_score * WEIGHTS["deadline"]
    )
    return round(min(total, 1.0), 3)


def _match_keywords(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.3  # Neutral if no profile
    hits = 0
    for kw in keywords:
        pattern = re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
        if pattern.search(text):
            hits += 1
    return min(hits / max(len(keywords), 1), 1.0)


# UK eligibility (keyword fallback when AI hasn't judged it)
_UK_TERMS = [
    "uk", "u.k", "united kingdom", "britain", "british", "england", "english",
    "scotland", "scottish", "wales", "welsh", "northern ireland", "london",
    "glasgow", "edinburgh", "manchester", "bristol", "leeds", "liverpool",
    "birmingham", "cardiff", "belfast",
]
_OPEN_TERMS = [
    "international", "worldwide", "global", "any nationality", "open to all",
    "anywhere", "online", "virtual", "remote", "europe", "european", "eu ",
]
_NON_UK_TERMS = [
    "usa", "u.s.", "united states", "america", "american", "canada", "canadian",
    "australia", "new zealand", "germany", "berlin", "france", "paris", "spain",
    "italy", "netherlands", "amsterdam", "new york", "los angeles", "chicago",
    "mexico", "brazil", "são paulo", "sao paulo", "argentina", "india", "china",
    "japan", "korea", "singapore", "africa", "nigeria", "south africa",
]


def is_uk_eligible(item: dict) -> bool:
    """Keyword heuristic: can a UK-based artist apply?

    Keeps UK / international / online / unknown listings; drops only those that
    clearly name a specific non-UK location with no UK or open-to-all signal.
    """
    text = f"{item.get('location') or ''} {item.get('eligibility') or ''}".lower()
    if not text.strip():
        return True  # unknown — don't over-filter
    if any(t in text for t in _UK_TERMS) or any(t in text for t in _OPEN_TERMS):
        return True
    if any(t in text for t in _NON_UK_TERMS):
        return False
    return True  # ambiguous — keep


def _score_location(location: str) -> float:
    if not location:
        return 0.3  # Unknown — neutral
    loc_lower = location.lower().strip()
    for key, score in LOCATION_SCORES.items():
        if key in loc_lower:
            return score
    return 0.2  # Specific non-UK country — low


def _score_opportunity_type(op_type: str) -> float:
    if not op_type:
        return 0.3
    type_lower = op_type.lower()
    # Prefer residencies, exhibitions, and grants
    preferred = {"residency": 0.9, "exhibition": 0.85, "grant": 0.8,
                 "fellowship": 0.8, "commission": 0.75, "competition": 0.6,
                 "open call": 0.5, "prize": 0.4}
    for key, score in preferred.items():
        if key in type_lower:
            return score
    return 0.3


def _score_deadline(deadline: str) -> float:
    if not deadline:
        return 0.0
    try:
        from dateutil.parser import parse
        from datetime import datetime, timezone
        deadline_dt = parse(deadline)
        now = datetime.now(timezone.utc)
        deadline_dt = deadline_dt.replace(tzinfo=timezone.utc) if deadline_dt.tzinfo is None else deadline_dt
        delta = (deadline_dt - now).days
        if delta < 0:
            return 0.0  # Past deadline
        elif delta <= 14:
            return 1.0
        elif delta <= 30:
            return 0.7
        elif delta <= 90:
            return 0.4
        else:
            return 0.1
    except (ValueError, TypeError):
        return 0.0
