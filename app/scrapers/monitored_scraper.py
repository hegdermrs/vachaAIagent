"""Monitored URL scraper — scrapes user-defined known opportunity sites."""
import logging
import asyncio
import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, RawOpportunity
from app.database import async_session
from app.models.settings_models import MonitoredUrl
from sqlalchemy import select

logger = logging.getLogger("varshini.scraper.monitored")


class MonitoredScraper(BaseScraper):
    """Scrapes user-defined URLs for art opportunities."""

    async def scrape(self) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(MonitoredUrl).where(MonitoredUrl.is_active == 1)
            )
            urls = result.scalars().all()

        if not urls:
            return []

        semaphore = asyncio.Semaphore(3)
        client_kwargs = {
            "timeout": httpx.Timeout(30.0),
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
            },
            "follow_redirects": True,
        }

        all_items = []
        async with httpx.AsyncClient(**client_kwargs) as client:
            tasks = [self._scrape_url(client, mu, semaphore) for mu in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"Monitored URL scrape error: {result}")

        logger.info(f"Monitored scraper: {len(all_items)} items from {len(urls)} URLs")
        return all_items

    async def _scrape_url(self, client: httpx.AsyncClient, mu: MonitoredUrl, sem: asyncio.Semaphore) -> list[dict]:
        from app.scrapers.http_utils import get_with_fallback
        async with sem:
            resp = await get_with_fallback(client, mu.url)
        if resp is None:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = []

        # Look for links that might be opportunity listings
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text(strip=True)

            if not text or len(text) < 10:
                continue

            # Resolve relative URLs
            from urllib.parse import urljoin
            full_url = urljoin(mu.url, href)

            # Only include links that look relevant
            if not _is_relevant_link(text):
                continue

            opp = RawOpportunity(
                source_url=full_url,
                title=text[:300],
                source_type="monitored_url",
                description=text[:2000],
                opportunity_type=_detect_type(text),
            )
            items.append(opp.to_dict())

        # Update last_scraped_at
        from datetime import datetime, timezone
        async with async_session() as session:
            m = await session.get(MonitoredUrl, mu.id)
            if m:
                m.last_scraped_at = datetime.now(timezone.utc).isoformat()
                await session.commit()

        return items[:10]  # Cap per URL


def _is_relevant_link(text: str) -> bool:
    text_lower = text.lower()
    keywords = [
        "open call", "call for", "opportunity", "residency", "grant",
        "exhibition", "apply", "submission", "fellowship", "commission",
        "deadline", "artist", "artists",
    ]
    return any(kw in text_lower for kw in keywords)


def _detect_type(text: str) -> str | None:
    text_lower = text.lower()
    checks = [
        ("residency", "residency"),
        ("exhibition", "exhibition"),
        ("grant", "grant"),
        ("fellowship", "fellowship"),
        ("commission", "commission"),
        ("competition", "competition"),
        ("open call", "open_call"),
    ]
    for pattern, otype in checks:
        if pattern in text_lower:
            return otype
    return None
