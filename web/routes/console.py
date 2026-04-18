"""
Console routes — owner-only channel browser and send-as-bot interface.
"""

import os
import asyncio
from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request, abort, current_app
from web.auth import login_required, owner_required

console_bp = Blueprint("console", __name__, url_prefix="/console")


@console_bp.get("/")
@owner_required
def console_index():
    bot = current_app.bot
    guilds = [
        {"id": str(g.id), "name": g.name, "icon": str(g.icon.url) if g.icon else None}
        for g in sorted(bot.guilds, key=lambda g: g.name)
    ]
    return render_template("console.html", user=session["user"], guilds=guilds)


@console_bp.get("/guild/<int:guild_id>/channels")
@owner_required
def guild_channels(guild_id):
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    channels = [
        {"id": str(ch.id), "name": ch.name, "category": ch.category.name if ch.category else None}
        for ch in sorted(guild.text_channels, key=lambda c: (c.category.position if c.category else 0, c.position))
    ]
    return jsonify(channels)


@console_bp.get("/guild/<int:guild_id>/channel/<int:channel_id>/messages")
@owner_required
def get_messages(guild_id, channel_id):
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        abort(404)
    channel = guild.get_channel(channel_id)
    if not channel:
        abort(404)

    before_id = request.args.get("before")
    limit = min(int(request.args.get("limit", 50)), 100)

    async def fetch():
        kwargs = {"limit": limit}
        if before_id:
            kwargs["before"] = discord_obj = await channel.fetch_message(int(before_id))
        msgs = []
        async for msg in channel.history(**kwargs):
            msgs.append(msg)
        return msgs

    import discord
    future = asyncio.run_coroutine_threadsafe(fetch(), bot.loop)
    messages = future.result(timeout=15)

    result = []
    for msg in messages:
        embeds = []
        for e in msg.embeds:
            ed = {"title": e.title, "description": e.description, "color": e.color.value if e.color else None,
                  "footer": e.footer.text if e.footer else None,
                  "image": e.image.url if e.image else None,
                  "thumbnail": e.thumbnail.url if e.thumbnail else None,
                  "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in e.fields]}
            embeds.append(ed)

        attachments = [{"filename": a.filename, "url": a.url, "content_type": a.content_type} for a in msg.attachments]
        reactions = [{"emoji": str(r.emoji), "count": r.count} for r in msg.reactions]

        result.append({
            "id": str(msg.id),
            "content": msg.content,
            "author": {
                "id": str(msg.author.id),
                "name": msg.author.display_name,
                "avatar": str(msg.author.display_avatar.url),
                "bot": msg.author.bot,
            },
            "timestamp": msg.created_at.isoformat(),
            "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
            "embeds": embeds,
            "attachments": attachments,
            "reactions": reactions,
        })
    return jsonify(result)


@console_bp.post("/guild/<int:guild_id>/channel/<int:channel_id>/send")
@owner_required
def send_message(guild_id, channel_id):
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        abort(404)
    channel = guild.get_channel(channel_id)
    if not channel:
        abort(404)

    data = request.json or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "Content required"}), 400

    async def send():
        await channel.send(content)

    asyncio.run_coroutine_threadsafe(send(), bot.loop).result(timeout=10)
    return jsonify({"ok": True})


@console_bp.get("/guild/<int:guild_id>/members")
@owner_required
def guild_members(guild_id):
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    query = request.args.get("q", "").lower()
    members = []
    for m in guild.members:
        if query and query not in m.name.lower() and query not in m.display_name.lower():
            continue
        members.append({
            "id": str(m.id),
            "name": m.name,
            "display_name": m.display_name,
            "avatar": str(m.display_avatar.url),
            "bot": m.bot,
            "top_role": m.top_role.name,
        })
        if len(members) >= 50:
            break
    return jsonify(members)
