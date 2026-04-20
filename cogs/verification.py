"""
Member verification gate — button-based, assigns a verified role on click.
Premium feature. /verification setup/toggle/status
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.permissions import is_admin
from utils.helpers import success_embed, error_embed, info_embed
from cogs.logging_cog import send_log
from config import LOG_COLORS, PREMIUM_URL
import logging

log = logging.getLogger(__name__)


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅  Verify Me",
        style=discord.ButtonStyle.success,
        custom_id="aegixa:verify_v1",
    )
    async def verify(self, interaction: discord.Interaction, _: discord.ui.Button):
        guild = interaction.guild
        cfg = await db.get_verification(guild.id)

        if not cfg.get("verified_role_id"):
            return await interaction.response.send_message(
                embed=error_embed("Verification is not fully configured. Contact an admin."),
                ephemeral=True,
            )

        verified_role = guild.get_role(cfg["verified_role_id"])
        if not verified_role:
            return await interaction.response.send_message(
                embed=error_embed("Verified role not found — contact an admin."),
                ephemeral=True,
            )

        if verified_role in interaction.user.roles:
            return await interaction.response.send_message(
                embed=info_embed("You are already verified!"), ephemeral=True
            )

        try:
            await interaction.user.add_roles(verified_role, reason="[Aegixa] Verification gate")
            unverified_id = cfg.get("unverified_role_id")
            if unverified_id:
                unverified_role = guild.get_role(unverified_id)
                if unverified_role and unverified_role in interaction.user.roles:
                    await interaction.user.remove_roles(unverified_role, reason="[Aegixa] Verification gate")
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("I lack permission to assign that role."), ephemeral=True
            )

        await interaction.response.send_message(
            embed=discord.Embed(
                title=":white_check_mark: Verified!",
                description=f"Welcome to **{guild.name}**! You now have full access.",
                color=0x57F287,
            ),
            ephemeral=True,
        )

        await send_log(guild, "member", discord.Embed(
            description=f":white_check_mark: **{interaction.user}** (`{interaction.user.id}`) verified.",
            color=LOG_COLORS["member"],
            timestamp=discord.utils.utcnow(),
        ))


class VerificationGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="verification", description="Member verification system (Premium)")

    @app_commands.command(name="setup", description="Send the verification panel to a channel (Premium)")
    @app_commands.describe(
        channel="Channel for the verification panel",
        verified_role="Role granted after verification",
        unverified_role="Role removed after verification (optional)",
    )
    @is_admin()
    async def v_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        verified_role: discord.Role,
        unverified_role: discord.Role = None,
    ):
        if not await db.is_premium(interaction.guild_id):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="⭐ Premium Required",
                    description=f"The verification system requires Premium.\n[Upgrade here]({PREMIUM_URL})",
                    color=0xFFD700,
                ),
                ephemeral=True,
            )

        await db.set_verification(
            interaction.guild_id,
            verification_enabled=1,
            verification_channel_id=channel.id,
            verified_role_id=verified_role.id,
            unverified_role_id=unverified_role.id if unverified_role else None,
        )

        embed = discord.Embed(
            title="🛡️ Server Verification",
            description=(
                f"Welcome to **{interaction.guild.name}**!\n\n"
                "Click the button below to verify yourself and gain access to the server.\n"
                "By verifying, you agree to follow the server rules."
            ),
            color=0x5865F2,
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text="Powered by Aegixa")

        await channel.send(embed=embed, view=VerifyView())
        await interaction.response.send_message(
            embed=success_embed(f"Verification panel sent to {channel.mention}."), ephemeral=True
        )

    @app_commands.command(name="toggle", description="Enable or disable verification")
    @is_admin()
    async def v_toggle(self, interaction: discord.Interaction):
        cfg = await db.get_verification(interaction.guild_id)
        new_state = not cfg.get("verification_enabled", 0)
        await db.set_verification(interaction.guild_id, verification_enabled=int(new_state))
        state = "enabled" if new_state else "disabled"
        await interaction.response.send_message(
            embed=success_embed(f"Verification {state}."), ephemeral=True
        )

    @app_commands.command(name="status", description="Show verification configuration")
    @is_admin()
    async def v_status(self, interaction: discord.Interaction):
        cfg = await db.get_verification(interaction.guild_id)
        is_prem = await db.is_premium(interaction.guild_id)
        embed = discord.Embed(title="🛡️ Verification Status", color=0x5865F2)
        embed.add_field(name="Enabled", value="✅" if cfg.get("verification_enabled") else "❌", inline=True)
        embed.add_field(name="Premium", value="⭐ Yes" if is_prem else "🔓 No", inline=True)
        ch = interaction.guild.get_channel(cfg.get("verification_channel_id") or 0)
        embed.add_field(name="Channel", value=ch.mention if ch else "Not set", inline=True)
        role = interaction.guild.get_role(cfg.get("verified_role_id") or 0)
        embed.add_field(name="Verified Role", value=role.mention if role else "Not set", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(VerificationGroup())
        bot.add_view(VerifyView())  # re-register persistent view on restart


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
