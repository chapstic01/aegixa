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
            "Use the dropdown below to browse commands by category.\n\n"
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
            "`/ban` `/unban` `/kick`\n"
            "`/mute <member> <duration>` `/unmute`\n"
            "`/tempban <member> <duration>`\n"
            "`/warn add/view/remove`\n"
            "`/lock` `/unlock` `/slowmode <seconds>`\n"
            "`/purge <amount>`\n"
            "`/nick <member> [nickname]`\n"
            "`/rolecolor <role> <#hex>`\n"
            "`/vcmove <member> <channel>`\n"
            "`/roletoggle <member> <role>`\n"
            "`/block` `/unblock`\n"
            "`/threshold <warnings>` — auto-ban at N warnings"
        ),
        color=0xED4245,
    ),
    "automod": lambda: discord.Embed(
        title="🤖 Auto-Moderation",
        description=(
            "`/filters toggle <filter> <true/false>`\n"
            "`/filters punishment <filter> <none/warn/mute/kick/ban>`\n"
            "`/filters bulk <true/false>` — toggle all at once\n"
            "`/filters list` — view all filter states\n\n"
            "`/words add <word>` `/words remove <word>` `/words list`\n\n"
            "**Filters:** spam · word · image · sticker · external_emoji\n"
            "link · invite · caps · rate_limit · **phishing ⭐**\n\n"
            "`/features toggle <feature>` `/features list`\n"
            "`/cmds toggle <command>` `/cmds list`"
        ),
        color=0xFEE75C,
    ),
    "raid": lambda: discord.Embed(
        title="🛡️ Anti-Raid",
        description=(
            "`/raidmode <true/false>` — manually lock all channels\n\n"
            "**Auto-Detection** monitors join rate and triggers lockdown\n"
            "automatically when a raid is detected.\n\n"
            "Configure via `/setup update` and guild settings."
        ),
        color=0xFF4444,
    ),
    "logging": lambda: discord.Embed(
        title="📋 Logging",
        description=(
            "`/setup logs` — set all 8 log channels at once\n\n"
            "**Log types:**\n"
            "• `general` — commands & misc actions\n"
            "• `spam` — automod alerts\n"
            "• `member` — joins, leaves, verifications\n"
            "• `edit` — message edits\n"
            "• `delete` — message deletes\n"
            "• `voice` — voice join/leave/switch (with duration)\n"
            "• `roles` — role changes on members\n"
            "• `channels` — channel create/edit/delete\n"
            "• `modactions` — bans, kicks, mutes, temp-bans"
        ),
        color=0x5865F2,
    ),
    "roles": lambda: discord.Embed(
        title="🎭 Role Tools",
        description=(
            "**Role Automation**\n"
            "`/roleauto swapadd <trigger> <remove>` — gain A → remove B\n"
            "`/roleauto swapremove <id>` `/roleauto swaplist`\n"
            "`/roleauto grantadd <trigger> <grant>` — gain A → also grant B\n"
            "`/roleauto grantremove <id>` `/roleauto grantlist`\n\n"
            "**Reaction Roles**\n"
            "`/reactionrole add <msg_id> <emoji> <role>`\n"
            "`/reactionrole remove <msg_id> <emoji>`\n"
            "`/reactionrole list`"
        ),
        color=0x00B0F4,
    ),
    "utility": lambda: discord.Embed(
        title="🔧 Utility",
        description=(
            "**Info**\n"
            "`/member [user]` `/avatar [user]` `/server` `/roles`\n\n"
            "**Messages**\n"
            "`/say [channel]` — send text as the bot\n"
            "`/embed send [channel]` `/embed edit <msg_id>`\n"
            "`/announce send [guild_id]` — broadcast to all servers\n\n"
            "**Sticky Messages**\n"
            "`/sticky set <content>` `/sticky clear` `/sticky view`\n\n"
            "**Setup**\n"
            "`/setup staff` `/setup logs` `/setup update`"
        ),
        color=0x57F287,
    ),
    "events": lambda: discord.Embed(
        title="🎉 Giveaways & Invite Tracking",
        description=(
            "**Giveaways**\n"
            "`/giveaway start <duration> <winners> <prize>`\n"
            "`/giveaway end <msg_id>` — end early\n"
            "`/giveaway reroll <msg_id>` — pick new winners\n"
            "`/giveaway list` — active giveaways\n\n"
            "**Invite Tracking**\n"
            "Automatically logs which invite link a new member used.\n"
            "Visible in the `member` log channel."
        ),
        color=0xFFA500,
    ),
    "premium": lambda: discord.Embed(
        title="⭐ Premium",
        description=(
            "`/premium` — check this server's premium status\n"
            "`/redeem <key>` — activate a license key\n"
            "`/verification setup` — member verification gate ⭐\n"
            "`/filters toggle phishing true` — phishing detection ⭐\n\n"
            f"[Buy a key]({PREMIUM_URL})  |  [Support]({SUPPORT_SERVER})\n\n"
            "**Owner commands** *(bot owner only)*\n"
            "`/genkey <tier> <days>` — generate a license key\n"
            "`/givepremium <guild_id> <days>` — grant free premium"
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
    discord.SelectOption(label="Premium",             value="premium",     emoji="⭐"),
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
