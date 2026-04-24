"""
Gumroad webhook — automatically activates premium on purchase/renewal.

Flow:
  1. Server admin runs /premiumcode in their Discord server → gets a 6-char code (valid 60 min)
  2. Admin buys the Gumroad product and fills in:
       - "Discord Server ID"   — right-click server → Copy Server ID
       - "Verification Code"   — the code from /premiumcode
  3. Gumroad hits this webhook → premium is instantly activated, no key or email needed
  4. On subscription renewal, the webhook extends premium automatically (no code needed)

Gumroad setup:
  Settings → Advanced → Webhooks → add:
  https://your-app.up.railway.app/webhooks/gumroad?token=YOUR_WEBHOOK_SECRET

  On your product, add two Custom Fields:
    Field 1: "Discord Server ID"   (required)
    Field 2: "Verification Code"   (required)

Environment variables required:
  WEBHOOK_SECRET      — random string you put in the webhook URL
  GUMROAD_SELLER_ID   — your Gumroad seller ID (Settings → Advanced)
"""

import os
import asyncio
import logging

import discord
from flask import Blueprint, request, jsonify, current_app

log = logging.getLogger(__name__)

webhooks_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")

# Map Gumroad product permalinks → (tier, days)
PRODUCT_TIERS: dict[str, tuple[str, int]] = {
    "ypngqs":          ("premium", 30),
    "aegixa-annual":   ("premium", 365),
    "aegixa-lifetime": ("premium", 36500),
}
DEFAULT_TIER = ("premium", 30)


# ---------------------------------------------------------------------------
# Guild notification (runs in bot's asyncio loop)
# ---------------------------------------------------------------------------

async def _notify_guild_premium(bot, guild_id: int, days: int, tier: str, renewed: bool):
    from utils.helpers import send_guild_alert
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    action = "Renewed" if renewed else "Activated"
    embed = discord.Embed(
        title=f"⭐ Premium {action}!",
        description=(
            f"**Aegixa Premium** has been {action.lower()} for **{guild.name}**.\n"
            f"All premium features are now unlocked."
        ),
        color=0xFEE75C,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Duration added", value=f"{days} days", inline=True)
    embed.add_field(name="Tier", value=tier.title(), inline=True)
    embed.set_footer(text="Thank you for supporting Aegixa!")
    await send_guild_alert(guild, embed)


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

    data = request.form

    seller_id = os.getenv("GUMROAD_SELLER_ID", "")
    if seller_id and data.get("seller_id") != seller_id:
        log.warning("Gumroad webhook: seller_id mismatch")
        return jsonify({"error": "forbidden"}), 403

    permalink       = data.get("permalink", "").strip().lower()
    subscription_id = data.get("subscription_id", "").strip()
    sale_id         = data.get("sale_id", "unknown")

    tier, days = PRODUCT_TIERS.get(permalink, DEFAULT_TIER)

    bot = current_app.bot
    import database as db

    # ── Renewal: subscription_id already in our DB ───────────────────────────
    if subscription_id:
        future = asyncio.run_coroutine_threadsafe(
            db.get_gumroad_subscription(subscription_id), bot.loop
        )
        existing = future.result(timeout=10)

        if existing:
            guild_id = existing["guild_id"]
            renewal_tier = existing["tier"]
            renewal_days = existing["days"]

            future = asyncio.run_coroutine_threadsafe(
                db.grant_premium(guild_id, renewal_days, renewal_tier), bot.loop
            )
            future.result(timeout=10)

            asyncio.run_coroutine_threadsafe(
                _notify_guild_premium(bot, guild_id, renewal_days, renewal_tier, renewed=True),
                bot.loop,
            )

            log.info(
                "Renewal: extended premium for guild %s by %dd (subscription %s, sale %s)",
                guild_id, renewal_days, subscription_id, sale_id,
            )
            return jsonify({"ok": True, "action": "renewed"}), 200

    # ── New purchase: require server ID + verification code ──────────────────
    # Gumroad sends custom fields as form fields named exactly as you labelled them
    server_id_str = (
        data.get("Discord Server ID") or
        data.get("discord_server_id") or
        ""
    ).strip()
    verification_code = (
        data.get("Verification Code") or
        data.get("verification_code") or
        ""
    ).strip()

    if not server_id_str or not server_id_str.isdigit():
        log.error(
            "Gumroad webhook: missing/invalid 'Discord Server ID' in sale %s "
            "(got: %r). Buyer must fill in the custom field at checkout.",
            sale_id, server_id_str,
        )
        return jsonify({
            "error": "missing Discord Server ID",
            "hint": "Buyer must fill in 'Discord Server ID' at checkout.",
        }), 400

    guild_id = int(server_id_str)

    if not verification_code:
        log.error(
            "Gumroad webhook: missing 'Verification Code' for guild %s, sale %s. "
            "Buyer must run /premiumcode in their server first.",
            guild_id, sale_id,
        )
        return jsonify({
            "error": "missing Verification Code",
            "hint": "Buyer must run /premiumcode in their server and enter the code at checkout.",
        }), 400

    # Verify the code matches the guild and hasn't expired
    future = asyncio.run_coroutine_threadsafe(
        db.verify_and_consume_premium_code(guild_id, verification_code), bot.loop
    )
    valid = future.result(timeout=10)

    if not valid:
        log.warning(
            "Gumroad webhook: invalid/expired verification code %r for guild %s, sale %s",
            verification_code, guild_id, sale_id,
        )
        return jsonify({
            "error": "invalid or expired Verification Code",
            "hint": "Code expires after 60 minutes. Run /premiumcode again and retry.",
        }), 400

    # Grant premium
    future = asyncio.run_coroutine_threadsafe(
        db.grant_premium(guild_id, days, tier), bot.loop
    )
    future.result(timeout=10)

    # Store subscription mapping for auto-renewal
    if subscription_id:
        future = asyncio.run_coroutine_threadsafe(
            db.store_gumroad_subscription(subscription_id, guild_id, tier, days), bot.loop
        )
        future.result(timeout=10)

    # Notify the guild in Discord
    asyncio.run_coroutine_threadsafe(
        _notify_guild_premium(bot, guild_id, days, tier, renewed=False),
        bot.loop,
    )

    log.info(
        "Premium activated: guild %s, %s, %dd (subscription %s, sale %s)",
        guild_id, tier, days, subscription_id or "one-time", sale_id,
    )
    return jsonify({"ok": True, "action": "activated", "guild_id": guild_id}), 200
