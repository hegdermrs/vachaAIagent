"""Opportunity API routes — archive, delete, mark as sent/unsent."""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.opportunity import Opportunity

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])

# These endpoints are called by htmx and swap out the table row, so they
# return an empty body — the row simply (and smoothly) disappears.


@router.post("/{opp_id}/archive", response_class=HTMLResponse)
async def archive_opportunity(opp_id: int, db: AsyncSession = Depends(get_db)):
    opp = await db.get(Opportunity, opp_id)
    if opp is not None:
        opp.is_archived = 1
        await db.commit()
    return HTMLResponse("")


@router.post("/{opp_id}/unarchive", response_class=HTMLResponse)
async def unarchive_opportunity(opp_id: int, db: AsyncSession = Depends(get_db)):
    opp = await db.get(Opportunity, opp_id)
    if opp is not None:
        opp.is_archived = 0
        await db.commit()
    return HTMLResponse("")


@router.delete("/{opp_id}", response_class=HTMLResponse)
async def delete_opportunity(opp_id: int, db: AsyncSession = Depends(get_db)):
    opp = await db.get(Opportunity, opp_id)
    if opp is not None:
        await db.delete(opp)
        await db.commit()
    return HTMLResponse("")
