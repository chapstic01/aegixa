"""
Admin / premium management.
Commands: /help (interactive), /premium, /redeem, /givepremium, /genkey
"""

import os
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from config import PREMIUM_URL, SUPPORT_SERVER, COLOR_PREMIUM
import logging

log = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))


# ---------------------------------------------------------------------------
# Interactive /help
# ---------------------------------------------------------------------------

def _make_home_embed(guild_count: int) -> discord.Embed:
    e = discord.Embed(
        title="Aegixa — Help",
        description=(
            "Select a category from the dropdown to browse commands.\n\n"
            "**Quick Start**\n"
            "1. `/setup staff` — set your staff role\n"
            "2. `/setup logs` — configure log channels\n"
            "3. `/filters list` — review automod filters\n"
            "4. `/website` — view dashboard & premium links\n\n"
            f"**Servers:** {guild_count}  |  "
            f"[Support]({SUPPORT_SERVER})  |  [Premium]({PREMIUM_URL})"
        ),
        color=0x5865F2,
    )
    e.set_footer(text="Select a category from the menu below")
    return e


CATEGORY_EMBEDS = {
    "moderation": lambda: discord.Embed(
        title="🔨 Moderation",
        description=(
            "**Actions**\n"
            "`/ban <member> [reason]` — ban from server\n"
            "`/unban <user_id> [reason]` — unban by ID\n"
            "`/kick <member> [reason]` — kick from server\n"
            "`/mute <member> <duration> [reason]` — timeout (e.g. `10m`, `2h`, `7d`)\n"
            "`/unmute <member>` — remove timeout\n"
            "`/tempban <member> <duration> [reason]` — ban then auto-unban\n\n"
            "**Warnings**\n"
            "`/warn add <member> [reason]` — issue a warning\n"
            "`/warn view <member>` — see all warnings\n"
            "`/warn remove <id>` — delete a warning by ID\n"
            "`/threshold <number>` — auto-ban after N warnings (0 = off)\n\n"
            "**Channel**\n"
            "`/lock [reason]` `/unlock [reason]` — block/allow @everyone\n"
            "`/slowmode <seconds>` — rate limit (0 = off)\n"
            "`/purge <amount>` — delete up to 100 messages\n"
            "`/block <member>` `/unblock <member>` — per-channel message block\n\n"
            "**Members**\n"
            "`/nick <member> [nickname]` — set or reset nickname\n"
            "`/roletoggle <member> <role>` — add or remove a role\n"
            "`/vcmove <member> <channel>` — move to voice channel\n"
            "`/rolecolor <role> <#hex>` — change role colour"
        ),
        color=0xED4245,
    ),
    "automod": lambda: discord.Embed(
        title="🤖 Auto-Moderation",
        description=(
            "**Filters** — each has its own on/off and punishment\n"
            "`/filters toggle <filter> <true/false>` — enable or disable\n"
            "`/filters punishment <filter> <none/warn/mute/kick/ban>` — set action\n"
            "`/filters bulk <true/false>` — toggle all at once\n"
            "`/filters list` — view all filter states\n\n"
            "**Available filters:**\n"
            "`spam` `word` `image` `sticker` `external_emoji`\n"
            "`link` `invite` `caps` `rate_limit` `mentions` `phishing` ⭐\n\n"
            "**Banned Words**\n"
            "`/words add <word>` — add to blocked list\n"
            "`/words remove <word>` — remove from list\n"
            "`/words list` — view all banned words\n\n"
            "**Feature & Command Control**\n"
            "`/features toggle <feature>` `/features list`\n"
            "`/cmds toggle <command>` `/cmds list`"
        ),
        color=0xFEE75C,
    ),
    "raid": lambda: discord.Embed(
        title="🛡️ Anti-Raid",
        description=(
            "`/raidmode true` — immediately lock every channel\n"
            "`/raidmode false` — unlock and resume normal operation\n\n"
            "**Auto-Detection**\n"
            "Aegixa monitors join rate automatically. When it detects a\n"
            "surge it locks the server, alerts your alert channel, and\n"
            "lifts the lockdown after 5 minutes.\n\n"
            "**Account-Age Gate**\n"
            "Accounts newer than the configured minimum age are kicked\n"
            "or banned on join before they can do anything.\n\n"
            "Configure thresholds with `/setup update`."
        ),
        color=0xFF4444,
    ),
    "logging": lambda: discord.Embed(
        title="📋 Logging",
        description=(
            "`/setup logs` — set all log channels at once\n\n"
            "**Log channels & what they capture:**\n"
            "• `general` — slash command usage, misc actions\n"
            "• `spam` — automod filter triggers\n"
            "• `member` — joins, leaves, bots added\n"
            "• `edit` — message edits (before & after)\n"
            "• `delete` — deleted messages & attachments\n"
            "• `voice` — VC joins, leaves, switches + duration\n"
            "• `roles` — role changes on members\n"
            "• `channels` — channel create/rename/delete, invites\n"
            "• `modactions` — bans, kicks, mutes, timeouts, nickname changes\n\n"
            "All logs include timestamp, user, and relevant context.\n"
            "Native Discord bans and timeouts are also captured."
        ),
        color=0x5865F2,
    ),
    "roles": lambda: discord.Embed(
        title="🎭 Role Tools",
        description=(
            "**Role Automation**\n"
            "`/roleauto swapadd <trigger> <remove>` — gain role A → lose role B\n"
            "`/roleauto swapremove <id>` — delete a swap rule\n"
            "`/roleauto swaplist` — view all swap rules\n"
            "`/roleauto grantadd <trigger> <grant>` — gain role A → also get role B\n"
            "`/roleauto grantremove <id>` — delete a grant rule\n"
            "`/roleauto grantlist` — view all grant rules\n\n"
            "**Reaction Roles**\n"
            "`/reactionrole add <msg_id> <emoji> <role>` — bind emoji to role\n"
            "`/reactionrole remove <msg_id> <emoji>` — remove binding\n"
            "`/reactionrole list` — view all reaction roles"
        ),
        color=0x00B0F4,
    ),
    "utility": lambda: discord.Embed(
        title="🔧 Utility",
        description=(
            "**Info**\n"
            "`/member [user]` — show member info, roles, join date\n"
            "`/avatar [user]` — show full-size avatar\n"
            "`/server` — show server stats\n"
            "`/roles` — list all server roles\n\n"
            "**Sending Messages**\n"
            "`/say [channel]` — send plain text as the bot (popup)\n"
            "`/embed send [channel]` — send a custom embed (popup)\n"
            "`/embed edit <msg_id>` — edit a bot message or embed\n"
            "`/announce send` — post to all servers' announcement channels\n"
            "`/welcome [channel]` — re-send the setup/welcome embed\n"
            "`/website` — show links to website and dashboard\n\n"
            "**Sticky Messages**\n"
            "`/sticky set` — pin a message to the bottom of a channel (popup)\n"
            "`/sticky clear` — remove sticky from this channel\n"
            "`/sticky view` — preview the current sticky\n\n"
            "**Setup**\n"
            "`/setup staff` — configure staff, config, and alert roles\n"
            "`/setup logs` — set log channels\n"
            "`/setup update <setting>` — change individual settings"
        ),
        color=0x57F287,
    ),
    "events": lambda: discord.Embed(
        title="🎉 Giveaways & Invite Tracking",
        description=(
            "**Giveaways**\n"
            "`/giveaway start [channel]` — opens a popup to set prize, duration & winners\n"
            "`/giveaway end <msg_id>` — end a giveaway early\n"
            "`/giveaway reroll <msg_id>` — pick new winners\n"
            "`/giveaway list` — view all active giveaways\n\n"
            "**How giveaways work:**\n"
            "Members react with 🎉 to enter. When time is up, Aegixa\n"
            "picks random winners and announces them in the channel.\n\n"
            "**Invite Tracking**\n"
            "When a member joins, Aegixa logs which invite link they used.\n"
            "This shows in the `member` log channel automatically — no setup needed."
        ),
        color=0xFFA500,
    ),
    "tickets": lambda: discord.Embed(
        title="🎫 Ticket System",
        description=(
            "Button-based support tickets with types, transcripts, and auto-close.\n\n"
            "**Setup**\n"
            "`/ticket config [support_role] [log_channel] [category]` — configure\n"
            "`/ticket types` — set up to 3 ticket categories shown on the panel\n"
            "`/ticket message` — edit the welcome message inside tickets\n"
            "`/ticket autoclose <hours>` — auto-close idle tickets (0 = off)\n"
            "`/ticket toggle <true/false>` — enable or disable\n"
            "`/ticket panel [channel]` — post the panel\n\n"
            "**Staff commands (inside a ticket)**\n"
            "`/ticket close [reason]` — close ticket, post HTML transcript\n"
            "`/ticket adduser <member>` — add someone to this ticket\n"
            "`/ticket removeuser <member>` — remove someone from this ticket\n"
            "`/ticket rename <name>` — rename the ticket channel\n"
            "`/ticket note <text>` — pin a staff-only note\n"
            "`/ticket unclaim` — release your claim\n"
            "`/ticket list` — view all open tickets\n\n"
            "Panel buttons: **Open** (per type) · **Claim** · **Unclaim** · **Close**"
        ),
        color=0x5865F2,
    ),
    "tools": lambda: discord.Embed(
        title="🛠️ Extra Tools",
        description=(
            "**Polls**\n"
            "`/poll [channel]` — opens a popup to write your question and options\n"
            "Leave options blank for a yes/no vote. Up to 5 options.\n\n"
            "**Custom Commands**\n"
            "`/cc add <name>` — create a `!command` (opens message editor)\n"
            "`/cc remove <name>` — delete a custom command\n"
            "`/cc list` — view all custom commands\n"
            "Members trigger them by typing `!commandname` in chat.\n\n"
            "**Scheduled Messages**\n"
            "`/schedule <when> [channel]` — schedule a message (30m, 2h, 1d…)\n"
            "`/schedulelist` — view pending scheduled messages\n"
            "`/schedulecancel <id>` — cancel a scheduled message\n\n"
            "**Server Stats Channels**\n"
            "`/stats setup` — create voice channels showing live member counts\n"
            "`/stats remove` — remove all stat channels\n"
            "`/stats refresh` — force-update stat channels now"
        ),
        color=0x57F287,
    ),
    "welcome": lambda: discord.Embed(
        title="👋 Join / Leave & Autoroles",
        description=(
            "**Join Announcements**\n"
            "`/joinmsg setup <channel>` — set channel and open message editor\n"
            "`/joinmsg message` — edit the join message\n"
            "`/joinmsg toggle <true/false>` — enable or disable\n"
            "`/joinmsg test` — send a test message\n"
            "`/joinmsg variables` — show available message variables\n\n"
            "**Leave Announcements**\n"
            "`/leavemsg setup <channel>` — set channel and open message editor\n"
            "`/leavemsg message` — edit the leave message\n"
            "`/leavemsg toggle <true/false>` — enable or disable\n"
            "`/leavemsg test` — send a test message\n\n"
            "**Welcome DM**\n"
            "`/welcomedm setup` — edit the private DM sent to new members\n"
            "`/welcomedm toggle <true/false>` — enable or disable\n"
            "`/welcomedm test` — send yourself a test DM\n\n"
            "**Autoroles**\n"
            "`/autorole add <role> [delay]` — assign role on join (optional delay in seconds)\n"
            "`/autorole remove <role>` — remove an autorole\n"
            "`/autorole list` — view configured autoroles\n\n"
            "**Message variables:** `{mention}` `{user}` `{server}` `{count}` `{id}`"
        ),
        color=0x57F287,
    ),
    "starboard": lambda: discord.Embed(
        title="⭐ Starboard",
        description=(
            "Starboard reposts popular messages to a dedicated channel\n"
            "when they reach a set number of reactions.\n\n"
            "`/starboard setup <channel>` — set the starboard channel\n"
            "`/starboard threshold <count>` — set minimum reactions needed (1–25)\n"
            "`/starboard emoji <emoji>` — choose which emoji triggers it (default ⭐)\n"
            "`/starboard toggle <true/false>` — enable or disable\n"
            "`/starboard status` — view current configuration\n\n"
            "When reactions drop below the threshold, the starboard post\n"
            "is automatically removed."
        ),
        color=0xFFAC33,
    ),
    "levels": lambda: discord.Embed(
        title="🏆 XP / Levels ⭐ Premium",
        description=(
            "Members earn XP by chatting and in voice. Each level-up is announced\n"
            "and optional role rewards are automatically assigned.\n\n"
            "**Member Commands**\n"
            "`/level [member]` — view level, XP, and progress bar\n"
            "`/leaderboard` — top 10 members by XP\n\n"
            "**Level Roles**\n"
            "`/levelroles add <level> <role>` — award a role at a level\n"
            "`/levelroles remove <level>` — remove a level role reward\n"
            "`/levelroles list` — view all level role rewards\n\n"
            "**Admin / XP Management**\n"
            "`/xp give <member> <amount>` — add XP to a member\n"
            "`/xp set <member> <amount>` — set XP directly\n"
            "`/xp reset <member>` — wipe a member's XP\n\n"
            "**Config**\n"
            "`/levelconfig channel [channel]` — set level-up announcement channel\n"
            "`/levelconfig cooldown <seconds>` — set XP cooldown (10–600s)\n"
            "`/levelconfig voicexp <true/false>` — award XP for voice time\n"
            "`/levelconfig toggle <true/false>` — enable or disable the system\n\n"
            "Requires **Aegixa Premium**."
        ),
        color=0xFFD700,
    ),
    "premium": lambda: discord.Embed(
        title="⭐ Premium",
        description=(
            "**Status & Activation**\n"
            "`/premium` — check if this server has premium active\n"
            "`/redeem <key>` — activate a license key you purchased\n\n"
            "**Premium Features**\n"
            "`/filters toggle phishing true` — scan every message for scam links\n"
            "`/verification setup` — hold new members in a gate until verified\n"
            "`/verification toggle` — enable or disable the gate\n"
            "`/verification status` — view current verification config\n"
            "`/level` `/leaderboard` — XP/Levels system\n\n"
            f"[Buy a key]({PREMIUM_URL})  |  [Support]({SUPPORT_SERVER})\n\n"
            "**Owner-only commands**\n"
            "`/genkey <tier> <days>` — generate a license key\n"
            "`/givepremium <days> [guild_id]` — grant free premium\n"
            "`/update` — broadcast an update to all servers"
        ),
        color=0xFFD700,
    ),
}

