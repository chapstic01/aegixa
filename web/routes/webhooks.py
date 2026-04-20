"""
Gumroad webhook — auto-generates and emails a license key on purchase.

Gumroad setup:
  Settings → Advanced → Webhooks → add your URL:
  https://your-app.up.railway.app/webhooks/gumroad?token=YOUR_WEBHOOK_SECRET

Environment variables required:
  WEBHOOK_SECRET   — random string you put in the Gumroad webhook URL
  GUMROAD_SELLER_ID — your Gumroad seller ID (Settings → Advanced)
  SMTP_HOST        — e.g. smtp.gmail.com
  SMTP_PORT        — 587
  SMTP_USER        — your email address
  SMTP_PASS        — your email app password
  EMAIL_FROM       — display name + address, e.g. "Aegixa <no-reply@yourdomain.com>"
"""

import os
import asyncio
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Blueprint, request, jsonify, current_app

log = logging.getLogger(__name__)

webhooks_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")

# Map Gumroad product permalinks → (tier, days)
# Add more entries if you create annual / lifetime products
PRODUCT_TIERS = {
    "aegixa":         ("premium", 30),
    "aegixa-annual":  ("annual",  365),
    "aegixa-lifetime":("premium", 36500),
}
DEFAULT_TIER = ("premium", 30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_email(to_address: str, buyer_name: str, key: str, days: int):
    """Send the license key to the buyer via SMTP."""
    host     = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port     = int(os.getenv("SMTP_PORT", "587"))
    user     = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    from_addr = os.getenv("EMAIL_FROM", f"Aegixa <{user}>")

    if not user or not password:
        log.warning("SMTP not configured — skipping email to %s", to_address)
        return

    subject = "Your Aegixa Premium License Key"
    body_html = f"""
<div style="font-family:Inter,sans-serif;max-width:520px;margin:0 auto;background:#1e1f22;color:#dbdee1;padding:32px;border-radius:12px">
  <h2 style="color:#fff;margin-bottom:8px">Thanks for getting Aegixa Premium!</h2>
  <p style="color:#949ba4;margin-bottom:24px">Hi {buyer_name or 'there'} — here's your license key:</p>

  <div style="background:#2b2d31;border:1px solid #3f4248;border-radius:8px;padding:20px;text-align:center;margin-bottom:24px">
    <code style="font-size:1.3rem;font-weight:700;letter-spacing:2px;color:#a5b4fc">{key}</code>
  </div>

  <p style="margin-bottom:8px"><strong>How to activate:</strong></p>
  <ol style="color:#949ba4;padding-left:20px;margin-bottom:24px">
    <li>Go to your Discord server</li>
    <li>Type <code style="background:#2b2d31;padding:2px 6px;border-radius:4px;color:#a5b4fc">/redeem {key}</code></li>
    <li>Premium is instantly activated for <strong>{days} days</strong></li>
  </ol>

  <p style="color:#949ba4;font-size:0.85rem">
    Need help? Join our <a href="{os.getenv('SUPPORT_SERVER','')}" style="color:#5865f2">support server</a>.
  </p>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_address
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(from_addr, to_address, msg.as_string())
        log.info("License key email sent to %s", to_address)
    except Exception as e:
        log.error("Failed to send email to %s: %s", to_address, e)
        raise


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@webhooks_bp.post("/gumroad")
def gumroad_webhook():
    # Verify secret token
    expected = os.getenv("WEBHOOK_SECRET", "")
    if expected and request.args.get("token") != expected:
        log.warning("Gumroad webhook: invalid token")
        return jsonify({"error": "forbidden"}), 403

    # Gumroad sends form-encoded data
    data = request.form

    seller_id = os.getenv("GUMROAD_SELLER_ID", "")
    if seller_id and data.get("seller_id") != seller_id:
        log.warning("Gumroad webhook: seller_id mismatch")
        return jsonify({"error": "forbidden"}), 403

    buyer_email = data.get("email", "").strip()
    buyer_name  = data.get("full_name", "").strip()
    permalink   = data.get("permalink", "").strip().lower()
    sale_id     = data.get("sale_id", "unknown")

    if not buyer_email:
        log.error("Gumroad webhook: no buyer email in payload")
        return jsonify({"error": "no email"}), 400

    tier, days = PRODUCT_TIERS.get(permalink, DEFAULT_TIER)

    log.info("Gumroad sale %s — %s bought %s (%s, %dd)", sale_id, buyer_email, permalink, tier, days)

    # Generate key using the bot's database (run in bot's async loop)
    bot = current_app.bot
    import database as db

    try:
        future = asyncio.run_coroutine_threadsafe(
            db.generate_license_key(tier, days, 0, 1),
            bot.loop,
        )
        key = future.result(timeout=10)
    except Exception as e:
        log.error("Failed to generate license key for %s: %s", buyer_email, e)
        return jsonify({"error": "key generation failed"}), 500

    # Send the key by email
    try:
        _send_email(buyer_email, buyer_name, key, days)
    except Exception:
        # Email failed — log the key so it isn't lost
        log.error("EMAIL FAILED — key for %s: %s", buyer_email, key)
        # Still return 200 so Gumroad doesn't retry endlessly
        return jsonify({"ok": True, "warning": "email failed, key logged"}), 200

    return jsonify({"ok": True}), 200
