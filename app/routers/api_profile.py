"""Artist profile API routes."""
import json
from fastapi import APIRouter, Depends, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.artist import ArtistProfile

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.post("")
async def save_profile(
    db: AsyncSession = Depends(get_db),
    name: str = Form(""),
    bio: str = Form(""),
    mediums: str = Form("[]"),
    themes: str = Form("[]"),
    portfolio_url: str = Form(""),
    cv_text: str = Form(""),
    website: str = Form(""),
):
    """Save or update the artist profile."""
    result = await db.execute(select(ArtistProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = ArtistProfile()
        db.add(profile)

    profile.name = name
    profile.bio = bio
    profile.portfolio_url = portfolio_url
    profile.cv_text = cv_text
    profile.website = website

    # Validate and store JSON arrays
    try:
        json.loads(mediums)
        profile.mediums = mediums
    except (json.JSONDecodeError, TypeError):
        profile.mediums = "[]"

    try:
        json.loads(themes)
        profile.themes = themes
    except (json.JSONDecodeError, TypeError):
        profile.themes = "[]"

    await db.commit()

    # Invalidate the ranking cache so next scrape uses updated profile
    from app.services import ranking_service
    ranking_service.invalidate_profile_cache()

    return {"status": "ok"}
