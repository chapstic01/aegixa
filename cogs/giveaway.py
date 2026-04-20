"""
Giveaway cog — timed giveaways with automatic winner selection.
/giveaway start <duration> <winners> <prize>
/giveaway end <message_id>
/giveaway reroll <message_id>
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import random
import database as db
from utils.helpers import parse_duration, format_duration, success_embed, error_embed, info_embed
from utils.permissions import is_staff
import logging

log = logging.getLogger(__name__)

GIVEAWAY_EMOJI = "🎉"


async def pick_winners(bot: commands.Bot, giveaway: dict, count: int) -> list[discord.User]:
    guild = bot.get_guild(giveaway["guild_id"])
    if not guild:
        return []
    channel = guild.get_channel(giveaway["channel_id"])
    if not channel or not giveaway.get("message_id"):
        return []
    try:
        message = await channel.fetch_message(giveaway["message_id"])
    except discord.HTTPException:
        return []

    for reaction in message.reactions:
        if str(reaction.emoji) == GIVEAWAY_EMOJI:
            users = [u async for u in reaction.users() if not u.bot]
            if not users:
                return []
            return random.sample(users, min(count, len(users)))
    return []


async def end_giveaway_logic(bot: commands.Bot, giveaway: dict):
    guild = bot.get_guild(giveaway["guild_id"])
    if not guild:
        await db.end_giveaway(giveaway["id"])
        return

    channel = guild.get_channel(giveaway["channel_id"])
    winners = await pick_winners(bot, giveaway, giveaway["winners"])
    await db.end_giveaway(giveaway["id"])

    if not channel:
        return

    if winners:
        winner_mentions = " ".join(w.mention for w in winners)
        embed = discord.Embed(
            title=f"🎉 Giveaway Ended — {giveaway['prize']}",
            description=f"**Winner(s):** {winner_mentions}\n\nCongratulations! React with {GIVEAWAY_EMOJI} to enter next time.",
            color=0x57F287,
        )
        await channel.send(content=winner_mentions, embed=embed)
    else:
        embed = discord.Embed(
            title=f"🎉 Giveaway Ended — {giveaway['prize']}",
            description="No valid entries — no winner could be selected.",
            color=0xED4245,
        )
        await channel.send(embed=embed)

    # Edit the original giveaway message
    if giveaway.get("message_id"):
        try:
            msg = await channel.fetch_message(giveaway["message_id"])
            ended_embed = discord.Embed(
                title=f"🎉 {giveaway['prize']}",
                description=f"**Ended!**\nWinner(s): {' '.join(w.mention for w in winners) if winners else 'None'}",
                color=0x747F8D,
            )
            ended_embed.set_footer(text="Giveaway ended")
            await msg.edit(embed=ended_embed)
        except discord.HTTPException:
            pass


class GiveawayModal(discord.ui.Modal, title="Start a Giveaway"):
    prize = discord.ui.TextInput(
        label="Prize",
        placeholder="e.g. Discord Nitro, £10 Steam Gift Card",
        max_length=200,
    )
    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="e.g. 10m, 2h, 1d, 7d",
        max_length=20,
    )
    winners = discord.ui.TextInput(
        label="Number of Winners",
        placeholder="1",
        default="1",
        max_length=2,
    )

    def __init__(self, channel: discord.TextChannel, host: discord.Member):
        super().__init__()
        self.channel = channel
        self.host = host

    async def on_submit(self, interaction: discord.Interaction):
        secs = parse_duration(self.duration.value.strip())
        if not secs or secs < 10:
            return await interaction.response.send_message(
                embed=error_embed("Invalid duration. Use formats like `10m`, `2h`, `1d`."), ephemeral=True
            )
        try:
            winner_count = max(1, min(int(self.winners.value.strip()), 20))
        except ValueError:
            winner_count = 1

        prize_text = self.prize.value.strip()
        ends_at = datetime.now(timezone.utc) + timedelta(seconds=secs)
        ends_str = ends_at.strftime("%Y-%m-%d %H:%M:%S")

        giveaway_id = await db.create_giveaway(
            interaction.guild_id, self.channel.id, prize_text, winner_count,
            self.host.id, ends_str,
        )

        embed = discord.Embed(
            title=f"🎉 {prize_text}",
            description=(
                f"React with {GIVEAWAY_EMOJI} to enter!\n\n"
                f"Ends: <t:{int(ends_at.timestamp())}:R>\n"
                f"Winners: **{winner_count}**\n"
                f"Hosted by: {self.host.mention}"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text=f"Giveaway ID: {giveaway_id} • Ends at")
        embed.timestamp = ends_at

        await interaction.response.send_message(embed=success_embed(f"🎉 Giveaway started in {self.channel.mention}!"), ephemeral=True)
        msg = await self.channel.send(embed=embed)
        await msg.add_reaction(GIVEAWAY_EMOJI)
        await db.set_giveaway_message(giveaway_id, msg.id)


class GiveawayGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="giveaway", description="Manage giveaways")

    @app_commands.command(name="start", description="Start a giveaway")
    @app_commands.describe(channel="Channel to post the giveaway in (defaults to current)")
    @is_staff()
    async def giveaway_start(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        await interaction.response.send_modal(GiveawayModal(target, interaction.user))

    @app_commands.command(name="end", description="End a giveaway early by message ID")
    @app_commands.describe(message_id="The giveaway message ID")
    @is_staff()
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            mid = int(message_id)
        except ValueError:
            return await interaction.followup.send(embed=error_embed("Invalid message ID."), ephemeral=True)

        giveaway = await db.get_giveaway_by_message(mid)
        if not giveaway or giveaway["ended"]:
            return await interaction.followup.send(embed=error_embed("Giveaway not found or already ended."), ephemeral=True)
        if giveaway["guild_id"] != interaction.guild_id:
            return await interaction.followup.send(embed=error_embed("That giveaway is not in this server."), ephemeral=True)

        await end_giveaway_logic(interaction.client, giveaway)
        await interaction.followup.send(embed=success_embed("Giveaway ended."), ephemeral=True)

    @app_commands.command(name="reroll", description="Reroll winners for an ended giveaway")
    @app_commands.describe(message_id="The giveaway message ID")
    @is_staff()
    async def giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            mid = int(message_id)
        except ValueError:
            return await interaction.followup.send(embed=error_embed("Invalid message ID."), ephemeral=True)

        giveaway = await db.get_giveaway_by_message(mid)
        if not giveaway:
            return await interaction.followup.send(embed=error_embed("Giveaway not found."), ephemeral=True)

        winners = await pick_winners(interaction.client, giveaway, giveaway["winners"])
        if not winners:
            return await interaction.followup.send(embed=error_embed("No valid entries to pick from."), ephemeral=True)

        winner_mentions = " ".join(w.mention for w in winners)
        await interaction.channel.send(embed=discord.Embed(
            title=f"🎉 Reroll — {giveaway['prize']}",
            description=f"New winner(s): {winner_mentions}",
            color=0x5865F2,
        ))
        await interaction.followup.send(embed=success_embed("Winners rerolled."), ephemeral=True)

    @app_commands.command(name="list", description="List active giveaways in this server")
    @is_staff()
    async def giveaway_list(self, interaction: discord.Interaction):
        active = await db.get_active_giveaways(interaction.guild_id)
        if not active:
            return await interaction.response.send_message(embed=info_embed("No active giveaways."), ephemeral=True)
        embed = discord.Embed(title="Active Giveaways", color=0x5865F2)
        for g in active:
            embed.add_field(
                name=g["prize"],
                value=f"Ends: <t:{int(datetime.strptime(g['ends_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())}:R>\nWinners: {g['winners']} | ID: {g['id']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(GiveawayGroup())
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    @tasks.loop(seconds=15)
    async def check_giveaways(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        expired = await db.get_expired_giveaways(now)
        for g in expired:
            try:
                await end_giveaway_logic(self.bot, g)
            except Exception as e:
                log.error("Giveaway end failed for ID %s: %s", g["id"], e)
                await db.end_giveaway(g["id"])

    @check_giveaways.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
