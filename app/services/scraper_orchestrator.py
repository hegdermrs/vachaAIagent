"""Scraper orchestrator — coordinates all scrapers, dedup, and ranking."""
import json
import logging
from datetime import datetime

from app.database import async_session
from app.models.settings_models import ScrapeLog
from sqlalchemy import select

logger = logging.getLogger("varshini.orchestrator")


async def run_full_scrape() -> dict:
    """Run web + Instagram + monitored scrapers, deduplicate, rank, and store."""
    results = {"web": 0, "instagram": 0, "monitored": 0, "new_total": 0}

    # Web scraper
    results["web"] = await _run_one("web", _scrape_web)

    # Instagram scraper
    results["instagram"] = await _run_one("instagram", _scrape_instagram)

    # Monitored URLs
    results["monitored"] = await _run_one("monitored_urls", _scrape_monitored)

    results["new_total"] = results["web"] + results["instagram"] + results["monitored"]
    logger.info(f"Full scrape complete: {results}")
    return results


async def run_instagram_scrape() -> dict:
    return {"instagram": await _run_one("instagram", _scrape_instagram)}


async def run_monitored_scrape() -> dict:
    return {"monitored": await _run_one("monitored_urls", _scrape_monitored)}


_STAGE_MESSAGES = {
    "web": "Searching the web for open calls…",
    "instagram": "Checking Instagram for opportunities…",
    "monitored_urls": "Scanning art-opportunity websites…",
}


def _set_stage(message: str) -> None:
    try:
        from app.services.scheduler import set_scrape_stage
        set_scrape_stage(message)
    except Exception:
        pass


async def _run_one(name: str, scrape_fn) -> int:
    """Run one scraper with logging."""
    from datetime import datetime, timezone
    _set_stage(_STAGE_MESSAGES.get(name, "Looking for opportunities…"))
    start = datetime.now(timezone.utc)
    log = ScrapeLog(scraper_name=name, status="started", started_at=start)
    try:
        async with async_session() as session:
            session.add(log)
            await session.commit()

        items_new = await scrape_fn()

        log.status = "completed"
        log.items_new = items_new
        log.completed_at = datetime.now(timezone.utc).isoformat()
        log.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()

        async with async_session() as session:
            session.add(log)
            await session.commit()

        return items_new
    except Exception as e:
        logger.exception(f"Scraper {name} failed")
        log.status = "failed"
        log.error_message = str(e)
        log.completed_at = datetime.now(timezone.utc).isoformat()
        async with async_session() as session:
            session.add(log)
            await session.commit()
        return 0


async def _process_source(scraper) -> int:
    """Shared pipeline: scrape → dedup → AI enrich → rank → store."""
    from app.services.dedup_service import filter_duplicates
    from app.services.ranking_service import rank_opportunities

    raw = await scraper.scrape()
    new_items = await filter_duplicates(raw)
    new_items = await _enrich_with_ai(new_items)
    new_items = _filter_uk(new_items)
    ranked = await rank_opportunities(new_items)
    return await _store_opportunities(ranked)


def _filter_uk(items: list) -> list:
    """Drop opportunities not open to a UK-based artist (when uk_only is on).

    Uses the AI's verdict when available, otherwise a keyword heuristic.
    Unknown/ambiguous locations are kept.
    """
    from app.config import settings_cache
    from app.services.ranking_service import is_uk_eligible

    if not items or settings_cache.get("uk_only", "true") != "true":
        return items

    kept = []
    for item in items:
        verdict = item.pop("_uk_eligible", None)
        if verdict is False:
            continue
        if verdict is True or is_uk_eligible(item):
            kept.append(item)

    dropped = len(items) - len(kept)
    if dropped:
        _set_stage(f"Keeping UK opportunities ({dropped} elsewhere filtered out)…")
        logger.info(f"UK filter: kept {len(kept)}, dropped {dropped} non-UK")
    return kept


async def _scrape_web() -> int:
    from app.scrapers.web_scraper import WebScraper
    return await _process_source(WebScraper())


async def _scrape_instagram() -> int:
    from app.scrapers.instagram_scraper import InstagramScraper
    return await _process_source(InstagramScraper())


async def _scrape_monitored() -> int:
    from app.scrapers.monitored_scraper import MonitoredScraper
    return await _process_source(MonitoredScraper())


_AI_CONCURRENCY = 5


async def _enrich_with_ai(items: list) -> list:
    """Use the LLM to extract fields and score relevance, in one concurrent pass.

    Drops items the LLM flags as non-opportunities. No-ops (returns items
    unchanged) when AI is disabled or unconfigured.
    """
    import asyncio
    from app.services import llm_service
    from app.services.ranking_service import load_profile

    if not items or not llm_service.is_enabled():
        return items

    _set_stage(f"Reading and ranking {len(items)} opportunities…")
    profile = await load_profile()
    sem = asyncio.Semaphore(_AI_CONCURRENCY)

    async def _analyze(item):
        async with sem:
            return await llm_service.analyze(item, profile)

    results = await asyncio.gather(*[_analyze(i) for i in items])

    kept = []
    for item, data in zip(items, results):
        if not data:
            kept.append(item)  # analysis failed — keep regex-derived fields
            continue
        if data.get("is_opportunity") is False:
            continue  # LLM is confident this isn't a real opportunity — drop it

        for key in ("opportunity_type", "deadline", "location", "eligibility",
                    "fee", "medium", "organization"):
            value = data.get(key)
            if value:
                item[key] = value
        if data.get("title"):
            item["title"] = str(data["title"])[:300]
        if data.get("summary"):
            item["ai_summary"] = str(data["summary"])[:500]
        if data.get("score") is not None:
            item["relevance_score"] = data["score"]
        if data.get("reasoning"):
            item["ai_reasoning"] = str(data["reasoning"])[:300]
        if "uk_eligible" in data:
            item["_uk_eligible"] = data.get("uk_eligible")
        kept.append(item)

    logger.info(f"AI enrich: {len(items)} in, {len(kept)} kept")
    return kept


async def _store_opportunities(items: list) -> None:
    from app.models.opportunity import Opportunity
    from sqlalchemy.exc import IntegrityError

    stored = 0
    async with async_session() as session:
        for item in items:
            opp = Opportunity(
                source_url=item["source_url"],
                source_type=item.get("source_type", "web"),
                title=item["title"],
                description=item.get("description", ""),
                opportunity_type=item.get("opportunity_type"),
                deadline=item.get("deadline"),
                location=item.get("location"),
                organization=item.get("organization"),
                eligibility=item.get("eligibility"),
                fee=item.get("fee"),
                medium=item.get("medium"),
                ai_summary=item.get("ai_summary"),
                ai_reasoning=item.get("ai_reasoning"),
                raw_data=json.dumps(item, default=str),
                relevance_score=float(item.get("relevance_score", 0)),
                url_hash=Opportunity.compute_url_hash(item["source_url"]),
                title_hash=Opportunity.compute_title_hash(item["title"]),
            )
            # Per-item savepoint so a single duplicate can't roll back the batch
            try:
                async with session.begin_nested():
                    session.add(opp)
            except IntegrityError:
                logger.debug(f"Skipping duplicate: {item['source_url']}")
                continue
            stored += 1
        await session.commit()
    return stored
