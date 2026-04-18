"""
Reaction roles cog — users react to a message to get/remove a role.
/reactionrole add <message_id> <emoji> <role>
/reactionrole remove <message_id> <emoji>
/reactionrole list
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_staff
import logging

log = logging.getLogger(__name__)


class ReactionRoleGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="reactionrole", description="Manage reaction roles")

    @app_commands.command(name="add", description="Add a reaction role to a message")
    @app_commands.describe(message_id="ID of the message", emoji="Emoji to react with", role="Role to assign")
    @is_staff()
    async def rr_add(self, interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        try:
            mid = int(message_id)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid message ID."), ephemeral=True)

        # Verify message exists
        target_msg = None
        for ch in interaction.guild.text_channels:
            try:
                target_msg = await ch.fetch_message(mid)
                break
            except (discord.NotFound, discord.Forbidden):
                continue

        if not target_msg:
            return await interaction.response.send_message(embed=error_embed("Message not found."), ephemeral=True)

        await db.add_reaction_role(interaction.guild_id, mid, emoji, role.id)

        # Add the reaction to the message
        try:
            await target_msg.add_reaction(emoji)
        except discord.HTTPException:
            pass

        await interaction.response.send_message(embed=success_embed(
            f"Reaction role added: {emoji} on message `{mid}` → {role.mention}"
        ), ephemeral=True)

    @app_commands.command(name="remove", description="Remove a reaction role from a message")
    @app_commands.describe(message_id="ID of the message", emoji="Emoji to remove")
    @is_staff()
    async def rr_remove(self, interaction: discord.Interaction, message_id: str, emoji: str):
        try:
            mid = int(message_id)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid message ID."), ephemeral=True)

        removed = await db.remove_reaction_role(mid, emoji)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Reaction role removed."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("No reaction role found for that emoji on that message."), ephemeral=True)

    @app_commands.command(name="list", description="List all reaction roles in this server")
    @is_staff()
    async def rr_list(self, interaction: discord.Interaction):
        rules = await db.get_reaction_roles(interaction.guild_id)
        if not rules:
            return await interaction.response.send_message(embed=info_embed("No reaction roles configured."), ephemeral=True)
        embed = discord.Embed(title="Reaction Roles", color=0x5865F2)
        for r in rules:
            role = interaction.guild.get_role(r["role_id"])
            embed.add_field(
                name=f"{r['emoji']} — Message `{r['message_id']}`",
                value=role.mention if role else f"Role ID: {r['role_id']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(ReactionRoleGroup())

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return
        if not await db.get_feature(payload.guild_id, "reaction_roles"):
            return

        emoji_str = str(payload.emoji)
        rr = await db.get_reaction_role(payload.message_id, emoji_str)
        if not rr:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        role = guild.get_role(rr["role_id"])
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="[Aegixa] Reaction role")
            except discord.HTTPException as e:
                log.warning("Reaction role add failed: %s", e)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return
        if not await db.get_feature(payload.guild_id, "reaction_roles"):
            return

        emoji_str = str(payload.emoji)
        rr = await db.get_reaction_role(payload.message_id, emoji_str)
        if not rr:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        role = guild.get_role(rr["role_id"])
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="[Aegixa] Reaction role removed")
            except discord.HTTPException as e:
                log.warning("Reaction role remove failed: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
