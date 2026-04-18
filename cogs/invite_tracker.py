"""
Invite tracker cog — logs which invite link a new member used to join.
Caches invite uses on startup and after each join.
"""

import discord
from discord.ext import commands
import database as db
from cogs.logging_cog import send_log
from config import LOG_COLORS
import logging

log = logging.getLogger(__name__)


class InviteTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _cache_invites(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
            for inv in invites:
                await db.upsert_invite(
                    guild.id,
                    inv.code,
                    inv.inviter.id if inv.inviter else None,
                    inv.uses or 0,
                )
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        await db.upsert_invite(
            invite.guild.id,
            invite.code,
            invite.inviter.id if invite.inviter else None,
            invite.uses or 0,
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        await db.delete_invite(invite.guild.id, invite.code)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not await db.get_feature(member.guild.id, "invite_tracking"):
            return

        guild = member.guild
        cached = await db.get_invites(guild.id)
        cached_map = {r["invite_code"]: r["uses"] for r in cached}

        used_invite = None
        used_inviter = None

        try:
            current_invites = await guild.invites()
        except discord.Forbidden:
            return

        for inv in current_invites:
            old_uses = cached_map.get(inv.code, 0)
            if (inv.uses or 0) > old_uses:
                used_invite = inv.code
                used_inviter = inv.inviter
                break

        # Refresh cache
        for inv in current_invites:
            await db.upsert_invite(
                guild.id,
                inv.code,
                inv.inviter.id if inv.inviter else None,
                inv.uses or 0,
            )

        if used_invite:
            inviter_str = f"**{used_inviter}** (`{used_inviter.id}`)" if used_inviter else "Unknown"
            await send_log(guild, "member", discord.Embed(
                description=f":link: **{member}** joined using invite `{used_invite}` — created by {inviter_str}",
                color=LOG_COLORS["member"],
                timestamp=discord.utils.utcnow(),
            ))


async def setup(bot: commands.Bot):
    await bot.add_cog(InviteTracker(bot))
