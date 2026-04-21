"""
Join/Leave announcements and Autoroles.
/joinmsg setup / toggle / test
/leavemsg setup / toggle / test
/autorole add / remove / list
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)

VARIABLES_HELP = (
    "`{mention}` — @mention the user\n"
    "`{user}` — display name\n"
    "`{server}` — server name\n"
    "`{count}` — total member count\n"
    "`{id}` — user ID"
)


def _format(template: str, member: discord.Member) -> str:
    return (
        template
        .replace("{mention}", member.mention)
        .replace("{user}", member.display_name)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count))
        .replace("{id}", str(member.id))
    )


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class JoinMsgModal(discord.ui.Modal, title="Set Join Message"):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="Welcome {mention} to **{server}**! 👋\n\nVariables: {mention} {user} {server} {count} {id}",
        max_length=1000,
    )

    def __init__(self, current: str = None):
        super().__init__()
        if current:
            self.message.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await db.set_join_leave_config(interaction.guild_id, join_message=self.message.value)
        embed = success_embed(f"Join message updated.\n\n**Preview:**\n{_format(self.message.value, interaction.user)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class LeaveMsgModal(discord.ui.Modal, title="Set Leave Message"):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="**{user}** has left the server.\n\nVariables: {mention} {user} {server} {count} {id}",
        max_length=1000,
    )

    def __init__(self, current: str = None):
        super().__init__()
        if current:
            self.message.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await db.set_join_leave_config(interaction.guild_id, leave_message=self.message.value)
        embed = success_embed(f"Leave message updated.\n\n**Preview:**\n{_format(self.message.value, interaction.user)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class WelcomeDMModal(discord.ui.Modal, title="Set Welcome DM"):
    message = discord.ui.TextInput(
        label="DM Message",
        style=discord.TextStyle.paragraph,
        placeholder="Welcome to {server}, {user}! We're glad to have you here.\n\nVariables: {user} {server} {id}",
        max_length=1000,
    )

    def __init__(self, current: str = None):
        super().__init__()
        if current:
            self.message.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await db.set_join_leave_config(interaction.guild_id, dm_message=self.message.value)
        await interaction.response.send_message(
            embed=success_embed("Welcome DM updated."), ephemeral=True
        )


class WelcomeDMGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="welcomedm", description="Send a private DM to members when they join")

    @app_commands.command(name="setup", description="Set the welcome DM message")
    @is_admin()
    async def wdm_setup(self, interaction: discord.Interaction):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        await interaction.response.send_modal(WelcomeDMModal(cfg.get("dm_message")))

    @app_commands.command(name="toggle", description="Enable or disable welcome DMs")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def wdm_toggle(self, interaction: discord.Interaction, enabled: bool):
        await db.set_join_leave_config(interaction.guild_id, dm_enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(embed=success_embed(f"Welcome DMs {state}."), ephemeral=True)

    @app_commands.command(name="test", description="Send yourself a test welcome DM")
    @is_staff()
    async def wdm_test(self, interaction: discord.Interaction):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        dm_msg = cfg.get("dm_message") or "Welcome to {server}, {user}!"
        text = _format(dm_msg, interaction.user)
        try:
            await interaction.user.send(text)
            await interaction.response.send_message(embed=success_embed("Test DM sent!"), ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=discord.Embed(description=":x: Could not DM you — your DMs may be closed.", color=0xED4245),
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# Command groups
# ---------------------------------------------------------------------------

class JoinMsgGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="joinmsg", description="Configure join announcements")

    @app_commands.command(name="setup", description="Set the channel and message for join announcements")
    @app_commands.describe(channel="Channel to post join messages in")
    @is_admin()
    async def joinmsg_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.set_join_leave_config(interaction.guild_id, join_channel_id=channel.id, join_enabled=1)
        cfg = await db.get_join_leave_config(interaction.guild_id)
        await interaction.response.send_modal(JoinMsgModal(cfg["join_message"]))

    @app_commands.command(name="message", description="Edit the join message text")
    @is_admin()
    async def joinmsg_message(self, interaction: discord.Interaction):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        await interaction.response.send_modal(JoinMsgModal(cfg["join_message"]))

    @app_commands.command(name="toggle", description="Enable or disable join announcements")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def joinmsg_toggle(self, interaction: discord.Interaction, enabled: bool):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        if enabled and not cfg["join_channel_id"]:
            return await interaction.response.send_message(
                embed=error_embed("Set a channel first with `/joinmsg setup`."), ephemeral=True
            )
        await db.set_join_leave_config(interaction.guild_id, join_enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(embed=success_embed(f"Join announcements {state}."), ephemeral=True)

    @app_commands.command(name="test", description="Send a test join message")
    @is_staff()
    async def joinmsg_test(self, interaction: discord.Interaction):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        if not cfg["join_channel_id"]:
            return await interaction.response.send_message(
                embed=error_embed("No join channel set. Use `/joinmsg setup` first."), ephemeral=True
            )
        channel = interaction.guild.get_channel(cfg["join_channel_id"])
        if not channel:
            return await interaction.response.send_message(embed=error_embed("Join channel not found."), ephemeral=True)
        await channel.send(_format(cfg["join_message"], interaction.user))
        await interaction.response.send_message(embed=success_embed(f"Test message sent to {channel.mention}."), ephemeral=True)

    @app_commands.command(name="variables", description="Show available message variables")
    async def joinmsg_variables(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(title="Join/Leave Message Variables", description=VARIABLES_HELP, color=0x5865F2),
            ephemeral=True,
        )


class LeaveMsgGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="leavemsg", description="Configure leave announcements")

    @app_commands.command(name="setup", description="Set the channel and message for leave announcements")
    @app_commands.describe(channel="Channel to post leave messages in")
    @is_admin()
    async def leavemsg_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.set_join_leave_config(interaction.guild_id, leave_channel_id=channel.id, leave_enabled=1)
        cfg = await db.get_join_leave_config(interaction.guild_id)
        await interaction.response.send_modal(LeaveMsgModal(cfg["leave_message"]))

    @app_commands.command(name="message", description="Edit the leave message text")
    @is_admin()
    async def leavemsg_message(self, interaction: discord.Interaction):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        await interaction.response.send_modal(LeaveMsgModal(cfg["leave_message"]))

    @app_commands.command(name="toggle", description="Enable or disable leave announcements")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def leavemsg_toggle(self, interaction: discord.Interaction, enabled: bool):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        if enabled and not cfg["leave_channel_id"]:
            return await interaction.response.send_message(
                embed=error_embed("Set a channel first with `/leavemsg setup`."), ephemeral=True
            )
        await db.set_join_leave_config(interaction.guild_id, leave_enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(embed=success_embed(f"Leave announcements {state}."), ephemeral=True)

    @app_commands.command(name="test", description="Send a test leave message")
    @is_staff()
    async def leavemsg_test(self, interaction: discord.Interaction):
        cfg = await db.get_join_leave_config(interaction.guild_id)
        if not cfg["leave_channel_id"]:
            return await interaction.response.send_message(
                embed=error_embed("No leave channel set. Use `/leavemsg setup` first."), ephemeral=True
            )
        channel = interaction.guild.get_channel(cfg["leave_channel_id"])
        if not channel:
            return await interaction.response.send_message(embed=error_embed("Leave channel not found."), ephemeral=True)
        await channel.send(_format(cfg["leave_message"], interaction.user))
        await interaction.response.send_message(embed=success_embed(f"Test message sent to {channel.mention}."), ephemeral=True)


class AutoroleGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="autorole", description="Automatically assign roles to new members")

    @app_commands.command(name="add", description="Add a role to give to new members")
    @app_commands.describe(role="Role to assign", delay="Delay in seconds before assigning (0 = instant)")
    @is_admin()
    async def autorole_add(self, interaction: discord.Interaction, role: discord.Role, delay: int = 0):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                embed=error_embed("That role is above my highest role — I can't assign it."), ephemeral=True
            )
        await db.add_autorole(interaction.guild_id, role.id, max(0, delay))
        delay_str = f" after {delay}s" if delay else " instantly"
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} will be assigned to new members{delay_str}."), ephemeral=True
        )

    @app_commands.command(name="remove", description="Remove an autorole")
    @app_commands.describe(role="Role to remove from autoroles")
    @is_admin()
    async def autorole_remove(self, interaction: discord.Interaction, role: discord.Role):
        removed = await db.remove_autorole(interaction.guild_id, role.id)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"{role.mention} removed from autoroles."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"{role.mention} is not an autorole."), ephemeral=True)

    @app_commands.command(name="list", description="Show all configured autoroles")
    @is_staff()
    async def autorole_list(self, interaction: discord.Interaction):
        rows = await db.get_autoroles(interaction.guild_id)
        if not rows:
            return await interaction.response.send_message(embed=info_embed("No autoroles configured."), ephemeral=True)
        lines = []
        for r in rows:
            role = interaction.guild.get_role(r["role_id"])
            name = role.mention if role else f"*(deleted role {r['role_id']})*"
            delay = f" — {r['delay_seconds']}s delay" if r["delay_seconds"] else " — instant"
            lines.append(f"{name}{delay}")
        embed = discord.Embed(title="Autoroles", description="\n".join(lines), color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class JoinLeave(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(JoinMsgGroup())
        self.bot.tree.add_command(LeaveMsgGroup())
        self.bot.tree.add_command(AutoroleGroup())
        self.bot.tree.add_command(WelcomeDMGroup())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        if not await db.get_feature(member.guild.id, "join_leave"):
            return

        cfg = await db.get_join_leave_config(member.guild.id)

        # Join announcement
        if cfg["join_enabled"] and cfg["join_channel_id"]:
            channel = member.guild.get_channel(cfg["join_channel_id"])
            if channel:
                try:
                    await channel.send(_format(cfg["join_message"], member))
                except discord.HTTPException:
                    pass

        # Welcome DM
        if cfg.get("dm_enabled") and cfg.get("dm_message"):
            try:
                await member.send(_format(cfg["dm_message"], member))
            except discord.HTTPException:
                pass

        # Autoroles
        rows = await db.get_autoroles(member.guild.id)
        for row in rows:
            delay = row["delay_seconds"]
            role = member.guild.get_role(row["role_id"])
            if not role:
                continue
            if delay:
                await asyncio.sleep(delay)
            try:
                # Re-fetch member to confirm they're still in the server
                fresh = member.guild.get_member(member.id)
                if fresh:
                    await fresh.add_roles(role, reason="Autorole")
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        if not await db.get_feature(member.guild.id, "join_leave"):
            return
        cfg = await db.get_join_leave_config(member.guild.id)
        if cfg["leave_enabled"] and cfg["leave_channel_id"]:
            channel = member.guild.get_channel(cfg["leave_channel_id"])
            if channel:
                try:
                    await channel.send(_format(cfg["leave_message"], member))
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinLeave(bot))
