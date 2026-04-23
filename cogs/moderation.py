"""
Moderation commands — all require Manage Messages or a configured staff role.
Member inputs are free-text (username, display name, or mention).
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta, timezone, datetime
import database as db
from utils.helpers import resolve_member, format_duration, parse_duration, error_embed, success_embed, info_embed
from utils.permissions import is_staff
from config import LOG_COLORS
import logging

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

async def _send_modlog(bot: commands.Bot, guild: discord.Guild, embed: discord.Embed):
    """Send to modactions log channel and forward to owner DM."""
    ch_id = await db.get_log_channel(guild.id, "modactions")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.HTTPException:
                pass
    bot.dispatch("owner_log", embed)


async def _send_general(bot: commands.Bot, guild: discord.Guild, description: str, color: int = LOG_COLORS["general"]):
    ch_id = await db.get_log_channel(guild.id, "general")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch:
            try:
                embed = discord.Embed(description=description, color=color, timestamp=discord.utils.utcnow())
                await ch.send(embed=embed)
            except discord.HTTPException:
                pass


def _mod_embed(
    title: str,
    color: int,
    moderator: discord.Member | discord.User,
    target: discord.Member | discord.User | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    embed.set_author(name=str(moderator), icon_url=moderator.display_avatar.url)
    if target:
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Moderator", value=moderator.mention, inline=True)
    return embed


# ---------------------------------------------------------------------------
# Warn group
# ---------------------------------------------------------------------------

class WarnGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="warn", description="Warning management")

    @app_commands.command(name="add", description="Issue a warning to a member")
    @app_commands.describe(member="Username, display name, or mention", reason="Reason for the warning")
    @is_staff()
    async def warn_add(self, interaction: discord.Interaction, member: str, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)

        warn_id = await db.add_warning(interaction.guild_id, target.id, interaction.user.id, reason)
        warnings = await db.get_warnings(interaction.guild_id, target.id)

        try:
            await target.send(embed=discord.Embed(
                description=f":warning: You have been warned in **{interaction.guild.name}**.\nReason: {reason}\nTotal warnings: **{len(warnings)}**",
                color=0xFEE75C,
            ))
        except discord.Forbidden:
            pass

        await interaction.followup.send(embed=success_embed(
            f"**{target.display_name}** has been warned (ID: `{warn_id}`).\nReason: {reason} | Total: **{len(warnings)}**"
        ), ephemeral=True)

        embed = _mod_embed("⚠️ Member Warned", 0xFEE75C, interaction.user, target)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Warning ID", value=f"`#{warn_id}`", inline=True)
        embed.add_field(name="Total Warnings", value=str(len(warnings)), inline=True)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)
        await db.log_mod_action(interaction.guild_id, "warn", interaction.user.id, target.id, reason)

        # Auto-ban threshold check
        settings = await db.get_guild_settings(interaction.guild_id)
        threshold = settings.get("auto_ban_threshold", 0)
        if threshold and len(warnings) >= threshold:
            try:
                await target.send(embed=discord.Embed(
                    description=f":hammer: You have been automatically banned from **{interaction.guild.name}** for reaching {threshold} warnings.",
                    color=0xED4245,
                ))
            except discord.Forbidden:
                pass
            await interaction.guild.ban(target, reason=f"[Aegixa Auto-ban] Reached {threshold} warnings", delete_message_days=0)
            auto_embed = _mod_embed("🔨 Auto-ban: Warning Threshold", 0xED4245, interaction.client.user, target)
            auto_embed.add_field(name="Threshold", value=str(threshold), inline=True)
            auto_embed.add_field(name="Total Warnings", value=str(len(warnings)), inline=True)
            auto_embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
            await _send_modlog(interaction.client, interaction.guild, auto_embed)
            await interaction.followup.send(embed=discord.Embed(
                description=f":hammer: **{target.display_name}** has been automatically banned for reaching **{threshold}** warnings.",
                color=0xED4245,
            ), ephemeral=True)

    @app_commands.command(name="view", description="View warnings for a member")
    @app_commands.describe(member="Username, display name, or mention")
    @is_staff()
    async def warn_view(self, interaction: discord.Interaction, member: str):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)

        warnings = await db.get_warnings(interaction.guild_id, target.id)
        if not warnings:
            return await interaction.followup.send(embed=info_embed(f"No warnings for {target.display_name}"), ephemeral=True)

        embed = discord.Embed(title=f"Warnings — {target.display_name}", color=0xFEE75C)
        embed.set_thumbnail(url=target.display_avatar.url)
        for w in warnings[:10]:
            mod = interaction.guild.get_member(w["moderator_id"])
            mod_name = str(mod) if mod else f"ID:{w['moderator_id']}"
            embed.add_field(
                name=f"ID {w['id']} — {w['created_at'][:10]}",
                value=f"Reason: {w['reason'] or 'None'}\nBy: {mod_name}",
                inline=False,
            )
        if len(warnings) > 10:
            embed.set_footer(text=f"Showing 10/{len(warnings)} warnings")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Remove a warning by ID")
    @app_commands.describe(warning_id="The warning ID to remove")
    @is_staff()
    async def warn_remove(self, interaction: discord.Interaction, warning_id: int):
        removed = await db.remove_warning(interaction.guild_id, warning_id)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Warning `{warning_id}` removed."), ephemeral=True)
            embed = discord.Embed(title="🗑️ Warning Removed", color=0x57F287, timestamp=discord.utils.utcnow())
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="Warning ID", value=f"`#{warning_id}`", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
            await _send_modlog(interaction.client, interaction.guild, embed)
        else:
            await interaction.response.send_message(embed=error_embed(f"Warning `{warning_id}` not found."), ephemeral=True)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(WarnGroup())

    # -----------------------------------------------------------------------
    # Ban
    # -----------------------------------------------------------------------

    @app_commands.command(name="ban", description="Ban a member from the server")
    @app_commands.describe(member="Username, display name, or mention", reason="Reason for ban", delete_days="Days of messages to delete (0-7)")
    @is_staff()
    async def ban(self, interaction: discord.Interaction, member: str, reason: str = "No reason provided", delete_days: int = 0):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        if target.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            return await interaction.followup.send(embed=error_embed("You cannot ban someone with an equal or higher role."), ephemeral=True)

        try:
            await target.send(embed=discord.Embed(
                description=f":hammer: You have been banned from **{interaction.guild.name}**.\nReason: {reason}",
                color=0xED4245,
            ))
        except discord.Forbidden:
            pass

        delete_days = max(0, min(7, delete_days))
        await interaction.guild.ban(target, reason=f"{interaction.user}: {reason}", delete_message_days=delete_days)
        await interaction.followup.send(embed=success_embed(f"**{target}** has been banned.\nReason: {reason}"), ephemeral=True)

        embed = _mod_embed("🔨 Member Banned", 0xED4245, interaction.user, target)
        embed.add_field(name="Reason", value=reason, inline=False)
        if delete_days:
            embed.add_field(name="Messages Deleted", value=f"{delete_days} day(s)", inline=True)
        embed.add_field(name="Account Age", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True)
        if target.joined_at:
            embed.add_field(name="Was Member For", value=f"<t:{int(target.joined_at.timestamp())}:R>", inline=True)
        roles = [r.mention for r in target.roles if r != interaction.guild.default_role]
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles[:10]), inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Unban
    # -----------------------------------------------------------------------

    @app_commands.command(name="unban", description="Unban a user by ID")
    @app_commands.describe(user_id="The user's Discord ID", reason="Reason for unban")
    @is_staff()
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(user_id)
        except ValueError:
            return await interaction.followup.send(embed=error_embed("Please provide a valid numeric user ID."), ephemeral=True)
        try:
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(user, reason=f"{interaction.user}: {reason}")
            await interaction.followup.send(embed=success_embed(f"**{user}** has been unbanned."), ephemeral=True)

            embed = discord.Embed(title="🔓 Member Unbanned", color=0x57F287, timestamp=discord.utils.utcnow())
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
            await _send_modlog(interaction.client, interaction.guild, embed)
        except discord.NotFound:
            await interaction.followup.send(embed=error_embed("That user is not banned or doesn't exist."), ephemeral=True)

    # -----------------------------------------------------------------------
    # Kick
    # -----------------------------------------------------------------------

    @app_commands.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(member="Username, display name, or mention", reason="Reason for kick")
    @is_staff()
    async def kick(self, interaction: discord.Interaction, member: str, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        if target.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            return await interaction.followup.send(embed=error_embed("You cannot kick someone with an equal or higher role."), ephemeral=True)

        try:
            await target.send(embed=discord.Embed(
                description=f":boot: You have been kicked from **{interaction.guild.name}**.\nReason: {reason}",
                color=0xED4245,
            ))
        except discord.Forbidden:
            pass

        joined_ts = int(target.joined_at.timestamp()) if target.joined_at else None
        roles = [r.mention for r in target.roles if r != interaction.guild.default_role]
        await target.kick(reason=f"{interaction.user}: {reason}")
        await interaction.followup.send(embed=success_embed(f"**{target}** has been kicked.\nReason: {reason}"), ephemeral=True)

        embed = _mod_embed("👢 Member Kicked", 0xE67E22, interaction.user, target)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Account Age", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True)
        if joined_ts:
            embed.add_field(name="Joined", value=f"<t:{joined_ts}:R>", inline=True)
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles[:10]), inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Mute (timeout)
    # -----------------------------------------------------------------------

    @app_commands.command(name="mute", description="Timeout a member (mute)")
    @app_commands.describe(member="Username, display name, or mention", duration="Duration e.g. 10m, 2h, 1d", reason="Reason")
    @is_staff()
    async def mute(self, interaction: discord.Interaction, member: str, duration: str = "10m", reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        secs = parse_duration(duration)
        if not secs:
            return await interaction.followup.send(embed=error_embed("Invalid duration. Use format like `10m`, `2h`, `1d`."), ephemeral=True)
        secs = min(secs, 2419200)  # Discord max 28 days
        until = discord.utils.utcnow() + timedelta(seconds=secs)
        await target.timeout(until, reason=f"{interaction.user}: {reason}")
        await db.add_mute_record(interaction.guild_id, target.id, interaction.user.id, reason, secs)
        await interaction.followup.send(embed=success_embed(
            f"**{target}** has been muted for **{format_duration(secs)}**.\nReason: {reason}"
        ), ephemeral=True)

        embed = _mod_embed("🔇 Member Muted", 0xFEE75C, interaction.user, target)
        embed.add_field(name="Duration", value=format_duration(secs), inline=True)
        embed.add_field(name="Expires", value=f"<t:{int(until.timestamp())}:F>", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Unmute
    # -----------------------------------------------------------------------

    @app_commands.command(name="unmute", description="Remove timeout from a member")
    @app_commands.describe(member="Username, display name, or mention", reason="Reason")
    @is_staff()
    async def unmute(self, interaction: discord.Interaction, member: str, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        await target.timeout(None, reason=f"{interaction.user}: {reason}")
        await interaction.followup.send(embed=success_embed(f"**{target}**'s mute has been removed."), ephemeral=True)

        embed = _mod_embed("🔊 Timeout Removed", 0x57F287, interaction.user, target)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Lock / Unlock
    # -----------------------------------------------------------------------

    @app_commands.command(name="lock", description="Lock the current channel (block @everyone from sending)")
    @app_commands.describe(reason="Reason for locking")
    @is_staff()
    async def lock(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        channel = interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
        await interaction.response.send_message(embed=discord.Embed(
            description=f":lock: This channel has been locked.\nReason: {reason}",
            color=0xED4245,
        ))

        embed = discord.Embed(title="🔒 Channel Locked", color=0xED4245, timestamp=discord.utils.utcnow())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    @app_commands.command(name="unlock", description="Unlock the current channel")
    @app_commands.describe(reason="Reason for unlocking")
    @is_staff()
    async def unlock(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        channel = interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
        await interaction.response.send_message(embed=discord.Embed(
            description=f":unlock: This channel has been unlocked.\nReason: {reason}",
            color=0x57F287,
        ))

        embed = discord.Embed(title="🔓 Channel Unlocked", color=0x57F287, timestamp=discord.utils.utcnow())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Slowmode
    # -----------------------------------------------------------------------

    @app_commands.command(name="slowmode", description="Set slowmode for the current channel")
    @app_commands.describe(seconds="Slowmode in seconds (0 to disable, max 21600)")
    @is_staff()
    async def slowmode(self, interaction: discord.Interaction, seconds: int):
        seconds = max(0, min(21600, seconds))
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await interaction.response.send_message(embed=success_embed("Slowmode disabled."), ephemeral=True)
            await _send_general(interaction.client, interaction.guild,
                f":stopwatch: **{interaction.user}** disabled slowmode in {interaction.channel.mention}")
        else:
            await interaction.response.send_message(embed=success_embed(f"Slowmode set to **{format_duration(seconds)}**."), ephemeral=True)
            await _send_general(interaction.client, interaction.guild,
                f":stopwatch: **{interaction.user}** set slowmode to {format_duration(seconds)} in {interaction.channel.mention}")

    # -----------------------------------------------------------------------
    # Purge
    # -----------------------------------------------------------------------

    @app_commands.command(name="purge", description="Bulk delete messages (1–100)")
    @app_commands.describe(amount="Number of messages to delete (1-100)", reason="Reason for purge")
    @is_staff()
    async def purge(self, interaction: discord.Interaction, amount: int, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        amount = max(1, min(100, amount))
        deleted = await interaction.channel.purge(limit=amount, reason=f"{interaction.user}: {reason}")
        await interaction.followup.send(embed=success_embed(f"Deleted **{len(deleted)}** messages."), ephemeral=True)

        embed = discord.Embed(title="🗑️ Messages Purged", color=0x99AAB5, timestamp=discord.utils.utcnow())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Deleted", value=str(len(deleted)), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Nick
    # -----------------------------------------------------------------------

    @app_commands.command(name="nick", description="Change or reset a member's nickname")
    @app_commands.describe(member="Username, display name, or mention", nickname="New nickname (leave empty to reset)")
    @is_staff()
    async def nick(self, interaction: discord.Interaction, member: str, nickname: str = ""):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        old = target.display_name
        new = nickname or None
        await target.edit(nick=new)
        action = f"reset to `{target.name}`" if not new else f"changed to `{new}`"
        await interaction.followup.send(embed=success_embed(f"Nickname for **{old}** {action}."), ephemeral=True)

        embed = _mod_embed("✏️ Nickname Changed", LOG_COLORS["roles"], interaction.user, target)
        embed.add_field(name="Before", value=f"`{old}`", inline=True)
        embed.add_field(name="After", value=f"`{new or target.name}`", inline=True)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    # -----------------------------------------------------------------------
    # Role color
    # -----------------------------------------------------------------------

    @app_commands.command(name="rolecolor", description="Change a role's colour by hex code")
    @app_commands.describe(role="The role to recolour", color="Hex colour code e.g. #FF5733")
    @is_staff()
    async def rolecolor(self, interaction: discord.Interaction, role: discord.Role, color: str):
        color = color.strip().lstrip("#")
        try:
            int_color = int(color, 16)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid hex colour. Use format `#FF5733`."), ephemeral=True)
        await role.edit(color=discord.Color(int_color))
        await interaction.response.send_message(embed=success_embed(f"**{role.name}** colour set to `#{color.upper()}`."), ephemeral=True)
        await _send_general(interaction.client, interaction.guild,
            f":art: **{interaction.user}** changed **{role.name}** colour to `#{color.upper()}`")

    # -----------------------------------------------------------------------
    # Voice move
    # -----------------------------------------------------------------------

    @app_commands.command(name="vcmove", description="Move a member to a different voice channel")
    @app_commands.describe(member="Username, display name, or mention", channel="Destination voice channel")
    @is_staff()
    async def vcmove(self, interaction: discord.Interaction, member: str, channel: discord.VoiceChannel):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        if not target.voice:
            return await interaction.followup.send(embed=error_embed(f"**{target.display_name}** is not in a voice channel."), ephemeral=True)
        await target.move_to(channel)
        await interaction.followup.send(embed=success_embed(f"**{target.display_name}** moved to **{channel.name}**."), ephemeral=True)
        await _send_general(interaction.client, interaction.guild,
            f":headphones: **{interaction.user}** moved **{target}** to **{channel.name}**")

    # -----------------------------------------------------------------------
    # Role toggle
    # -----------------------------------------------------------------------

    @app_commands.command(name="roletoggle", description="Add or remove a role from a member")
    @app_commands.describe(member="Username, display name, or mention", role="The role to toggle")
    @is_staff()
    async def roletoggle(self, interaction: discord.Interaction, member: str, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."), ephemeral=True)
        if role in target.roles:
            await target.remove_roles(role)
            await interaction.followup.send(embed=success_embed(f"Removed **{role.name}** from **{target.display_name}**."), ephemeral=True)
            await _send_general(interaction.client, interaction.guild,
                f":minus: **{interaction.user}** removed **{role.name}** from **{target}**")
        else:
            await target.add_roles(role)
            await interaction.followup.send(embed=success_embed(f"Added **{role.name}** to **{target.display_name}**."), ephemeral=True)
            await _send_general(interaction.client, interaction.guild,
                f":plus: **{interaction.user}** added **{role.name}** to **{target}**")

    # -----------------------------------------------------------------------
    # Block / Unblock
    # -----------------------------------------------------------------------

    @app_commands.command(name="block", description="Block a member from chatting in this channel")
    @app_commands.describe(member="Username, display name, or mention", reason="Reason for block")
    @is_staff()
    async def block(self, interaction: discord.Interaction, member: str, reason: str = "No reason provided"):
        await interaction.response.defer()
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."))
        channel = interaction.channel
        overwrite = channel.overwrites_for(target)
        overwrite.send_messages = False
        overwrite.add_reactions = False
        await channel.set_permissions(target, overwrite=overwrite, reason=reason)
        await db.add_channel_block(interaction.guild_id, channel.id, target.id, interaction.user.id)
        await interaction.followup.send(embed=discord.Embed(
            description=f":no_entry: **{target.display_name}** has been blocked from chatting in this channel.\nReason: {reason}",
            color=0xED4245,
        ).set_footer(text=f"Actioned by {interaction.user}"))

        embed = _mod_embed("🚫 Member Blocked", 0xED4245, interaction.user, target)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    @app_commands.command(name="unblock", description="Restore a blocked member's chat access in this channel")
    @app_commands.describe(member="Username, display name, or mention")
    @is_staff()
    async def unblock(self, interaction: discord.Interaction, member: str):
        await interaction.response.defer()
        target = await resolve_member(interaction.guild, member)
        if not target:
            return await interaction.followup.send(embed=error_embed(f"Member `{member}` not found."))
        channel = interaction.channel
        overwrite = channel.overwrites_for(target)
        overwrite.send_messages = None
        overwrite.add_reactions = None
        if overwrite.is_empty():
            await channel.set_permissions(target, overwrite=None)
        else:
            await channel.set_permissions(target, overwrite=overwrite)
        await db.remove_channel_block(interaction.guild_id, channel.id, target.id)
        await interaction.followup.send(embed=success_embed(f"**{target.display_name}** can now chat in this channel again."))

        embed = _mod_embed("✅ Member Unblocked", 0x57F287, interaction.user, target)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)

    @app_commands.command(name="threshold", description="Set the warning count that triggers an auto-ban (0 to disable)")
    @app_commands.describe(count="Number of warnings before auto-ban (0 = disabled)")
    @is_staff()
    async def threshold(self, interaction: discord.Interaction, count: int):
        count = max(0, count)
        await db.set_guild_setting(interaction.guild_id, "auto_ban_threshold", count)
        if count == 0:
            await interaction.response.send_message(embed=success_embed("Auto-ban on warnings disabled."), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=success_embed(f"Members will be auto-banned after **{count}** warnings."), ephemeral=True
            )

        embed = discord.Embed(title="⚙️ Auto-ban Threshold Updated", color=0x5865F2, timestamp=discord.utils.utcnow())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Threshold", value=str(count) if count else "Disabled", inline=True)
        embed.add_field(name="Set By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Server", value=f"{interaction.guild.name} (`{interaction.guild_id}`)", inline=False)
        await _send_modlog(interaction.client, interaction.guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
