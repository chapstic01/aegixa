"""
Feature & command control cog.
/features toggle/list — toggle any of the 11 features
/cmds toggle/list — enable or disable individual commands
/filters toggle/bulk/punishment — per-filter automod control
/words add/remove/list — manage the banned word list
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import error_embed, success_embed, info_embed
from utils.permissions import is_admin, is_staff
from config import FEATURES, FILTER_NAMES, PUNISHMENTS, PROTECTED_COMMANDS
from cogs.logging_cog import send_log


# ---------------------------------------------------------------------------
# Features group
# ---------------------------------------------------------------------------

class FeaturesGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="features", description="Toggle bot features on or off")

    @app_commands.command(name="toggle", description="Enable or disable a feature")
    @app_commands.describe(feature="Feature name", enabled="True to enable, False to disable")
    @is_admin()
    async def features_toggle(self, interaction: discord.Interaction, feature: str, enabled: bool):
        if feature not in FEATURES:
            return await interaction.response.send_message(
                embed=error_embed(f"Unknown feature. Valid: {', '.join(f'`{f}`' for f in FEATURES)}"),
                ephemeral=True,
            )
        await db.set_feature(interaction.guild_id, feature, enabled)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"Feature `{feature}` is now **{state}**."), ephemeral=True
        )
        await send_log(interaction.guild, "general", discord.Embed(
            description=f":gear: **{interaction.user}** {state} feature `{feature}`", color=0x5865F2
        ))

    @app_commands.command(name="list", description="List all features and their current state")
    @is_staff()
    async def features_list(self, interaction: discord.Interaction):
        all_features = await db.get_all_features(interaction.guild_id)
        lines = []
        for f in FEATURES:
            enabled = all_features.get(f, True)
            icon = ":green_circle:" if enabled else ":red_circle:"
            lines.append(f"{icon} `{f}`")
        embed = discord.Embed(title="Feature Toggles", description="\n".join(lines), color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @features_toggle.autocomplete("feature")
    async def feature_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f, value=f)
            for f in FEATURES if current.lower() in f.lower()
        ][:25]


# ---------------------------------------------------------------------------
# Commands group
# ---------------------------------------------------------------------------

ALL_COMMANDS = [
    "warn", "ban", "unban", "kick", "mute", "unmute", "lock", "unlock",
    "slowmode", "purge", "nick", "rolecolor", "vcmove", "roletoggle",
    "block", "unblock", "say", "embed", "announce", "member", "avatar",
    "server", "roles", "roleauto", "filters", "features", "words",
]


class CmdsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="cmds", description="Enable or disable individual commands")

    @app_commands.command(name="toggle", description="Enable or disable a command")
    @app_commands.describe(command="Command name", enabled="True to enable, False to disable")
    @is_admin()
    async def cmds_toggle(self, interaction: discord.Interaction, command: str, enabled: bool):
        if command in PROTECTED_COMMANDS:
            return await interaction.response.send_message(
                embed=error_embed(f"`/{command}` is protected and cannot be disabled."),
                ephemeral=True,
            )
        await db.set_command_enabled(interaction.guild_id, command, enabled)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"Command `/{command}` is now **{state}**."), ephemeral=True
        )
        await send_log(interaction.guild, "general", discord.Embed(
            description=f":gear: **{interaction.user}** {state} command `/{command}`", color=0x5865F2
        ))

    @app_commands.command(name="list", description="List all commands and their enabled state")
    @is_staff()
    async def cmds_list(self, interaction: discord.Interaction):
        config = await db.get_all_commands_config(interaction.guild_id)
        lines = []
        for cmd in ALL_COMMANDS:
            enabled = config.get(cmd, True)
            icon = ":green_circle:" if enabled else ":red_circle:"
            protected = " 🔒" if cmd in PROTECTED_COMMANDS else ""
            lines.append(f"{icon} `/{cmd}`{protected}")
        embed = discord.Embed(title="Command Status", description="\n".join(lines), color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cmds_toggle.autocomplete("command")
    async def command_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=c, value=c)
            for c in ALL_COMMANDS if current.lower() in c.lower()
        ][:25]


# ---------------------------------------------------------------------------
# Filters group
# ---------------------------------------------------------------------------

class FiltersGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="filters", description="Configure automod content filters")

    @app_commands.command(name="toggle", description="Enable or disable a specific filter")
    @app_commands.describe(filter_name="Filter to toggle", enabled="True to enable, False to disable")
    @is_staff()
    async def filters_toggle(self, interaction: discord.Interaction, filter_name: str, enabled: bool):
        if filter_name not in FILTER_NAMES:
            return await interaction.response.send_message(
                embed=error_embed(f"Unknown filter. Valid: {', '.join(f'`{f}`' for f in FILTER_NAMES)}"),
                ephemeral=True,
            )
        await db.set_filter(interaction.guild_id, filter_name, enabled=enabled)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"Filter `{filter_name}` is now **{state}**."), ephemeral=True
        )

    @app_commands.command(name="punishment", description="Set the punishment for a filter")
    @app_commands.describe(filter_name="Which filter", punishment="none / warn / mute / kick / ban")
    @is_staff()
    async def filters_punishment(self, interaction: discord.Interaction, filter_name: str, punishment: str):
        if filter_name not in FILTER_NAMES:
            return await interaction.response.send_message(embed=error_embed("Unknown filter."), ephemeral=True)
        if punishment not in PUNISHMENTS:
            return await interaction.response.send_message(
                embed=error_embed(f"Invalid punishment. Choose from: {', '.join(PUNISHMENTS)}"),
                ephemeral=True,
            )
        await db.set_filter(interaction.guild_id, filter_name, punishment=punishment)
        await interaction.response.send_message(
            embed=success_embed(f"Filter `{filter_name}` punishment set to `{punishment}`."), ephemeral=True
        )

    @app_commands.command(name="bulk", description="Enable or disable all content filters at once")
    @app_commands.describe(enabled="True to enable all, False to disable all")
    @is_staff()
    async def filters_bulk(self, interaction: discord.Interaction, enabled: bool):
        for f in FILTER_NAMES:
            await db.set_filter(interaction.guild_id, f, enabled=enabled)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"All filters {state}."), ephemeral=True
        )

    @app_commands.command(name="list", description="Show all filter states and punishments")
    @is_staff()
    async def filters_list(self, interaction: discord.Interaction):
        all_filters = await db.get_all_filters(interaction.guild_id)
        embed = discord.Embed(title="Automod Filters", color=0x5865F2)
        for f in FILTER_NAMES:
            data = all_filters.get(f, {"enabled": 1, "punishment": "none"})
            icon = ":green_circle:" if data["enabled"] else ":red_circle:"
            embed.add_field(
                name=f"{icon} {f.replace('_', ' ').title()}",
                value=f"Punishment: `{data['punishment']}`",
                inline=True,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @filters_toggle.autocomplete("filter_name")
    @filters_punishment.autocomplete("filter_name")
    async def filter_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f, value=f)
            for f in FILTER_NAMES if current.lower() in f.lower()
        ]

    @filters_punishment.autocomplete("punishment")
    async def punishment_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=p, value=p)
            for p in PUNISHMENTS if current.lower() in p.lower()
        ]


# ---------------------------------------------------------------------------
# Words group
# ---------------------------------------------------------------------------

class WordsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="words", description="Manage the banned word list")

    @app_commands.command(name="add", description="Add a word to the banned list")
    @app_commands.describe(word="Word or phrase to ban")
    @is_staff()
    async def words_add(self, interaction: discord.Interaction, word: str):
        added = await db.add_banned_word(interaction.guild_id, word.lower())
        if added:
            await interaction.response.send_message(
                embed=success_embed(f"Added `{word.lower()}` to the banned word list."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=info_embed(f"`{word.lower()}` is already in the banned word list."), ephemeral=True
            )

    @app_commands.command(name="remove", description="Remove a word from the banned list")
    @app_commands.describe(word="Word to remove")
    @is_staff()
    async def words_remove(self, interaction: discord.Interaction, word: str):
        removed = await db.remove_banned_word(interaction.guild_id, word.lower())
        if removed:
            await interaction.response.send_message(
                embed=success_embed(f"Removed `{word.lower()}` from the banned word list."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(f"`{word.lower()}` is not in the banned word list."), ephemeral=True
            )

    @app_commands.command(name="list", description="Show all banned words")
    @is_staff()
    async def words_list(self, interaction: discord.Interaction):
        words = await db.get_banned_words(interaction.guild_id)
        if not words:
            return await interaction.response.send_message(embed=info_embed("No banned words configured."), ephemeral=True)
        # Show in chunks of 30 per embed field
        chunks = [words[i:i+30] for i in range(0, len(words), 30)]
        embed = discord.Embed(title=f"Banned Words ({len(words)})", color=0xED4245)
        for i, chunk in enumerate(chunks[:5]):
            embed.add_field(name=f"Words {i*30+1}–{i*30+len(chunk)}", value=", ".join(f"`{w}`" for w in chunk), inline=False)
        if len(chunks) > 5:
            embed.set_footer(text=f"Showing first 150 of {len(words)} words")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class FeatureControl(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(FeaturesGroup())
        self.bot.tree.add_command(CmdsGroup())
        self.bot.tree.add_command(FiltersGroup())
        self.bot.tree.add_command(WordsGroup())

    @app_commands.command(name="about", description="About Aegixa")
    async def about(self, interaction: discord.Interaction):
        from config import PREMIUM_URL, SUPPORT_SERVER
        is_prem = await db.is_premium(interaction.guild_id) if interaction.guild else False
        embed = discord.Embed(
            title="Aegixa — Security & Moderation Bot",
            description=(
                "A full-featured Discord security and moderation bot.\n\n"
                "**Core Features**\n"
                "• 9 content filters with per-filter punishments\n"
                "• Full moderation suite (ban, kick, mute, temp-ban, purge…)\n"
                "• 9 independent log channels\n"
                "• Role swap & grant automation\n"
                "• Reaction roles, giveaways, sticky messages\n"
                "• Invite tracking, anti-raid auto-detection\n"
                "• Web dashboard with Discord OAuth2\n\n"
                "**Premium Features ⭐**\n"
                "• Phishing & scam link detection\n"
                "• Member verification gate\n\n"
                f"[Get Premium]({PREMIUM_URL})  |  [Support]({SUPPORT_SERVER})\n\n"
                "Use `/help` to browse all commands.\n"
                "Use `/setup staff` and `/setup logs` to get started."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Servers",  value=str(len(interaction.client.guilds)), inline=True)
        embed.add_field(name="Commands", value="50+",                                inline=True)
        embed.add_field(name="Premium",  value="⭐ Active" if is_prem else "🔓 Free", inline=True)
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FeatureControl(bot))
