"""Dashboard HTML routes — server-rendered pages."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

router = APIRouter(prefix="", tags=["dashboard"])

templates_dir = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return await dashboard_index(request)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index(request: Request):
    """Main dashboard — stats, recent ops, last scrape status."""
    from app.database import async_session
    from app.models.opportunity import Opportunity
    from app.models.settings_models import ScrapeLog, EmailLog
    from sqlalchemy import select, func

    async with async_session() as session:
        total_opps = (await session.execute(select(func.count(Opportunity.id)))).scalar()
        unsent_opps = (await session.execute(
            select(func.count(Opportunity.id)).where(Opportunity.is_sent == 0)
        )).scalar()
        high_rel = (await session.execute(
            select(func.count(Opportunity.id)).where(
                Opportunity.is_sent == 0, Opportunity.relevance_score >= 0.7
            )
        )).scalar()

        last_scrape = (await session.execute(
            select(ScrapeLog).order_by(ScrapeLog.started_at.desc()).limit(1)
        )).scalar_one_or_none()

        last_email = (await session.execute(
            select(EmailLog).order_by(EmailLog.sent_at.desc()).limit(1)
        )).scalar_one_or_none()

        recent_opps = (await session.execute(
            select(Opportunity).order_by(Opportunity.created_at.desc()).limit(10)
        )).scalars().all()

    # Get next run times from scheduler
    from app.services.scheduler import scheduler, _get_email_cron, _get_scrape_cron
    next_scrape = "Not scheduled"
    next_email = "Not scheduled"
    if scheduler.running:
        for job in scheduler.get_jobs():
            if job.id == "full_scrape" and job.next_run_time:
                next_scrape = job.next_run_time.strftime("%Y-%m-%d %H:%M")
            elif job.id == "email_digest" and job.next_run_time:
                next_email = job.next_run_time.strftime("%Y-%m-%d %H:%M")

    email_cron = _get_email_cron()
    scrape_cron = _get_scrape_cron()
    email_time_str = f"{email_cron['hour']:02d}:{email_cron['minute']:02d}"
    scrape_time_str = f"{scrape_cron['hour']:02d}:{scrape_cron['minute']:02d}"

    return templates.TemplateResponse("dashboard/index.html", {
        "request": request,
        "total_opps": total_opps or 0,
        "unsent_opps": unsent_opps or 0,
        "high_relevance": high_rel or 0,
        "last_scrape": last_scrape,
        "last_email": last_email,
        "recent_opps": recent_opps,
        "next_scrape": next_scrape,
        "next_email": next_email,
        "email_time": email_time_str,
        "scrape_time": scrape_time_str,
    })


@router.get("/dashboard/opportunities", response_class=HTMLResponse)
async def dashboard_opportunities(request: Request):
    """Paginated opportunity list with filters."""
    from app.database import async_session
    from app.models.opportunity import Opportunity
    from sqlalchemy import select, func

    # Filter params
    opp_type = request.query_params.get("type", "")
    sent = request.query_params.get("sent", "")
    archived = request.query_params.get("archived", "")
    search = request.query_params.get("search", "")

    query = select(Opportunity)

    if opp_type:
        query = query.where(Opportunity.opportunity_type == opp_type)
    if sent == "1":
        query = query.where(Opportunity.is_sent == 1)
    elif sent == "0":
        query = query.where(Opportunity.is_sent == 0)
    if archived == "1":
        query = query.where(Opportunity.is_archived == 1)
    else:
        query = query.where(Opportunity.is_archived == 0)
    if search:
        query = query.where(Opportunity.title.ilike(f"%{search}%"))

    query = query.order_by(Opportunity.relevance_score.desc(), Opportunity.created_at.desc()).limit(100)

    async with async_session() as session:
        total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar()
        opps = (await session.execute(query)).scalars().all()

    types = ["residency", "exhibition", "grant", "fellowship", "commission", "competition", "open_call", "prize"]

    return templates.TemplateResponse("dashboard/opportunities.html", {
        "request": request,
        "opportunities": opps,
        "total": total or 0,
        "types": types,
        "current_type": opp_type,
        "current_sent": sent,
        "current_archived": archived,
        "search": search,
    })


@router.get("/dashboard/history", response_class=HTMLResponse)
async def dashboard_history(request: Request):
    """Email history view."""
    from app.database import async_session
    from app.models.settings_models import EmailLog
    from sqlalchemy import select

    async with async_session() as session:
        logs = (await session.execute(
            select(EmailLog).order_by(EmailLog.sent_at.desc()).limit(50)
        )).scalars().all()

    return templates.TemplateResponse("dashboard/history.html", {
        "request": request,
        "logs": logs,
    })


@router.get("/dashboard/settings", response_class=HTMLResponse)
async def dashboard_settings(request: Request):
    """Settings editor."""
    from app.database import async_session
    from app.models.settings_models import Setting
    from sqlalchemy import select

    async with async_session() as session:
        settings_result = await session.execute(select(Setting))
        settings_rows = settings_result.scalars().all()

    settings_map = {s.key: s.value for s in settings_rows}

    # Never send secrets to the browser. Expose only whether each is set, so the
    # form can show a "saved" placeholder; leaving the field blank keeps the
    # stored value untouched (see save_settings). This also prevents the secret
    # from being round-tripped and re-encrypted on every save.
    for key in ("smtp_password", "instagram_password", "deepseek_api_key"):
        settings_map[f"{key}_set"] = bool(settings_map.get(key))
        settings_map[key] = ""

    # Present keyword/hashtag lists as friendly one-per-line text
    for list_key in ("scrape_keywords", "instagram_hashtags"):
        settings_map[list_key] = _json_list_to_text(settings_map.get(list_key))

    return templates.TemplateResponse("dashboard/settings.html", {
        "request": request,
        "settings": settings_map,
    })


@router.get("/dashboard/profile", response_class=HTMLResponse)
async def dashboard_profile(request: Request):
    """Artist profile editor."""
    from app.database import async_session
    from app.models.artist import ArtistProfile
    from sqlalchemy import select

    async with async_session() as session:
        profile = (await session.execute(select(ArtistProfile).limit(1))).scalar_one_or_none()

    import json
    mediums = _safe_json_parse(profile.mediums) if profile else []
    themes = _safe_json_parse(profile.themes) if profile else []

    return templates.TemplateResponse("dashboard/profile.html", {
        "request": request,
        "profile": profile,
        "mediums_list": mediums,
        "themes_list": themes,
        "mediums_str": ", ".join(mediums),
        "themes_str": ", ".join(themes),
    })


@router.get("/dashboard/urls", response_class=HTMLResponse)
async def dashboard_urls(request: Request):
    """Monitored URLs management."""
    from app.database import async_session
    from app.models.settings_models import MonitoredUrl
    from sqlalchemy import select

    async with async_session() as session:
        urls = (await session.execute(select(MonitoredUrl))).scalars().all()

    return templates.TemplateResponse("dashboard/urls.html", {
        "request": request,
        "urls": urls,
    })


def _json_list_to_text(raw: str | None) -> str:
    """Render a stored JSON array as one item per line for friendly editing."""
    import json
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return "\n".join(str(x) for x in data)
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


def _safe_json_parse(raw: str | None) -> list:
    import json
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
