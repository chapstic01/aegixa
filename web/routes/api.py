"""
REST API routes consumed by the dashboard JS.
All endpoints require login and guild access.
"""

import os
import asyncio
import datetime
from flask import Blueprint, request, jsonify, session, current_app
from web.auth import login_required
import database as db
from config import FEATURES, FEATURES_DEFAULT_OFF, FILTER_NAMES, PUNISHMENTS, LOG_TYPES

api = Blueprint("api", __name__, url_prefix="/api")


def run_async(coro):
    """Run a coroutine from a sync Flask context using the bot's event loop."""
    bot = current_app.bot
    future = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    return future.result(timeout=10)


def _check_guild_access(guild_id: int) -> bool:
    """Verify the current session user can manage this guild."""
    user_id = int(session["user"]["id"])
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    if user_id == owner_id:
        return True
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return False
    if user_id == guild.owner_id:
        return True
    # Check config roles
    rows = run_async(db.get_guild_roles(guild_id, "config"))
    config_role_ids = {r["role_id"] for r in rows}
    member = guild.get_member(user_id)
    if member and any(r.id in config_role_ids for r in member.roles):
        return True
    return False


# ---------------------------------------------------------------------------
# Guilds list
# ---------------------------------------------------------------------------

@api.get("/guilds")
@login_required
def list_guilds():
    bot = current_app.bot
    user_id = int(session["user"]["id"])
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    result = []
    for guild in bot.guilds:
        is_owner = user_id == owner_id or user_id == guild.owner_id
        rows = run_async(db.get_guild_roles(guild.id, "config"))
        config_role_ids = {r["role_id"] for r in rows}
        member = guild.get_member(user_id)
        has_config = member and any(r.id in config_role_ids for r in member.roles)
        if is_owner or has_config:
            result.append({
                "id": str(guild.id),
                "name": guild.name,
                "icon": str(guild.icon.url) if guild.icon else None,
                "member_count": guild.member_count,
            })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Log channels
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/logs")
@login_required
def get_logs(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    channels = run_async(db.get_all_log_channels(guild_id))
    return jsonify({k: str(v) for k, v in channels.items()})


@api.post("/guild/<int:guild_id>/logs")
@login_required
def set_logs(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    for log_type in LOG_TYPES:
        if log_type in data:
            val = data[log_type]
            ch_id = int(val) if val else None
            run_async(db.set_log_channel(guild_id, log_type, ch_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/features")
@login_required
def get_features(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    all_f = run_async(db.get_all_features(guild_id))
    result = {f: all_f.get(f, True) for f in FEATURES}
    return jsonify(result)


@api.post("/guild/<int:guild_id>/features/<feature>")
@login_required
def set_feature(guild_id, feature):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    if feature not in FEATURES:
        return jsonify({"error": "Unknown feature"}), 400
    data = request.json or {}
    enabled = bool(data.get("enabled", True))
    run_async(db.set_feature(guild_id, feature, enabled))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/filters")
@login_required
def get_filters(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    all_f = run_async(db.get_all_filters(guild_id))
    result = {}
    for f in FILTER_NAMES:
        data = all_f.get(f, {"enabled": 1, "punishment": "none"})
        result[f] = {"enabled": bool(data["enabled"]), "punishment": data["punishment"]}
    return jsonify(result)


@api.post("/guild/<int:guild_id>/filters/<filter_name>")
@login_required
def set_filter(guild_id, filter_name):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    if filter_name not in FILTER_NAMES:
        return jsonify({"error": "Unknown filter"}), 400
    data = request.json or {}
    enabled = data.get("enabled")
    punishment = data.get("punishment")
    if punishment and punishment not in PUNISHMENTS:
        return jsonify({"error": "Invalid punishment"}), 400
    run_async(db.set_filter(guild_id, filter_name, enabled=enabled, punishment=punishment))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Banned words
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/words")
@login_required
def get_words(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    words = run_async(db.get_banned_words(guild_id))
    return jsonify(words)


@api.post("/guild/<int:guild_id>/words")
@login_required
def add_word(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    word = (data.get("word") or "").strip().lower()
    if not word:
        return jsonify({"error": "Word required"}), 400
    added = run_async(db.add_banned_word(guild_id, word))
    return jsonify({"ok": True, "added": added})


@api.delete("/guild/<int:guild_id>/words/<word>")
@login_required
def remove_word(guild_id, word):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.remove_banned_word(guild_id, word))
    return jsonify({"ok": removed})


# ---------------------------------------------------------------------------
# Role rules
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/roleswap")
@login_required
def get_swaps(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    rules = run_async(db.get_role_swaps(guild_id))
    return jsonify([{k: str(v) if isinstance(v, int) else v for k, v in r.items()} for r in rules])


@api.post("/guild/<int:guild_id>/roleswap")
@login_required
def add_swap(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    try:
        trigger = int(data["trigger_role_id"])
        remove = int(data["remove_role_id"])
    except (KeyError, ValueError):
        return jsonify({"error": "trigger_role_id and remove_role_id required"}), 400
    note = data.get("note", "")
    rule_id = run_async(db.add_role_swap(guild_id, trigger, remove, note))
    return jsonify({"ok": True, "id": rule_id})


@api.delete("/guild/<int:guild_id>/roleswap/<int:rule_id>")
@login_required
def delete_swap(guild_id, rule_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.remove_role_swap(guild_id, rule_id))
    return jsonify({"ok": removed})


@api.get("/guild/<int:guild_id>/rolegrant")
@login_required
def get_grants(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    rules = run_async(db.get_role_grants(guild_id))
    return jsonify([{k: str(v) if isinstance(v, int) else v for k, v in r.items()} for r in rules])


@api.post("/guild/<int:guild_id>/rolegrant")
@login_required
def add_grant(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    try:
        trigger = int(data["trigger_role_id"])
        grant = int(data["grant_role_id"])
    except (KeyError, ValueError):
        return jsonify({"error": "trigger_role_id and grant_role_id required"}), 400
    note = data.get("note", "")
    rule_id = run_async(db.add_role_grant(guild_id, trigger, grant, note))
    return jsonify({"ok": True, "id": rule_id})


@api.delete("/guild/<int:guild_id>/rolegrant/<int:rule_id>")
@login_required
def delete_grant(guild_id, rule_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.remove_role_grant(guild_id, rule_id))
    return jsonify({"ok": removed})


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/warnings")
@login_required
def get_warnings(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    warnings = run_async(db.get_all_warnings(guild_id))
    result = []
    for w in warnings:
        row = dict(w)
        # Resolve display names from the bot's cache
        bot = current_app.bot
        guild = bot.get_guild(guild_id)
        user = guild.get_member(w["user_id"]) if guild else None
        mod = guild.get_member(w["moderator_id"]) if guild else None
        row["user_name"] = str(user) if user else str(w["user_id"])
        row["moderator_name"] = str(mod) if mod else str(w["moderator_id"])
        result.append(row)
    return jsonify(result)


@api.delete("/guild/<int:guild_id>/warnings/<int:warning_id>")
@login_required
def delete_warning(guild_id, warning_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.remove_warning(guild_id, warning_id))
    return jsonify({"ok": removed})


# ---------------------------------------------------------------------------
# Guild config (alert channel, roles)
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/config")
@login_required
def get_config(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    guild_row = run_async(db.get_guild(guild_id))
    staff_roles = run_async(db.get_guild_roles(guild_id, "staff"))
    config_roles = run_async(db.get_guild_roles(guild_id, "config"))
    alert_roles = run_async(db.get_guild_roles(guild_id, "alert"))
    excluded = run_async(db.get_excluded_channels(guild_id))
    return jsonify({
        "guild": {k: str(v) if isinstance(v, int) and v else v for k, v in (guild_row or {}).items()},
        "staff_roles": [str(r["role_id"]) for r in staff_roles],
        "config_roles": [str(r["role_id"]) for r in config_roles],
        "alert_roles": [str(r["role_id"]) for r in alert_roles],
        "excluded_channels": [str(c) for c in excluded],
    })


@api.post("/guild/<int:guild_id>/alerts")
@login_required
def save_alerts(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    alert_ch = data.get("alert_channel_id") or None
    announce_ch = data.get("announcement_channel_id") or None
    run_async(db.set_guild_field(guild_id, "alert_channel_id", int(alert_ch) if alert_ch else None))
    run_async(db.set_guild_field(guild_id, "announcement_channel_id", int(announce_ch) if announce_ch else None))
    return jsonify({"ok": True})


@api.get("/guild/<int:guild_id>/channels")
@login_required
def get_channels(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    return jsonify([
        {"id": str(ch.id), "name": ch.name}
        for ch in sorted(guild.text_channels, key=lambda c: c.position)
    ])


@api.get("/guild/<int:guild_id>/categories")
@login_required
def get_categories(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    return jsonify([
        {"id": str(cat.id), "name": cat.name}
        for cat in sorted(guild.categories, key=lambda c: c.position)
    ])


@api.get("/guild/<int:guild_id>/roles_list")
@login_required
def get_roles(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    return jsonify([
        {"id": str(r.id), "name": r.name, "color": r.color.value}
        for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
        if r != guild.default_role
    ])


# ---------------------------------------------------------------------------
# Mod actions (dashboard moderation)
# ---------------------------------------------------------------------------

@api.post("/guild/<int:guild_id>/modaction")
@login_required
def do_mod_action(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    action = data.get("action", "")
    member_query = data.get("member", "").strip()
    reason = data.get("reason", "No reason provided")
    duration = data.get("duration", "10m")

    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify({"error": "Guild not found"}), 404

    moderator_id = int(session["user"]["id"])

    async def execute():
        from utils.helpers import resolve_member, parse_duration, format_duration
        from datetime import datetime, timedelta, timezone

        if action == "unban":
            try:
                uid = int(member_query)
                user = await bot.fetch_user(uid)
                await guild.unban(user, reason=f"[Dashboard] {reason}")
                await db.log_mod_action(guild_id, "unban", moderator_id, uid, reason)
                return {"ok": True, "message": f"Unbanned {user}"}
            except Exception as e:
                return {"error": str(e)}

        member = await resolve_member(guild, member_query)
        if not member:
            return {"error": f"Member '{member_query}' not found"}

        if action == "warn":
            wid = await db.add_warning(guild_id, member.id, moderator_id, reason)
            warnings = await db.get_warnings(guild_id, member.id)
            await db.log_mod_action(guild_id, "warn", moderator_id, member.id, reason)
            # Auto-ban check
            settings = await db.get_guild_settings(guild_id)
            threshold = settings.get("auto_ban_threshold", 0)
            auto_banned = False
            if threshold and len(warnings) >= threshold:
                await guild.ban(member, reason=f"[Dashboard Auto-ban] {threshold} warnings reached")
                auto_banned = True
            msg = f"Warned {member} (warn #{wid}, total: {len(warnings)})"
            if auto_banned:
                msg += f" — auto-banned at {threshold} warnings"
            return {"ok": True, "message": msg}

        elif action == "mute":
            secs = parse_duration(duration) or 600
            secs = min(secs, 2419200)
            until = discord.utils.utcnow() + timedelta(seconds=secs)
            try:
                await member.timeout(until, reason=f"[Dashboard] {reason}")
            except discord.Forbidden:
                return {"error": f"Missing permission to mute {member} (check role hierarchy)"}
            except discord.HTTPException as e:
                return {"error": f"Discord error: {e}"}
            await db.log_mod_action(guild_id, "mute", moderator_id, member.id, reason, f"duration:{secs}s")
            return {"ok": True, "message": f"Muted {member} for {format_duration(secs)}"}

        elif action == "kick":
            try:
                await member.kick(reason=f"[Dashboard] {reason}")
            except discord.Forbidden:
                return {"error": f"Missing permission to kick {member} (check role hierarchy)"}
            except discord.HTTPException as e:
                return {"error": f"Discord error: {e}"}
            await db.log_mod_action(guild_id, "kick", moderator_id, member.id, reason)
            return {"ok": True, "message": f"Kicked {member}"}

        elif action == "ban":
            try:
                await guild.ban(member, reason=f"[Dashboard] {reason}", delete_message_days=0)
            except discord.Forbidden:
                return {"error": f"Missing permission to ban {member} (check role hierarchy)"}
            except discord.HTTPException as e:
                return {"error": f"Discord error: {e}"}
            await db.log_mod_action(guild_id, "ban", moderator_id, member.id, reason)
            return {"ok": True, "message": f"Banned {member}"}

        elif action == "tempban":
            secs = parse_duration(duration) or 86400
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=secs)
            expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
            try:
                await guild.ban(member, reason=f"[Dashboard Temp-ban] {reason}", delete_message_days=0)
            except discord.Forbidden:
                return {"error": f"Missing permission to ban {member} (check role hierarchy)"}
            except discord.HTTPException as e:
                return {"error": f"Discord error: {e}"}
            await db.add_temp_ban(guild_id, member.id, moderator_id, reason, expires_str)
            await db.log_mod_action(guild_id, "tempban", moderator_id, member.id, reason, f"duration:{secs}s")
            return {"ok": True, "message": f"Temp-banned {member} for {format_duration(secs)}"}

        return {"error": "Unknown action"}

    import discord
    result = run_async(execute())
    return jsonify(result)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/auditlog")
@login_required
def get_audit_log(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    actions = run_async(db.get_mod_actions(guild_id, limit=100))
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    result = []
    for a in actions:
        row = dict(a)
        mod = guild.get_member(a["moderator_id"]) if guild else None
        target = guild.get_member(a["target_id"]) if guild and a["target_id"] else None
        row["moderator_name"] = str(mod) if mod else str(a["moderator_id"])
        row["target_name"] = str(target) if target else (str(a["target_id"]) if a["target_id"] else "—")
        result.append(row)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Guild settings (threshold, rate limit, caps)
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/settings")
@login_required
def get_settings(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    settings = run_async(db.get_guild_settings(guild_id))
    return jsonify(settings)


@api.post("/guild/<int:guild_id>/settings")
@login_required
def save_settings(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    allowed = {"auto_ban_threshold", "rate_limit_count", "rate_limit_seconds", "caps_percent", "caps_min_length"}
    for field, value in data.items():
        if field in allowed:
            try:
                run_async(db.set_guild_setting(guild_id, field, int(value)))
            except (ValueError, TypeError):
                return jsonify({"error": f"Invalid value for {field}: expected integer"}), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Giveaways
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/giveaways")
@login_required
def get_giveaways(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    giveaways = run_async(db.get_active_giveaways(guild_id))
    return jsonify([{k: str(v) if isinstance(v, int) else v for k, v in g.items()} for g in giveaways])


# ---------------------------------------------------------------------------
# Reaction roles
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/reactionroles")
@login_required
def get_reaction_roles(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    rrs = run_async(db.get_reaction_roles(guild_id))
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    result = []
    for r in rrs:
        row = dict(r)
        role = guild.get_role(r["role_id"]) if guild else None
        row["role_name"] = role.name if role else str(r["role_id"])
        result.append(row)
    return jsonify(result)


@api.delete("/guild/<int:guild_id>/reactionroles/<int:message_id>/<path:emoji>")
@login_required
def delete_reaction_role(guild_id, message_id, emoji):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.remove_reaction_role(message_id, emoji))
    return jsonify({"ok": removed})


# ---------------------------------------------------------------------------
# Join / Leave config
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/joinleave")
@login_required
def get_joinleave(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    cfg = run_async(db.get_join_leave_config(guild_id))
    return jsonify({
        "join_channel_id": str(cfg["join_channel_id"]) if cfg["join_channel_id"] else "",
        "join_enabled": bool(cfg["join_enabled"]),
        "leave_channel_id": str(cfg["leave_channel_id"]) if cfg["leave_channel_id"] else "",
        "leave_enabled": bool(cfg["leave_enabled"]),
        "dm_enabled": bool(cfg.get("dm_enabled", 0)),
    })


@api.post("/guild/<int:guild_id>/joinleave")
@login_required
def save_joinleave(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    kwargs = {}
    for field in ("join_channel_id", "leave_channel_id"):
        if field in data:
            kwargs[field] = int(data[field]) if data[field] else None
    for field in ("join_enabled", "leave_enabled", "dm_enabled"):
        if field in data:
            kwargs[field] = 1 if data[field] else 0
    run_async(db.set_join_leave_config(guild_id, **kwargs))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/tickets/config")
@login_required
def get_ticket_config(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    cfg = run_async(db.get_ticket_config(guild_id))
    return jsonify({
        "support_role_id": str(cfg["support_role_id"]) if cfg["support_role_id"] else "",
        "log_channel_id": str(cfg["log_channel_id"]) if cfg["log_channel_id"] else "",
        "category_id": str(cfg["category_id"]) if cfg["category_id"] else "",
        "enabled": bool(cfg["enabled"]),
    })


@api.post("/guild/<int:guild_id>/tickets/config")
@login_required
def save_ticket_config(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    kwargs = {}
    for field in ("support_role_id", "log_channel_id", "category_id"):
        if field in data:
            kwargs[field] = int(data[field]) if data[field] else None
    if "enabled" in data:
        kwargs["enabled"] = 1 if data["enabled"] else 0
    run_async(db.set_ticket_config(guild_id, **kwargs))
    return jsonify({"ok": True})


@api.get("/guild/<int:guild_id>/tickets/open")
@login_required
def get_open_tickets(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    tickets = run_async(db.get_open_tickets(guild_id))
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    result = []
    for t in tickets:
        member = guild.get_member(t["user_id"]) if guild else None
        ch = guild.get_channel(t["channel_id"]) if guild else None
        result.append({
            "id": t["id"],
            "ticket_number": t["ticket_number"],
            "user_name": str(member) if member else str(t["user_id"]),
            "channel_name": ch.name if ch else "deleted",
            "claimed_by": t.get("claimed_by"),
            "created_at": t["created_at"],
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Starboard
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/starboard")
@login_required
def get_starboard(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    cfg = run_async(db.get_starboard_config(guild_id))
    return jsonify({
        "channel_id": str(cfg["channel_id"]) if cfg["channel_id"] else "",
        "threshold": cfg["threshold"],
        "emoji": cfg["emoji"],
        "enabled": bool(cfg["enabled"]),
    })


@api.post("/guild/<int:guild_id>/starboard")
@login_required
def save_starboard(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    kwargs = {}
    if "channel_id" in data:
        kwargs["channel_id"] = int(data["channel_id"]) if data["channel_id"] else None
    if "threshold" in data:
        kwargs["threshold"] = max(1, min(25, int(data["threshold"])))
    if "emoji" in data and data["emoji"]:
        kwargs["emoji"] = data["emoji"]
    if "enabled" in data:
        kwargs["enabled"] = 1 if data["enabled"] else 0
    run_async(db.set_starboard_config(guild_id, **kwargs))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Sticky messages
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/stickies")
@login_required
def get_stickies(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    stickies = run_async(db.get_all_stickies(guild_id))
    result = []
    for s in stickies:
        ch = guild.get_channel(s["channel_id"]) if guild else None
        result.append({
            "channel_id": str(s["channel_id"]),
            "channel_name": f"#{ch.name}" if ch else f"#{s['channel_id']}",
            "content": s["content"][:80] + ("…" if len(s["content"]) > 80 else ""),
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Custom commands
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/customcmds")
@login_required
def get_custom_cmds(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    cmds = run_async(db.get_custom_commands(guild_id))
    return jsonify([dict(c) for c in cmds])


@api.delete("/guild/<int:guild_id>/customcmds/<name>")
@login_required
def delete_custom_cmd(guild_id, name):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.delete_custom_command(guild_id, name))
    return jsonify({"ok": removed})


# ---------------------------------------------------------------------------
# Scheduled messages
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/scheduled")
@login_required
def get_scheduled(guild_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    msgs = run_async(db.get_scheduled_messages(guild_id))
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    result = []
    for m in msgs:
        ch = guild.get_channel(m["channel_id"]) if guild else None
        result.append({
            "id": m["id"],
            "channel_name": f"#{ch.name}" if ch else f"#{m['channel_id']}",
            "content": m["content"][:80] + ("…" if len(m["content"]) > 80 else ""),
            "send_at": m["send_at"],
        })
    return jsonify(result)


@api.delete("/guild/<int:guild_id>/scheduled/<int:msg_id>")
@login_required
def cancel_scheduled(guild_id, msg_id):
    if not _check_guild_access(guild_id):
        return jsonify({"error": "Forbidden"}), 403
    removed = run_async(db.delete_scheduled_message(msg_id, guild_id))
    return jsonify({"ok": removed})


@api.post("/guild/<int:guild_id>/leave")
@login_required
def leave_guild(guild_id):
    user_id = int(session["user"]["id"])
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    if user_id != owner_id:
        return jsonify({"error": "Forbidden"}), 403
    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    run_async(guild.leave())
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# System check (bot owner only)
# ---------------------------------------------------------------------------

@api.get("/guild/<int:guild_id>/systemcheck")
@login_required
def system_check(guild_id):
    user_id = int(session["user"]["id"])
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    if user_id != owner_id:
        return jsonify({"error": "Forbidden"}), 403

    bot = current_app.bot
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify({"error": "Guild not found"}), 404

    me = guild.me
    checks = []

    def _c(category, name, status, detail=""):
        """Append a single check result. status: pass | warn | fail | info"""
        checks.append({"category": category, "name": name, "status": status, "detail": detail})

    # ------------------------------------------------------------------
    # Bot status
    # ------------------------------------------------------------------
    _c("Bot Status", "Gateway Connected",
       "pass" if bot.is_ready() else "fail",
       "Ready and receiving events" if bot.is_ready() else "Bot is not ready")

    lat_ms = round(bot.latency * 1000)
    _c("Bot Status", "Gateway Latency",
       "pass" if lat_ms < 150 else "warn" if lat_ms < 400 else "fail",
       f"{lat_ms} ms")

    _c("Bot Status", "Total Servers",
       "pass",
       f"Active in {len(bot.guilds)} server{'s' if len(bot.guilds) != 1 else ''}")

    _c("Bot Status", "Member Cache",
       "pass" if me else "fail",
       f"{guild.member_count:,} members" if me else "Bot member object not in cache")

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------
    if me:
        p = me.guild_permissions
        CRITICAL = [
            ("send_messages",        "Send Messages"),
            ("embed_links",          "Embed Links"),
            ("read_message_history", "Read Message History"),
            ("manage_messages",      "Manage Messages (automod delete)"),
            ("moderate_members",     "Moderate Members (timeout / mute)"),
            ("kick_members",         "Kick Members"),
            ("ban_members",          "Ban Members"),
            ("manage_roles",         "Manage Roles (role automation)"),
            ("manage_channels",      "Manage Channels (raid lock)"),
            ("view_audit_log",       "View Audit Log (log attribution)"),
            ("manage_guild",         "Manage Server"),
        ]
        for perm, label in CRITICAL:
            has = getattr(p, perm, False)
            _c("Permissions", label,
               "pass" if has else "fail",
               "Granted" if has else "MISSING — some features will not work")

        top = me.top_role
        _c("Permissions", "Role Hierarchy",
           "pass" if top.position > 1 else "warn",
           f"Highest role: {top.name!r} (position {top.position})"
           + ("" if top.position > 1 else " — bot may be unable to manage members"))
    else:
        _c("Permissions", "Permission Check", "fail", "Bot member not in cache — cannot check")

    # ------------------------------------------------------------------
    # Log channels
    # ------------------------------------------------------------------
    log_channels = run_async(db.get_all_log_channels(guild_id))
    LOG_LABELS = {
        "general":    "General / Commands",
        "spam":       "Spam / Automod",
        "member":     "Members",
        "edit":       "Message Edits",
        "delete":     "Message Deletes",
        "voice":      "Voice Activity",
        "roles":      "Role Changes",
        "channels":   "Channel Updates",
        "modactions": "Mod Actions",
    }
    for log_type, label in LOG_LABELS.items():
        ch_id = log_channels.get(log_type)
        if not ch_id:
            _c("Log Channels", label, "info", "Not configured")
            continue
        ch = guild.get_channel(int(ch_id))
        if not ch:
            _c("Log Channels", label, "warn", f"Configured channel ({ch_id}) no longer exists")
            continue
        can_send = me and ch.permissions_for(me).send_messages and ch.permissions_for(me).embed_links
        _c("Log Channels", label,
           "pass" if can_send else "warn",
           f"#{ch.name}" + ("" if can_send else " — bot cannot send/embed here"))

    # ------------------------------------------------------------------
    # Alert channel
    # ------------------------------------------------------------------
    guild_row = run_async(db.get_guild(guild_id)) or {}
    alert_ch_id = guild_row.get("alert_channel_id")
    if alert_ch_id:
        alert_ch = guild.get_channel(int(alert_ch_id))
        can_alert = me and alert_ch and alert_ch.permissions_for(me).send_messages
        _c("Infrastructure", "Alert Channel",
           "pass" if can_alert else "warn",
           f"#{alert_ch.name}" if alert_ch else f"Channel {alert_ch_id} not found in this server")
    else:
        _c("Infrastructure", "Alert Channel", "warn",
           "Not set — raid alerts and security events will have nowhere to go")

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    settings = run_async(db.get_guild_settings(guild_id))

    an_cfg = run_async(db.get_anti_nuke_config(guild_id))
    _c("Security", "Anti-Nuke",
       "pass" if an_cfg.get("enabled") else "warn",
       f"Enabled — punishment: {an_cfg.get('punishment', 'kick')}" if an_cfg.get("enabled")
       else "Disabled — server unprotected against rogue admin attacks")

    auto_detect = settings.get("auto_detect_raids", 1)
    _c("Security", "Auto Raid Detection",
       "pass" if auto_detect else "warn",
       f"Active — threshold: {settings.get('raid_join_threshold', 10)} joins / {settings.get('raid_join_window', 10)} s"
       if auto_detect else "Disabled")

    min_age = settings.get("min_account_age", 0)
    _c("Security", "Account Age Gate",
       "pass" if min_age else "info",
       f"Minimum {min_age} day{'s' if min_age != 1 else ''}" if min_age else "Not set — new accounts can join freely")

    honeypot = run_async(db.get_honeypot(guild_id))
    hp_ch_id = honeypot.get("channel_id") if honeypot else None
    hp_ch = guild.get_channel(int(hp_ch_id)) if hp_ch_id else None
    _c("Security", "Honeypot Channel",
       "pass" if hp_ch else "info",
       f"#{hp_ch.name} — action: {honeypot.get('action', 'kick')}" if hp_ch else "Not configured")

    raid_mode_on = bool(settings.get("raid_mode", 0))
    _c("Security", "Raid Mode",
       "warn" if raid_mode_on else "pass",
       "ACTIVE — all channels currently locked" if raid_mode_on else "Standby (normal)")

    exempt_roles = run_async(db.get_automod_exempt_roles(guild_id))
    _c("Security", "Automod Exempt Roles",
       "pass",
       f"{len(exempt_roles)} role{'s' if len(exempt_roles) != 1 else ''} exempt from automod"
       if exempt_roles else "None configured")

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------
    stored_features = run_async(db.get_all_features(guild_id))
    for feat in FEATURES:
        enabled = stored_features.get(feat, feat not in FEATURES_DEFAULT_OFF)
        _c("Features", feat.replace("_", " ").title(),
           "pass" if enabled else "info",
           "Enabled" if enabled else "Disabled")

    # ------------------------------------------------------------------
    # Automod filters
    # ------------------------------------------------------------------
    filters_raw = run_async(db.get_all_filters(guild_id))
    filter_labels = {
        "spam": "Spam Links", "word": "Word Filter", "image": "Image Block",
        "sticker": "Sticker Block", "external_emoji": "External Emoji",
        "link": "Link Filter", "invite": "Invite Links", "caps": "Excessive Caps",
        "rate_limit": "Rate Limit", "mentions": "Mass Mentions",
        "zalgo": "Zalgo / Unicode", "repeated_chars": "Repeated Characters",
        "emoji_spam": "Emoji Spam", "phishing": "Phishing Detection",
    }
    for fname, flabel in filter_labels.items():
        fdata = filters_raw.get(fname, {})
        enabled = fdata.get("enabled", False)
        punishment = fdata.get("punishment", "none")
        _c("Automod Filters", flabel,
           "pass" if enabled else "info",
           f"On — punishment: {punishment}" if enabled else "Off")

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------
    try:
        run_async(db.get_guild_settings(guild_id))
        _c("Infrastructure", "Database", "pass", "SQLite read OK")
    except Exception as exc:
        _c("Infrastructure", "Database", "fail", str(exc)[:120])

    _c("Infrastructure", "Web API", "pass", "Responding (this endpoint itself)")

    return jsonify({
        "checks": checks,
        "guild_name": guild.name,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })


# ---------------------------------------------------------------------------
# Premium overview (bot owner only — all servers)
# ---------------------------------------------------------------------------

@api.get("/owner/premium")
@login_required
def owner_premium():
    user_id = int(session["user"]["id"])
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    if user_id != owner_id:
        return jsonify({"error": "Forbidden"}), 403

    bot = current_app.bot
    premium_rows = run_async(db.get_all_premium_guilds())
    premium_map = {r["guild_id"]: r for r in premium_rows}

    now = datetime.datetime.utcnow()
    guilds = []
    for guild in bot.guilds:
        row = premium_map.get(guild.id)
        if row:
            expires_at = row["expires_at"]
            active = bool(row["active"])
            days_left = None
            if expires_at:
                try:
                    exp_dt = datetime.datetime.fromisoformat(expires_at)
                    days_left = max(0, (exp_dt - now).days)
                except Exception:
                    pass
        else:
            active = False
            expires_at = None
            days_left = None

        guilds.append({
            "id": str(guild.id),
            "name": guild.name,
            "member_count": guild.member_count or 0,
            "premium": active,
            "tier": row["tier"] if row else None,
            "expires_at": expires_at,
            "days_left": days_left,
        })

    guilds.sort(key=lambda g: (0 if g["premium"] else 1, g["name"].lower()))
    premium_count = sum(1 for g in guilds if g["premium"])
    return jsonify({"guilds": guilds, "total": len(guilds), "premium_count": premium_count})
