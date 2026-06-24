"""Email service — builds and sends HTML digest emails via SMTP."""
import json
import logging
from datetime import datetime, timezone
from aiosmtplib import SMTP
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import async_session
from app.config import settings_cache, decrypt_value
from app.models.opportunity import Opportunity
from app.models.settings_models import EmailLog
from sqlalchemy import select

logger = logging.getLogger("varshini.email")


async def send_digest_email():
    """Query unsent opportunities, build HTML email, send via SMTP, mark as sent."""
    threshold = float(settings_cache.get("relevance_threshold", "0.2"))
    recipient = settings_cache.get("email_recipient", "")
    max_results = int(settings_cache.get("max_results_per_source", "50")) * 3

    if not recipient:
        logger.warning("No email recipient configured — skipping digest")
        return

    async with async_session() as session:
        result = await session.execute(
            select(Opportunity)
            .where(
                Opportunity.is_sent == 0,
                Opportunity.is_archived == 0,
                Opportunity.relevance_score >= threshold,
            )
            .order_by(Opportunity.relevance_score.desc())
            .limit(max_results)
        )
        opps = result.scalars().all()

    if not opps:
        logger.info("No new opportunities to email")
        return

    html_body = _render_email_template(opps)
    success, error = await _send_smtp(recipient, "Varshini — Art Opportunities Digest", html_body)

    log = EmailLog(
        recipient=recipient,
        subject="Varshini — Art Opportunities Digest",
        opportunity_ids=json.dumps([o.id for o in opps]),
        opportunity_count=len(opps),
        status="sent" if success else "failed",
        error_message=error,
        sent_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    async with async_session() as session:
        session.add(log)
        if success:
            for o in opps:
                o.is_sent = 1
                o.email_sent_at = datetime.now(timezone.utc).isoformat()
        await session.commit()

    logger.info(f"Email digest: {len(opps)} opportunities sent to {recipient}")


async def send_test_email(recipient: str | None = None) -> tuple[bool, str | None]:
    """Send a simple test message to verify email delivery is working."""
    recipient = recipient or settings_cache.get("email_recipient", "")
    if not recipient:
        return False, "No recipient email is set. Add one in Settings first."

    html = """
    <div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#1a1a2e">
        <h2 style="color:#e94560;margin:0 0 8px">✓ Your email is working</h2>
        <p>This is a test message from <strong>Varshini</strong> confirming your
        email delivery is set up correctly.</p>
        <p>You'll receive your daily art-opportunity digest at your scheduled time.</p>
        <p style="color:#6b7280;font-size:13px;margin-top:24px">If this landed in spam,
        mark it &ldquo;Not spam&rdquo; so future digests reach your inbox.</p>
    </div>
    """
    success, error = await _send_smtp(recipient, "Varshini — Test Email ✓", html)

    log = EmailLog(
        recipient=recipient,
        subject="Varshini — Test Email ✓",
        opportunity_ids=json.dumps([]),
        opportunity_count=0,
        status="sent" if success else "failed",
        error_message=error,
        sent_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    async with async_session() as session:
        session.add(log)
        await session.commit()

    if success:
        logger.info(f"Test email sent to {recipient}")
    else:
        logger.warning(f"Test email failed: {error}")
    return success, error


def _render_email_template(opportunities: list[Opportunity]) -> str:
    from jinja2 import Environment, BaseLoader
    from pathlib import Path

    template_path = Path(__file__).resolve().parent.parent / "templates" / "email" / "digest.html"
    with open(template_path, encoding="utf-8") as f:
        template_str = f.read()

    env = Environment(loader=BaseLoader())
    template = env.from_string(template_str)

    high = [o for o in opportunities if o.relevance_score >= 0.7]
    medium = [o for o in opportunities if 0.4 <= o.relevance_score < 0.7]
    low = [o for o in opportunities if o.relevance_score < 0.4]

    return template.render(high=high, medium=medium, low=low, date=datetime.now().strftime("%d %B %Y"))


async def _send_smtp(recipient: str, subject: str, html_body: str) -> tuple[bool, str | None]:
    host = settings_cache.get("smtp_host", "smtp.gmail.com")
    port = int(settings_cache.get("smtp_port", "587"))
    user = settings_cache.get("smtp_user", "")
    password = decrypt_value(settings_cache.get("smtp_password", ""))
    use_tls = settings_cache.get("smtp_use_tls", "true") == "true"

    if not user or not password:
        return False, "SMTP credentials not configured"

    # Port 465 = implicit TLS; port 587/25 = STARTTLS (upgrade after connecting).
    # Using implicit TLS on 587 causes an SSL "wrong version number" error.
    implicit_tls = use_tls and port == 465
    start_tls = use_tls and port != 465

    msg = MIMEMultipart("alternative")
    msg["From"] = user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        smtp = SMTP(hostname=host, port=port, use_tls=implicit_tls, start_tls=start_tls)
        await smtp.connect()
        await smtp.login(user, password)
        await smtp.send_message(msg)
        await smtp.quit()
        return True, None
    except Exception as e:
        logger.exception("SMTP send failed")
        return False, str(e)
