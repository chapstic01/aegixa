"""
Custom commands — server-defined !commands that the bot responds to.
/cc add  /cc edit  /cc remove  /cc list
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)

MAX_COMMANDS = 50


class CCResponseModal(discord.ui.Modal, title="Custom Command Response"):
    response = discord.ui.TextInput(
        label="Response",
        style=discord.TextStyle.paragraph,
        placeholder="The bot will send this message when the command is used.",
        max_length=2000,
    )

    def __init__(self, name: str, current: str = None):
        super().__init__()
        self.command_name = name
        if current:
            self.response.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await db.set_custom_command(
            interaction.guild_id, self.command_name, self.response.value, interaction.user.id
        )
        await interaction.response.send_message(
            embed=success_embed(f"Custom command `!{self.command_name}` saved."), ephemeral=True
        )


class CCGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="cc", description="Manage custom bot commands")

    @app_commands.command(name="add", description="Add a custom command (opens a message editor)")
    @app_commands.describe(name="Command name (without !)")
    @is_admin()
    async def cc_add(self, interaction: discord.Interaction, name: str):
        name = name.lower().strip().replace(" ", "_")
        if len(name) < 1 or len(name) > 32:
            return await interaction.response.send_message(
                embed=error_embed("Command name must be 1–32 characters."), ephemeral=True
            )

        existing = await db.get_custom_commands(interaction.guild_id)
        if len(existing) >= MAX_COMMANDS:
            return await interaction.response.send_message(
                embed=error_embed(f"Maximum of {MAX_COMMANDS} custom commands reached."), ephemeral=True
            )

        current_cmd = await db.get_custom_command(interaction.guild_id, name)
        current_text = current_cmd["response"] if current_cmd else None
        await interaction.response.send_modal(CCResponseModal(name, current_text))

    @app_commands.command(name="remove", description="Remove a custom command")
    @app_commands.describe(name="Command name to remove (without !)")
    @is_admin()
    async def cc_remove(self, interaction: discord.Interaction, name: str):
        removed = await db.delete_custom_command(interaction.guild_id, name.lower().strip())
        if removed:
            await interaction.response.send_message(
                embed=success_embed(f"Custom command `!{name.lower()}` removed."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(f"No command named `!{name.lower()}` found."), ephemeral=True
            )

    @app_commands.command(name="list", description="List all custom commands")
    @is_staff()
    async def cc_list(self, interaction: discord.Interaction):
        cmds = await db.get_custom_commands(interaction.guild_id)
        if not cmds:
            return await interaction.response.send_message(
                embed=info_embed("No custom commands set up.", "Use `/cc add` to create one."), ephemeral=True
            )
        lines = [f"`!{c['name']}` — {c['response'][:60]}{'…' if len(c['response']) > 60 else ''}" for c in cmds]
        embed = discord.Embed(
            title=f"Custom Commands ({len(cmds)}/{MAX_COMMANDS})",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CustomCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(CCGroup())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not message.content.startswith("!"):
            return

        parts = message.content[1:].split(None, 1)
        if not parts:
            return
        name = parts[0].lower()

        cmd = await db.get_custom_command(message.guild.id, name)
        if not cmd:
            return

        try:
            await message.channel.send(cmd["response"])
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CustomCommands(bot))
