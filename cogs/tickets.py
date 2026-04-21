"""
Ticket system — embed-based, button-driven support tickets.
/ticket panel  /ticket config  /ticket close  /ticket list
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import timezone
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)

PANEL_COLOR  = 0x5865F2
OPEN_COLOR   = 0x57F287
CLOSED_COLOR = 0xED4245


# ---------------------------------------------------------------------------
# Ticket panel embed
# ---------------------------------------------------------------------------

def _panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎫  Support Tickets",
        description=(
            "Need help or have a question?\n"
            "Click **Open a Ticket** below to start a private conversation with our team.\n\n"
            "• One ticket per member at a time\n"
            "• Please describe your issue clearly\n"
            "• A staff member will respond as soon as possible"
        ),
        color=PANEL_COLOR,
    )
    embed.set_footer(text="Tickets are private between you and staff")
    return embed


def _ticket_embed(member: discord.Member, ticket_number: int, welcome_msg: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎫  Ticket #{ticket_number:04d}",
        description=welcome_msg,
        color=OPEN_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Opened by", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.set_footer(text="Use the buttons below to manage this ticket")
    return embed


# ---------------------------------------------------------------------------
# Close reason modal
# ---------------------------------------------------------------------------

class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Describe why this ticket is being closed…",
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await _do_close_ticket(interaction, self.reason.value or "No reason provided")


# ---------------------------------------------------------------------------
# Persistent views
# ---------------------------------------------------------------------------

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open a Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="aegixa:open_ticket",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await db.get_feature(interaction.guild_id, "tickets"):
            return await interaction.response.send_message(
                embed=error_embed("The ticket system is not enabled on this server."), ephemeral=True
            )
        cfg = await db.get_ticket_config(interaction.guild_id)
        if not cfg["enabled"]:
            return await interaction.response.send_message(
                embed=error_embed("Tickets are currently disabled."), ephemeral=True
            )

        # One ticket per user
        existing = await db.get_user_open_ticket(interaction.guild_id, interaction.user.id)
        if existing:
            ch = interaction.guild.get_channel(existing["channel_id"])
            if ch:
                return await interaction.response.send_message(
                    embed=error_embed(f"You already have an open ticket: {ch.mention}"), ephemeral=True
                )

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        member = interaction.user

        # Resolve support role
        support_role = guild.get_role(cfg["support_role_id"]) if cfg["support_role_id"] else None

        # Build overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # Get or create category
        category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None

        count_row = await db.get_open_tickets(interaction.guild_id)
        ticket_number = len(count_row) + 1

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{member.name[:16]}-{ticket_number:04d}",
                overwrites=overwrites,
                category=category,
                reason=f"Ticket opened by {member}",
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(embed=error_embed(f"Could not create ticket channel: {e}"), ephemeral=True)

        await db.create_ticket(guild.id, channel.id, member.id)

        embed = _ticket_embed(member, ticket_number, cfg["welcome_message"])
        view = TicketView()
        await channel.send(embed=embed, view=view)

        if support_role:
            await channel.send(f"{support_role.mention}", delete_after=3)

        await interaction.followup.send(
            embed=success_embed(f"Ticket opened! Head to {channel.mention}"),
            ephemeral=True,
        )


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="aegixa:close_ticket",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        await interaction.response.send_modal(CloseReasonModal())

    @discord.ui.button(
        label="Claim",
        emoji="🙋",
        style=discord.ButtonStyle.secondary,
        custom_id="aegixa:claim_ticket",
    )
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        if ticket["claimed_by"]:
            claimer = interaction.guild.get_member(ticket["claimed_by"])
            name = claimer.display_name if claimer else f"User {ticket['claimed_by']}"
            return await interaction.response.send_message(
                embed=error_embed(f"This ticket is already claimed by **{name}**."), ephemeral=True
            )

        await db.claim_ticket(interaction.channel_id, interaction.user.id)
        embed = discord.Embed(
            description=f"🙋 **{interaction.user.display_name}** has claimed this ticket.",
            color=PANEL_COLOR,
        )
        await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Close helper (used by modal and command)
# ---------------------------------------------------------------------------

async def _do_close_ticket(interaction: discord.Interaction, reason: str):
    ticket = await db.get_ticket_by_channel(interaction.channel_id)
    if not ticket:
        try:
            await interaction.followup.send(embed=error_embed("This is not an active ticket."), ephemeral=True)
        except Exception:
            pass
        return

    cfg = await db.get_ticket_config(interaction.guild_id)
    guild = interaction.guild
    channel = interaction.channel
    member = guild.get_member(ticket["user_id"])

    # Generate transcript
    transcript_lines = [
        f"Ticket #{ticket['ticket_number']:04d} — Transcript",
        f"Opened by: {member} ({ticket['user_id']})" if member else f"User ID: {ticket['user_id']}",
        f"Closed by: {interaction.user} ({interaction.user.id})",
        f"Reason: {reason}",
        "=" * 60,
    ]
    try:
        async for msg in channel.history(limit=200, oldest_first=True):
            if msg.author.bot and not msg.content:
                continue
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M")
            transcript_lines.append(f"[{ts}] {msg.author.display_name}: {msg.content or '(attachment/embed)'}")
    except discord.HTTPException:
        pass

    transcript_text = "\n".join(transcript_lines)

    # Post to log channel
    if cfg["log_channel_id"]:
        log_ch = guild.get_channel(cfg["log_channel_id"])
        if log_ch:
            log_embed = discord.Embed(
                title=f"🔒 Ticket #{ticket['ticket_number']:04d} Closed",
                color=CLOSED_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            log_embed.add_field(name="Opened by", value=f"{member.mention if member else ticket['user_id']}", inline=True)
            log_embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)

            transcript_file = discord.File(
                fp=__import__("io").StringIO(transcript_text),
                filename=f"ticket-{ticket['ticket_number']:04d}.txt",
            )
            try:
                await log_ch.send(embed=log_embed, file=transcript_file)
            except discord.HTTPException:
                await log_ch.send(embed=log_embed)

    await db.close_ticket(interaction.channel_id)

    # Notify user via DM
    if member:
        try:
            dm_embed = discord.Embed(
                title="Your ticket has been closed",
                description=f"**Server:** {guild.name}\n**Reason:** {reason}",
                color=CLOSED_COLOR,
            )
            await member.send(embed=dm_embed)
        except discord.HTTPException:
            pass

    close_embed = discord.Embed(
        description=f"🔒 Ticket closed by {interaction.user.mention}.\n**Reason:** {reason}\n\nThis channel will be deleted in 5 seconds.",
        color=CLOSED_COLOR,
    )
    await channel.send(embed=close_embed)

    import asyncio
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}")
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Config modal
# ---------------------------------------------------------------------------

class TicketWelcomeModal(discord.ui.Modal, title="Ticket Welcome Message"):
    message = discord.ui.TextInput(
        label="Welcome message",
        style=discord.TextStyle.paragraph,
        placeholder="Support will be with you shortly. Please describe your issue.",
        max_length=500,
    )

    def __init__(self, current: str = None):
        super().__init__()
        if current:
            self.message.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await db.set_ticket_config(interaction.guild_id, welcome_message=self.message.value)
        await interaction.response.send_message(
            embed=success_embed("Ticket welcome message updated."), ephemeral=True
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class TicketGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ticket", description="Ticket system management")

    @app_commands.command(name="panel", description="Post the ticket panel in a channel")
    @app_commands.describe(channel="Channel to post the panel in (defaults to current)")
    @is_admin()
    async def ticket_panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await db.get_feature(interaction.guild_id, "tickets"):
            return await interaction.response.send_message(
                embed=error_embed("Enable the Ticket System feature first via the dashboard or `/feature enable tickets`."),
                ephemeral=True,
            )
        ch = channel or interaction.channel
        view = TicketPanelView()
        await ch.send(embed=_panel_embed(), view=view)
        await db.set_ticket_config(interaction.guild_id, panel_channel_id=ch.id)
        await interaction.response.send_message(
            embed=success_embed(f"Ticket panel posted in {ch.mention}."), ephemeral=True
        )

    @app_commands.command(name="config", description="Configure tickets: support role, log channel, category")
    @app_commands.describe(
        support_role="Role that can see and manage tickets",
        log_channel="Channel to send transcripts when tickets close",
        category="Category to create ticket channels under",
    )
    @is_admin()
    async def ticket_config(
        self,
        interaction: discord.Interaction,
        support_role: discord.Role = None,
        log_channel: discord.TextChannel = None,
        category: discord.CategoryChannel = None,
    ):
        kwargs = {}
        if support_role:
            kwargs["support_role_id"] = support_role.id
        if log_channel:
            kwargs["log_channel_id"] = log_channel.id
        if category:
            kwargs["category_id"] = category.id

        if not kwargs:
            cfg = await db.get_ticket_config(interaction.guild_id)
            sr = interaction.guild.get_role(cfg["support_role_id"]) if cfg["support_role_id"] else None
            lc = interaction.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
            cat = interaction.guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            lines = [
                f"**Support role:** {sr.mention if sr else '*not set*'}",
                f"**Log channel:** {lc.mention if lc else '*not set*'}",
                f"**Category:** {cat.name if cat else '*not set*'}",
                f"**Enabled:** {'yes' if cfg['enabled'] else 'no'}",
            ]
            return await interaction.response.send_message(
                embed=discord.Embed(title="Ticket Config", description="\n".join(lines), color=PANEL_COLOR),
                ephemeral=True,
            )

        await db.set_ticket_config(interaction.guild_id, **kwargs)
        await interaction.response.send_message(embed=success_embed("Ticket config updated."), ephemeral=True)

    @app_commands.command(name="message", description="Edit the welcome message shown inside new tickets")
    @is_admin()
    async def ticket_message(self, interaction: discord.Interaction):
        cfg = await db.get_ticket_config(interaction.guild_id)
        await interaction.response.send_modal(TicketWelcomeModal(cfg["welcome_message"]))

    @app_commands.command(name="toggle", description="Enable or disable the ticket system")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def ticket_toggle(self, interaction: discord.Interaction, enabled: bool):
        await db.set_ticket_config(interaction.guild_id, enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(embed=success_embed(f"Ticket system {state}."), ephemeral=True)

    @app_commands.command(name="close", description="Close the current ticket")
    @app_commands.describe(reason="Reason for closing")
    @is_staff()
    async def ticket_close(self, interaction: discord.Interaction, reason: str = "Resolved"):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        await interaction.response.defer()
        await _do_close_ticket(interaction, reason)

    @app_commands.command(name="list", description="List all open tickets in this server")
    @is_staff()
    async def ticket_list(self, interaction: discord.Interaction):
        tickets = await db.get_open_tickets(interaction.guild_id)
        if not tickets:
            return await interaction.response.send_message(
                embed=info_embed("No open tickets.", ""), ephemeral=True
            )
        lines = []
        for t in tickets[:20]:
            ch = interaction.guild.get_channel(t["channel_id"])
            member = interaction.guild.get_member(t["user_id"])
            ch_str = ch.mention if ch else f"*(deleted)*"
            name = member.display_name if member else f"User {t['user_id']}"
            claimed = f" — claimed by <@{t['claimed_by']}>" if t["claimed_by"] else ""
            lines.append(f"#{t['ticket_number']:04d} {ch_str} — **{name}**{claimed}")
        embed = discord.Embed(
            title=f"Open Tickets ({len(tickets)})",
            description="\n".join(lines),
            color=PANEL_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(TicketGroup())

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
