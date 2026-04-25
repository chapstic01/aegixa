"""
Raid mode cog — manually lock all channels or auto-detect join floods.
/raidmode on/off
Auto-detection: tracks join rate and triggers lockdown automatically.
"""

import asyncio
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, send_guild_alert
from utils.permissions import is_staff
from cogs.logging_cog import send_log
from config import LOG_COLORS
import logging

log = logging.getLogger(__name__)


class RaidMode(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Auto-detection state per guild
        self._joins: dict[int, deque] = defaultdict(deque)
        self._auto_locked: dict[int, bool] = {}

    # -----------------------------------------------------------------------
    # Auto join-flood detection
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        settings = await db.get_guild_settings(guild.id)

        if not settings.get("auto_detect_raids", 1):
            return

        # Account-age gate
        min_age = settings.get("min_account_age", 0)
        if min_age:
            age_days = (discord.utils.utcnow() - member.created_at).days
            if age_days < min_age:
                action = settings.get("raid_action", "kick")
                try:
                    await member.send(
                        f"Your account must be at least **{min_age} days old** to join **{guild.name}**."
                    )
                except discord.Forbidden:
                    pass
                try:
                    if action == "ban":
                        await guild.ban(member, reason=f"[Aegixa] Account too new ({age_days}d)")
                    else:
                        await member.kick(reason=f"[Aegixa] Account too new ({age_days}d)")
                except discord.Forbidden:
                    pass
                return

        # If already in auto-lockdown, handle new join
        if self._auto_locked.get(guild.id):
            action = settings.get("raid_action", "kick")
            try:
                if action == "ban":
                    await guild.ban(member, reason="[Aegixa] Server in raid lockdown")
                else:
                    await member.kick(reason="[Aegixa] Server in raid lockdown")
            except discord.Forbidden:
                pass
            return

        # Track join rate
        threshold = settings.get("raid_join_threshold", 10)
        window    = settings.get("raid_join_window", 10)
        now = time.monotonic()
        dq = self._joins[guild.id]
        while dq and now - dq[0] > window:
            dq.popleft()
        dq.append(now)

        if len(dq) >= threshold:
            self._auto_locked[guild.id] = True
            asyncio.create_task(self._auto_lockdown(guild, settings))
            asyncio.create_task(db.log_security_event(
                guild.id, "raid_detected", None,
                f"{len(dq)} joins in {window}s — auto-lockdown triggered"
            ))

    async def _auto_lockdown(self, guild: discord.Guild, settings: dict):
        detect_embed = discord.Embed(
            title="🚨 RAID DETECTED — Auto-Lockdown Active",
            description=(
                "Unusual join rate detected. New members are being auto-kicked/banned.\n\n"
                "Use `/raidmode False` to lift the lockdown manually, or wait 5 minutes."
            ),
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        detect_embed.add_field(
            name="Join threshold",
            value=f"{settings.get('raid_join_threshold', 10)} joins / {settings.get('raid_join_window', 10)}s",
            inline=True,
        )
        detect_embed.add_field(
            name="Action",
            value=settings.get("raid_action", "kick").title(),
            inline=True,
        )
        detect_embed.set_footer(text=guild.name)
        await send_guild_alert(guild, detect_embed)

        fresh_settings = await db.get_guild_settings(guild.id)
        duration = fresh_settings.get("raid_lockdown_duration", 300)
        await asyncio.sleep(duration)
        if self._auto_locked.get(guild.id):
            self._auto_locked[guild.id] = False
            self._joins[guild.id].clear()
            lift_embed = discord.Embed(
                title="✅ Raid Lockdown Lifted",
                description="Auto-lifted after 5 minutes. Monitor for follow-up attacks.",
                color=0x57F287,
                timestamp=discord.utils.utcnow(),
            )
            lift_embed.set_footer(text=guild.name)
            await send_guild_alert(guild, lift_embed)

    @app_commands.command(name="raidmode", description="Enable or disable raid mode (locks all channels + strict automod)")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_staff()
    async def raidmode(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = await db.get_guild_settings(guild.id)

        if enabled == bool(settings["raid_mode"]):
            state = "already enabled" if enabled else "already disabled"
            return await interaction.followup.send(embed=error_embed(f"Raid mode is {state}."), ephemeral=True)

        await db.set_guild_setting(guild.id, "raid_mode", int(enabled))

        locked = 0
        failed = 0

        if enabled:
            # Lock all text channels for @everyone
            for channel in guild.text_channels:
                try:
                    overwrite = channel.overwrites_for(guild.default_role)
                    overwrite.send_messages = False
                    await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="[Aegixa] Raid mode enabled")
                    locked += 1
                except discord.HTTPException:
                    failed += 1

            enable_embed = discord.Embed(
                title="🚨 RAID MODE ENABLED",
                description=(
                    f"All channels have been locked.\n"
                    f"Locked: **{locked}** | Failed: **{failed}**\n\n"
                    f"Disable with `/raidmode False` when the raid is over."
                ),
                color=0xED4245,
                timestamp=discord.utils.utcnow(),
            )
            enable_embed.add_field(name="Triggered by", value=interaction.user.mention, inline=True)
            enable_embed.set_footer(text=guild.name)
            await interaction.followup.send(embed=enable_embed, ephemeral=False)
            await send_guild_alert(guild, enable_embed)
        else:
            # Unlock all text channels
            for channel in guild.text_channels:
                try:
                    overwrite = channel.overwrites_for(guild.default_role)
                    overwrite.send_messages = None
                    if overwrite.is_empty():
                        await channel.set_permissions(guild.default_role, overwrite=None, reason="[Aegixa] Raid mode disabled")
                    else:
                        await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="[Aegixa] Raid mode disabled")
                    locked += 1
                except discord.HTTPException:
                    failed += 1

            disable_embed = discord.Embed(
                title="✅ Raid Mode Disabled",
                description=f"All channels have been unlocked.\nUnlocked: **{locked}** | Failed: **{failed}**",
                color=0x57F287,
                timestamp=discord.utils.utcnow(),
            )
            disable_embed.add_field(name="Disabled by", value=interaction.user.mention, inline=True)
            disable_embed.set_footer(text=guild.name)
            await interaction.followup.send(embed=disable_embed, ephemeral=False)
            await send_guild_alert(guild, disable_embed)

        action = "enabled" if enabled else "disabled"
        await db.log_mod_action(guild.id, f"raid_mode_{action}", interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidMode(bot))
