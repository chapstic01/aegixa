"""
Dashboard web routes — landing page, guild selector, main management interface.
"""

import os
from flask import Blueprint, render_template, session, redirect, url_for, request, abort, current_app
from web.auth import login_required
from config import PREMIUM_URL, SUPPORT_SERVER

dashboard = Blueprint("dashboard", __name__)

INVITE_URL = os.getenv(
    "BOT_INVITE_URL",
    "https://discord.com/oauth2/authorize?permissions=8&scope=bot%20applications.commands",
)
PREMIUM_PRICE = os.getenv("PREMIUM_PRICE", "2.99")


@dashboard.get("/")
def index():
    bot = current_app.bot
    guild_count = len(bot.guilds) if bot.is_ready() else 0
    return render_template(
        "landing.html",
        guild_count=guild_count,
        invite_url=INVITE_URL,
        premium_url=PREMIUM_URL,
        support_server=SUPPORT_SERVER,
        premium_price=PREMIUM_PRICE,
    )


@dashboard.get("/servers")
@login_required
def servers():
    return render_template("index.html", user=session["user"])


@dashboard.get("/dashboard/<int:guild_id>")
@login_required
def guild_dashboard(guild_id):
    from flask import current_app
    import asyncio
    import database as db

    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        abort(404)

    # Check access
    user_id = int(session["user"]["id"])
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    is_owner = user_id == owner_id or user_id == guild.owner_id

    if not is_owner:
        future = asyncio.run_coroutine_threadsafe(
            db.get_guild_roles(guild_id, "config"), bot.loop
        )
        config_role_ids = {r["role_id"] for r in future.result(timeout=5)}
        member = guild.get_member(user_id)
        if not member or not any(r.id in config_role_ids for r in member.roles):
            abort(403)

    is_bot_owner = user_id == owner_id

    return render_template(
        "dashboard.html",
        user=session["user"],
        guild={"id": str(guild.id), "name": guild.name, "icon": str(guild.icon.url) if guild.icon else None},
        is_bot_owner=is_bot_owner,
    )
