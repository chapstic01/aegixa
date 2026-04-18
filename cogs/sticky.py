"""
Sticky messages cog — re-posts a message every time someone sends in the channel.
/sticky set <content>
/sticky clear
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_staff
import logging

log = logging.getLogger(__name__)


class StickyGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="sticky", description="Manage sticky messages")

    @app_commands.command(name="set", description="Set a sticky message in the current channel")
    @app_commands.describe(content="The message to keep pinned at the bottom")
    @is_staff()
    async def sticky_set(self, interaction: discord.Interaction, content: str):
        await db.set_sticky(interaction.guild_id, interaction.channel_id, content)
        await interaction.response.send_message(embed=success_embed(f"Sticky message set in {interaction.channel.mention}."), ephemeral=True)

        # Post it immediately
        msg = await interaction.channel.send(embed=discord.Embed(description=f"📌 {content}", color=0xFEE75C))
        await db.update_sticky_message_id(interaction.guild_id, interaction.channel_id, msg.id)

    @app_commands.command(name="clear", description="Remove the sticky message from the current channel")
    @is_staff()
    async def sticky_clear(self, interaction: discord.Interaction):
        sticky = await db.get_sticky(interaction.guild_id, interaction.channel_id)
        if not sticky:
            return await interaction.response.send_message(embed=info_embed("No sticky message in this channel."), ephemeral=True)

        # Delete old sticky message
        if sticky.get("last_message_id"):
            try:
                old_msg = await interaction.channel.fetch_message(sticky["last_message_id"])
                await old_msg.delete()
            except discord.HTTPException:
                pass

        await db.remove_sticky(interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message(embed=success_embed("Sticky message removed."), ephemeral=True)

    @app_commands.command(name="view", description="Show the current sticky message for this channel")
    async def sticky_view(self, interaction: discord.Interaction):
        sticky = await db.get_sticky(interaction.guild_id, interaction.channel_id)
        if not sticky:
            return await interaction.response.send_message(embed=info_embed("No sticky message in this channel."), ephemeral=True)
        await interaction.response.send_message(embed=discord.Embed(
            title="Current Sticky Message",
            description=sticky["content"],
            color=0xFEE75C,
        ), ephemeral=True)


class Sticky(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(StickyGroup())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not await db.get_feature(message.guild.id, "sticky_messages"):
            return

        sticky = await db.get_sticky(message.guild.id, message.channel.id)
        if not sticky:
            return

        # Don't re-post if the last message is already the sticky
        if sticky.get("last_message_id") and message.id == sticky["last_message_id"]:
            return

        # Delete old sticky
        if sticky.get("last_message_id"):
            try:
                old = await message.channel.fetch_message(sticky["last_message_id"])
                await old.delete()
            except discord.HTTPException:
                pass

        # Repost
        try:
            new_msg = await message.channel.send(embed=discord.Embed(
                description=f"📌 {sticky['content']}", color=0xFEE75C
            ))
            await db.update_sticky_message_id(message.guild.id, message.channel.id, new_msg.id)
        except discord.HTTPException as e:
            log.warning("Failed to repost sticky in %s: %s", message.channel.id, e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Sticky(bot))