SELECT_OPTIONS = [
    discord.SelectOption(label="Moderation",          value="moderation",  emoji="🔨"),
    discord.SelectOption(label="Auto-Moderation",     value="automod",     emoji="🤖"),
    discord.SelectOption(label="Anti-Raid",           value="raid",        emoji="🛡️"),
    discord.SelectOption(label="Logging",             value="logging",     emoji="📋"),
    discord.SelectOption(label="Role Tools",          value="roles",       emoji="🎭"),
    discord.SelectOption(label="Utility",             value="utility",     emoji="🔧"),
    discord.SelectOption(label="Giveaways & Invites", value="events",      emoji="🎉"),
    discord.SelectOption(label="Ticket System",       value="tickets",     emoji="🎫"),
    discord.SelectOption(label="Extra Tools",         value="tools",       emoji="🛠️"),
    discord.SelectOption(label="Join / Leave & DMs",  value="welcome",     emoji="👋"),
    discord.SelectOption(label="Starboard",           value="starboard",   emoji="⭐"),
    discord.SelectOption(label="XP / Levels",         value="levels",      emoji="🏆"),
    discord.SelectOption(label="Premium",             value="premium",     emoji="💎"),
]


class HelpSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose a category…",
            min_values=1,
            max_values=1,
            options=SELECT_OPTIONS,
            custom_id="aegixa:help_select",
        )

    async def callback(self, interaction: discord.Interaction):
        builder = CATEGORY_EMBEDS.get(self.values[0])
        embed = builder() if builder else _make_home_embed(len(interaction.client.guilds))
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpSelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ---------------------------------------------------------------------------
# Update modal
# ---------------------------------------------------------------------------

