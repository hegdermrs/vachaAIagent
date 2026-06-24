"""Shared HTTP helpers for scrapers.

Some art-listing sites (e.g. resartis.org) serve incomplete TLS certificate
chains that fail verification on Windows. `get_with_fallback` tries a normal
verified request first and only retries without verification on a certificate
error, so we keep security where possible but still collect the data.
"""
import logging

import httpx

logger = logging.getLogger("varshini.scraper.http")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _is_cert_error(exc: Exception) -> bool:
    text = str(exc).upper()
    return "CERTIFICATE" in text or "SSL" in text


async def get_with_fallback(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """GET `url` using the shared client; on a TLS cert error retry unverified.

    Returns the response (status already validated) or None on failure.
    """
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as e:
        logger.warning(f"Failed to fetch {url}: HTTP {e.response.status_code}")
        return None
    except httpx.TransportError as e:
        if not _is_cert_error(e):
            logger.warning(f"Failed to fetch {url}: {e}")
            return None
        # TLS verification failed — retry once without verification
        try:
            async with httpx.AsyncClient(
                timeout=client.timeout,
                follow_redirects=True,
                headers=client.headers,
                verify=False,
            ) as insecure:
                resp = await insecure.get(url)
                resp.raise_for_status()
                logger.info(f"Fetched {url} without TLS verification (certificate issue)")
                return resp
        except Exception as e2:
            logger.warning(f"Failed to fetch {url} (even unverified): {e2}")
            return None
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None
