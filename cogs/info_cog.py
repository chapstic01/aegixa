"""
Info & utility commands — member, avatar, server, roles.
"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.helpers import resolve_member, info_embed
import logging

log = logging.getLogger(__name__)


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="member", description="Look up a member's info")
    @app_commands.describe(member="Username, display name, or mention (defaults to you)")
    async def member_info(self, interaction: discord.Interaction, member: str = None):
        await interaction.response.defer(ephemeral=True)
        if member:
            target = await resolve_member(interaction.guild, member)
            if not target:
                return await interaction.followup.send(
                    embed=discord.Embed(description=f":x: Member `{member}` not found.", color=0xED4245),
                    ephemeral=True,
                )
        else:
            target = interaction.user

        embed = discord.Embed(title=str(target), color=target.color if isinstance(target, discord.Member) else 0x5865F2)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="ID", value=str(target.id), inline=True)
        embed.add_field(name="Display Name", value=target.display_name, inline=True)
        embed.add_field(
            name="Account Created",
            value=f"<t:{int(target.created_at.timestamp())}:F> (<t:{int(target.created_at.timestamp())}:R>)",
            inline=False,
        )
        if isinstance(target, discord.Member):
            if target.joined_at:
                embed.add_field(
                    name="Joined Server",
                    value=f"<t:{int(target.joined_at.timestamp())}:F> (<t:{int(target.joined_at.timestamp())}:R>)",
                    inline=False,
                )
            roles = [r for r in reversed(target.roles) if r != interaction.guild.default_role]
            if roles:
                role_str = " ".join(r.mention for r in roles[:20])
                if len(roles) > 20:
                    role_str += f" ... (+{len(roles)-20} more)"
                embed.add_field(name=f"Roles ({len(roles)})", value=role_str, inline=False)
            if target.premium_since:
                embed.add_field(name="Boosting Since", value=f"<t:{int(target.premium_since.timestamp())}:R>", inline=True)
        embed.set_footer(text=f"Bot: {target.bot}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="avatar", description="Show a member's full-size avatar")
    @app_commands.describe(member="Username, display name, or mention (defaults to you)")
    async def avatar(self, interaction: discord.Interaction, member: str = None):
        await interaction.response.defer()
        if member:
            target = await resolve_member(interaction.guild, member)
            if not target:
                return await interaction.followup.send(
                    embed=discord.Embed(description=f":x: Member `{member}` not found.", color=0xED4245)
                )
        else:
            target = interaction.user

        avatar_url = target.display_avatar.url
        static_url = target.display_avatar.with_format("png").url
        gif_url = target.display_avatar.with_format("gif").url if target.display_avatar.is_animated() else None

        embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=0x5865F2)
        embed.set_image(url=avatar_url)

        links = [f"[PNG]({static_url})"]
        if gif_url:
            links.append(f"[GIF]({gif_url})")
        links.append(f"[WebP]({target.display_avatar.with_format('webp').url})")
        embed.description = " | ".join(links)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="server", description="Show server statistics")
    async def server_info(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=guild.name, color=0x5865F2)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="ID", value=str(guild.id), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans
        embed.add_field(name="Humans / Bots", value=f"{humans} / {bots}", inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        embed.add_field(name="Text / Voice", value=f"{text_channels} / {voice_channels}", inline=True)
        embed.add_field(name="Boost Level", value=f"Level {guild.premium_tier} ({guild.premium_subscription_count} boosts)", inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).title(), inline=True)
        if guild.description:
            embed.description = guild.description
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roles", description="List all server roles with member counts")
    async def roles_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        roles = sorted(
            [r for r in interaction.guild.roles if r != interaction.guild.default_role],
            key=lambda r: r.position,
            reverse=True,
        )
        lines = []
        for role in roles:
            lines.append(f"{role.mention} — {len(role.members)} members")

        # Split into pages of 20
        pages = [lines[i:i+20] for i in range(0, len(lines), 20)]
        embed = discord.Embed(
            title=f"Server Roles ({len(roles)})",
            description="\n".join(pages[0]) if pages else "No roles.",
            color=0x5865F2,
        )
        if len(pages) > 1:
            embed.set_footer(text=f"Showing 1-20 of {len(roles)} roles")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Info(bot))
