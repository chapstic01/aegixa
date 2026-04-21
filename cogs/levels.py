"""
XP / Levels system (premium-gated).
on_message grants XP with cooldown; level-up posts to configured channel.
/level /leaderboard /levelroles add/remove/list /xp set/give/reset /levelconfig
"""

import math
import random
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import database as db
from utils.helpers import success_embed, error_embed, info_embed
from utils.permissions import is_admin, is_staff
import logging

log = logging.getLogger(__name__)


def _xp_for_level(level: int) -> int:
    """Total XP required to reach `level` (MEE6-style cumulative)."""
    return 5 * level * level + 50 * level + 100


def _level_from_xp(xp: int) -> int:
    """Calculate level from total XP."""
    level = 0
    while xp >= _xp_for_level(level):
        xp -= _xp_for_level(level)
        level += 1
    return level


def _xp_progress(total_xp: int) -> tuple[int, int, int]:
    """Returns (level, current_xp_in_level, xp_needed_for_next_level)."""
    level = 0
    remaining = total_xp
    while remaining >= _xp_for_level(level):
        remaining -= _xp_for_level(level)
        level += 1
    return level, remaining, _xp_for_level(level)


async def _is_premium(guild_id: int) -> bool:
    return await db.is_premium(guild_id)


# ---------------------------------------------------------------------------
# Command groups
# ---------------------------------------------------------------------------

class LevelRolesGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="levelroles", description="Configure roles awarded at certain levels")

    @app_commands.command(name="add", description="Award a role when a member reaches a level")
    @app_commands.describe(level="Level that triggers the role award", role="Role to give")
    @is_admin()
    async def lr_add(self, interaction: discord.Interaction, level: int, role: discord.Role):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        if level < 1:
            return await interaction.response.send_message(
                embed=error_embed("Level must be 1 or higher."), ephemeral=True
            )
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                embed=error_embed("That role is above my highest role."), ephemeral=True
            )
        await db.add_level_role(interaction.guild_id, level, role.id)
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} will be awarded at **level {level}**."), ephemeral=True
        )

    @app_commands.command(name="remove", description="Remove a level role reward")
    @app_commands.describe(level="Level to remove the role reward from")
    @is_admin()
    async def lr_remove(self, interaction: discord.Interaction, level: int):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        removed = await db.remove_level_role(interaction.guild_id, level)
        if removed:
            await interaction.response.send_message(
                embed=success_embed(f"Level {level} role reward removed."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(f"No role reward found for level {level}."), ephemeral=True
            )

    @app_commands.command(name="list", description="List all level role rewards")
    @is_staff()
    async def lr_list(self, interaction: discord.Interaction):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        rows = await db.get_level_roles(interaction.guild_id)
        if not rows:
            return await interaction.response.send_message(
                embed=info_embed("No level roles configured."), ephemeral=True
            )
        lines = []
        for r in sorted(rows, key=lambda x: x["level"]):
            role = interaction.guild.get_role(r["role_id"])
            name = role.mention if role else f"*(deleted role {r['role_id']})*"
            lines.append(f"**Level {r['level']}** → {name}")
        embed = discord.Embed(title="Level Role Rewards", description="\n".join(lines), color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class XPAdminGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="xp", description="Manage XP for members")

    @app_commands.command(name="set", description="Set a member's XP directly")
    @app_commands.describe(member="Member to update", amount="New XP total")
    @is_admin()
    async def xp_set(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        if amount < 0:
            return await interaction.response.send_message(
                embed=error_embed("XP cannot be negative."), ephemeral=True
            )
        level = _level_from_xp(amount)
        await db.set_user_xp(interaction.guild_id, member.id, amount, level)
        await interaction.response.send_message(
            embed=success_embed(f"{member.mention} XP set to **{amount}** (level {level})."), ephemeral=True
        )

    @app_commands.command(name="give", description="Give XP to a member")
    @app_commands.describe(member="Member to give XP to", amount="Amount of XP to add")
    @is_admin()
    async def xp_give(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        if amount <= 0:
            return await interaction.response.send_message(
                embed=error_embed("Amount must be positive."), ephemeral=True
            )
        row = await db.get_user_xp(interaction.guild_id, member.id)
        new_xp = (row["xp"] if row else 0) + amount
        new_level = _level_from_xp(new_xp)
        await db.set_user_xp(interaction.guild_id, member.id, new_xp, new_level)
        await interaction.response.send_message(
            embed=success_embed(f"Added **{amount} XP** to {member.mention} (total {new_xp}, level {new_level})."), ephemeral=True
        )

    @app_commands.command(name="reset", description="Reset a member's XP to zero")
    @app_commands.describe(member="Member to reset")
    @is_admin()
    async def xp_reset(self, interaction: discord.Interaction, member: discord.Member):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        await db.set_user_xp(interaction.guild_id, member.id, 0, 0)
        await interaction.response.send_message(
            embed=success_embed(f"{member.mention}'s XP has been reset."), ephemeral=True
        )


class LevelConfigGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="levelconfig", description="Configure the XP/levels system")

    @app_commands.command(name="channel", description="Set the channel for level-up announcements")
    @app_commands.describe(channel="Channel to post level-up messages in (leave empty to use current channel)")
    @is_admin()
    async def lc_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        ch = channel or interaction.channel
        await db.set_xp_config(interaction.guild_id, levelup_channel_id=ch.id)
        await interaction.response.send_message(
            embed=success_embed(f"Level-up announcements will post in {ch.mention}."), ephemeral=True
        )

    @app_commands.command(name="toggle", description="Enable or disable the XP/levels system")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def lc_toggle(self, interaction: discord.Interaction, enabled: bool):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        await db.set_xp_config(interaction.guild_id, enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"XP/Levels system {state}."), ephemeral=True
        )

    @app_commands.command(name="cooldown", description="Set XP cooldown between messages (seconds)")
    @app_commands.describe(seconds="Cooldown in seconds (10–600)")
    @is_admin()
    async def lc_cooldown(self, interaction: discord.Interaction, seconds: int):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        if not 10 <= seconds <= 600:
            return await interaction.response.send_message(
                embed=error_embed("Cooldown must be between 10 and 600 seconds."), ephemeral=True
            )
        await db.set_xp_config(interaction.guild_id, cooldown_seconds=seconds)
        await interaction.response.send_message(
            embed=success_embed(f"XP cooldown set to **{seconds}s**."), ephemeral=True
        )

    @app_commands.command(name="voicexp", description="Enable or disable XP for time spent in voice channels")
    @app_commands.describe(enabled="True to enable, False to disable")
    @is_admin()
    async def lc_voicexp(self, interaction: discord.Interaction, enabled: bool):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        await db.set_xp_config(interaction.guild_id, voice_xp_enabled=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"Voice XP {state}. Members now earn XP for time spent in voice channels."),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Levels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(LevelRolesGroup())
        self.bot.tree.add_command(XPAdminGroup())
        self.bot.tree.add_command(LevelConfigGroup())
        # voice session tracking: {(guild_id, user_id): join_datetime}
        self._voice_sessions: dict[tuple[int, int], datetime] = {}

    @app_commands.command(name="level", description="Check your level and XP progress")
    @app_commands.describe(member="Member to check (defaults to you)")
    async def level(self, interaction: discord.Interaction, member: discord.Member = None):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        target = member or interaction.user
        row = await db.get_user_xp(interaction.guild_id, target.id)
        total_xp = row["xp"] if row else 0
        lvl, current, needed = _xp_progress(total_xp)

        bar_filled = int((current / needed) * 20) if needed else 20
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        embed = discord.Embed(color=0x5865F2)
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)
        embed.add_field(name="Level", value=str(lvl), inline=True)
        embed.add_field(name="Total XP", value=str(total_xp), inline=True)
        embed.add_field(name="Progress", value=f"`{bar}` {current}/{needed} XP", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show the top 10 members by XP")
    async def leaderboard(self, interaction: discord.Interaction):
        if not await _is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=error_embed("The XP/Levels system requires **Aegixa Premium**."), ephemeral=True
            )
        rows = await db.get_xp_leaderboard(interaction.guild_id, limit=10)
        if not rows:
            return await interaction.response.send_message(
                embed=info_embed("No XP data yet — start chatting!"), ephemeral=True
            )
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            member = interaction.guild.get_member(r["user_id"])
            name = member.display_name if member else f"User {r['user_id']}"
            prefix = medals[i] if i < 3 else f"**{i+1}.**"
            lvl, _, _ = _xp_progress(r["xp"])
            lines.append(f"{prefix} {name} — Level {lvl} ({r['xp']} XP)")
        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            description="\n".join(lines),
            color=0xFFAC33,
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not await _is_premium(message.guild.id):
            return

        cfg = await db.get_xp_config(message.guild.id)
        if not cfg["enabled"]:
            return

        row = await db.get_user_xp(message.guild.id, message.author.id)
        now = datetime.now(timezone.utc)

        # Cooldown check
        if row and row["last_xp_at"]:
            last = datetime.fromisoformat(row["last_xp_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < cfg["cooldown_seconds"]:
                return

        xp_gain = random.randint(cfg["xp_min"], cfg["xp_max"])
        old_xp = row["xp"] if row else 0
        # Calculate old_level from XP directly — the DB level column is only
        # updated here and can't be trusted as a source of truth.
        old_level = _level_from_xp(old_xp)
        new_xp = old_xp + xp_gain
        new_level = _level_from_xp(new_xp)

        await db.add_user_xp(message.guild.id, message.author.id, xp_gain)

        if new_level > old_level:
            await db.update_user_level(message.guild.id, message.author.id, new_level)
            await self._handle_levelup(message, new_level, cfg)

    async def _handle_levelup(self, message: discord.Message, new_level: int, cfg: dict):
        member = message.author

        # Assign level roles
        level_roles = await db.get_level_roles(message.guild.id)
        for lr in level_roles:
            if lr["level"] <= new_level:
                role = message.guild.get_role(lr["role_id"])
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Level {lr['level']} reward")
                    except discord.HTTPException:
                        pass

        # Send level-up message
        ch_id = cfg.get("levelup_channel_id")
        channel = message.guild.get_channel(ch_id) if ch_id else message.channel
        if not channel:
            channel = message.channel

        text = (
            cfg.get("levelup_message", "GG {mention}, you reached **level {level}**! 🎉")
            .replace("{mention}", member.mention)
            .replace("{user}", member.display_name)
            .replace("{level}", str(new_level))
            .replace("{server}", message.guild.name)
        )
        try:
            await channel.send(text)
        except discord.HTTPException:
            pass


    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        if not await _is_premium(member.guild.id):
            return

        cfg = await db.get_xp_config(member.guild.id)
        if not cfg.get("voice_xp_enabled") or not cfg["enabled"]:
            return

        key = (member.guild.id, member.id)
        now = datetime.now(timezone.utc)

        # Joined a voice channel
        if before.channel is None and after.channel is not None:
            self._voice_sessions[key] = now

        # Left a voice channel
        elif before.channel is not None and after.channel is None:
            join_time = self._voice_sessions.pop(key, None)
            if join_time:
                minutes = (now - join_time).total_seconds() / 60
                xp_gain = int(minutes * cfg.get("voice_xp_per_minute", 1))
                if xp_gain > 0:
                    row = await db.get_user_xp(member.guild.id, member.id)
                    old_level = _level_from_xp(row["xp"] if row else 0)
                    await db.add_user_xp(member.guild.id, member.id, xp_gain)
                    updated = await db.get_user_xp(member.guild.id, member.id)
                    new_level = _level_from_xp(updated["xp"])
                    if new_level > old_level:
                        await db.update_user_level(member.guild.id, member.id, new_level)
                        class _FakeMsg:
                            guild = member.guild
                            author = member
                            channel = before.channel
                        await self._handle_levelup(_FakeMsg(), new_level, cfg)


async def setup(bot: commands.Bot):
    await bot.add_cog(Levels(bot))
