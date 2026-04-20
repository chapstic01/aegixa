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
from utils.helpers import success_embed, error_embed
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

    async def _auto_lockdown(self, guild: discord.Guild, settings: dict):
        g_row = await db.get_guild(guild.id)
        alert_ch = None
        if g_row and g_row.get("alert_channel_id"):
            alert_ch = guild.get_channel(g_row["alert_channel_id"])

        embed = discord.Embed(
            title=":warning: RAID DETECTED — Auto-Lockdown Active",
            description=(
                "Unusual join rate detected. New members are being auto-kicked/banned.\n\n"
                "Use `/raidmode False` to lift the lockdown manually, or wait 5 minutes."
            ),
            color=0xED4245,
        )
        if alert_ch:
            await alert_ch.send(embed=embed)
        await send_log(guild, "modactions", embed)

        await asyncio.sleep(300)  # auto-lift after 5 min
        if self._auto_locked.get(guild.id):
            self._auto_locked[guild.id] = False
            self._joins[guild.id].clear()
            if alert_ch:
                await alert_ch.send(embed=discord.Embed(
                    title=":white_check_mark: Raid Lockdown Lifted",
                    description="Auto-lifted after 5 minutes. Monitor for follow-up attacks.",
                    color=0x57F287,
                ))

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

            embed = discord.Embed(
                title=":warning: RAID MODE ENABLED",
                description=(
                    f"All channels have been locked.\n"
                    f"Locked: **{locked}** | Failed: **{failed}**\n\n"
                    f"Disable with `/raidmode False` when the raid is over."
                ),
                color=0xED4245,
            )
            await interaction.followup.send(embed=embed, ephemeral=False)

            # Announce in the alert channel if configured
            g_row = await db.get_guild(guild.id)
            if g_row and g_row.get("alert_channel_id"):
                alert_ch = guild.get_channel(g_row["alert_channel_id"])
                if alert_ch:
                    await alert_ch.send(embed=discord.Embed(
                        title=":warning: RAID MODE ACTIVE",
                        description="The server is currently in raid mode. All channels are locked.",
                        color=0xED4245,
                    ))
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

            embed = discord.Embed(
                title=":white_check_mark: Raid Mode Disabled",
                description=f"All channels have been unlocked.\nUnlocked: **{locked}** | Failed: **{failed}**",
                color=0x57F287,
            )
            await interaction.followup.send(embed=embed, ephemeral=False)

        action = "enabled" if enabled else "disabled"
        await db.log_mod_action(guild.id, f"raid_mode_{action}", interaction.user.id)
        await send_log(guild, "modactions", discord.Embed(
            description=f":warning: **{interaction.user}** {action} **raid mode** — {locked} channels affected",
            color=LOG_COLORS["modactions"],
        ))
        await send_log(guild, "general", discord.Embed(
            description=f":warning: **{interaction.user}** {action} **raid mode**",
            color=LOG_COLORS["general"],
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidMode(bot))
