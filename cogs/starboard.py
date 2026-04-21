"""
Starboard — repost highly-reacted messages to a dedicated channel.
/starboard setup / threshold / emoji / toggle
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)

DEFAULT_EMOJI = "⭐"


def _star_embed(message: discord.Message, star_count: int, emoji: str) -> discord.Embed:
    embed = discord.Embed(
        description=message.content or "",
        color=0xFFAC33,
        timestamp=message.created_at,
    )
    embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
    embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=True)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.set_footer(text=f"{emoji} {star_count}")

    if message.attachments:
        img = next((a for a in message.attachments if a.content_type and a.content_type.startswith("image/")), None)
        if img:
            embed.set_image(url=img.url)

    return embed


class StarboardGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="starboard", description="Configure the starboard")

    @app_commands.command(name="setup", description="Set the starboard channel")
    @app_commands.describe(channel="Channel to post starred messages in")
    @is_admin()
    async def sb_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.set_starboard_config(interaction.guild_id, channel_id=channel.id, enabled=1)
        await interaction.response.send_message(
            embed=success_embed(f"Starboard channel set to {channel.mention}."), ephemeral=True
        )

    @app_commands.command(name="threshold", description="Set how many reactions needed to star a message")
    @app_commands.describe(count="Minimum reactions (1–25)")
    @is_admin()
    async def sb_threshold(self, interaction: discord.Interaction, count: int):
        if not 1 <= count <= 25:
            return await interaction.response.send_message(
                embed=error_embed("Threshold must be between 1 and 25."), ephemeral=True
            )
        await db.set_starboard_config(interaction.guild_id, threshold=count)
        await interaction.response.send_message(
            embed=success_embed(f"Starboard threshold set to **{count}** reactions."), ephemeral=True
        )

    @app_commands.command(name="emoji", description="Set the reaction emoji used for starring")
    @app_commands.describe(emoji="Emoji to watch (default: ⭐)")
    @is_admin()
    async def sb_emoji(self, interaction: discord.Interaction, emoji: str):
        await db.set_starboard_config(interaction.guild_id, emoji=emoji.strip())
        await interaction.response.send_message(
            embed=success_embed(f"Starboard emoji set to {emoji.strip()}."), ephemeral=True
        )

    @app_commands.command(name="toggle", description="Enable or disable the starboard")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def sb_toggle(self, interaction: discord.Interaction, enabled: bool):
        cfg = await db.get_starboard_config(interaction.guild_id)
        if enabled and not cfg["channel_id"]:
            return await interaction.response.send_message(
                embed=error_embed("Set a channel first with `/starboard setup`."), ephemeral=True
            )
        await db.set_starboard_config(interaction.guild_id, enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"Starboard {state}."), ephemeral=True
        )

    @app_commands.command(name="status", description="Show current starboard configuration")
    @is_staff()
    async def sb_status(self, interaction: discord.Interaction):
        cfg = await db.get_starboard_config(interaction.guild_id)
        channel = interaction.guild.get_channel(cfg["channel_id"]) if cfg["channel_id"] else None
        lines = [
            f"**Channel:** {channel.mention if channel else '*not set*'}",
            f"**Threshold:** {cfg['threshold']} reactions",
            f"**Emoji:** {cfg['emoji']}",
            f"**Status:** {'enabled' if cfg['enabled'] else 'disabled'}",
        ]
        embed = discord.Embed(title="Starboard Config", description="\n".join(lines), color=0xFFAC33)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(StarboardGroup())

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        cfg = await db.get_starboard_config(payload.guild_id)
        if not cfg["enabled"] or not cfg["channel_id"]:
            return

        # Check emoji matches
        emoji_str = str(payload.emoji)
        if emoji_str != cfg["emoji"]:
            return

        # Don't star messages in the starboard channel itself
        if payload.channel_id == cfg["channel_id"]:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        # Count reactions matching the configured emoji
        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == cfg["emoji"]:
                count = reaction.count
                break

        if count < cfg["threshold"]:
            return

        starboard_ch = guild.get_channel(cfg["channel_id"])
        if not starboard_ch:
            return

        existing = await db.get_starboard_entry(payload.guild_id, payload.message_id)

        if existing:
            # Update the existing starboard post's footer count
            try:
                sb_msg = await starboard_ch.fetch_message(existing["starboard_message_id"])
                if sb_msg.embeds:
                    updated_embed = sb_msg.embeds[0]
                    updated_embed.set_footer(text=f"{cfg['emoji']} {count}")
                    await sb_msg.edit(embed=updated_embed)
            except discord.HTTPException:
                pass
        else:
            # Post new starboard entry
            embed = _star_embed(message, count, cfg["emoji"])
            try:
                sb_msg = await starboard_ch.send(embed=embed)
                await db.set_starboard_entry(payload.guild_id, payload.message_id, sb_msg.id)
            except discord.HTTPException as e:
                log.warning("Starboard post failed (%s): %s", payload.guild_id, e)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        cfg = await db.get_starboard_config(payload.guild_id)
        if not cfg["enabled"] or not cfg["channel_id"]:
            return
        if str(payload.emoji) != cfg["emoji"]:
            return

        existing = await db.get_starboard_entry(payload.guild_id, payload.message_id)
        if not existing:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == cfg["emoji"]:
                count = reaction.count
                break

        starboard_ch = guild.get_channel(cfg["channel_id"])
        if not starboard_ch:
            return

        try:
            sb_msg = await starboard_ch.fetch_message(existing["starboard_message_id"])
            if count < cfg["threshold"]:
                await sb_msg.delete()
                await db.delete_starboard_entry(payload.guild_id, payload.message_id)
            elif sb_msg.embeds:
                updated_embed = sb_msg.embeds[0]
                updated_embed.set_footer(text=f"{cfg['emoji']} {count}")
                await sb_msg.edit(embed=updated_embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Starboard(bot))
