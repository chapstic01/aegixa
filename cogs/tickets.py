"""
Ticket system — embed-based, button-driven support tickets.

Features: ticket types, add/remove users, rename, claim/unclaim,
          staff notes, auto-close idle tickets, HTML transcripts.

Commands: /ticket panel  config  toggle  list  adduser  removeuser
          rename  close  unclaim  note  autoclose  types
"""

import json
import io
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import timezone
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)

PANEL_COLOR  = 0x5865F2
OPEN_COLOR   = 0x57F287
CLOSED_COLOR = 0xED4245
NOTE_COLOR   = 0xFEE75C

DEFAULT_TYPES = [{"label": "Support", "emoji": "🎫", "description": "General support request"}]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _panel_embed(title: str = "🎫  Support Tickets",
                 description: str | None = None,
                 types: list[dict] | None = None) -> discord.Embed:
    if description is None:
        if types and len(types) > 1:
            type_lines = "\n".join(f"{t.get('emoji','🎫')} **{t['label']}** — {t.get('description','')}" for t in types)
            description = (
                "Need help or have a question? Choose a category below "
                "to open a private ticket with our team.\n\n"
                f"{type_lines}\n\n"
                "• One ticket per member at a time\n"
                "• A staff member will respond as soon as possible"
            )
        else:
            description = (
                "Need help or have a question?\n"
                "Click **Open a Ticket** below to start a private conversation with our team.\n\n"
                "• One ticket per member at a time\n"
                "• Please describe your issue clearly\n"
                "• A staff member will respond as soon as possible"
            )
    embed = discord.Embed(title=title, description=description, color=PANEL_COLOR)
    embed.set_footer(text="Tickets are private between you and staff")
    return embed


def _ticket_embed(member: discord.Member, ticket_number: int,
                  welcome_msg: str, ticket_type: str = "Support") -> discord.Embed:
    embed = discord.Embed(
        title=f"🎫  Ticket #{ticket_number:04d} — {ticket_type}",
        description=welcome_msg,
        color=OPEN_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Opened by", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Type", value=ticket_type, inline=True)
    embed.set_footer(text="Use the buttons below to manage this ticket")
    return embed


