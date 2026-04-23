"""
Owner audit log — DMs the bot owner for every slash command, button press,
automated bot action, and server join/leave.

Other cogs signal this via: bot.dispatch("owner_log", embed)
"""

import os
import asyncio
import discord
from discord.ext import commands
import logging

log = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

if not OWNER_ID:
    logging.getLogger(__name__).warning(
        "BOT_OWNER_ID is not set — owner DMs are disabled. "
        "Set it in Railway env vars to your Discord user ID."
    )

# Colour scheme for log types
C_CMD      = 0x5865F2   # slash command / button
C_AUTO     = 0xFEE75C   # automated bot action (automod, levelup, etc.)
C_SERVER   = 0x57F287   # guild join / leave
C_MOD      = 0xED4245   # moderation action
C_ERROR    = 0xFF0000   # command error

# These commands dispatch their own rich embeds via bot.dispatch("owner_log", embed)
# so the generic on_interaction handler skips them to avoid duplicates.
_MOD_COMMANDS = frozenset({
    "ban", "unban", "kick", "mute", "unmute", "purge",
    "nick", "block", "unblock", "lock", "unlock", "threshold",
    "warn add", "warn remove",
})


def _guild_str(guild: discord.Guild | None) -> str:
    if not guild:
        return "*(DM)*"
    return f"{guild.name} (`{guild.id}`)"


def _user_str(user: discord.User | discord.Member | None) -> str:
    if not user:
        return "Unknown"
    return f"{user} (`{user.id}`)"


class OwnerLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._queue: asyncio.Queue = asyncio.Queue()
        self._send_task: asyncio.Task | None = None

    async def cog_load(self):
        self._send_task = asyncio.create_task(self._sender_loop())
        log.info("OwnerLog loaded — owner ID: %s", OWNER_ID or "NOT SET")

    def cog_unload(self):
        if self._send_task:
            self._send_task.cancel()

    # -----------------------------------------------------------------------
    # Background sender — drains the queue, one DM per event
    # with a small delay between sends to respect rate limits
    # -----------------------------------------------------------------------

    async def _sender_loop(self):
        await self.bot.wait_until_ready()
        while True:
            embed = await self._queue.get()
            await self._dm_owner(embed)
            await asyncio.sleep(0.6)   # ~100 DMs/min max — well within limits
            self._queue.task_done()

    async def _dm_owner(self, embed: discord.Embed):
        if not OWNER_ID:
            return
        try:
            owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)
            await owner.send(embed=embed)
            log.debug("OwnerLog: DM sent to %s", owner)
        except discord.Forbidden:
            log.error(
                "OwnerLog: FORBIDDEN — bot cannot DM user %s. "
                "Make sure you share a server with the bot and DMs are enabled.",
                OWNER_ID,
            )
        except Exception as e:
            log.warning("OwnerLog: could not DM owner: %s", e)

    def _enqueue(self, embed: discord.Embed):
        """Thread-safe enqueue from any cog via bot.dispatch('owner_log', embed)."""
        try:
            self._queue.put_nowait(embed)
        except asyncio.QueueFull:
            log.warning("OwnerLog queue full — dropping event")

    # -----------------------------------------------------------------------
    # Slash commands & interactions
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        itype = interaction.type

        # ---- Slash command ----
        if itype == discord.InteractionType.application_command:
            cmd_name = interaction.command.qualified_name if interaction.command else "unknown"

            # Mod commands dispatch their own rich embeds — skip to avoid duplicates
            if cmd_name in _MOD_COMMANDS:
                return

            # Build a readable options string
            opts = ""
            if interaction.data and interaction.data.get("options"):
                parts = []
                for opt in interaction.data["options"]:
                    v = opt.get("value", "")
                    if v != "":
                        parts.append(f"`{opt['name']}: {v}`")
                if parts:
                    opts = " ".join(parts)

            embed = discord.Embed(
                title="🔧 Slash Command",
                color=C_CMD,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Command", value=f"`/{cmd_name}`", inline=True)
            embed.add_field(name="User", value=_user_str(interaction.user), inline=True)
            embed.add_field(name="Server", value=_guild_str(interaction.guild), inline=True)
            if interaction.channel:
                embed.add_field(name="Channel", value=f"#{interaction.channel}", inline=True)
            if opts:
                embed.add_field(name="Options", value=opts[:200], inline=False)
            self._enqueue(embed)

        # ---- Button click ----
        elif itype == discord.InteractionType.component:
            cid = interaction.data.get("custom_id", "")
            # Filter to known Aegixa buttons only (avoid spamming for every reaction-role etc.)
            if cid.startswith("aegixa:"):
                label_map = {
                    "aegixa:open_ticket":  "🎫 Opened ticket",
                    "aegixa:close_ticket": "🔒 Closed ticket",
                    "aegixa:claim_ticket": "🙋 Claimed ticket",
                }
                label = label_map.get(cid, f"Button: `{cid}`")
                embed = discord.Embed(
                    title="🖱️ Button Pressed",
                    color=C_CMD,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Action", value=label, inline=True)
                embed.add_field(name="User", value=_user_str(interaction.user), inline=True)
                embed.add_field(name="Server", value=_guild_str(interaction.guild), inline=True)
                if interaction.channel:
                    embed.add_field(name="Channel", value=f"#{interaction.channel}", inline=True)
                self._enqueue(embed)

    # -----------------------------------------------------------------------
    # Server join / leave
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        embed = discord.Embed(
            title="✅ Bot Added to Server",
            color=C_SERVER,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Server", value=f"**{guild.name}**", inline=True)
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        self._enqueue(embed)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        embed = discord.Embed(
            title="❌ Bot Removed from Server",
            color=C_MOD,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Server", value=f"**{guild.name}**", inline=True)
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        self._enqueue(embed)

    # -----------------------------------------------------------------------
    # App command errors
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        # Don't log CheckFailure — those are normal permission denials
        if isinstance(error, discord.app_commands.CheckFailure):
            return

        cmd_name = interaction.command.qualified_name if interaction.command else "unknown"
        embed = discord.Embed(
            title="⚠️ Command Error",
            color=C_ERROR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Command", value=f"`/{cmd_name}`", inline=True)
        embed.add_field(name="User", value=_user_str(interaction.user), inline=True)
        embed.add_field(name="Server", value=_guild_str(interaction.guild), inline=True)
        embed.add_field(name="Error", value=str(error)[:500], inline=False)
        self._enqueue(embed)

    # -----------------------------------------------------------------------
    # Generic hook — any cog can call bot.dispatch("owner_log", embed)
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_owner_log(self, embed: discord.Embed):
        self._enqueue(embed)

    # -----------------------------------------------------------------------
    # Test command — owner only, verifies DM delivery end-to-end
    # -----------------------------------------------------------------------

    @discord.app_commands.command(name="ownertest", description="Send a test DM to the bot owner (owner only)")
    async def ownertest(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("This command is restricted to the bot owner.", ephemeral=True)
        embed = discord.Embed(
            title="✅ Owner Log Test",
            description="If you can read this, the owner DM system is working correctly.",
            color=C_SERVER,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Queue size before", value=str(self._queue.qsize()), inline=True)
        self._enqueue(embed)
        await interaction.response.send_message(
            f"Test DM queued! Check your DMs. (BOT_OWNER_ID = `{OWNER_ID}`)", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerLog(bot))
