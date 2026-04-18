"""
Dashboard web routes — guild selector and main management interface.
"""

import os
from flask import Blueprint, render_template, session, redirect, url_for, request, abort
from web.auth import login_required

dashboard = Blueprint("dashboard", __name__)


@dashboard.get("/")
@login_required
def index():
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