COLOR_MAP = {
    "blue":   0x5865F2,
    "green":  0x57F287,
    "red":    0xED4245,
    "yellow": 0xFEE75C,
    "purple": 0x9B59B6,
    "gold":   0xFFD700,
    "white":  0xFFFFFF,
}


class _UpdateModal(discord.ui.Modal, title="Broadcast Update"):
    embed_title = discord.ui.TextInput(
        label="Title",
        placeholder="e.g. v2.1 Released",
        max_length=256,
    )
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="Write your update here. Press Enter for new paragraphs.",
        max_length=4000,
    )
    color = discord.ui.TextInput(
        label="Colour",
        placeholder="blue / green / red / yellow / purple / gold / white",
        default="blue",
        max_length=10,
    )
    footer = discord.ui.TextInput(
        label="Footer (optional)",
        placeholder="Aegixa Bot Update",
        default="Aegixa Bot Update",
        required=False,
        max_length=100,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        hex_color = COLOR_MAP.get(self.color.value.strip().lower(), 0x5865F2)
        footer_text = self.footer.value.strip() or "Aegixa Bot Update"

        embed = discord.Embed(
            title=self.embed_title.value,
            description=self.message.value,
            color=hex_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=footer_text)

        sent = 0
        failed = 0
        channel_posts = 0

        for guild in self.bot.guilds:
            row = await db.get_guild(guild.id)
            update_channel_id = row.get("update_channel_id") if row else None

            if update_channel_id:
                channel = guild.get_channel(update_channel_id)
                if channel:
                    try:
                        await channel.send(embed=embed)
                        channel_posts += 1
                        continue
                    except discord.HTTPException:
                        pass

            try:
                owner = await self.bot.fetch_user(guild.owner_id)
                owner_embed = discord.Embed(
                    title=self.embed_title.value,
                    description=self.message.value,
                    color=hex_color,
                    timestamp=discord.utils.utcnow(),
                )
                owner_embed.set_footer(text=f"{footer_text} • Sent to you as owner of {guild.name}")
                await owner.send(embed=owner_embed)
                sent += 1
            except (discord.HTTPException, discord.Forbidden):
                failed += 1

        summary = f"**Update sent.**\n• Posted to update channels: **{channel_posts}**\n• DMed server owners: **{sent}**"
        if failed:
            summary += f"\n• Failed (DMs closed): **{failed}**"

        await interaction.followup.send(embed=success_embed(summary), ephemeral=True)
        log.info("Owner sent update to %d channels, %d owner DMs (%d failed)", channel_posts, sent, failed)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse all Aegixa commands by category")
    async def help(self, interaction: discord.Interaction):
        embed = _make_home_embed(len(self.bot.guilds))
        await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)

    @app_commands.command(name="premium", description="Check this server's premium status")
    async def premium(self, interaction: discord.Interaction):
        is_prem = await db.is_premium(interaction.guild_id)
        if is_prem:
            embed = discord.Embed(
                title="⭐ Premium Active",
                description="This server has **Aegixa Premium** — all features unlocked!",
                color=COLOR_PREMIUM,
            )
        else:
            embed = discord.Embed(
                title="🔓 Free Tier",
                description=(
                    "Upgrade to unlock phishing detection, verification gate, and more.\n\n"
                    f"[Get Premium]({PREMIUM_URL})  |  [Support]({SUPPORT_SERVER})"
                ),
                color=0x5865F2,
            )
            embed.add_field(
                name="Premium Includes",
                value=(
                    "• Phishing & scam link detection\n"
                    "• Member verification gate\n"
                    "• All future premium features\n"
                    "• Priority support"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="redeem", description="Redeem a premium license key")
    @app_commands.describe(key="Your license key")
    async def redeem(self, interaction: discord.Interaction, key: str):
        success, message = await db.redeem_license_key(interaction.guild_id, key)
        embed = discord.Embed(
            title=":white_check_mark: License Redeemed" if success else ":x: Redemption Failed",
            description=message,
            color=0x57F287 if success else 0xED4245,
        )
        if success:
            embed.add_field(
                name="Unlocked",
                value=(
                    "• Phishing link detection\n"
                    "• Member verification gate\n"
                    "• Future premium features"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="givepremium", description="Grant free premium to a server (owner only)")
    @app_commands.describe(days="Duration in days", guild_id="Target server ID (leave blank = this server)")
    async def givepremium(self, interaction: discord.Interaction, days: int, guild_id: str = None):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message(
                embed=error_embed("This command is restricted to the bot owner."), ephemeral=True
            )
        try:
            target_id = int(guild_id) if guild_id else interaction.guild_id
        except ValueError:
            return await interaction.response.send_message(
                embed=error_embed("Invalid guild ID."), ephemeral=True
            )
        await db.grant_premium(target_id, days)
        guild = self.bot.get_guild(target_id)
        name = guild.name if guild else str(target_id)
        await interaction.response.send_message(
            embed=success_embed(f"⭐ Premium granted to **{name}** for **{days}** days."),
            ephemeral=True,
        )
        log.info("Owner granted %d days premium to guild %s (%s)", days, name, target_id)

    @app_commands.command(name="update", description="Compose and send a broadcast embed to all servers (owner only)")
    async def update(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message(
                embed=error_embed("This command is restricted to the bot owner."), ephemeral=True
            )
        await interaction.response.send_modal(_UpdateModal(self.bot))

    @app_commands.command(name="genkey", description="Generate a premium license key (owner only)")
    @app_commands.describe(days="Duration in days", uses="Max redemptions (default 1)")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Premium monthly (30d)",  value="premium"),
        app_commands.Choice(name="Premium annual (365d)", value="annual"),
    ])
    async def genkey(self, interaction: discord.Interaction, tier: str, days: int, uses: int = 1):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message(
                embed=error_embed("This command is restricted to the bot owner."), ephemeral=True
            )
        key = await db.generate_license_key(tier, days, interaction.user.id, uses)
        embed = discord.Embed(title="🔑 License Key Generated", color=COLOR_PREMIUM)
        embed.add_field(name="Key",      value=f"||`{key}`||",   inline=False)
        embed.add_field(name="Tier",     value=tier,             inline=True)
        embed.add_field(name="Duration", value=f"{days} days",   inline=True)
        embed.add_field(name="Max Uses", value=str(uses),        inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
