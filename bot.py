"""
Aegixa bot class — sets up intents, loads all cogs, syncs slash commands.
"""

import discord
from discord.ext import commands
import database as db
import logging
import os

log = logging.getLogger(__name__)

COGS = [
    "cogs.automod",
    "cogs.moderation",
    "cogs.logging_cog",
    "cogs.role_automation",
    "cogs.message_management",
    "cogs.setup_cog",
    "cogs.feature_control",
    "cogs.info_cog",
    "cogs.raid_mode",
    "cogs.temp_ban",
    "cogs.sticky",
    "cogs.reaction_roles",
    "cogs.giveaway",
    "cogs.invite_tracker",
]


class Aegixa(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.voice_states = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self):
        await db.init_db()
        log.info("Database initialised.")

        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as e:
                log.error("Failed to load cog %s: %s", cog, e)

        # Sync slash commands globally
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s).", len(synced))

    async def on_ready(self):
        log.info("Aegixa online as %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers | /about",
            )
        )

    async def on_guild_join(self, guild: discord.Guild):
        await db.ensure_guild(guild.id)
        log.info("Joined guild: %s (%s)", guild.name, guild.id)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        if isinstance(error, discord.app_commands.CheckFailure):
            # Permission check already sent its own response
            return
        log.error("App command error in %s: %s", interaction.command, error)
        msg = "An unexpected error occurred. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=discord.Embed(description=f":x: {msg}", color=0xED4245), ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=discord.Embed(description=f":x: {msg}", color=0xED4245), ephemeral=True
                )
        except Exception:
            pass


def create_bot() -> Aegixa:
    return Aegixa()
