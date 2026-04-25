"""
Logging cog — 10 independent log channels.
general / spam / member / edit / delete / voice / roles / channels / modactions / userwatch

Every audit-log-attributable event shows who performed the action and the reason.
Serious mod actions (timeout, ban, unban) are also forwarded to the owner's DMs.
"""

import asyncio
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

    async def _audit(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int | None = None,
        limit: int = 5,
    ) -> discord.AuditLogEntry | None:
        """Return the most recent audit entry for *action* matching *target_id*."""
        try:
            async for entry in guild.audit_logs(limit=limit, action=action):
                if target_id is None or (entry.target and entry.target.id == target_id):
                    return entry
        except discord.Forbidden:
            pass
        return None

    def _by(self, entry: discord.AuditLogEntry | None) -> str:
        if entry and entry.user:
            return f"{entry.user.mention} (`{entry.user.id}`)"
        return "*Unknown*"

    def _reason(self, entry: discord.AuditLogEntry | None) -> str | None:
        return entry.reason if entry and entry.reason else None

    def _owner_dispatch(self, embed: discord.Embed):
        self.bot.dispatch("owner_log", embed)

    # -----------------------------------------------------------------------
    # Member join / leave
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if not await self._feature_enabled(guild.id):
            return

        if member.bot:
            entry = await self._audit(guild, discord.AuditLogAction.bot_add, member.id)
            embed = discord.Embed(
                title="Bot Added",
                color=LOG_COLORS["member"],
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.add_field(name="Bot", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Added by", value=self._by(entry), inline=True)
            embed.set_footer(text="A new bot was added to the server")
            await send_log(guild, "member", embed)
            return

        account_age = (discord.utils.utcnow() - member.created_at).days
        embed = discord.Embed(
            title="Member Joined",
            color=LOG_COLORS["member"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Account Age", value=f"{account_age:,} day{'s' if account_age != 1 else ''}", inline=True)
        if account_age < 7:
            embed.add_field(name="⚠️ New Account", value=f"Only {account_age}d old", inline=False)
        embed.set_footer(text=f"Total members: {guild.member_count}")
        await send_log(guild, "member", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if not await self._feature_enabled(guild.id):
            return

        # Try to distinguish leave vs kick
        entry = await self._audit(guild, discord.AuditLogAction.kick, member.id)
        was_kicked = entry and entry.target.id == member.id

        roles = [r.mention for r in member.roles if r != guild.default_role]
        embed = discord.Embed(
            title="Member Kicked" if was_kicked else "Member Left",
            color=LOG_COLORS["spam"] if was_kicked else LOG_COLORS["delete"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(
            name="Joined",
            value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown",
            inline=True,
        )
        if was_kicked:
            embed.add_field(name="Kicked by", value=self._by(entry), inline=True)
            reason = self._reason(entry)
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles[:20]), inline=False)
        embed.set_footer(text=f"Total members: {guild.member_count}")
        await send_log(guild, "member", embed)

    # -----------------------------------------------------------------------
    # Message edit / delete
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

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not await self._feature_enabled(message.guild.id):
            return

        # Check if a mod deleted it (audit log)
        entry = await self._audit(message.guild, discord.AuditLogAction.message_delete, message.author.id)
        deleted_by_mod = entry and entry.user and entry.user.id != message.author.id

        embed = discord.Embed(
            title="Message Deleted",
            color=LOG_COLORS["delete"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        if deleted_by_mod:
            embed.add_field(name="Deleted by", value=self._by(entry), inline=True)
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
            embed.title = "Voice Joined"
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            await db.record_voice_join(member.guild.id, member.id, after.channel.id)

        elif before.channel is not None and after.channel is None:
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
            embed.title = "Voice Switched"
            embed.add_field(name="From", value=before.channel.mention, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)
            await db.record_voice_join(member.guild.id, member.id, after.channel.id)
        else:
            return  # Mute/deafen only, skip

        await send_log(member.guild, "voice", embed)

    # -----------------------------------------------------------------------
    # Member update — nickname / timeout / roles / screening
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if not await self._feature_enabled(guild.id):
            return

        # Membership screening passed
        if before.pending and not after.pending:
            embed = discord.Embed(
                title="Screening Passed",
                description=f"{after.mention} completed membership screening.",
                color=LOG_COLORS["member"],
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
            embed.add_field(
                name="Joined",
                value=f"<t:{int(after.joined_at.timestamp())}:R>" if after.joined_at else "Unknown",
                inline=True,
            )
            await send_log(guild, "member", embed)

        # Nickname change
        if before.nick != after.nick:
            entry = await self._audit(guild, discord.AuditLogAction.member_update, after.id)
            embed = discord.Embed(
                title="Nickname Changed",
                color=LOG_COLORS["roles"],
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
            embed.add_field(name="Changed by", value=self._by(entry), inline=True)
            embed.add_field(name="Before", value=before.nick or "*none*", inline=True)
            embed.add_field(name="After", value=after.nick or "*none*", inline=True)
            reason = self._reason(entry)
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
            await send_log(guild, "modactions", embed)

        # Timeout added / removed
        before_to = before.timed_out_until
        after_to = after.timed_out_until
        if before_to != after_to:
            entry = await self._audit(guild, discord.AuditLogAction.member_update, after.id)

            if after_to and (not before_to or after_to > discord.utils.utcnow()):
                duration_secs = (after_to - discord.utils.utcnow()).total_seconds()
                embed = discord.Embed(
                    title="Member Timed Out",
                    color=LOG_COLORS["spam"],
                    timestamp=discord.utils.utcnow(),
                )
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
                embed.add_field(name="By", value=self._by(entry), inline=True)
                embed.add_field(name="Until", value=f"<t:{int(after_to.timestamp())}:F>", inline=True)
                embed.add_field(name="Duration", value=format_duration(duration_secs), inline=True)
                reason = self._reason(entry)
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)
                await send_log(guild, "modactions", embed)
                self._owner_dispatch(embed)

            elif before_to and not after_to:
                embed = discord.Embed(
                    title="Timeout Removed",
                    color=LOG_COLORS["member"],
                    timestamp=discord.utils.utcnow(),
                )
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
                embed.add_field(name="Removed by", value=self._by(entry), inline=True)
                embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
                await send_log(guild, "modactions", embed)
                self._owner_dispatch(embed)

        # Role changes
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        added = after_roles - before_roles
        removed = before_roles - after_roles
        if not added and not removed:
            return

        entry = await self._audit(guild, discord.AuditLogAction.member_role_update, after.id)
        embed = discord.Embed(
            title="Roles Updated",
            color=LOG_COLORS["roles"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(after), icon_url=after.display_avatar.url)
        embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
        embed.add_field(name="Changed by", value=self._by(entry), inline=True)
        if added:
            embed.add_field(name="Added", value=" ".join(r.mention for r in added), inline=False)
        if removed:
            embed.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=False)
        reason = self._reason(entry)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        await send_log(guild, "roles", embed)

        # Forward to owner DMs if a high-privilege role was involved
        dangerous = {"administrator", "ban_members", "kick_members", "manage_guild", "manage_roles"}
        high_roles = {r for r in (added | removed) if any(
            getattr(r.permissions, p, False) for p in dangerous
        )}
        if high_roles:
            owner_embed = discord.Embed(
                title="⚠️ High-Privilege Role Change",
                color=0xED4245,
                timestamp=discord.utils.utcnow(),
            )
            owner_embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
            owner_embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
            owner_embed.add_field(name="Changed by", value=self._by(entry), inline=True)
            if added & high_roles:
                owner_embed.add_field(name="Granted", value=" ".join(r.name for r in added & high_roles), inline=False)
            if removed & high_roles:
                owner_embed.add_field(name="Revoked", value=" ".join(r.name for r in removed & high_roles), inline=False)
            self._owner_dispatch(owner_embed)

    # -----------------------------------------------------------------------
    # Global user update — avatar / username changes
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        for guild in self.bot.guilds:
            member = guild.get_member(after.id)
            if not member:
                continue
            if not await self._feature_enabled(guild.id):
                continue

            changes = []
            if before.name != after.name:
                changes.append(("Username", f"`{before.name}`", f"`{after.name}`"))
            if before.discriminator != after.discriminator:
                changes.append(("Discriminator", f"`#{before.discriminator}`", f"`#{after.discriminator}`"))
            if before.global_name != after.global_name:
                changes.append(("Display Name", before.global_name or "*none*", after.global_name or "*none*"))
            avatar_changed = before.avatar != after.avatar

            if not changes and not avatar_changed:
                continue

            embed = discord.Embed(
                title="User Updated",
                color=LOG_COLORS["member"],
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=True)
            for label, old, new in changes:
                embed.add_field(name=f"{label}: Before", value=old, inline=True)
                embed.add_field(name=f"{label}: After", value=new, inline=True)
            if avatar_changed:
                embed.add_field(name="Avatar", value="Avatar was changed", inline=False)
                if after.avatar:
                    embed.set_thumbnail(url=after.display_avatar.url)
            await send_log(guild, "member", embed)

    # -----------------------------------------------------------------------
    # Role changes log — role created / updated / deleted
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not await self._feature_enabled(role.guild.id):
            return
        entry = await self._audit(role.guild, discord.AuditLogAction.role_create, role.id)
        embed = discord.Embed(title="Role Created", color=LOG_COLORS["roles"], timestamp=discord.utils.utcnow())
        embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)", inline=True)
        embed.add_field(name="Created by", value=self._by(entry), inline=True)
        embed.add_field(name="Colour", value=str(role.colour), inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        await send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not await self._feature_enabled(role.guild.id):
            return
        entry = await self._audit(role.guild, discord.AuditLogAction.role_delete, role.id)
        embed = discord.Embed(title="Role Deleted", color=0xED4245, timestamp=discord.utils.utcnow())
        embed.add_field(name="Role", value=f"`{role.name}` (`{role.id}`)", inline=True)
        embed.add_field(name="Deleted by", value=self._by(entry), inline=True)
        await send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if not await self._feature_enabled(after.guild.id):
            return
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if before.colour != after.colour:
            changes.append(f"**Colour:** `{before.colour}` → `{after.colour}`")
        if before.hoist != after.hoist:
            changes.append(f"**Hoisted:** {'yes' if after.hoist else 'no'}")
        if before.mentionable != after.mentionable:
            changes.append(f"**Mentionable:** {'yes' if after.mentionable else 'no'}")
        # Permission diff
        before_perms = dict(before.permissions)
        after_perms = dict(after.permissions)
        granted = [p for p, v in after_perms.items() if v and not before_perms.get(p)]
        revoked = [p for p, v in before_perms.items() if v and not after_perms.get(p)]
        if granted:
            changes.append(f"**Perms granted:** {', '.join(f'`{p}`' for p in granted)}")
        if revoked:
            changes.append(f"**Perms revoked:** {', '.join(f'`{p}`' for p in revoked)}")
        if not changes:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.role_update, after.id)
        embed = discord.Embed(title="Role Updated", color=LOG_COLORS["roles"], timestamp=discord.utils.utcnow())
        embed.add_field(name="Role", value=f"{after.mention} (`{after.id}`)", inline=True)
        embed.add_field(name="Changed by", value=self._by(entry), inline=True)
        embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
        await send_log(after.guild, "roles", embed)

    # -----------------------------------------------------------------------
    # Channel updates log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not await self._feature_enabled(channel.guild.id):
            return
        entry = await self._audit(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        embed = discord.Embed(title="Channel Created", color=LOG_COLORS["channels"], timestamp=discord.utils.utcnow())
        embed.add_field(name="Name", value=channel.mention if hasattr(channel, "mention") else channel.name, inline=True)
        embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        embed.add_field(name="Created by", value=self._by(entry), inline=True)
        await send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not await self._feature_enabled(channel.guild.id):
            return
        entry = await self._audit(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        embed = discord.Embed(title="Channel Deleted", color=0xED4245, timestamp=discord.utils.utcnow())
        embed.add_field(name="Name", value=f"#{channel.name}", inline=True)
        embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        embed.add_field(name="Deleted by", value=self._by(entry), inline=True)
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
        if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
            changes.append(f"**Slowmode:** {before.slowmode_delay}s → {after.slowmode_delay}s")
        if hasattr(before, "nsfw") and before.nsfw != after.nsfw:
            changes.append(f"**NSFW:** {'enabled' if after.nsfw else 'disabled'}")
        if not changes:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.channel_update, after.id)
        embed = discord.Embed(title="Channel Updated", color=LOG_COLORS["channels"], timestamp=discord.utils.utcnow())
        embed.add_field(name="Channel", value=after.mention if hasattr(after, "mention") else f"#{after.name}", inline=True)
        embed.add_field(name="Changed by", value=self._by(entry), inline=True)
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
        await send_log(after.guild, "channels", embed)

    # -----------------------------------------------------------------------
    # Ban / unban — also forwarded to owner DMs
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if not await self._feature_enabled(guild.id):
            return
        entry = await self._audit(guild, discord.AuditLogAction.ban, user.id)
        embed = discord.Embed(
            title="Member Banned",
            color=LOG_COLORS["spam"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Banned by", value=self._by(entry), inline=True)
        reason = self._reason(entry)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
        await send_log(guild, "modactions", embed)
        self._owner_dispatch(embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if not await self._feature_enabled(guild.id):
            return
        entry = await self._audit(guild, discord.AuditLogAction.unban, user.id)
        embed = discord.Embed(
            title="Member Unbanned",
            color=LOG_COLORS["member"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Unbanned by", value=self._by(entry), inline=True)
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
        await send_log(guild, "modactions", embed)
        self._owner_dispatch(embed)

    # -----------------------------------------------------------------------
    # Invite log
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if not await self._feature_enabled(invite.guild.id):
            return
        embed = discord.Embed(
            title="Invite Created",
            color=LOG_COLORS["channels"],
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
        embed.add_field(name="Created by", value=f"{invite.inviter.mention}" if invite.inviter else "Unknown", inline=True)
        embed.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown", inline=True)
        max_uses = str(invite.max_uses) if invite.max_uses else "∞"
        expires = f"<t:{int(invite.expires_at.timestamp())}:R>" if invite.expires_at else "Never"
        embed.add_field(name="Max Uses", value=max_uses, inline=True)
        embed.add_field(name="Expires", value=expires, inline=True)
        await send_log(invite.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if not await self._feature_enabled(invite.guild.id):
            return
        embed = discord.Embed(
            title="Invite Deleted",
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
        embed.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown", inline=True)
        await send_log(invite.guild, "channels", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logging(bot))
