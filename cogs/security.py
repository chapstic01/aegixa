"""
Security cog — supplemental protection features.

• Honeypot channels      — anyone who messages gets banned/kicked instantly
• Ghost-ping detection   — logs deleted messages that contained @mentions
• Auto-slowmode          — applies Discord slowmode when a channel floods
• Role permission monitor— alerts when a role gains dangerous permissions
• Softban               — ban + immediate unban to clear messages
• Raid / join config     — configure detection thresholds from one place
• Automod exempt roles   — roles that bypass all automod filters
• /security status       — single-pane view of all security settings
"""

import asyncio
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed, send_guild_alert
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)

# Permissions that trigger a role-update alert when newly granted
DANGEROUS_PERMS = frozenset({
    "administrator",
    "ban_members",
    "kick_members",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "mention_everyone",
})

# Auto-slowmode parameters
_SM_THRESHOLD = 12   # messages in window to trigger slowmode
_SM_WINDOW    = 5    # seconds
_SM_DELAY     = 8    # slowmode seconds to apply
_SM_COOLDOWN  = 60   # seconds before reverting if rate drops


class Security(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # channel_id -> deque of message timestamps (for auto-slowmode)
        self._ch_msgs: dict[int, deque] = defaultdict(deque)
        # channel_id -> monotonic time when slowmode was applied
        self._sm_active: dict[int, float] = {}

    # -----------------------------------------------------------------------
    # Honeypot + auto-slowmode listener
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        # ---- Honeypot ----
        cfg = await db.get_honeypot(message.guild.id)
        if cfg and message.channel.id == cfg["channel_id"]:
            member = message.author
            if isinstance(member, discord.Member) and (
                member.guild_permissions.administrator
                or member.guild_permissions.manage_guild
            ):
                return  # never trap admins in their own honeypot

            action = cfg.get("action", "ban")
            try:
                await message.delete()
            except discord.HTTPException:
                pass

            result = "no action taken"
            try:
                try:
                    await member.send(
                        f"You have been **{action}ned** from **{message.guild.name}** "
                        "for sending a message in a restricted channel."
                    )
                except discord.Forbidden:
                    pass
                if action == "ban":
                    await message.guild.ban(
                        member,
                        reason="[Aegixa] Honeypot channel triggered",
                        delete_message_days=1,
                    )
                    result = "banned"
                else:
                    await member.kick(reason="[Aegixa] Honeypot channel triggered")
                    result = "kicked"
            except discord.Forbidden:
                result = "action failed (bot lacks permission)"
            except discord.HTTPException as e:
                result = f"Discord error: {e}"

            await db.log_security_event(
                message.guild.id, "honeypot",
                member.id,
                f"Sent message in honeypot channel #{message.channel.name} — {result}",
            )

            embed = discord.Embed(
                title="🍯 Honeypot Triggered",
                description=(
                    f"{member.mention} (`{member.id}`) sent a message in the honeypot channel.\n"
                    f"**Action:** {result.title()}"
                ),
                color=0xED4245,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            embed.add_field(
                name="Message",
                value=(message.content[:300] or "*(no text)*"),
                inline=False,
            )
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            await send_guild_alert(message.guild, embed)
            return

        # ---- Auto-slowmode ----
        if isinstance(message.channel, discord.TextChannel):
            await self._check_auto_slowmode(message)

    # -----------------------------------------------------------------------
    # Auto-slowmode logic
    # -----------------------------------------------------------------------

    async def _check_auto_slowmode(self, message: discord.Message):
        channel = message.channel
        now = time.monotonic()
        dq = self._ch_msgs[channel.id]

        # Purge timestamps outside the window
        while dq and now - dq[0] > _SM_WINDOW:
            dq.popleft()
        dq.append(now)

        # If slowmode is active, check whether to lift it
        if channel.id in self._sm_active:
            elapsed = now - self._sm_active[channel.id]
            if elapsed >= _SM_COOLDOWN and len(dq) < _SM_THRESHOLD // 2:
                try:
                    await channel.edit(slowmode_delay=0, reason="[Aegixa] Auto-slowmode lifted")
                except discord.HTTPException:
                    pass
                del self._sm_active[channel.id]
            return

        # Apply slowmode if threshold exceeded and channel has none active
        if len(dq) >= _SM_THRESHOLD and channel.slowmode_delay == 0:
            try:
                await channel.edit(
                    slowmode_delay=_SM_DELAY,
                    reason="[Aegixa] Auto-slowmode: high message rate detected",
                )
                self._sm_active[channel.id] = now
            except discord.HTTPException:
                return

            embed = discord.Embed(
                title="⏱️ Auto-Slowmode Applied",
                description=(
                    f"{channel.mention} is experiencing a high message rate.\n"
                    f"Slowmode set to **{_SM_DELAY}s** automatically and will be lifted once traffic drops."
                ),
                color=0xFEE75C,
                timestamp=discord.utils.utcnow(),
            )
            await send_guild_alert(message.guild, embed)

    # -----------------------------------------------------------------------
    # Ghost-ping detection
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not (message.mentions or message.role_mentions or message.mention_everyone):
            return

        mentioned_users = [m.mention for m in message.mentions[:5]]
        mentioned_roles = [r.mention for r in message.role_mentions[:3]]
        all_mentions = mentioned_users + mentioned_roles
        if message.mention_everyone:
            all_mentions.append("@everyone/@here")
        if not all_mentions:
            return

        await db.log_security_event(
            message.guild.id, "ghost_ping",
            message.author.id,
            f"Deleted message with mentions: {', '.join(all_mentions[:5])} in #{message.channel.name}",
        )

        from cogs.logging_cog import send_log
        embed = discord.Embed(
            title="👻 Ghost Ping Detected",
            description=f"{message.author.mention} deleted a message that contained mentions.",
            color=0xFEE75C,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Mentions", value=", ".join(all_mentions), inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        if message.content:
            embed.add_field(name="Deleted Content", value=message.content[:500], inline=False)
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        await send_log(message.guild, "general", embed)

    # -----------------------------------------------------------------------
    # Role permission monitor
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        newly_dangerous = [
            perm.replace("_", " ").title()
            for perm in DANGEROUS_PERMS
            if not getattr(before.permissions, perm, False)
            and getattr(after.permissions, perm, False)
        ]
        if not newly_dangerous:
            return

        await db.log_security_event(
            guild.id, "dangerous_role_update",
            None,
            f"Role '{after.name}' ({after.id}) gained: {', '.join(newly_dangerous)}",
        )

        embed = discord.Embed(
            title="⚠️ Dangerous Role Permission Added",
            description=(
                f"The role {after.mention} was updated with elevated permissions.\n"
                "Review this change to ensure it was intentional."
            ),
            color=0xFF6B35,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Role", value=f"{after.name} (`{after.id}`)", inline=True)
        embed.add_field(
            name="Newly Granted",
            value="\n".join(f"• {p}" for p in newly_dangerous),
            inline=False,
        )
        await send_guild_alert(guild, embed)

    # -----------------------------------------------------------------------
    # /security command group
    # -----------------------------------------------------------------------

    security = app_commands.Group(name="security", description="Advanced security configuration")

    # ---- Honeypot subgroup ----

    honeypot = app_commands.Group(
        name="honeypot", description="Manage the honeypot trap channel", parent=security
    )

    @honeypot.command(name="set", description="Set a honeypot channel — any non-admin who messages there gets actioned")
    @app_commands.describe(
        channel="Channel to use as the honeypot",
        action="What to do when it's triggered",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Ban",  value="ban"),
        app_commands.Choice(name="Kick", value="kick"),
    ])
    @is_admin()
    async def honeypot_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        action: str = "ban",
    ):
        await db.set_honeypot(interaction.guild_id, channel.id, action)
        await interaction.response.send_message(
            embed=success_embed(
                f"{channel.mention} is now a **honeypot channel**.\n"
                f"Any non-admin who sends a message there will be **{action}ned**.\n\n"
                "⚠️ Ensure the channel is hidden from legitimate members."
            ),
            ephemeral=True,
        )

    @honeypot.command(name="clear", description="Remove the honeypot channel")
    @is_admin()
    async def honeypot_clear(self, interaction: discord.Interaction):
        await db.clear_honeypot(interaction.guild_id)
        await interaction.response.send_message(
            embed=success_embed("Honeypot channel removed."), ephemeral=True
        )

    @honeypot.command(name="status", description="Show current honeypot configuration")
    @is_admin()
    async def honeypot_status(self, interaction: discord.Interaction):
        cfg = await db.get_honeypot(interaction.guild_id)
        if not cfg:
            return await interaction.response.send_message(
                embed=info_embed("No honeypot configured.", "Use `/security honeypot set` to set one up."),
                ephemeral=True,
            )
        ch = interaction.guild.get_channel(cfg["channel_id"])
        ch_str = ch.mention if ch else f"#{cfg['channel_id']} *(deleted)*"
        await interaction.response.send_message(
            embed=info_embed("🍯 Honeypot Active", f"Channel: {ch_str}\nAction: **{cfg['action'].title()}**"),
            ephemeral=True,
        )

    # ---- Automod exempt subgroup ----

    exempt = app_commands.Group(
        name="exempt", description="Manage automod-exempt roles", parent=security
    )

    @exempt.command(name="add", description="Exempt a role from all automod filters")
    @app_commands.describe(role="Role to exempt")
    @is_admin()
    async def exempt_add(self, interaction: discord.Interaction, role: discord.Role):
        await db.add_automod_exempt_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} is now exempt from all automod filters."),
            ephemeral=True,
        )

    @exempt.command(name="remove", description="Remove a role's automod exemption")
    @app_commands.describe(role="Role to un-exempt")
    @is_admin()
    async def exempt_remove(self, interaction: discord.Interaction, role: discord.Role):
        await db.remove_automod_exempt_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} is no longer exempt from automod."),
            ephemeral=True,
        )

    @exempt.command(name="list", description="List all automod-exempt roles")
    @is_admin()
    async def exempt_list(self, interaction: discord.Interaction):
        role_ids = await db.get_automod_exempt_roles(interaction.guild_id)
        if not role_ids:
            return await interaction.response.send_message(
                embed=info_embed("No exempt roles configured.", "Use `/security exempt add` to add one."),
                ephemeral=True,
            )
        roles = [interaction.guild.get_role(r) for r in role_ids]
        lines = [r.mention for r in roles if r]
        await interaction.response.send_message(
            embed=info_embed("Automod Exempt Roles", "\n".join(lines) if lines else "*(all deleted)*"),
            ephemeral=True,
        )

    # ---- Softban ----

    @security.command(name="softban", description="Ban then immediately unban to purge messages without a permanent ban")
    @app_commands.describe(
        member="Member to softban",
        delete_days="Days of messages to delete (1–7)",
        reason="Reason for the softban",
    )
    @is_staff()
    async def softban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        delete_days: int = 1,
        reason: str = "No reason provided",
    ):
        await interaction.response.defer(ephemeral=True)
        delete_days = max(1, min(7, delete_days))

        if member.top_role.position >= interaction.guild.me.top_role.position:
            return await interaction.followup.send(
                embed=error_embed("That member's role is at or above my highest role."),
                ephemeral=True,
            )
        if member.id == interaction.guild.owner_id:
            return await interaction.followup.send(
                embed=error_embed("Cannot softban the server owner."), ephemeral=True
            )

        try:
            try:
                await member.send(
                    embed=discord.Embed(
                        description=(
                            f":warning: You have been softbanned from **{interaction.guild.name}**.\n"
                            f"Reason: {reason}\n"
                            f"Your last {delete_days} day(s) of messages have been deleted."
                        ),
                        color=0xFEE75C,
                    )
                )
            except discord.Forbidden:
                pass

            await interaction.guild.ban(
                member,
                reason=f"[Softban] {reason}",
                delete_message_days=delete_days,
            )
            await interaction.guild.unban(
                discord.Object(id=member.id),
                reason=f"[Softban] Immediate unban — {reason}",
            )
            await db.log_mod_action(
                interaction.guild_id, "softban",
                interaction.user.id, member.id, reason,
            )
            await interaction.followup.send(
                embed=success_embed(
                    f"Softbanned **{member}** — {delete_days} day(s) of messages deleted. "
                    "They can rejoin with a fresh invite."
                ),
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Missing permission to ban this member."), ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Discord error: {e}"), ephemeral=True
            )

    # ---- Raid config ----

    @security.command(name="raidconfig", description="Configure automatic raid detection thresholds")
    @app_commands.describe(
        threshold="Number of joins in the window before auto-lockdown triggers",
        window="Rolling time window in seconds (5–60)",
        action="What happens to members joining during lockdown",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Ban",  value="ban"),
    ])
    @is_admin()
    async def raidconfig(
        self,
        interaction: discord.Interaction,
        threshold: int,
        window: int,
        action: str,
    ):
        if not 2 <= threshold <= 100:
            return await interaction.response.send_message(
                embed=error_embed("Threshold must be 2–100."), ephemeral=True
            )
        if not 5 <= window <= 60:
            return await interaction.response.send_message(
                embed=error_embed("Window must be 5–60 seconds."), ephemeral=True
            )
        await db.set_guild_setting(interaction.guild_id, "raid_join_threshold", threshold)
        await db.set_guild_setting(interaction.guild_id, "raid_join_window", window)
        await db.set_guild_setting(interaction.guild_id, "raid_action", action)
        await interaction.response.send_message(
            embed=success_embed(
                f"Raid detection: **{threshold}** joins in **{window}s** → **{action}**.\n"
                "Auto-detection is always active when raid mode is off."
            ),
            ephemeral=True,
        )

    # ---- Lock duration ----

    @security.command(name="lockduration", description="Set how long auto-lockdown lasts before lifting itself")
    @app_commands.describe(seconds="Duration in seconds (60–3600, default 300)")
    @is_admin()
    async def lockduration(self, interaction: discord.Interaction, seconds: int):
        if not 60 <= seconds <= 3600:
            return await interaction.response.send_message(
                embed=error_embed("Duration must be 60–3600 seconds."), ephemeral=True
            )
        await db.set_guild_setting(interaction.guild_id, "raid_lockdown_duration", seconds)
        mins = seconds // 60
        secs = seconds % 60
        label = f"{mins}m {secs}s" if secs else f"{mins}m"
        await interaction.response.send_message(
            embed=success_embed(f"Auto-lockdown duration set to **{label}**."),
            ephemeral=True,
        )

    # ---- Join check ----

    @security.command(name="joincheck", description="Set a minimum account age requirement to join this server")
    @app_commands.describe(
        min_age="Minimum account age in days (0 = disabled)",
        action="Action when account is too new",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Ban",  value="ban"),
    ])
    @is_admin()
    async def joincheck(
        self,
        interaction: discord.Interaction,
        min_age: int,
        action: str = "kick",
    ):
        if not 0 <= min_age <= 365:
            return await interaction.response.send_message(
                embed=error_embed("Min age must be 0–365 days."), ephemeral=True
            )
        await db.set_guild_setting(interaction.guild_id, "min_account_age", min_age)
        await db.set_guild_setting(interaction.guild_id, "raid_action", action)
        if min_age == 0:
            msg = "Account age gate **disabled**. All accounts can join."
        else:
            msg = (
                f"Accounts must be at least **{min_age} day(s)** old to join.\n"
                f"Accounts that fail will be **{action}ned**."
            )
        await interaction.response.send_message(embed=success_embed(msg), ephemeral=True)

    # ---- Security status ----

    @security.command(name="status", description="Show a full security overview for this server")
    @is_admin()
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        settings = await db.get_guild_settings(guild.id)
        honeypot = await db.get_honeypot(guild.id)
        events   = await db.get_security_events(guild.id, limit=5)
        exempt   = await db.get_automod_exempt_roles(guild.id)

        embed = discord.Embed(
            title=f"🛡️ Security Status — {guild.name}",
            color=0xdc2626,
            timestamp=discord.utils.utcnow(),
        )

        # Raid protection block
        raid_on      = bool(settings.get("raid_mode", 0))
        auto_detect  = bool(settings.get("auto_detect_raids", 1))
        threshold    = settings.get("raid_join_threshold", 10)
        window       = settings.get("raid_join_window", 10)
        action       = settings.get("raid_action", "kick")
        duration     = settings.get("raid_lockdown_duration", 300)
        min_age      = settings.get("min_account_age", 0)

        raid_lines = [
            f"Raid Mode: **{'🔴 ACTIVE' if raid_on else '🟢 Off'}**",
            f"Auto-Detect: {'✅' if auto_detect else '❌'} — {threshold} joins/{window}s → **{action}**",
            f"Auto-unlock after: **{duration}s**",
            f"Min account age: **{min_age}d**" if min_age else "Min account age: *none*",
        ]
        embed.add_field(name="Raid Protection", value="\n".join(raid_lines), inline=False)

        # Honeypot
        if honeypot:
            hp_ch = guild.get_channel(honeypot["channel_id"])
            hp_str = f"{hp_ch.mention if hp_ch else '`#deleted`'} → **{honeypot['action']}**"
        else:
            hp_str = "*Not configured*"
        embed.add_field(name="🍯 Honeypot", value=hp_str, inline=True)

        # Exempt roles
        if exempt:
            roles    = [guild.get_role(r) for r in exempt]
            ex_lines = [r.mention for r in roles if r]
            ex_str   = ", ".join(ex_lines) if ex_lines else "*all deleted*"
        else:
            ex_str = "*None*"
        embed.add_field(name="Automod Exempt Roles", value=ex_str, inline=True)

        # Recent security events
        if events:
            lines = [
                f"`{e['created_at'][:16]}` **{e['event_type']}**"
                + (f" — <@{e['user_id']}>" if e.get("user_id") else "")
                for e in events
            ]
            embed.add_field(
                name="Recent Security Events",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="Recent Security Events", value="*No events logged yet.*", inline=False)

        embed.set_footer(text="• /antinuke status for anti-nuke • /raidmode to toggle lockdown")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Security(bot))
