"""Settings API routes — save settings, CRUD for monitored URLs."""
import json
import re

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.settings_models import Setting, MonitoredUrl
from app.config import settings_cache, encrypt_value

router = APIRouter(prefix="/api", tags=["settings"])


def _text_to_json_list(raw: str) -> str:
    """Convert friendly text (one item per line, or comma-separated) into a
    JSON array string for storage. Also accepts a pasted JSON array."""
    raw = (raw or "").strip()
    if not raw:
        return "[]"
    # Accept a pasted JSON array for backwards compatibility
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                items = [str(x).strip().lstrip("#").strip() for x in data]
                return json.dumps([i for i in items if i])
        except (json.JSONDecodeError, TypeError):
            pass
    parts = re.split(r"[\n,]+", raw)
    items = [p.strip().lstrip("#").strip() for p in parts]
    return json.dumps([i for i in items if i])


@router.post("/settings")
async def save_settings(
    db: AsyncSession = Depends(get_db),
    email_time: str = Form("08:00"),
    email_recipient: str = Form(""),
    smtp_host: str = Form("smtp.gmail.com"),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_use_tls: str = Form("true"),
    email_provider: str = Form("smtp"),
    instagram_username: str = Form(""),
    instagram_password: str = Form(""),
    scrape_keywords: str = Form("[]"),
    instagram_hashtags: str = Form("[]"),
    max_results_per_source: str = Form("50"),
    relevance_threshold: str = Form("0.2"),
    scrape_hour: str = Form("2"),
    ai_enabled: str = Form("false"),
    deepseek_model: str = Form("deepseek-v4-pro"),
    deepseek_api_key: str = Form(""),
    uk_only: str = Form("false"),
):
    """Save all settings from the dashboard form."""
    from sqlalchemy import select

    upsert_data = {
        "email_time": email_time,
        "email_recipient": email_recipient,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_use_tls": smtp_use_tls,
        "email_provider": email_provider,
        "instagram_username": instagram_username,
        "scrape_keywords": scrape_keywords,
        "instagram_hashtags": instagram_hashtags,
        "max_results_per_source": max_results_per_source,
        "relevance_threshold": relevance_threshold,
        "scrape_hour": scrape_hour,
        "ai_enabled": "true" if ai_enabled in ("true", "on", "1") else "false",
        "deepseek_model": deepseek_model or "deepseek-v4-pro",
        "uk_only": "true" if uk_only in ("true", "on", "1") else "false",
    }

    # Encrypt secrets (skip the masked placeholder so we don't overwrite them)
    if smtp_password and smtp_password != "••••••••":
        upsert_data["smtp_password"] = encrypt_value(smtp_password)
    if instagram_password and instagram_password != "••••••••":
        upsert_data["instagram_password"] = encrypt_value(instagram_password)
    if deepseek_api_key and deepseek_api_key != "••••••••":
        upsert_data["deepseek_api_key"] = encrypt_value(deepseek_api_key)

    # Convert friendly text input (one per line / comma-separated) into JSON
    upsert_data["scrape_keywords"] = _text_to_json_list(scrape_keywords)
    upsert_data["instagram_hashtags"] = _text_to_json_list(instagram_hashtags)

    for key, value in upsert_data.items():
        existing = await db.get(Setting, key)
        if existing:
            existing.value = value
        else:
            db.add(Setting(key=key, value=value))

    await db.commit()

    # Update in-memory cache
    for key, value in upsert_data.items():
        settings_cache[key] = value

    return {"status": "ok"}


@router.post("/send-test-email", response_class=HTMLResponse)
async def send_test_email_route():
    """Send a one-off test email so the user can check deliverability."""
    from app.services.email_service import send_test_email
    success, error = await send_test_email()
    if success:
        recipient = settings_cache.get("email_recipient", "your inbox")
        return HTMLResponse(
            f'<span class="scrape-pill scrape-pill--done">✓ Test email sent to {recipient} — check your inbox (and spam).</span>'
        )
    return HTMLResponse(
        f'<span class="scrape-pill scrape-pill--error">Could not send: {str(error)[:160]}</span>'
    )


@router.post("/urls/add")
async def add_monitored_url(
    db: AsyncSession = Depends(get_db),
    url: str = Form(""),
    label: str = Form(""),
):
    mu = MonitoredUrl(url=url, label=label, is_active=1)
    db.add(mu)
    await db.commit()
    return {"status": "ok", "id": mu.id}


@router.delete("/urls/{url_id}", response_class=HTMLResponse)
async def delete_monitored_url(url_id: int, db: AsyncSession = Depends(get_db)):
    mu = await db.get(MonitoredUrl, url_id)
    if mu:
        await db.delete(mu)
        await db.commit()
    return HTMLResponse("")  # htmx removes the row


@router.post("/urls/{url_id}/toggle")
async def toggle_monitored_url(url_id: int, db: AsyncSession = Depends(get_db)):
    mu = await db.get(MonitoredUrl, url_id)
    if mu:
        mu.is_active = 0 if mu.is_active == 1 else 1
        await db.commit()
    return {"status": "ok"}
