"""
Server stats voice channels — auto-updating member/bot/channel counts.
/stats setup  /stats remove  /stats refresh
"""

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin
import logging

log = logging.getLogger(__name__)

STAT_LABELS = {
    "members": "👥 Members: {value}",
    "online":  "🟢 Online: {value}",
    "bots":    "🤖 Bots: {value}",
    "channels":"📢 Channels: {value}",
}


def _stat_value(guild: discord.Guild, stat_type: str) -> str:
    if stat_type == "members":
        return str(sum(1 for m in guild.members if not m.bot))
    if stat_type == "online":
        return str(sum(1 for m in guild.members if not m.bot and m.status != discord.Status.offline))
    if stat_type == "bots":
        return str(sum(1 for m in guild.members if m.bot))
    if stat_type == "channels":
        return str(len(guild.channels))
    return "?"


class StatsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="stats", description="Server statistics display channels")

    @app_commands.command(name="setup", description="Create voice channels showing live server stats")
    @app_commands.describe(
        members="Show human member count",
        online="Show online member count",
        bots="Show bot count",
        channels="Show channel count",
    )
    @is_admin()
    async def stats_setup(
        self,
        interaction: discord.Interaction,
        members: bool = True,
        online: bool = False,
        bots: bool = False,
        channels: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # Find or create a Stats category
        category = discord.utils.get(guild.categories, name="📊 Server Stats")
        if not category:
            try:
                category = await guild.create_category("📊 Server Stats")
            except discord.HTTPException as e:
                return await interaction.followup.send(embed=error_embed(f"Could not create category: {e}"), ephemeral=True)

        # No one can connect/speak in stat channels
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True),
            guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True),
        }

        await db.delete_stats_channels(guild.id)

        created = []
        for stat_type, enabled in [("members", members), ("online", online), ("bots", bots), ("channels", channels)]:
            if not enabled:
                continue
            label = STAT_LABELS[stat_type].format(value=_stat_value(guild, stat_type))
            try:
                ch = await guild.create_voice_channel(label, category=category, overwrites=overwrites)
                await db.set_stats_channel(guild.id, stat_type, ch.id)
                created.append(stat_type)
            except discord.HTTPException as e:
                log.warning("Could not create stats channel %s: %s", stat_type, e)

        if created:
            await interaction.followup.send(
                embed=success_embed(f"Created stat channels: {', '.join(created)}. They update every 10 minutes."),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(embed=error_embed("No stat types selected."), ephemeral=True)

    @app_commands.command(name="remove", description="Remove all server stat channels")
    @is_admin()
    async def stats_remove(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channels = await db.get_stats_channels(interaction.guild_id)
        if not channels:
            return await interaction.followup.send(embed=info_embed("No stats channels configured.", ""), ephemeral=True)

        for ch_id in channels.values():
            ch = interaction.guild.get_channel(ch_id)
            if ch:
                try:
                    await ch.delete(reason="Stats channels removed")
                except discord.HTTPException:
                    pass

        await db.delete_stats_channels(interaction.guild_id)
        await interaction.followup.send(embed=success_embed("Stats channels removed."), ephemeral=True)

    @app_commands.command(name="refresh", description="Force-update stat channels now")
    @is_admin()
    async def stats_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await _update_guild_stats(interaction.guild)
        await interaction.followup.send(embed=success_embed("Stats channels refreshed."), ephemeral=True)


async def _update_guild_stats(guild: discord.Guild):
    channels = await db.get_stats_channels(guild.id)
    for stat_type, ch_id in channels.items():
        ch = guild.get_channel(ch_id)
        if not ch:
            continue
        label = STAT_LABELS[stat_type].format(value=_stat_value(guild, stat_type))
        if ch.name != label:
            try:
                await ch.edit(name=label)
            except discord.HTTPException:
                pass


class ServerStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(StatsGroup())

    async def cog_load(self):
        self.update_stats.start()

    def cog_unload(self):
        self.update_stats.cancel()

    @tasks.loop(minutes=10)
    async def update_stats(self):
        for guild in self.bot.guilds:
            try:
                await _update_guild_stats(guild)
            except Exception as e:
                log.warning("Stats update failed for %s: %s", guild.id, e)

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await _update_guild_stats(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await _update_guild_stats(member.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerStats(bot))
