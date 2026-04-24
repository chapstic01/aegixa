"""
Anti-nuke — detects and punishes rogue admins/bots who mass-destroy the server.

Monitors via on_audit_log_entry_create:
  mass ban · mass kick · mass channel delete · mass role delete
  webhook spam · bot adds

Each action tracks a per-(guild, user) count in a rolling time window.
Exceeding a threshold triggers the configured punishment and sends an alert.
Server owner and whitelisted users are always exempt.
"""

import asyncio
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import error_embed, success_embed, info_embed, send_guild_alert
from utils.permissions import is_admin
import logging

log = logging.getLogger(__name__)

# Audit log actions we track
_TRACKED = {
    discord.AuditLogAction.ban:            "ban",
    discord.AuditLogAction.kick:           "kick",
    discord.AuditLogAction.channel_delete: "channel_delete",
    discord.AuditLogAction.role_delete:    "role_delete",
    discord.AuditLogAction.webhook_create: "webhook_create",
    discord.AuditLogAction.bot_add:        "bot_add",
}

# Default thresholds: (count, window_seconds)
_DEFAULTS = {
    "ban":            (3,  10),
    "kick":           (5,  10),
    "channel_delete": (2,  10),
    "role_delete":    (2,  10),
    "webhook_create": (3,  10),
    "bot_add":        (1,  60),
}

