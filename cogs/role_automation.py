"""
Role automation cog — swap and grant rules fired on every role-change event.
Swap: gaining role A → remove role B
Grant: gaining role A → add role B
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.helpers import error_embed, success_embed, info_embed
from utils.permissions import is_staff
from config import LOG_COLORS
from cogs.logging_cog import send_log
import logging

log = logging.getLogger(__name__)


class RoleAutomationGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="roleauto", description="Role automation rules")

    # ---------- Swap subgroup ----------

    swap = app_commands.Group(name="swap", description="Role swap rules", parent=None)

    @app_commands.command(name="swapadd", description="Add a role swap rule (gaining trigger removes target)")
    @app_commands.describe(trigger="Role that triggers the swap", remove="Role to remove", note="Optional note")
    @is_staff()
    async def swap_add(self, interaction: discord.Interaction, trigger: discord.Role, remove: discord.Role, note: str = ""):
        rule_id = await db.add_role_swap(interaction.guild_id, trigger.id, remove.id, note)
        await interaction.response.send_message(embed=success_embed(
            f"Swap rule `#{rule_id}` added: gaining **{trigger.name}** → removes **{remove.name}**"
            + (f"\nNote: {note}" if note else "")
        ), ephemeral=True)
        await send_log(interaction.guild, "general", discord.Embed(
            description=f":twisted_rightwards_arrows: **{interaction.user}** added role swap rule #{rule_id}: gain **{trigger.name}** → remove **{remove.name}**",
            color=LOG_COLORS["general"],
        ))

    @app_commands.command(name="swapremove", description="Remove a swap rule by ID")
    @app_commands.describe(rule_id="The rule ID to remove")
    @is_staff()
    async def swap_remove(self, interaction: discord.Interaction, rule_id: int):
        removed = await db.remove_role_swap(interaction.guild_id, rule_id)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Swap rule `#{rule_id}` removed."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Swap rule `#{rule_id}` not found."), ephemeral=True)

    @app_commands.command(name="swaplist", description="List all role swap rules")
    @is_staff()
    async def swap_list(self, interaction: discord.Interaction):
        rules = await db.get_role_swaps(interaction.guild_id)
        if not rules:
            return await interaction.response.send_message(embed=info_embed("No swap rules configured."), ephemeral=True)
        embed = discord.Embed(title="Role Swap Rules", color=LOG_COLORS["roles"])
        for r in rules:
            trigger = interaction.guild.get_role(r["trigger_role_id"])
            remove = interaction.guild.get_role(r["remove_role_id"])
            embed.add_field(
                name=f"Rule #{r['id']}",
                value=f"Gain: {trigger.mention if trigger else r['trigger_role_id']}\nRemove: {remove.mention if remove else r['remove_role_id']}"
                      + (f"\nNote: {r['note']}" if r["note"] else ""),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- Grant subgroup ----------

    @app_commands.command(name="grantadd", description="Add a role grant rule (gaining trigger also grants target)")
    @app_commands.describe(trigger="Role that triggers the grant", grant="Role to also grant", note="Optional note")
    @is_staff()
    async def grant_add(self, interaction: discord.Interaction, trigger: discord.Role, grant: discord.Role, note: str = ""):
        rule_id = await db.add_role_grant(interaction.guild_id, trigger.id, grant.id, note)
        await interaction.response.send_message(embed=success_embed(
            f"Grant rule `#{rule_id}` added: gaining **{trigger.name}** → also grants **{grant.name}**"
            + (f"\nNote: {note}" if note else "")
        ), ephemeral=True)
        await send_log(interaction.guild, "general", discord.Embed(
            description=f":gift: **{interaction.user}** added role grant rule #{rule_id}: gain **{trigger.name}** → grant **{grant.name}**",
            color=LOG_COLORS["general"],
        ))

    @app_commands.command(name="grantremove", description="Remove a grant rule by ID")
    @app_commands.describe(rule_id="The rule ID to remove")
    @is_staff()
    async def grant_remove(self, interaction: discord.Interaction, rule_id: int):
        removed = await db.remove_role_grant(interaction.guild_id, rule_id)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Grant rule `#{rule_id}` removed."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Grant rule `#{rule_id}` not found."), ephemeral=True)

    @app_commands.command(name="grantlist", description="List all role grant rules")
    @is_staff()
    async def grant_list(self, interaction: discord.Interaction):
        rules = await db.get_role_grants(interaction.guild_id)
        if not rules:
            return await interaction.response.send_message(embed=info_embed("No grant rules configured."), ephemeral=True)
        embed = discord.Embed(title="Role Grant Rules", color=LOG_COLORS["roles"])
        for r in rules:
            trigger = interaction.guild.get_role(r["trigger_role_id"])
            grant = interaction.guild.get_role(r["grant_role_id"])
            embed.add_field(
                name=f"Rule #{r['id']}",
                value=f"Gain: {trigger.mention if trigger else r['trigger_role_id']}\nGrant: {grant.mention if grant else r['grant_role_id']}"
                      + (f"\nNote: {r['note']}" if r["note"] else ""),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RoleAutomation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(RoleAutomationGroup())

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not await db.get_feature(after.guild.id, "role_automation"):
            return

        before_role_ids = {r.id for r in before.roles}
        after_role_ids = {r.id for r in after.roles}
        gained = after_role_ids - before_role_ids

        if not gained:
            return

        # Process swap rules
        swap_rules = await db.get_role_swaps(after.guild.id)
        for rule in swap_rules:
            if rule["trigger_role_id"] in gained:
                remove_role = after.guild.get_role(rule["remove_role_id"])
                if remove_role and remove_role in after.roles:
                    try:
                        await after.remove_roles(remove_role, reason=f"[Aegixa] Role swap rule #{rule['id']}")
                        await send_log(after.guild, "general", discord.Embed(
                            description=f":twisted_rightwards_arrows: Auto-removed **{remove_role.name}** from **{after}** (swap rule #{rule['id']})",
                            color=LOG_COLORS["general"],
                        ))
                    except discord.HTTPException as e:
                        log.warning("Swap rule failed: %s", e)

        # Process grant rules
        grant_rules = await db.get_role_grants(after.guild.id)
        for rule in grant_rules:
            if rule["trigger_role_id"] in gained:
                grant_role = after.guild.get_role(rule["grant_role_id"])
                if grant_role and grant_role not in after.roles:
                    try:
                        await after.add_roles(grant_role, reason=f"[Aegixa] Role grant rule #{rule['id']}")
                        await send_log(after.guild, "general", discord.Embed(
                            description=f":gift: Auto-granted **{grant_role.name}** to **{after}** (grant rule #{rule['id']})",
                            color=LOG_COLORS["general"],
                        ))
                    except discord.HTTPException as e:
                        log.warning("Grant rule failed: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleAutomation(bot))
