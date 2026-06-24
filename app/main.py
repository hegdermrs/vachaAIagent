"""Varshini AI Agent — FastAPI application entry point."""
import logging
import os
import asyncio
import hashlib
import hmac
from html import escape
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path

from app.database import init_db, async_session
from app.models.settings_models import Setting
from app.config import settings_cache, SECRET_KEY
from sqlalchemy import select

# Ensure logs directory exists (needed before FileHandler)
_logs_dir = Path(__file__).resolve().parent.parent / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_logs_dir / "app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("varshini")

# ---- Auth ----
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin123")
if DASHBOARD_PASSWORD == "admin123":
    logger.warning("DASHBOARD_PASSWORD is still the default 'admin123' — change it for production!")
COOKIE_NAME = "varshini_auth"
COOKIE_SECRET = SECRET_KEY.encode() if isinstance(SECRET_KEY, str) else SECRET_KEY


def _sanitize_next(url: str) -> str:
    """Reject open redirects and XSS; only allow same-origin relative paths."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return "/dashboard"


def _check_auth(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    expected = hmac.new(COOKIE_SECRET, DASHBOARD_PASSWORD.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, expected)


def require_auth(request: Request):
    """FastAPI dependency — redirects to login if not authenticated."""
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return True


# ---- App Setup ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting Varshini AI Agent...")
    await init_db()
    await _load_settings()

    from app.services.scheduler import setup_scheduler, scheduler
    setup_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    yield

    # Cancel any in-progress background scrape tasks before stopping the scheduler
    from app.services.scheduler import _bg_tasks
    for task in list(_bg_tasks):
        task.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)

    scheduler.shutdown(wait=False)
    logger.info("Varshini AI Agent stopped")


async def _load_settings():
    """Load settings from DB into in-memory cache."""
    async with async_session() as session:
        result = await session.execute(select(Setting))
        for row in result.scalars().all():
            settings_cache[row.key] = row.value
    logger.info(f"Loaded {len(settings_cache)} settings")


app = FastAPI(
    title="Varshini AI Agent",
    description="AI agent that finds open calls for art and sends daily email digests",
    version="0.1.0",
    lifespan=lifespan,
)

# Static files — mount only if the directory exists (safety for missing dirs)
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
else:
    logger.warning("Static directory not found at %s — serving without CSS/JS", static_dir)

# Redirect 401 to login page
@app.exception_handler(401)
async def auth_exception_handler(request: Request, exc):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


# Routers — all protected by require_auth dependency
from app.routers import dashboard, api_opportunities, api_settings, api_scrape, api_profile

app.include_router(dashboard.router, dependencies=[Depends(require_auth)])
app.include_router(api_opportunities.router, dependencies=[Depends(require_auth)])
app.include_router(api_settings.router, dependencies=[Depends(require_auth)])
app.include_router(api_scrape.router, dependencies=[Depends(require_auth)])
app.include_router(api_profile.router, dependencies=[Depends(require_auth)])


# ---- Auth routes (public) ----
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    template_path = Path(__file__).resolve().parent / "templates" / "login.html"
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    next_url = _sanitize_next(request.query_params.get("next", "/dashboard"))
    return HTMLResponse(
        content=html.replace("{{ next }}", escape(next_url)).replace("{{ error }}", ""))


@app.post("/api/login")
async def login(request: Request):
    body = await request.form()
    password = body.get("password", "")
    next_url = _sanitize_next(body.get("next", "/dashboard"))

    if password != DASHBOARD_PASSWORD:
        template_path = Path(__file__).resolve().parent / "templates" / "login.html"
        with open(template_path, encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(
            content=html.replace("{{ next }}", escape(next_url)).replace(
                "{{ error }}", '<div class="flash flash-error">Invalid password</div>'))

    token = hmac.new(COOKIE_SECRET, DASHBOARD_PASSWORD.encode(), hashlib.sha256).hexdigest()
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(COOKIE_NAME, token, httponly=True, max_age=86400 * 30)
    return response


@app.get("/")
async def root(request: Request):
    if _check_auth(request):
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")
