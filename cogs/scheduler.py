"""
Scheduled messages — post a message to a channel after a delay.
/schedule  /schedule list  /schedule cancel
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import database as db
from utils.helpers import success_embed, error_embed, info_embed, parse_duration
from utils.permissions import is_staff, is_admin
import logging

log = logging.getLogger(__name__)


class ScheduleModal(discord.ui.Modal, title="Schedule a Message"):
    content = discord.ui.TextInput(
        label="Message content",
        style=discord.TextStyle.paragraph,
        placeholder="Write the message to schedule here…",
        max_length=2000,
    )

    def __init__(self, channel: discord.TextChannel, delay_seconds: int):
        super().__init__()
        self.target_channel = channel
        self.delay_seconds = delay_seconds

    async def on_submit(self, interaction: discord.Interaction):
        send_at = datetime.now(timezone.utc) + timedelta(seconds=self.delay_seconds)
        send_at_str = send_at.strftime("%Y-%m-%d %H:%M:%S")

        msg_id = await db.add_scheduled_message(
            interaction.guild_id,
            self.target_channel.id,
            self.content.value,
            send_at_str,
            interaction.user.id,
        )

        # Human-readable time
        h, rem = divmod(self.delay_seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            when = f"{h}h {m}m"
        elif m:
            when = f"{m}m {s}s"
        else:
            when = f"{s}s"

        await interaction.response.send_message(
            embed=success_embed(
                f"Message scheduled! It will be sent to {self.target_channel.mention} in **{when}**.\n"
                f"ID: `{msg_id}` — use `/schedule cancel {msg_id}` to cancel."
            ),
            ephemeral=True,
        )


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.check_scheduled.start()

    def cog_unload(self):
        self.check_scheduled.cancel()

    @app_commands.command(name="schedule", description="Schedule a message to be sent after a delay")
    @app_commands.describe(
        when="When to send: e.g. 30m, 2h, 1d",
        channel="Channel to post in (defaults to current)",
    )
    @is_staff()
    async def schedule(self, interaction: discord.Interaction, when: str, channel: discord.TextChannel = None):
        delay = parse_duration(when)
        if delay is None or delay < 60:
            return await interaction.response.send_message(
                embed=error_embed("Invalid time. Use `30m`, `2h`, `1d` etc. Minimum 60 seconds."), ephemeral=True
            )
        if delay > 86400 * 30:
            return await interaction.response.send_message(
                embed=error_embed("Maximum schedule time is 30 days."), ephemeral=True
            )
        target = channel or interaction.channel
        await interaction.response.send_modal(ScheduleModal(target, delay))

    @app_commands.command(name="schedulelist", description="List pending scheduled messages in this server")
    @is_staff()
    async def schedule_list(self, interaction: discord.Interaction):
        msgs = await db.get_scheduled_messages(interaction.guild_id)
        if not msgs:
            return await interaction.response.send_message(
                embed=info_embed("No pending scheduled messages.", ""), ephemeral=True
            )
        lines = []
        for m in msgs[:15]:
            ch = interaction.guild.get_channel(m["channel_id"])
            ch_str = ch.mention if ch else f"*(deleted)*"
            preview = m["content"][:60] + ("…" if len(m["content"]) > 60 else "")
            lines.append(f"`#{m['id']}` → {ch_str} at `{m['send_at']}` — {preview}")
        embed = discord.Embed(
            title="Scheduled Messages",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="schedulecancel", description="Cancel a scheduled message by ID")
    @app_commands.describe(message_id="The ID shown when scheduling (use /schedulelist to find it)")
    @is_staff()
    async def schedule_cancel(self, interaction: discord.Interaction, message_id: int):
        removed = await db.delete_scheduled_message(message_id, interaction.guild_id)
        if removed:
            await interaction.response.send_message(
                embed=success_embed(f"Scheduled message `#{message_id}` cancelled."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(f"No pending scheduled message with ID `#{message_id}` found."), ephemeral=True
            )

    @tasks.loop(seconds=30)
    async def check_scheduled(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        pending = await db.get_pending_scheduled_messages(now)
        for msg in pending:
            guild = self.bot.get_guild(msg["guild_id"])
            if not guild:
                await db.mark_scheduled_sent(msg["id"])
                continue
            channel = guild.get_channel(msg["channel_id"])
            if not channel:
                await db.mark_scheduled_sent(msg["id"])
                continue
            try:
                await channel.send(msg["content"])
            except discord.HTTPException as e:
                log.warning("Scheduled message %s failed: %s", msg["id"], e)
            await db.mark_scheduled_sent(msg["id"])

    @check_scheduled.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
