"""Web scraper — search engine queries + page scraping."""
import json
import logging
import re
import asyncio
from urllib.parse import quote_plus
import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, RawOpportunity
from app.config import settings_cache

logger = logging.getLogger("varshini.scraper.web")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class WebScraper(BaseScraper):
    """Searches for art open calls via DuckDuckGo and scrapes result pages."""

    def __init__(self):
        self.semaphore = asyncio.Semaphore(3)
        self.client: httpx.AsyncClient | None = None

    async def scrape(self) -> list[dict]:
        keywords_str = settings_cache.get("scrape_keywords", "[]")
        try:
            keywords = json.loads(keywords_str)
        except json.JSONDecodeError:
            keywords = ["open call for artists UK"]

        client_kwargs = {
            "timeout": httpx.Timeout(30.0),
            "headers": {"User-Agent": USER_AGENTS[0]},
            "follow_redirects": True,
        }

        all_items = []
        async with httpx.AsyncClient(**client_kwargs) as self.client:
            for keyword in keywords[:5]:  # Limit to 5 keywords to be respectful
                results = await self._search_keyword(keyword)
                all_items.extend(results)
                await asyncio.sleep(2)  # Rate limit between keyword searches

        # Deduplicate by URL within this batch
        seen = set()
        unique = []
        for item in all_items:
            url = item["source_url"]
            if url not in seen:
                seen.add(url)
                unique.append(item)

        logger.info(f"Web scraper: {len(all_items)} total, {len(unique)} unique")
        return unique[:int(settings_cache.get("max_results_per_source", "50"))]

    async def _search_keyword(self, keyword: str) -> list[dict]:
        """Search DuckDuckGo for a keyword, scrape result pages."""
        results = []
        try:
            encoded = quote_plus(keyword)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            resp = await self.client.get(url)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.select("a.result__a")
            snippets = soup.select("a.result__snippet")

            for i, link in enumerate(links[:10]):
                href = link.get("href", "")
                if not href or not href.startswith("http"):
                    continue
                title = link.get_text(strip=True)
                snippet = snippets[i].get_text(strip=True) if i < len(snippets) else ""

                # Try to scrape the actual page for more details
                details = await self._scrape_page(href)

                opp = RawOpportunity(
                    source_url=href,
                    title=details.get("title") or title,
                    source_type="web",
                    description=details.get("description") or snippet,
                    opportunity_type=_detect_type(title, snippet),
                    deadline=_extract_deadline(snippet + " " + (details.get("description") or "")),
                    location=_extract_location(snippet + " " + (details.get("description") or ""), keyword),
                    organization=_extract_org(title, href),
                    raw_data={"full_text": details.get("full_text", "")},
                )
                results.append(opp.to_dict())

        except Exception as e:
            logger.warning(f"Search failed for '{keyword}': {e}")

        return results

    async def _scrape_page(self, url: str) -> dict:
        """Scrape an individual opportunity page for details."""
        from app.scrapers.http_utils import get_with_fallback
        async with self.semaphore:
            try:
                resp = await get_with_fallback(self.client, url)
                if resp is None:
                    return {}
                soup = BeautifulSoup(resp.text, "html.parser")

                # Remove script and style elements
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()

                body = soup.find("body")
                text = body.get_text(separator="\n", strip=True) if body else ""

                # Try to find the main title
                title_tag = soup.find("h1") or soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else ""

                # Get description from meta or first paragraphs
                meta_desc = soup.find("meta", attrs={"name": "description"})
                desc = meta_desc.get("content", "") if meta_desc else ""

                if not desc:
                    paragraphs = soup.find_all("p")[:10]
                    desc = " ".join(p.get_text(strip=True) for p in paragraphs)[:2000]

                return {
                    "title": title[:300] if title else "",
                    "description": desc[:3000] if desc else text[:3000] if text else "",
                    "full_text": text[:5000],
                }
            except Exception:
                return {}


def _detect_type(title: str, snippet: str) -> str | None:
    text = f"{title} {snippet}".lower()
    checks = [
        ("residency", "residency"),
        ("exhibition", "exhibition"),
        ("grant", "grant"),
        ("fellowship", "fellowship"),
        ("commission", "commission"),
        ("competition", "competition"),
        ("prize", "prize"),
        ("biennial", "biennial"),
        ("open call", "open_call"),
        ("call for entries", "open_call"),
    ]
    for pattern, otype in checks:
        if pattern in text:
            return otype
    return None


def _extract_deadline(text: str) -> str | None:
    patterns = [
        r'(?:deadline|due|closes?|submission date)[:\s]*([A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})',
        r'(?:deadline|due|closes?|submission date)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(?:deadline|due|closes?|submission date)[:\s]*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
        r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _extract_location(text: str, keyword: str = "") -> str | None:
    text_lower = text.lower() + " " + keyword.lower()
    locations = [
        "uk", "united kingdom", "london", "england", "scotland", "wales", "northern ireland",
        "international", "worldwide", "global", "online", "virtual", "remote",
        "europe", "berlin", "paris", "amsterdam", "new york",
    ]
    found = []
    for loc in locations:
        if loc in text_lower:
            found.append(loc)
    return ", ".join(found[:3]) if found else None


def _extract_org(title: str, url: str) -> str | None:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        return domain.split(".")[0].title()
    except Exception:
        return None
