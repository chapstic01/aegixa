"""
Logging cog — 8 independent log channels.
general / spam / member / edit / delete / voice / roles / channels
"""

import discord
from discord.ext import commands
from datetime import datetime, timezone
import database as db
from config import LOG_COLORS
from utils.helpers import format_duration
import logging

log = logging.getLogger(__name__)


async def send_log(guild: discord.Guild, log_type: str, embed: discord.Embed):
    ch_id = await db.get_log_channel(guild.id, log_type)
    if not ch_id:
        return
    ch = guild.get_channel(ch_id)
    if not ch:
        return
    try:
        await ch.send(embed=embed)
    except discord.HTTPException as e:
        log.warning("Log send failed (%s/%s): %s", guild.id, log_type, e)


class Logging(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _feature_enabled(self, guild_id: int) -> bool:
        return await db.get_feature(guild_id, "logging")

    # -----------------------------------------------------------------------
    # Member log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not await self._feature_enabled(member.guild.id):
            return
        embed = discord.Embed(
            title="Member Joined",
            color=LOG_COLORS["member"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        embed.set_footer(text=f"Total members: {member.guild.member_count}")
        await send_log(member.guild, "member", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not await self._feature_enabled(member.guild.id):
            return
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        embed = discord.Embed(
            title="Member Left",
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="User", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown", inline=True)
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles[:20]), inline=False)
        embed.set_footer(text=f"Total members: {member.guild.member_count}")
        await send_log(member.guild, "member", embed)

    # -----------------------------------------------------------------------
    # Message edit log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot:
            return
        if before.content == after.content:
            return
        if not await self._feature_enabled(after.guild.id):
            return
        embed = discord.Embed(
            title="Message Edited",
            color=LOG_COLORS["edit"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(after.author), icon_url=after.author.display_avatar.url)
        embed.add_field(name="Author", value=f"{after.author.mention} (`{after.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=after.channel.mention, inline=True)
        embed.add_field(name="Before", value=(before.content[:1000] or "*(empty)*"), inline=False)
        embed.add_field(name="After", value=(after.content[:1000] or "*(empty)*"), inline=False)
        embed.add_field(name="Jump", value=f"[Go to message]({after.jump_url})", inline=False)
        await send_log(after.guild, "edit", embed)

    # -----------------------------------------------------------------------
    # Message delete log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not await self._feature_enabled(message.guild.id):
            return
        embed = discord.Embed(
            title="Message Deleted",
            color=LOG_COLORS["delete"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        if message.content:
            embed.add_field(name="Content", value=message.content[:1000], inline=False)
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(a.filename for a in message.attachments),
                inline=False,
            )
        await send_log(message.guild, "delete", embed)

    # -----------------------------------------------------------------------
    # Voice log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if not await self._feature_enabled(member.guild.id):
            return

        embed = discord.Embed(color=LOG_COLORS["voice"], timestamp=discord.utils.utcnow())
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)

        if before.channel is None and after.channel is not None:
            # Joined
            embed.title = "Voice Joined"
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            await db.record_voice_join(member.guild.id, member.id, after.channel.id)

        elif before.channel is not None and after.channel is None:
            # Left
            embed.title = "Voice Left"
            embed.add_field(name="Channel", value=before.channel.mention, inline=True)
            join_row = await db.pop_voice_join(member.guild.id, member.id)
            if join_row:
                joined_at = datetime.fromisoformat(join_row["joined_at"])
                if joined_at.tzinfo is None:
                    joined_at = joined_at.replace(tzinfo=timezone.utc)
                duration = (discord.utils.utcnow() - joined_at).total_seconds()
                embed.add_field(name="Duration", value=format_duration(duration), inline=True)

        elif before.channel != after.channel:
            # Switched
            embed.title = "Voice Switched"
            embed.add_field(name="From", value=before.channel.mention, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)
            await db.record_voice_join(member.guild.id, member.id, after.channel.id)
        else:
            return  # Mute/deafen only, skip

        await send_log(member.guild, "voice", embed)

    # -----------------------------------------------------------------------
    # Role changes log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not await self._feature_enabled(after.guild.id):
            return

        before_roles = set(before.roles)
        after_roles = set(after.roles)

        added = after_roles - before_roles
        removed = before_roles - after_roles

        if not added and not removed:
            return

        embed = discord.Embed(
            title="Roles Updated",
            color=LOG_COLORS["roles"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(after), icon_url=after.display_avatar.url)
        embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
        if added:
            embed.add_field(name="Added", value=" ".join(r.mention for r in added), inline=True)
        if removed:
            embed.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=True)
        await send_log(after.guild, "roles", embed)

    # -----------------------------------------------------------------------
    # Channel updates log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not await self._feature_enabled(channel.guild.id):
            return
        embed = discord.Embed(title="Channel Created", color=LOG_COLORS["channels"], timestamp=discord.utils.utcnow())
        embed.add_field(name="Name", value=channel.mention if hasattr(channel, 'mention') else channel.name, inline=True)
        embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        await send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not await self._feature_enabled(channel.guild.id):
            return
        embed = discord.Embed(title="Channel Deleted", color=0xED4245, timestamp=discord.utils.utcnow())
        embed.add_field(name="Name", value=f"#{channel.name}", inline=True)
        embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        await send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if not await self._feature_enabled(after.guild.id):
            return

        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if hasattr(before, "topic") and before.topic != after.topic:
            changes.append(f"**Topic:** `{before.topic or 'none'}` → `{after.topic or 'none'}`")

        if not changes:
            # Check permission overwrites
            if before.overwrites != after.overwrites:
                changes.append("Permission overwrites changed.")

        if not changes:
            return

        embed = discord.Embed(title="Channel Updated", color=LOG_COLORS["channels"], timestamp=discord.utils.utcnow())
        embed.add_field(name="Channel", value=after.mention if hasattr(after, 'mention') else f"#{after.name}", inline=True)
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
        await send_log(after.guild, "channels", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logging(bot))
