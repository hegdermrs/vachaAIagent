"""Scrape trigger and status API routes."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.services.scheduler import trigger_scrape_now, scheduler, get_scrape_state

router = APIRouter(prefix="/api", tags=["scrape"])


def _busy_pill(message: str) -> str:
    """A status pill that keeps re-polling until the search finishes."""
    return (
        f'<div class="scrape-pill scrape-pill--busy" hx-get="/api/scrape-status" '
        f'hx-trigger="load delay:2s" hx-target="this" hx-swap="outerHTML">'
        f'<span class="spinner" aria-hidden="true"></span><span>{message}</span></div>'
    )


@router.post("/trigger-scrape", response_class=HTMLResponse)
async def trigger_scrape(which: str = "full"):
    """Start a search in the background; returns a self-updating status pill."""
    result = await trigger_scrape_now(which)
    state = get_scrape_state()
    if result.get("status") == "already_running":
        return HTMLResponse(_busy_pill("Already searching — hang tight…"))
    return HTMLResponse(_busy_pill(state.get("stage") or "Getting started…"))


@router.get("/scrape-status", response_class=HTMLResponse)
async def scrape_status_html():
    """HTML status pill polled by the dashboard button."""
    state = get_scrape_state()
    if state["running"]:
        return HTMLResponse(_busy_pill(state.get("stage") or "Working on it…"))

    result = state.get("last_result") or {}
    if result.get("status") == "failed":
        return HTMLResponse(
            '<div class="scrape-pill scrape-pill--error">'
            'Something went wrong while searching. Please try again.</div>'
        )
    if result:
        total = result.get("new_total")
        if total is None:
            total = sum(v for k, v in result.items() if isinstance(v, int))
        if total:
            noun = "opportunity" if total == 1 else "opportunities"
            msg = f'🎉 Found {total} new {noun}!'
        else:
            msg = "All caught up — no new opportunities this time."
        return HTMLResponse(
            f'<div class="scrape-pill scrape-pill--done">{msg} '
            f'<a class="pill-link" href="/dashboard/opportunities">View them →</a></div>'
        )
    return HTMLResponse('')


@router.get("/status")
async def scraper_status():
    """Get current scheduler and scraper status (JSON)."""
    jobs_info = []
    if scheduler.running:
        for job in scheduler.get_jobs():
            jobs_info.append({
                "id": job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })

    return {
        "scheduler_running": scheduler.running,
        "scrape": get_scrape_state(),
        "jobs": jobs_info,
    }