_ACTION_LABELS = {
    "ban":            "Mass Ban",
    "kick":           "Mass Kick",
    "channel_delete": "Mass Channel Delete",
    "role_delete":    "Mass Role Delete",
    "webhook_create": "Webhook Spam",
    "bot_add":        "Unauthorized Bot Add",
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def _get_config(guild_id: int) -> dict:
    return await db.get_anti_nuke_config(guild_id)


async def _save_config(guild_id: int, cfg: dict):
    await db.set_anti_nuke_config(guild_id, cfg)


def _resolve_threshold(cfg: dict, action: str) -> tuple[int, int]:
    """Return (count, window) for an action, falling back to defaults."""
    if action in cfg["thresholds"]:
        t = cfg["thresholds"][action]
        return t["count"], t["window"]
    return _DEFAULTS[action]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {guild_id: {user_id: {action: deque[timestamp]}}}
        self._counts: dict[int, dict[int, dict[str, deque]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(deque))
        )
        # Guilds currently being punished (avoid double-triggering)
        self._cooldown: set[tuple[int, int, str]] = set()

    # -----------------------------------------------------------------------
    # Audit log listener
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        action_key = _TRACKED.get(entry.action)
        if not action_key:
            return

        guild = entry.guild
        cfg = await _get_config(guild.id)
        if not cfg["enabled"]:
            return

        user = entry.user
        if not user:
            return

        # Always exempt: server owner, bot itself, whitelisted users
        if user.id in (guild.owner_id, self.bot.user.id, *cfg["whitelist"]):
            return

        # Track this action
        now = time.monotonic()
        count_limit, window = _resolve_threshold(cfg, action_key)
        dq = self._counts[guild.id][user.id][action_key]
        while dq and now - dq[0] > window:
            dq.popleft()
        dq.append(now)

        if len(dq) < count_limit:
            return  # threshold not yet reached

        # Cooldown: only trigger once per (guild, user, action) per 60s
        key = (guild.id, user.id, action_key)
        if key in self._cooldown:
            return
        self._cooldown.add(key)
        asyncio.get_event_loop().call_later(60, self._cooldown.discard, key)

        dq.clear()  # reset counter after triggering
        asyncio.create_task(self._punish(guild, user, action_key, cfg))

    # -----------------------------------------------------------------------
    # Punishment
    # -----------------------------------------------------------------------

    async def _punish(self, guild: discord.Guild, user: discord.Member | discord.User, action_key: str, cfg: dict):
        punishment = cfg["punishment"]
        label = _ACTION_LABELS[action_key]

        # Attempt to get a fresh Member object to check roles
        member = guild.get_member(user.id)

        result_line = ""
        try:
            if punishment == "ban":
                await guild.ban(discord.Object(id=user.id),
                                reason=f"[Aegixa Anti-Nuke] {label} detected")
                result_line = "🔨 Banned"
            elif punishment == "strip":
                if member:
                    roles_to_remove = [r for r in member.roles
                                       if r != guild.default_role and not r.managed]
                    if roles_to_remove:
                        await member.remove_roles(*roles_to_remove,
                                                  reason=f"[Aegixa Anti-Nuke] {label} — roles stripped")
                    result_line = "🚫 Roles stripped"
                else:
                    result_line = "⚠️ Could not strip roles (member not in cache)"
            else:  # kick
                await guild.kick(discord.Object(id=user.id),
                                 reason=f"[Aegixa Anti-Nuke] {label} detected")
                result_line = "👢 Kicked"
        except discord.Forbidden:
            result_line = "⚠️ Could not punish — bot lacks permission"
        except discord.HTTPException as e:
            result_line = f"⚠️ Discord error: {e}"

        log.warning("AntiNuke triggered in %s (%s): %s by %s — %s",
                    guild.name, guild.id, label, user, result_line)

        # Build alert embed
        embed = discord.Embed(
            title=f"🚨 Anti-Nuke Triggered — {label}",
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        avatar = user.display_avatar.url if hasattr(user, "display_avatar") else None
        if avatar:
            embed.set_author(name=str(user), icon_url=avatar)
        else:
            embed.set_author(name=str(user))
        embed.add_field(name="Perpetrator", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Action", value=label, inline=True)
        embed.add_field(name="Punishment", value=result_line, inline=True)
        embed.set_footer(text=f"Server: {guild.name}")

        # Deliver alert: try modactions log → alert channel → system channel → first writable channel
        await self._send_alert(guild, embed)

        # DM the owner
        self.bot.dispatch("owner_log", embed)

    async def _send_alert(self, guild: discord.Guild, embed: discord.Embed):
        await send_guild_alert(guild, embed)

    # -----------------------------------------------------------------------
    # /antinuke group
    # -----------------------------------------------------------------------

    antinuke = app_commands.Group(name="antinuke", description="Configure the anti-nuke protection system")

    @antinuke.command(name="enable", description="Enable anti-nuke protection")
    @is_admin()
    async def an_enable(self, interaction: discord.Interaction):
        cfg = await _get_config(interaction.guild_id)
        cfg["enabled"] = True
        await _save_config(interaction.guild_id, cfg)

        # Confirmation to the admin who ran the command
        await interaction.response.send_message(
            embed=success_embed("Anti-nuke is now **enabled**. Use `/antinuke status` to review thresholds."),
            ephemeral=True,
        )

        # Public setup embed sent to the best available channel
        threshold_lines = []
        for action, label in _ACTION_LABELS.items():
            count, window = _resolve_threshold(cfg, action)
            threshold_lines.append(f"**{label}** — {count} in {window}s")

        setup_embed = discord.Embed(
            title="🛡️ Anti-Nuke Protection Enabled",
            description=(
                "This server is now protected against rogue admins and compromised bots.\n"
                "Any user exceeding the limits below will be automatically punished."
            ),
            color=0xdc2626,
            timestamp=discord.utils.utcnow(),
        )
        setup_embed.add_field(
            name="Punishment",
            value=cfg["punishment"].title(),
            inline=True,
        )
        setup_embed.add_field(
            name="Enabled by",
            value=interaction.user.mention,
            inline=True,
        )
        setup_embed.add_field(
            name="Thresholds",
            value="\n".join(threshold_lines),
            inline=False,
        )
        wl = cfg["whitelist"]
        if wl:
            setup_embed.add_field(
                name="Whitelisted users",
                value=" ".join(f"<@{uid}>" for uid in wl),
                inline=False,
            )
        setup_embed.set_footer(text="Use /antinuke threshold to adjust limits · /antinuke disable to turn off")
        await send_guild_alert(interaction.guild, setup_embed)

    @antinuke.command(name="disable", description="Disable anti-nuke protection")
    @is_admin()
    async def an_disable(self, interaction: discord.Interaction):
        cfg = await _get_config(interaction.guild_id)
        cfg["enabled"] = False
        await _save_config(interaction.guild_id, cfg)
        await interaction.response.send_message(
            embed=success_embed("Anti-nuke has been **disabled**."),
            ephemeral=True,
        )

    @antinuke.command(name="status", description="Show current anti-nuke configuration")
    @is_admin()
    async def an_status(self, interaction: discord.Interaction):
        cfg = await _get_config(interaction.guild_id)
        embed = discord.Embed(
            title="🛡️ Anti-Nuke Configuration",
            color=0xdc2626 if cfg["enabled"] else 0x6b7280,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Status",
            value="✅ Enabled" if cfg["enabled"] else "❌ Disabled",
            inline=True,
        )
        embed.add_field(name="Punishment", value=cfg["punishment"].title(), inline=True)

        threshold_lines = []
        for action, label in _ACTION_LABELS.items():
            count, window = _resolve_threshold(cfg, action)
            custom = "†" if action in cfg["thresholds"] else ""
            threshold_lines.append(f"`{label}`: {count} in {window}s{custom}")
        embed.add_field(name="Thresholds († = custom)", value="\n".join(threshold_lines), inline=False)

        wl = cfg["whitelist"]
        wl_str = " ".join(f"<@{uid}>" for uid in wl) if wl else "*(none)*"
        embed.add_field(name="Whitelist", value=wl_str, inline=False)
        embed.set_footer(text="Server owner is always exempt · Use /antinuke threshold to adjust limits")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @antinuke.command(name="punishment", description="Set what happens to the attacker")
    @app_commands.describe(action="kick · ban · strip (remove all roles)")
    @app_commands.choices(action=[
        app_commands.Choice(name="Kick",         value="kick"),
        app_commands.Choice(name="Ban",          value="ban"),
        app_commands.Choice(name="Strip roles",  value="strip"),
    ])
    @is_admin()
    async def an_punishment(self, interaction: discord.Interaction, action: str):
        cfg = await _get_config(interaction.guild_id)
        cfg["punishment"] = action
        await _save_config(interaction.guild_id, cfg)
        await interaction.response.send_message(
            embed=success_embed(f"Punishment set to **{action}**."),
            ephemeral=True,
        )

    @antinuke.command(name="threshold", description="Set the trigger threshold for a specific action")
    @app_commands.describe(
        action="Which action to configure",
        count="Number of actions before triggering",
        window="Time window in seconds",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Mass Ban",            value="ban"),
        app_commands.Choice(name="Mass Kick",           value="kick"),
        app_commands.Choice(name="Mass Channel Delete", value="channel_delete"),
        app_commands.Choice(name="Mass Role Delete",    value="role_delete"),
        app_commands.Choice(name="Webhook Spam",        value="webhook_create"),
        app_commands.Choice(name="Bot Add",             value="bot_add"),
    ])
    @is_admin()
    async def an_threshold(self, interaction: discord.Interaction, action: str, count: int, window: int):
        if count < 1 or count > 100:
            return await interaction.response.send_message(
                embed=error_embed("Count must be between 1 and 100."), ephemeral=True
            )
        if window < 5 or window > 300:
            return await interaction.response.send_message(
                embed=error_embed("Window must be between 5 and 300 seconds."), ephemeral=True
            )
        cfg = await _get_config(interaction.guild_id)
        cfg["thresholds"][action] = {"count": count, "window": window}
        await _save_config(interaction.guild_id, cfg)
        label = _ACTION_LABELS[action]
        await interaction.response.send_message(
            embed=success_embed(f"**{label}** threshold set to {count} actions in {window}s."),
            ephemeral=True,
        )

    @antinuke.command(name="reset", description="Reset a threshold back to its default")
    @app_commands.describe(action="Which action to reset")
    @app_commands.choices(action=[
        app_commands.Choice(name="Mass Ban",            value="ban"),
        app_commands.Choice(name="Mass Kick",           value="kick"),
        app_commands.Choice(name="Mass Channel Delete", value="channel_delete"),
        app_commands.Choice(name="Mass Role Delete",    value="role_delete"),
        app_commands.Choice(name="Webhook Spam",        value="webhook_create"),
        app_commands.Choice(name="Bot Add",             value="bot_add"),
    ])
    @is_admin()
    async def an_reset(self, interaction: discord.Interaction, action: str):
        cfg = await _get_config(interaction.guild_id)
        cfg["thresholds"].pop(action, None)
        await _save_config(interaction.guild_id, cfg)
        count, window = _DEFAULTS[action]
        label = _ACTION_LABELS[action]
        await interaction.response.send_message(
            embed=success_embed(f"**{label}** reset to default: {count} in {window}s."),
            ephemeral=True,
        )

    @antinuke.command(name="whitelist", description="Add or remove a user from the anti-nuke whitelist")
    @app_commands.describe(user="User to toggle on/off the whitelist", action="Add or remove")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add",    value="add"),
        app_commands.Choice(name="Remove", value="remove"),
    ])
    @is_admin()
    async def an_whitelist(self, interaction: discord.Interaction, user: discord.Member, action: str):
        if user.id == interaction.guild.owner_id:
            return await interaction.response.send_message(
                embed=info_embed("The server owner is always exempt — no need to whitelist."),
                ephemeral=True,
            )
        cfg = await _get_config(interaction.guild_id)
        if action == "add":
            if user.id not in cfg["whitelist"]:
                cfg["whitelist"].append(user.id)
            await _save_config(interaction.guild_id, cfg)
            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} added to the anti-nuke whitelist."),
                ephemeral=True,
            )
        else:
            cfg["whitelist"] = [uid for uid in cfg["whitelist"] if uid != user.id]
            await _save_config(interaction.guild_id, cfg)
            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} removed from the whitelist."),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