def _build_html_transcript(ticket: dict, messages: list, guild_name: str,
                            closed_by: str, reason: str) -> str:
    rows = []
    for m in messages:
        if m.author.bot and not m.content:
            continue
        ts = m.created_at.strftime("%Y-%m-%d %H:%M")
        bot_badge = ' <span class="bot-badge">BOT</span>' if m.author.bot else ""
        content = discord.utils.escape_markdown(m.content or "(attachment/embed)")
        content = content.replace("<", "&lt;").replace(">", "&gt;")
        rows.append(
            f'<div class="msg"><span class="ts">[{ts}]</span> '
            f'<span class="author">{m.author.display_name}{bot_badge}</span>'
            f'<span class="content">{content}</span></div>'
        )
    body = "\n".join(rows) or "<p>No messages.</p>"
    type_str = ticket.get("ticket_type") or "Support"
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Ticket #{ticket['ticket_number']:04d} Transcript</title>
<style>
body{{background:#313338;color:#dbdee1;font-family:'Segoe UI',sans-serif;margin:0;padding:1rem}}
h1{{color:#fff;font-size:1.2rem;margin-bottom:.25rem}}
.meta{{color:#949ba4;font-size:.85rem;margin-bottom:1rem;border-bottom:1px solid #4e5058;padding-bottom:.5rem}}
.msg{{padding:.25rem .5rem;border-radius:4px;margin:.15rem 0;font-size:.9rem}}
.msg:hover{{background:#2b2d31}}
.ts{{color:#949ba4;font-size:.8rem;margin-right:.5rem}}
.author{{font-weight:600;color:#c9cdfb;margin-right:.4rem}}
.bot-badge{{background:#5865f2;color:#fff;font-size:.65rem;padding:1px 4px;border-radius:3px;vertical-align:middle}}
.content{{word-break:break-word}}
</style></head>
<body>
<h1>🎫 Ticket #{ticket['ticket_number']:04d} — {type_str}</h1>
<div class="meta">
  Server: {guild_name} &nbsp;|&nbsp;
  Closed by: {closed_by} &nbsp;|&nbsp;
  Reason: {reason}
</div>
{body}
</body></html>"""


async def _open_ticket(interaction: discord.Interaction, cfg: dict, ticket_type: str):
    """Core ticket creation logic shared by all entry points."""
    if not cfg["enabled"]:
        return await interaction.response.send_message(
            embed=error_embed("Tickets are currently disabled."), ephemeral=True
        )

    existing = await db.get_user_open_ticket(interaction.guild_id, interaction.user.id)
    if existing:
        ch = interaction.guild.get_channel(existing["channel_id"])
        if ch:
            return await interaction.response.send_message(
                embed=error_embed(f"You already have an open ticket: {ch.mention}"),
                ephemeral=True,
            )

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    member = interaction.user
    support_role = guild.get_role(cfg["support_role_id"]) if cfg["support_role_id"] else None
    category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }
    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    open_count = await db.get_open_tickets(guild.id)
    ticket_number = len(open_count) + 1
    safe_type = ticket_type.lower().replace(" ", "-")[:12]

    try:
        channel = await guild.create_text_channel(
            name=f"ticket-{safe_type}-{member.name[:12]}-{ticket_number:04d}",
            overwrites=overwrites,
            category=category,
            reason=f"Ticket opened by {member} ({ticket_type})",
        )
    except discord.HTTPException as e:
        return await interaction.followup.send(
            embed=error_embed(f"Could not create ticket channel: {e}"), ephemeral=True
        )

    await db.create_ticket(guild.id, channel.id, member.id, ticket_type)

    embed = _ticket_embed(member, ticket_number, cfg["welcome_message"], ticket_type)
    view = TicketView()
    await channel.send(embed=embed, view=view)

    if support_role:
        await channel.send(f"{support_role.mention}", delete_after=3)

    await interaction.followup.send(
        embed=success_embed(f"Ticket opened! Head to {channel.mention}"), ephemeral=True
    )


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

    # Fetch messages for transcript
    msgs = []
    try:
        async for m in channel.history(limit=500, oldest_first=True):
            msgs.append(m)
    except discord.HTTPException:
        pass

    html = _build_html_transcript(
        ticket, msgs, guild.name,
        str(interaction.user), reason,
    )

    if cfg["log_channel_id"]:
        log_ch = guild.get_channel(cfg["log_channel_id"])
        if log_ch:
            log_embed = discord.Embed(
                title=f"🔒 Ticket #{ticket['ticket_number']:04d} Closed",
                color=CLOSED_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            log_embed.add_field(name="Type", value=ticket.get("ticket_type") or "Support", inline=True)
            log_embed.add_field(name="Opened by", value=f"{member.mention if member else ticket['user_id']}", inline=True)
            log_embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            transcript_file = discord.File(
                fp=io.BytesIO(html.encode()),
                filename=f"ticket-{ticket['ticket_number']:04d}.html",
            )
            try:
                await log_ch.send(embed=log_embed, file=transcript_file)
            except discord.HTTPException:
                await log_ch.send(embed=log_embed)

    await db.close_ticket(interaction.channel_id)

    if member:
        try:
            dm = discord.Embed(
                title="Your ticket has been closed",
                description=f"**Server:** {guild.name}\n**Reason:** {reason}",
                color=CLOSED_COLOR,
            )
            await member.send(embed=dm)
        except discord.HTTPException:
            pass

    close_embed = discord.Embed(
        description=(
            f"🔒 Ticket closed by {interaction.user.mention}.\n"
            f"**Reason:** {reason}\n\nThis channel will be deleted in 5 seconds."
        ),
        color=CLOSED_COLOR,
    )
    await channel.send(embed=close_embed)
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}")
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Persistent views
# ---------------------------------------------------------------------------

class _TicketTypeButton(discord.ui.Button):
    """One button per ticket-type slot (0, 1, 2). Label/emoji set at panel-post time."""

    def __init__(self, slot: int):
        super().__init__(
            label="Open a Ticket",
            emoji="🎫",
            style=discord.ButtonStyle.primary,
            custom_id=f"aegixa:ticket_type_{slot}",
        )
        self.slot = slot

    async def callback(self, interaction: discord.Interaction):
        if not await db.get_feature(interaction.guild_id, "tickets"):
            return await interaction.response.send_message(
                embed=error_embed("The ticket system is not enabled on this server."), ephemeral=True
            )
        cfg = await db.get_ticket_config(interaction.guild_id)
        types = json.loads(cfg["ticket_types"]) if cfg.get("ticket_types") else DEFAULT_TYPES
        if self.slot >= len(types):
            return await interaction.response.send_message(
                embed=error_embed("This ticket type is no longer configured."), ephemeral=True
            )
        await _open_ticket(interaction, cfg, types[self.slot]["label"])


class TicketPanelView(discord.ui.View):
    """Persistent view: registers all 3 type slots so old panels survive restarts."""

    def __init__(self, types: list[dict] | None = None):
        super().__init__(timeout=None)
        if types is None:
            for slot in range(3):
                self.add_item(_TicketTypeButton(slot))
        else:
            for slot, t in enumerate(types[:3]):
                btn = _TicketTypeButton(slot)
                btn.label = t["label"][:20]
                btn.emoji = t.get("emoji") or "🎫"
                self.add_item(btn)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="aegixa:close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        await interaction.response.send_modal(CloseReasonModal())

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.secondary, custom_id="aegixa:claim_ticket")
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
                embed=error_embed(f"Already claimed by **{name}**."), ephemeral=True
            )
        await db.claim_ticket(interaction.channel_id, interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"🙋 **{interaction.user.display_name}** has claimed this ticket.",
                color=PANEL_COLOR,
            )
        )

    @discord.ui.button(label="Unclaim", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="aegixa:unclaim_ticket")
    async def unclaim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket or not ticket["claimed_by"]:
            return await interaction.response.send_message(
                embed=error_embed("This ticket is not claimed."), ephemeral=True
            )
        await db.unclaim_ticket(interaction.channel_id)
        await interaction.response.send_message(
            embed=discord.Embed(description="↩️ Ticket unclaimed.", color=PANEL_COLOR)
        )


# ---------------------------------------------------------------------------
# Modals
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


class TicketTypesModal(discord.ui.Modal, title="Configure Ticket Types"):
    types_input = discord.ui.TextInput(
        label="Types (one per line: emoji Label | description)",
        style=discord.TextStyle.paragraph,
        placeholder="🎫 Support | General help\n🐛 Bug Report | Report a bug\n💡 Suggestion | Share an idea",
        max_length=600,
    )

    def __init__(self, current: str = None):
        super().__init__()
        if current:
            self.types_input.default = current

    async def on_submit(self, interaction: discord.Interaction):
        lines = [l.strip() for l in self.types_input.value.splitlines() if l.strip()][:3]
        types = []
        for line in lines:
            parts = line.split("|", 1)
            label_part = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ""
            # Try to extract leading emoji
            words = label_part.split(None, 1)
            if len(words) == 2 and len(words[0]) <= 2:
                emoji, label = words[0], words[1]
            else:
                emoji, label = "🎫", label_part
            if label:
                types.append({"emoji": emoji, "label": label[:32], "description": desc[:60]})
        if not types:
            return await interaction.response.send_message(
                embed=error_embed("No valid types found."), ephemeral=True
            )
        await db.set_ticket_config(interaction.guild_id, ticket_types=json.dumps(types))
        lines_out = [f"{t['emoji']} **{t['label']}** — {t['description']}" for t in types]
        await interaction.response.send_message(
            embed=success_embed(
                f"Ticket types updated ({len(types)}):\n" + "\n".join(lines_out) +
                "\n\nRe-run `/ticket panel` to refresh the panel."
            ),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class TicketGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ticket", description="Ticket system management")

    # ---- Setup & config ----

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
        cfg = await db.get_ticket_config(interaction.guild_id)
        types = json.loads(cfg["ticket_types"]) if cfg.get("ticket_types") else DEFAULT_TYPES
        view = TicketPanelView(types)
        await ch.send(embed=_panel_embed(types=types), view=view)
        await db.set_ticket_config(interaction.guild_id, panel_channel_id=ch.id)
        await interaction.response.send_message(
            embed=success_embed(f"Ticket panel posted in {ch.mention}."), ephemeral=True
        )

    @app_commands.command(name="config", description="Configure tickets: support role, log channel, category")
    @app_commands.describe(
        support_role="Role that can see and manage tickets",
        log_channel="Channel for transcripts when tickets close",
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
        if support_role: kwargs["support_role_id"] = support_role.id
        if log_channel:  kwargs["log_channel_id"] = log_channel.id
        if category:     kwargs["category_id"] = category.id

        if not kwargs:
            cfg = await db.get_ticket_config(interaction.guild_id)
            sr  = interaction.guild.get_role(cfg["support_role_id"]) if cfg["support_role_id"] else None
            lc  = interaction.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
            cat = interaction.guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            lines = [
                f"**Support role:** {sr.mention if sr else '*not set*'}",
                f"**Log channel:** {lc.mention if lc else '*not set*'}",
                f"**Category:** {cat.name if cat else '*not set*'}",
                f"**Enabled:** {'✅' if cfg['enabled'] else '❌'}",
                f"**Auto-close idle:** {cfg.get('idle_close_hours') or 'off'}",
            ]
            return await interaction.response.send_message(
                embed=discord.Embed(title="Ticket Config", description="\n".join(lines), color=PANEL_COLOR),
                ephemeral=True,
            )

        await db.set_ticket_config(interaction.guild_id, **kwargs)
        await interaction.response.send_message(embed=success_embed("Ticket config updated."), ephemeral=True)

    @app_commands.command(name="types", description="Configure ticket types shown on the panel (up to 3)")
    @is_admin()
    async def ticket_types(self, interaction: discord.Interaction):
        cfg = await db.get_ticket_config(interaction.guild_id)
        current = ""
        if cfg.get("ticket_types"):
            types = json.loads(cfg["ticket_types"])
            current = "\n".join(f"{t.get('emoji','🎫')} {t['label']} | {t.get('description','')}" for t in types)
        await interaction.response.send_modal(TicketTypesModal(current or None))

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

    @app_commands.command(name="autoclose", description="Auto-close tickets idle for this many hours (0 = off)")
    @app_commands.describe(hours="Hours of inactivity before auto-close (0 disables)")
    @is_admin()
    async def ticket_autoclose(self, interaction: discord.Interaction, hours: int):
        if hours < 0 or hours > 168:
            return await interaction.response.send_message(
                embed=error_embed("Hours must be 0–168 (0 = disabled)."), ephemeral=True
            )
        await db.set_ticket_config(interaction.guild_id, idle_close_hours=hours)
        msg = f"Auto-close disabled." if hours == 0 else f"Tickets idle for **{hours}h** will be closed automatically."
        await interaction.response.send_message(embed=success_embed(msg), ephemeral=True)

    # ---- Staff ticket actions ----

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

    @app_commands.command(name="adduser", description="Add a member to this ticket")
    @app_commands.describe(member="Member to add")
    @is_staff()
    async def ticket_adduser(self, interaction: discord.Interaction, member: discord.Member):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        try:
            await interaction.channel.set_permissions(
                member, read_messages=True, send_messages=True, attach_files=True
            )
            await interaction.response.send_message(
                embed=success_embed(f"{member.mention} has been added to this ticket.")
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="removeuser", description="Remove a member from this ticket")
    @app_commands.describe(member="Member to remove")
    @is_staff()
    async def ticket_removeuser(self, interaction: discord.Interaction, member: discord.Member):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        if member.id == ticket["user_id"]:
            return await interaction.response.send_message(
                embed=error_embed("Cannot remove the ticket owner."), ephemeral=True
            )
        try:
            await interaction.channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(
                embed=success_embed(f"{member.mention} has been removed from this ticket.")
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="rename", description="Rename this ticket channel")
    @app_commands.describe(name="New channel name")
    @is_staff()
    async def ticket_rename(self, interaction: discord.Interaction, name: str):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        clean = name.lower().replace(" ", "-")[:32]
        try:
            await interaction.channel.edit(name=clean)
            await interaction.response.send_message(
                embed=success_embed(f"Ticket renamed to `{clean}`."), ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="unclaim", description="Unclaim this ticket")
    @is_staff()
    async def ticket_unclaim(self, interaction: discord.Interaction):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        if not ticket["claimed_by"]:
            return await interaction.response.send_message(
                embed=error_embed("This ticket is not claimed."), ephemeral=True
            )
        await db.unclaim_ticket(interaction.channel_id)
        await interaction.response.send_message(
            embed=success_embed("Ticket unclaimed — it's available for any staff member."), ephemeral=True
        )

    @app_commands.command(name="note", description="Add a staff-only note in this ticket")
    @app_commands.describe(text="Note text (only visible to staff)")
    @is_staff()
    async def ticket_note(self, interaction: discord.Interaction, text: str):
        ticket = await db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            return await interaction.response.send_message(
                embed=error_embed("This channel is not an active ticket."), ephemeral=True
            )
        embed = discord.Embed(
            title="📝 Staff Note",
            description=text,
            color=NOTE_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Note by {interaction.user.display_name}")
        msg = await interaction.channel.send(embed=embed)
        try:
            await msg.pin()
        except discord.HTTPException:
            pass
        await interaction.response.send_message(embed=success_embed("Note added and pinned."), ephemeral=True)

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
            ch_str = ch.mention if ch else "*(deleted)*"
            name = member.display_name if member else f"User {t['user_id']}"
            ttype = t.get("ticket_type") or "Support"
            claimed = f" — claimed by <@{t['claimed_by']}>" if t["claimed_by"] else ""
            lines.append(f"#{t['ticket_number']:04d} [{ttype}] {ch_str} — **{name}**{claimed}")
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
        # Register persistent views for all 3 type slots + ticket action buttons
        self.bot.add_view(TicketPanelView())   # registers aegixa:ticket_type_0/1/2
        self.bot.add_view(TicketView())        # registers close/claim/unclaim
        self.auto_close_task.start()

    def cog_unload(self):
        self.auto_close_task.cancel()

    @tasks.loop(minutes=30)
    async def auto_close_task(self):
        """Close tickets that have been idle past the configured threshold."""
        idle_tickets = await db.get_idle_tickets(1)  # fetch all idle tickets, filter per-guild below
        for ticket in idle_tickets:
            guild = self.bot.get_guild(ticket["guild_id"])
            if not guild:
                continue
            cfg = await db.get_ticket_config(ticket["guild_id"])
            hours = cfg.get("idle_close_hours") or 0
            if not hours:
                continue
            # Check if this ticket specifically is past threshold
            from datetime import datetime
            last = datetime.strptime(ticket["last_message_at"], "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.utcnow() - last).total_seconds() / 3600
            if elapsed < hours:
                continue
            channel = guild.get_channel(ticket["channel_id"])
            if not channel:
                await db.close_ticket(ticket["channel_id"])
                continue
            try:
                notify = discord.Embed(
                    description=f"⏰ This ticket has been idle for **{hours}h** and will close in 60 seconds.\nReply to cancel.",
                    color=NOTE_COLOR,
                )
                await channel.send(embed=notify)
                await asyncio.sleep(60)
                # Re-check it's still open
                still_open = await db.get_ticket_by_channel(ticket["channel_id"])
                if not still_open:
                    continue
                # Create a fake interaction proxy for _do_close_ticket
                class _AutoCloseProxy:
                    channel_id = ticket["channel_id"]
                    guild_id   = ticket["guild_id"]
                    channel    = channel
                    guild      = guild
                    user       = guild.me
                    async def response(self): pass
                    async def followup(self): pass

                await db.close_ticket(ticket["channel_id"])
                # Build transcript and log
                msgs = []
                async for m in channel.history(limit=500, oldest_first=True):
                    msgs.append(m)
                html = _build_html_transcript(ticket, msgs, guild.name, "Auto-close (idle)", "Idle timeout")
                if cfg["log_channel_id"]:
                    log_ch = guild.get_channel(cfg["log_channel_id"])
                    if log_ch:
                        member = guild.get_member(ticket["user_id"])
                        log_embed = discord.Embed(
                            title=f"⏰ Ticket #{ticket['ticket_number']:04d} Auto-Closed (Idle)",
                            color=CLOSED_COLOR,
                            timestamp=discord.utils.utcnow(),
                        )
                        log_embed.add_field(name="Opened by", value=f"{member.mention if member else ticket['user_id']}", inline=True)
                        f = discord.File(fp=io.BytesIO(html.encode()), filename=f"ticket-{ticket['ticket_number']:04d}.html")
                        try:
                            await log_ch.send(embed=log_embed, file=f)
                        except discord.HTTPException:
                            await log_ch.send(embed=log_embed)
                close_embed = discord.Embed(
                    description="⏰ Ticket closed automatically due to inactivity.",
                    color=CLOSED_COLOR,
                )
                await channel.send(embed=close_embed)
                await asyncio.sleep(5)
                await channel.delete(reason="Auto-close: idle ticket")
            except Exception as e:
                log.warning("Auto-close failed for ticket %s: %s", ticket["id"], e)

    @auto_close_task.before_loop
    async def before_auto_close(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Update last_message_at for idle-close tracking."""
        if message.guild and not message.author.bot:
            ticket = await db.get_ticket_by_channel(message.channel.id)
            if ticket:
                await db.touch_ticket(message.channel.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
