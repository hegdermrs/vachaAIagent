"""Instagram scraper — uses instaloader to search hashtags for art opportunities."""
import json
import logging
import re
import asyncio
from pathlib import Path

from app.scrapers.base import BaseScraper, RawOpportunity
from app.config import settings_cache, decrypt_value, BASE_DIR

logger = logging.getLogger("varshini.scraper.instagram")

SESSION_FILE = BASE_DIR / "sessions" / "instagram_session"

try:
    from instaloader import Instaloader, Hashtag, Profile, Post
except ImportError:
    Instaloader = Hashtag = Profile = Post = None


class InstagramScraper(BaseScraper):
    """Scrapes Instagram hashtags for open call posts using instaloader."""

    async def scrape(self) -> list[dict]:
        hashtags_str = settings_cache.get("instagram_hashtags", "[]")
        try:
            hashtags = json.loads(hashtags_str)
        except json.JSONDecodeError:
            hashtags = ["opencallforartists", "artistopportunity", "callforartists"]

        username = settings_cache.get("instagram_username", "")
        password = decrypt_value(settings_cache.get("instagram_password", ""))

        all_items = []
        for tag in hashtags[:8]:  # Limit to 8 hashtags to avoid rate limits
            results = await self._scrape_hashtag(tag, username, password)
            all_items.extend(results)
            await asyncio.sleep(5)  # Rate limit between hashtags

        logger.info(f"Instagram scraper: {len(all_items)} items")
        return all_items[:int(settings_cache.get("max_results_per_source", "50"))]

    async def _scrape_hashtag(self, hashtag: str, username: str, password: str) -> list[dict]:
        """Scrape posts from a single hashtag."""
        items = []
        try:
            # Run instaloader in a thread pool to avoid blocking the event loop
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    _instaloader_scrape, hashtag, username, password, SESSION_FILE
                ),
                timeout=120,
            )
            items.extend(result)
        except Exception as e:
            logger.warning(f"Instagram scrape failed for #{hashtag}: {e}")

        return items


def _instaloader_scrape(hashtag: str, username: str, password: str, session_file: Path) -> list[dict]:
    """Synchronous instaloader call — runs in a thread."""
    if Instaloader is None:
        logger.warning("instaloader not installed — skipping Instagram scrape")
        return []

    loader = Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        max_connection_attempts=1,
    )

    # Try to login / load session
    if username and password:
        try:
            if session_file.exists():
                loader.load_session_from_file(username, str(session_file))
            else:
                loader.login(username, password)
                loader.save_session_to_file(str(session_file))
        except Exception as e:
            logger.warning(f"Instagram login failed: {e} — scraping without auth")

    items = []
    try:
        hashtag_obj = Hashtag.from_name(loader.context, hashtag)
        posts = hashtag_obj.get_posts()
    except Exception as e:
        logger.warning(f"Cannot get posts for #{hashtag}: {e}")
        return items

    for post in posts:
        try:
            caption = post.caption or ""
            if not caption:
                caption = ""

            # Skip posts that don't look like opportunities
            if not _is_opportunity_post(caption):
                continue

            post_url = f"https://www.instagram.com/p/{post.shortcode}/"

            opp = RawOpportunity(
                source_url=post_url,
                title=_extract_title_from_caption(caption)[:300] or f"Instagram post by @{post.owner_username}",
                source_type="instagram",
                description=caption[:3000],
                opportunity_type=_detect_ig_type(caption),
                deadline=_extract_ig_deadline(caption),
                location=_extract_ig_location(caption),
                organization=f"@{post.owner_username}",
                raw_data={
                    "instagram_shortcode": post.shortcode,
                    "instagram_owner": post.owner_username,
                    "hashtag": hashtag,
                    "likes": post.likes,
                    "date": str(post.date_utc) if post.date_utc else None,
                },
            )
            items.append(opp.to_dict())

            if len(items) >= 15:  # Limit per hashtag
                break

        except Exception as e:
            logger.debug(f"Skipping post: {e}")
            continue

    return items


def _is_opportunity_post(caption: str) -> bool:
    caption_lower = caption.lower()
    signal_words = [
        "open call", "call for", "submission", "apply now", "deadline",
        "residency", "grant", "fellowship", "exhibition opportunity",
        "call for artists", "call for entries", "artist opportunity",
        "commission", "opportunity for artists",
    ]
    return any(w in caption_lower for w in signal_words)


def _extract_title_from_caption(caption: str) -> str:
    lines = caption.strip().split("\n")
    for line in lines:
        clean = line.strip().lstrip("📢🎨🖌️✨🔥🎯💫").strip()
        if len(clean) > 10 and len(clean) < 200:
            return clean
    return caption[:200]


def _detect_ig_type(caption: str) -> str | None:
    import re as _re
    text = caption.lower()
    checks = [
        (r'\bresidency\b', 'residency'),
        (r'\bexhibition\b', 'exhibition'),
        (r'\bgrant\b', 'grant'),
        (r'\bfellowship\b', 'fellowship'),
        (r'\bcommission\b', 'commission'),
        (r'\bcompetition\b', 'competition'),
        (r'\bprize\b', 'prize'),
        (r'\bopen.call\b', 'open_call'),
    ]
    for pat, otype in checks:
        if _re.search(pat, text):
            return otype
    return None


def _extract_ig_deadline(caption: str) -> str | None:
    import re as _re
    patterns = [
        r'(?:deadline|due|closes?)[:\s]*([A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})',
        r'(?:deadline|due|closes?)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})',
    ]
    for pat in patterns:
        m = _re.search(pat, caption, _re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _extract_ig_location(caption: str) -> str | None:
    import re as _re
    text = caption.lower()
    locations = [
        "uk", "united kingdom", "london", "england", "scotland", "wales",
        "international", "worldwide", "global", "online", "virtual", "remote",
        "europe", "berlin", "paris", "amsterdam",
    ]
    found = []
    for loc in locations:
        if loc in text:
            found.append(loc)
    return ", ".join(found[:3]) if found else None
