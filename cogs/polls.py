"""
Polls — /poll for yes/no or multi-option voting.
"""

import json
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed
from utils.permissions import is_staff
import logging

log = logging.getLogger(__name__)

OPTION_EMOJIS = ["🇦", "🇧", "🇨", "🇩", "🇪"]
YESNO_EMOJIS  = ["👍", "👎"]


class PollModal(discord.ui.Modal, title="Create a Poll"):
    question = discord.ui.TextInput(
        label="Question",
        placeholder="What should we have for lunch?",
        max_length=256,
    )
    options = discord.ui.TextInput(
        label="Options (one per line, leave blank for Yes/No)",
        style=discord.TextStyle.paragraph,
        placeholder="Pizza\nBurgers\nSushi\nSalad",
        required=False,
        max_length=500,
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.target_channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        question = self.question.value.strip()
        raw_opts = [o.strip() for o in self.options.value.splitlines() if o.strip()] if self.options.value else []

        if len(raw_opts) == 1:
            return await interaction.followup.send(
                embed=error_embed("Provide at least 2 options, or leave blank for Yes/No."), ephemeral=True
            )
        if len(raw_opts) > 5:
            return await interaction.followup.send(
                embed=error_embed("Maximum 5 options per poll."), ephemeral=True
            )

        is_yesno = len(raw_opts) == 0

        if is_yesno:
            description = ""
            emojis = YESNO_EMOJIS
            options_json = json.dumps(["Yes", "No"])
        else:
            lines = [f"{OPTION_EMOJIS[i]}  {opt}" for i, opt in enumerate(raw_opts)]
            description = "\n".join(lines)
            emojis = OPTION_EMOJIS[:len(raw_opts)]
            options_json = json.dumps(raw_opts)

        embed = discord.Embed(
            title=f"📊  {question}",
            description=description or None,
            color=0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Poll by {interaction.user.display_name}")

        try:
            msg = await self.target_channel.send(embed=embed)
        except discord.HTTPException as e:
            return await interaction.followup.send(embed=error_embed(f"Could not send poll: {e}"), ephemeral=True)

        for emoji in emojis:
            try:
                await msg.add_reaction(emoji)
            except discord.HTTPException:
                pass

        poll_id = await db.create_poll(
            interaction.guild_id, self.target_channel.id, question, options_json, interaction.user.id
        )
        await db.set_poll_message(poll_id, msg.id)

        await interaction.followup.send(
            embed=success_embed(f"Poll posted in {self.target_channel.mention}."), ephemeral=True
        )


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Create a poll with optional multiple choices")
    @app_commands.describe(channel="Channel to post the poll in (defaults to current)")
    @is_staff()
    async def poll(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        await interaction.response.send_modal(PollModal(target))


async def setup(bot: commands.Bot):
    await bot.add_cog(Polls(bot))
