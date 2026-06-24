"""Deduplication service — URL hash + fuzzy title matching."""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select

from app.database import async_session
from app.models.opportunity import Opportunity

logger = logging.getLogger("varshini.dedup")


async def filter_duplicates(raw_items: list[dict]) -> list[dict]:
    """Filter out items whose URL or normalized title already exists in DB."""
    if not raw_items:
        return []

    url_hashes = [Opportunity.compute_url_hash(item["source_url"]) for item in raw_items]
    title_hashes = [Opportunity.compute_title_hash(item["title"]) for item in raw_items]

    async with async_session() as session:
        # Check URL hash duplicates
        result = await session.execute(
            select(Opportunity.url_hash).where(Opportunity.url_hash.in_(url_hashes))
        )
        existing_url_hashes = set(result.scalars().all())

        # Check title hash duplicates (90-day window)
        cutoff = datetime.utcnow() - timedelta(days=90)
        result = await session.execute(
            select(Opportunity.title_hash)
            .where(
                Opportunity.title_hash.in_(title_hashes),
                Opportunity.created_at >= cutoff,
            )
        )
        existing_title_hashes = set(result.scalars().all())

    new_items = []
    skipped_url = 0
    skipped_title = 0

    for item in raw_items:
        uh = Opportunity.compute_url_hash(item["source_url"])
        th = Opportunity.compute_title_hash(item["title"])

        if uh in existing_url_hashes:
            skipped_url += 1
            continue
        if th in existing_title_hashes:
            skipped_title += 1
            continue

        new_items.append(item)
        # Track within this batch too, so duplicate links on the same page
        # don't collide on insert.
        existing_url_hashes.add(uh)
        existing_title_hashes.add(th)

    logger.info(f"Dedup: {len(raw_items)} raw, {len(new_items)} new (skipped {skipped_url} url, {skipped_title} title)")
    return new_items
