"""Permission helpers for Aegixa commands."""

import os
import discord
from discord import app_commands
import database as db
from typing import Callable


def _is_owner(user_id: int) -> bool:
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    return owner_id != 0 and user_id == owner_id


def is_staff() -> Callable:
    """Check: bot owner, Manage Messages, OR a configured staff role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        if _is_owner(interaction.user.id):
            return True
        member = interaction.user
        if member.guild_permissions.manage_messages:
            return True
        staff_role_ids = {r["role_id"] for r in await db.get_guild_roles(interaction.guild_id, "staff")}
        if any(r.id in staff_role_ids for r in member.roles):
            return True
        await interaction.response.send_message(
            embed=discord.Embed(
                description=":no_entry: You need **Manage Messages** or a staff role to use this command.",
                color=0xED4245,
            ),
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)


def is_admin() -> Callable:
    """Check: bot owner OR Administrator permission."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        if _is_owner(interaction.user.id):
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            embed=discord.Embed(
                description=":no_entry: You need **Administrator** permission to use this command.",
                color=0xED4245,
            ),
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)


async def has_config_access_web(user_id: int, guild: discord.Guild) -> bool:
    """Dashboard access: bot owner, guild owner, or config role holder."""
    import os
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    if user_id == owner_id:
        return True
    if user_id == guild.owner_id:
        return True
    config_role_ids = {r["role_id"] for r in await db.get_guild_roles(guild.id, "config")}
    member = guild.get_member(user_id)
    if member and any(r.id in config_role_ids for r in member.roles):
        return True
    return False
