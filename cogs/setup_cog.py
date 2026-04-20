"""
Setup cog — /setup staff, /setup logs, /setup update
Full initial setup in two commands, then individual updates after.
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import error_embed, success_embed, info_embed
from utils.permissions import is_admin
from config import LOG_TYPES
from cogs.logging_cog import send_log


class SetupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="setup", description="Configure Aegixa for this server")

    # ------------------------------------------------------------------
    # /setup staff
    # ------------------------------------------------------------------

    @app_commands.command(name="staff", description="Configure staff roles, config roles, alert channel, and excluded channels")
    @is_admin()
    async def setup_staff(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=info_embed(
                "Setup — Staff & Config",
                "Use the subcommands below to configure roles and channels:\n\n"
                "• `/setup update staffrole add/remove <role>` — staff roles\n"
                "• `/setup update configrole add/remove <role>` — config/dashboard roles\n"
                "• `/setup update alertrole add/remove <role>` — alert ping roles\n"
                "• `/setup update alertchannel <channel>` — automod alert channel\n"
                "• `/setup update excludechannel add/remove <channel>` — channels excluded from automod\n\n"
                "Use `/setup logs` to set up logging channels.",
            ),
            ephemeral=True,
        )
        await db.ensure_guild(interaction.guild_id)

    # ------------------------------------------------------------------
    # /setup logs
    # ------------------------------------------------------------------

    @app_commands.command(name="logs", description="Set all 8 log channels at once")
    @app_commands.describe(
        general="General / command log",
        spam="Spam / automod log",
        member="Member join/leave log",
        edit="Message edit log",
        delete="Message delete log",
        voice="Voice activity log",
        roles="Role changes log",
        channels="Channel updates log",
    )
    @is_admin()
    async def setup_logs(
        self,
        interaction: discord.Interaction,
        general: discord.TextChannel = None,
        spam: discord.TextChannel = None,
        member: discord.TextChannel = None,
        edit: discord.TextChannel = None,
        delete: discord.TextChannel = None,
        voice: discord.TextChannel = None,
        roles: discord.TextChannel = None,
        channels: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)
        await db.ensure_guild(interaction.guild_id)

        mapping = {
            "general": general,
            "spam": spam,
            "member": member,
            "edit": edit,
            "delete": delete,
            "voice": voice,
            "roles": roles,
            "channels": channels,
        }

        lines = []
        for log_type, channel in mapping.items():
            if channel:
                await db.set_log_channel(interaction.guild_id, log_type, channel.id)
                lines.append(f"**{log_type}** → {channel.mention}")

        if lines:
            await interaction.followup.send(embed=success_embed("Log channels configured:\n" + "\n".join(lines)), ephemeral=True)
        else:
            await interaction.followup.send(embed=info_embed("No channels provided — nothing changed."), ephemeral=True)

    # ------------------------------------------------------------------
    # /setup update — individual setting changes
    # ------------------------------------------------------------------

    @app_commands.command(name="update", description="Update an individual Aegixa setting")
    @app_commands.describe(
        setting="Which setting to change",
        action="add or remove (for role/channel lists)",
        role="Role (for role settings)",
        channel="Channel (for channel settings)",
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="staffrole", value="staffrole"),
        app_commands.Choice(name="configrole", value="configrole"),
        app_commands.Choice(name="alertrole", value="alertrole"),
        app_commands.Choice(name="alertchannel", value="alertchannel"),
        app_commands.Choice(name="excludechannel", value="excludechannel"),
        app_commands.Choice(name="announcement_channel", value="announcement_channel"),
        app_commands.Choice(name="announcement_role", value="announcement_role"),
        app_commands.Choice(name="update_channel", value="update_channel"),
    ])
    @app_commands.choices(action=[
        app_commands.Choice(name="add", value="add"),
        app_commands.Choice(name="remove", value="remove"),
        app_commands.Choice(name="set", value="set"),
        app_commands.Choice(name="clear", value="clear"),
    ])
    @is_admin()
    async def setup_update(
        self,
        interaction: discord.Interaction,
        setting: str,
        action: str = "set",
        role: discord.Role = None,
        channel: discord.TextChannel = None,
    ):
        await db.ensure_guild(interaction.guild_id)

        if setting == "staffrole":
            if not role:
                return await interaction.response.send_message(embed=error_embed("Provide a role."), ephemeral=True)
            if action == "add":
                await db.add_guild_role(interaction.guild_id, role.id, "staff")
                await interaction.response.send_message(embed=success_embed(f"{role.mention} added as a staff role."), ephemeral=True)
            else:
                await db.remove_guild_role(interaction.guild_id, role.id, "staff")
                await interaction.response.send_message(embed=success_embed(f"{role.mention} removed from staff roles."), ephemeral=True)

        elif setting == "configrole":
            if not role:
                return await interaction.response.send_message(embed=error_embed("Provide a role."), ephemeral=True)
            if action == "add":
                await db.add_guild_role(interaction.guild_id, role.id, "config")
                await interaction.response.send_message(embed=success_embed(f"{role.mention} added as a config (dashboard) role."), ephemeral=True)
            else:
                await db.remove_guild_role(interaction.guild_id, role.id, "config")
                await interaction.response.send_message(embed=success_embed(f"{role.mention} removed from config roles."), ephemeral=True)

        elif setting == "alertrole":
            if not role:
                return await interaction.response.send_message(embed=error_embed("Provide a role."), ephemeral=True)
            if action == "add":
                await db.add_guild_role(interaction.guild_id, role.id, "alert")
                await interaction.response.send_message(embed=success_embed(f"{role.mention} will be pinged on automod alerts."), ephemeral=True)
            else:
                await db.remove_guild_role(interaction.guild_id, role.id, "alert")
                await interaction.response.send_message(embed=success_embed(f"{role.mention} removed from alert roles."), ephemeral=True)

        elif setting == "alertchannel":
            if action == "clear":
                await db.set_guild_field(interaction.guild_id, "alert_channel_id", None)
                await interaction.response.send_message(embed=success_embed("Alert channel cleared."), ephemeral=True)
            elif channel:
                await db.set_guild_field(interaction.guild_id, "alert_channel_id", channel.id)
                await interaction.response.send_message(embed=success_embed(f"Alert channel set to {channel.mention}."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Provide a channel."), ephemeral=True)

        elif setting == "excludechannel":
            if not channel:
                return await interaction.response.send_message(embed=error_embed("Provide a channel."), ephemeral=True)
            if action == "add":
                await db.add_excluded_channel(interaction.guild_id, channel.id)
                await interaction.response.send_message(embed=success_embed(f"{channel.mention} excluded from automod."), ephemeral=True)
            else:
                await db.remove_excluded_channel(interaction.guild_id, channel.id)
                await interaction.response.send_message(embed=success_embed(f"{channel.mention} removed from exclusions."), ephemeral=True)

        elif setting == "announcement_channel":
            if action == "clear":
                await db.set_guild_field(interaction.guild_id, "announcement_channel_id", None)
                await interaction.response.send_message(embed=success_embed("Announcement channel cleared."), ephemeral=True)
            elif channel:
                await db.set_guild_field(interaction.guild_id, "announcement_channel_id", channel.id)
                await interaction.response.send_message(embed=success_embed(f"Announcement channel set to {channel.mention}."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Provide a channel."), ephemeral=True)

        elif setting == "announcement_role":
            if action == "clear":
                await db.set_guild_field(interaction.guild_id, "announcement_role_id", None)
                await interaction.response.send_message(embed=success_embed("Announcement role cleared."), ephemeral=True)
            elif role:
                await db.set_guild_field(interaction.guild_id, "announcement_role_id", role.id)
                await interaction.response.send_message(embed=success_embed(f"{role.mention} will be pinged on announcements."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Provide a role."), ephemeral=True)

        elif setting == "update_channel":
            if action == "clear":
                await db.set_guild_field(interaction.guild_id, "update_channel_id", None)
                await interaction.response.send_message(embed=success_embed("Update channel cleared — bot updates will be sent to the server owner via DM."), ephemeral=True)
            elif channel:
                await db.set_guild_field(interaction.guild_id, "update_channel_id", channel.id)
                await interaction.response.send_message(embed=success_embed(f"Bot update announcements will be posted to {channel.mention}."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Provide a channel."), ephemeral=True)

        await send_log(interaction.guild, "general", discord.Embed(
            description=f":gear: **{interaction.user}** updated setting `{setting}` ({action})",
            color=0x5865F2,
        ))


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(SetupGroup())


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
