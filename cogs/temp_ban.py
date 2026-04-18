"""
Temp-ban cog — ban with automatic unban after a duration.
Background task checks every 30 seconds for expired bans.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import timezone
import database as db
from utils.helpers import resolve_member, parse_duration, format_duration, success_embed, error_embed
from utils.permissions import is_staff
from cogs.logging_cog import send_log
from config import LOG_COLORS
import logging

log = logging.getLogger(__name__)


class TempBan(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_temp_bans.start()

    def cog_unload(self):
        self.check_temp_bans.cancel()

    @tasks.loop(seconds=30)
    async def check_temp_bans(self):
        from datetime import datetime
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        expired = await db.get_expired_temp_bans(now)
        for ban in expired:
            guild = self.bot.get_guild(ban["guild_id"])
            if not guild:
                await db.remove_temp_ban(ban["id"])
                continue
            try:
                user = await self.bot.fetch_user(ban["user_id"])
                await guild.unban(user, reason="[Aegixa] Temp-ban expired")
                await send_log(guild, "modactions", discord.Embed(
                    description=f":unlock: Temp-ban expired — **{user}** (`{user.id}`) has been unbanned automatically.",
                    color=LOG_COLORS["modactions"],
                ))
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                log.warning("Failed to unban %s: %s", ban["user_id"], e)
            finally:
                await db.remove_temp_ban(ban["id"])

    @check_temp_bans.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="tempban", description="Ban a member for a set duration, then automatically unban")
    @app_commands.describe(member="Username, display name, or mention", duration="Duration e.g. 1h, 7d, 30m", reason="Reason")
    @is_staff()
    async def tempban(self, interaction: discord.Interaction, member: str, duration: str, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)

        secs = parse_duration(duration)
        if not secs:
            return await interaction.followup.send(embed=error_embed("Invalid duration. Use e.g. `1h`, `7d`, `30m`."), ephemeral=True)

        from datetime import datetime, timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=secs)
        expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")

        try:
            await target.send(embed=discord.Embed(
                description=f":hammer: You have been temporarily banned from **{interaction.guild.name}** for **{format_duration(secs)}**.\nReason: {reason}",
                color=0xED4245,
            ))
        except discord.Forbidden:
            pass

        await interaction.guild.ban(target, reason=f"[Aegixa Temp-ban] {reason}", delete_message_days=0)
        await db.add_temp_ban(interaction.guild_id, target.id, interaction.user.id, reason, expires_str)
        await db.log_mod_action(interaction.guild_id, "tempban", interaction.user.id, target.id, reason, f"duration:{secs}s")

        await interaction.followup.send(embed=success_embed(
            f"**{target}** has been temp-banned for **{format_duration(secs)}**.\nThey will be automatically unbanned <t:{int(expires_at.timestamp())}:R>.\nReason: {reason}"
        ), ephemeral=True)

        await send_log(interaction.guild, "modactions", discord.Embed(
            description=f":hammer: **{interaction.user}** temp-banned **{target}** for **{format_duration(secs)}** — {reason}",
            color=LOG_COLORS["modactions"],
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(TempBan(bot))
