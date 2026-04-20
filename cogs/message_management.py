"""
Message management cog — send/edit plain text and embeds as the bot, announcements.
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import error_embed, success_embed
from utils.permissions import is_staff
import logging

log = logging.getLogger(__name__)


class SayModal(discord.ui.Modal, title="Send Message"):
    content = discord.ui.TextInput(
        label="Message Content",
        style=discord.TextStyle.long,
        placeholder="Enter the message to send...",
        required=True,
        max_length=2000,
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.send(self.content.value)
        await interaction.response.send_message(embed=success_embed(f"Message sent to {self.channel.mention}."), ephemeral=True)


class EmbedModal(discord.ui.Modal, title="Send Embed"):
    embed_title = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.long, required=False, max_length=4096)
    color = discord.ui.TextInput(label="Color (hex)", placeholder="#5865F2", required=False, max_length=7)
    footer = discord.ui.TextInput(label="Footer", required=False, max_length=2048)
    image_url = discord.ui.TextInput(label="Image URL", required=False, max_length=500)

    def __init__(self, channel: discord.TextChannel, message: discord.Message = None):
        super().__init__()
        self.channel = channel
        self.existing_message = message
        if message and message.embeds:
            e = message.embeds[0]
            if e.title:
                self.embed_title.default = e.title
            if e.description:
                self.description.default = e.description
            if e.color:
                self.color.default = f"#{e.color.value:06X}"
            if e.footer:
                self.footer.default = e.footer.text
            if e.image:
                self.image_url.default = e.image.url

    async def on_submit(self, interaction: discord.Interaction):
        color_val = 0x5865F2
        if self.color.value:
            try:
                color_val = int(self.color.value.strip().lstrip("#"), 16)
            except ValueError:
                pass

        embed = discord.Embed(
            title=self.embed_title.value or None,
            description=self.description.value or None,
            color=color_val,
        )
        if self.footer.value:
            embed.set_footer(text=self.footer.value)
        if self.image_url.value:
            embed.set_image(url=self.image_url.value)

        if self.existing_message:
            await self.existing_message.edit(embed=embed)
            await interaction.response.send_message(embed=success_embed("Embed updated."), ephemeral=True)
        else:
            await self.channel.send(embed=embed)
            await interaction.response.send_message(embed=success_embed(f"Embed sent to {self.channel.mention}."), ephemeral=True)


class EditTextModal(discord.ui.Modal, title="Edit Message"):
    content = discord.ui.TextInput(
        label="New Content",
        style=discord.TextStyle.long,
        required=True,
        max_length=2000,
    )

    def __init__(self, message: discord.Message):
        super().__init__()
        self.target_message = message
        self.content.default = message.content

    async def on_submit(self, interaction: discord.Interaction):
        await self.target_message.edit(content=self.content.value)
        await interaction.response.send_message(embed=success_embed("Message updated."), ephemeral=True)


class AnnouncementModal(discord.ui.Modal, title="Send Announcement"):
    ann_title = discord.ui.TextInput(label="Title", required=True, max_length=256)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.long, required=True, max_length=4096)
    color = discord.ui.TextInput(label="Color (hex)", placeholder="#5865F2", required=False, max_length=7)

    def __init__(self, bot: commands.Bot, target_guild_id: int = None):
        super().__init__()
        self.bot = bot
        self.target_guild_id = target_guild_id

    async def on_submit(self, interaction: discord.Interaction):
        color_val = 0x5865F2
        if self.color.value:
            try:
                color_val = int(self.color.value.strip().lstrip("#"), 16)
            except ValueError:
                pass

        embed = discord.Embed(
            title=self.ann_title.value,
            description=self.description.value,
            color=color_val,
        )
        embed.set_footer(text=f"Announcement from {interaction.guild.name}")

        sent = 0
        failed = 0

        if self.target_guild_id:
            guilds = [self.bot.get_guild(self.target_guild_id)]
        else:
            guilds = self.bot.guilds

        for guild in guilds:
            if not guild:
                continue
            g_row = await db.get_guild(guild.id)
            if not g_row or not g_row.get("announcement_channel_id"):
                continue
            ch = guild.get_channel(g_row["announcement_channel_id"])
            if not ch:
                continue
            ann_role_id = g_row.get("announcement_role_id")
            content = None
            if ann_role_id:
                role = guild.get_role(ann_role_id)
                if role:
                    content = role.mention
            try:
                await ch.send(content=content, embed=embed)
                sent += 1
            except discord.HTTPException:
                failed += 1

        await interaction.response.send_message(
            embed=success_embed(f"Announcement sent to **{sent}** server(s)." + (f" Failed: {failed}" if failed else "")),
            ephemeral=True,
        )


class EmbedGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="embed", description="Send and edit embeds as the bot")

    @app_commands.command(name="send", description="Send an embed as the bot")
    @app_commands.describe(channel="Channel to send the embed in")
    @is_staff()
    async def embed_send(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        channel = channel or interaction.channel
        await interaction.response.send_modal(EmbedModal(channel))

    @app_commands.command(name="edit", description="Edit a previously sent bot message or embed by ID")
    @app_commands.describe(message_id="ID of the bot message to edit")
    @is_staff()
    async def embed_edit(self, interaction: discord.Interaction, message_id: str):
        try:
            mid = int(message_id)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid message ID."), ephemeral=True)

        target = None
        for channel in interaction.guild.text_channels:
            try:
                target = await channel.fetch_message(mid)
                break
            except (discord.NotFound, discord.Forbidden):
                continue

        if not target:
            return await interaction.response.send_message(embed=error_embed("Message not found."), ephemeral=True)
        if target.author != interaction.client.user:
            return await interaction.response.send_message(embed=error_embed("That message wasn't sent by me."), ephemeral=True)

        if target.embeds:
            await interaction.response.send_modal(EmbedModal(target.channel, message=target))
        else:
            await interaction.response.send_modal(EditTextModal(target))


class AnnounceGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="announce", description="Send announcements to servers")

    @app_commands.command(name="send", description="Send an announcement embed to all servers or a specific one")
    @app_commands.describe(guild_id="Optional: target a specific server by ID (owner only)")
    @is_staff()
    async def announce_send(self, interaction: discord.Interaction, guild_id: str = None):
        import os
        owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
        target_gid = None
        if guild_id:
            if interaction.user.id != owner_id:
                return await interaction.response.send_message(embed=error_embed("Only the bot owner can target specific servers."), ephemeral=True)
            try:
                target_gid = int(guild_id)
            except ValueError:
                return await interaction.response.send_message(embed=error_embed("Invalid guild ID."), ephemeral=True)
        await interaction.response.send_modal(AnnouncementModal(interaction.client, target_gid))


class MessageManagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(EmbedGroup())
        self.bot.tree.add_command(AnnounceGroup())

    @app_commands.command(name="say", description="Send a plain-text message as the bot")
    @app_commands.describe(channel="Channel to send to (defaults to current)")
    @is_staff()
    async def say(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        channel = channel or interaction.channel
        await interaction.response.send_modal(SayModal(channel))

    @app_commands.command(name="welcome", description="Send the Aegixa welcome/setup message in this channel")
    @app_commands.describe(channel="Channel to send to (defaults to current)")
    @is_staff()
    async def welcome(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        from config import PREMIUM_URL, SUPPORT_SERVER
        target = channel or interaction.channel
        embed = discord.Embed(
            title="Thanks for adding Aegixa!",
            description=(
                "Aegixa is a full-featured security and moderation bot.\n\n"
                "**Get started:**\n"
                "• `/setup staff` — set your staff role\n"
                "• `/setup logs` — configure log channels\n"
                "• `/setup update` — configure anti-raid thresholds\n"
                "• `/help` — browse all commands\n\n"
                "**Automod is on by default.** Use `/filters list` to review.\n\n"
                f"[Get Premium]({PREMIUM_URL})  |  [Support]({SUPPORT_SERVER})"
            ),
            color=0x5865F2,
        )
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
        embed.set_footer(text="Use /about for more info")
        await target.send(embed=embed)
        await interaction.response.send_message(
            embed=success_embed(f"Welcome message sent to {target.mention}."), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageManagement(bot))
