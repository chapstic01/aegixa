"""
Automod cog — all content filters with per-filter punishments.
Filters: spam, word, image, sticker, external_emoji, link, invite, caps, rate_limit
"""

import discord
from discord.ext import commands
import database as db
from config import LOG_COLORS, DEFAULT_RATE_LIMIT_COUNT, DEFAULT_RATE_LIMIT_SECONDS, DEFAULT_CAPS_PERCENT, DEFAULT_CAPS_MIN_LENGTH
from utils.helpers import (
    message_has_links,
    message_has_media,
    message_has_sticker,
    message_has_external_emoji,
    mention_count,
    error_embed,
)
from utils.text_normalize import contains_banned_word
from utils.phishing import scan_message as phishing_scan
from datetime import timedelta
from collections import defaultdict
import time
import re
import logging

log = logging.getLogger(__name__)

DISCORD_INVITE_RE = re.compile(r"(discord\.gg|discord\.com/invite|discordapp\.com/invite)/\S+", re.IGNORECASE)

FILTER_SPAM = "spam"
FILTER_WORD = "word"
FILTER_IMAGE = "image"
FILTER_STICKER = "sticker"
FILTER_EXTERNAL_EMOJI = "external_emoji"
FILTER_LINK = "link"


class Automod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory rate limit tracker: {guild_id: {user_id: [timestamps]}}
        self._rate_cache: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _get_alert_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        g = await db.get_guild(guild.id)
        if not g or not g.get("alert_channel_id"):
            return None
        return guild.get_channel(g["alert_channel_id"])

    async def _get_alert_roles(self, guild: discord.Guild) -> list[discord.Role]:
        rows = await db.get_guild_roles(guild.id, "alert")
        roles = []
        for r in rows:
            role = guild.get_role(r["role_id"])
            if role:
                roles.append(role)
        return roles

    async def _send_alert(
        self,
        guild: discord.Guild,
        filter_name: str,
        message: discord.Message,
        reason: str,
    ):
        channel = await self._get_alert_channel(guild)
        if not channel:
            return
        roles = await self._get_alert_roles(guild)
        role_mentions = " ".join(r.mention for r in roles) if roles else ""

        embed = discord.Embed(
            title=f"Automod — {filter_name.replace('_', ' ').title()} Filter",
            description=reason,
            color=LOG_COLORS["spam"],
            timestamp=message.created_at,
        )
        embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        if message.content:
            embed.add_field(name="Content", value=message.content[:1000] or "*(no text)*", inline=False)
        embed.set_footer(text=f"Message ID: {message.id}")

        await channel.send(content=role_mentions or None, embed=embed)

        # Log to spam log channel
        spam_ch_id = await db.get_log_channel(guild.id, "spam")
        if spam_ch_id and spam_ch_id != channel.id:
            spam_ch = guild.get_channel(spam_ch_id)
            if spam_ch:
                await spam_ch.send(embed=embed)

    async def _apply_punishment(
        self,
        message: discord.Message,
        punishment: str,
        filter_name: str,
        reason: str,
    ):
        member = message.author
        guild = message.guild
        if not isinstance(member, discord.Member):
            return

        # Skip bots and admins
        if member.bot or member.guild_permissions.administrator:
            return

        try:
            if punishment == "warn":
                await db.add_warning(guild.id, member.id, self.bot.user.id, f"[Automod] {reason}")
                try:
                    await member.send(
                        embed=discord.Embed(
                            description=f":warning: You have been warned in **{guild.name}**.\nReason: {reason}",
                            color=0xFEE75C,
                        )
                    )
                except discord.Forbidden:
                    pass
            elif punishment == "mute":
                await member.timeout(timedelta(minutes=10), reason=f"[Automod] {reason}")
            elif punishment == "kick":
                try:
                    await member.send(
                        embed=discord.Embed(
                            description=f":boot: You have been kicked from **{guild.name}**.\nReason: {reason}",
                            color=0xED4245,
                        )
                    )
                except discord.Forbidden:
                    pass
                await member.kick(reason=f"[Automod] {reason}")
            elif punishment == "ban":
                try:
                    await member.send(
                        embed=discord.Embed(
                            description=f":hammer: You have been banned from **{guild.name}**.\nReason: {reason}",
                            color=0xED4245,
                        )
                    )
                except discord.Forbidden:
                    pass
                await guild.ban(member, reason=f"[Automod] {reason}", delete_message_days=0)
        except discord.HTTPException as e:
            log.warning("Automod punishment failed: %s", e)

    async def _is_excluded(self, message: discord.Message) -> bool:
        if not message.guild:
            return True
        if message.author.bot:
            return True
        excluded = await db.get_excluded_channels(message.guild.id)
        return message.channel.id in excluded

    async def _feature_enabled(self, guild_id: int) -> bool:
        return await db.get_feature(guild_id, "automod")

    # -----------------------------------------------------------------------
    # Main listener
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if await self._is_excluded(message):
            return
        if not await self._feature_enabled(message.guild.id):
            return

        await self._run_filters(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild:
            return
        if await self._is_excluded(after):
            return
        if not await self._feature_enabled(after.guild.id):
            return
        # Only recheck word filter on edits
        f = await db.get_filter(after.guild.id, FILTER_WORD)
        if f["enabled"]:
            words = await db.get_banned_words(after.guild.id)
            matched = contains_banned_word(after.content, words)
            if matched:
                try:
                    await after.delete()
                except discord.HTTPException:
                    pass
                reason = f"Edited message contained banned word: `{matched}`"
                await self._send_alert(after.guild, "Word", after, reason)
                await self._apply_punishment(after, f["punishment"], FILTER_WORD, reason)

    # -----------------------------------------------------------------------
    # Filter runner
    # -----------------------------------------------------------------------

    async def _run_filters(self, message: discord.Message):
        guild = message.guild
        member = message.author

        # Skip bots
        if member.bot:
            return

        # Skip admins and staff for all filters
        if isinstance(member, discord.Member) and member.guild_permissions.administrator:
            return

        # ---- Spam filter (links + mass mentions) ----
        f_spam = await db.get_filter(guild.id, FILTER_SPAM)
        if f_spam["enabled"]:
            triggered = False
            reason = ""
            if message_has_links(message):
                triggered = True
                reason = "Message contained a link."
            elif mention_count(message) >= 5:
                triggered = True
                reason = f"Message contained {mention_count(message)} user mentions."
            if triggered:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self._send_alert(guild, "Spam", message, reason)
                await self._apply_punishment(message, f_spam["punishment"], FILTER_SPAM, reason)
                return

        # ---- Link filter (separate from spam — links only) ----
        f_link = await db.get_filter(guild.id, FILTER_LINK)
        if f_link["enabled"]:
            if message_has_links(message):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = "Message contained a link."
                await self._send_alert(guild, "Link", message, reason)
                await self._apply_punishment(message, f_link["punishment"], FILTER_LINK, reason)
                return

        # ---- Word filter ----
        f_word = await db.get_filter(guild.id, FILTER_WORD)
        if f_word["enabled"] and message.content:
            words = await db.get_banned_words(guild.id)
            if words:
                matched = contains_banned_word(message.content, words)
                if matched:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    reason = f"Message contained banned word: `{matched}`"
                    await self._send_alert(guild, "Word", message, reason)
                    await self._apply_punishment(message, f_word["punishment"], FILTER_WORD, reason)
                    return

        # ---- Image / GIF block ----
        f_image = await db.get_filter(guild.id, FILTER_IMAGE)
        if f_image["enabled"]:
            if message_has_media(message):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = "Image/GIF uploads are blocked in this server."
                await self._send_alert(guild, "Image", message, reason)
                await self._apply_punishment(message, f_image["punishment"], FILTER_IMAGE, reason)
                return

        # ---- Sticker block ----
        f_sticker = await db.get_filter(guild.id, FILTER_STICKER)
        if f_sticker["enabled"]:
            if message_has_sticker(message):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = "Sticker messages are not allowed."
                await self._send_alert(guild, "Sticker", message, reason)
                await self._apply_punishment(message, f_sticker["punishment"], FILTER_STICKER, reason)
                return

        # ---- External emoji block ----
        f_emoji = await db.get_filter(guild.id, FILTER_EXTERNAL_EMOJI)
        if f_emoji["enabled"]:
            if message_has_external_emoji(message):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = "External server emojis are not allowed."
                await self._send_alert(guild, "External Emoji", message, reason)
                await self._apply_punishment(message, f_emoji["punishment"], FILTER_EXTERNAL_EMOJI, reason)
                return

        # ---- Anti-invite filter ----
        f_invite = await db.get_filter(guild.id, "invite")
        if f_invite["enabled"] and message.content:
            if DISCORD_INVITE_RE.search(message.content):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = "Discord invite links are not allowed."
                await self._send_alert(guild, "Anti-Invite", message, reason)
                await self._apply_punishment(message, f_invite["punishment"], "invite", reason)
                return

        # ---- Caps filter ----
        f_caps = await db.get_filter(guild.id, "caps")
        if f_caps["enabled"] and message.content:
            settings = await db.get_guild_settings(guild.id)
            min_len = settings.get("caps_min_length", DEFAULT_CAPS_MIN_LENGTH)
            caps_pct = settings.get("caps_percent", DEFAULT_CAPS_PERCENT)
            letters = [c for c in message.content if c.isalpha()]
            if len(letters) >= min_len:
                upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters) * 100
                if upper_ratio >= caps_pct:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    reason = f"Message was {int(upper_ratio)}% uppercase (limit: {caps_pct}%)."
                    await self._send_alert(guild, "Caps Filter", message, reason)
                    await self._apply_punishment(message, f_caps["punishment"], "caps", reason)
                    return

        # ---- Phishing / scam detection (Premium) ----
        if await db.is_premium(guild.id) and message.content:
            f_phish = await db.get_filter(guild.id, "phishing")
            if f_phish["enabled"]:
                flagged, phish_reason = phishing_scan(message.content)
                if flagged:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    await self._send_alert(guild, "Phishing", message, phish_reason)
                    await self._apply_punishment(message, f_phish["punishment"], "phishing", phish_reason)
                    return

        # ---- Rate limit filter ----
        f_rate = await db.get_filter(guild.id, "rate_limit")
        if f_rate["enabled"]:
            settings = await db.get_guild_settings(guild.id)
            limit_count = settings.get("rate_limit_count", DEFAULT_RATE_LIMIT_COUNT)
            limit_secs = settings.get("rate_limit_seconds", DEFAULT_RATE_LIMIT_SECONDS)
            now = time.monotonic()
            user_times = self._rate_cache[guild.id][member.id]
            # Purge old timestamps outside the window
            user_times[:] = [t for t in user_times if now - t < limit_secs]
            user_times.append(now)
            if len(user_times) >= limit_count:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = f"Sending messages too fast ({limit_count} in {limit_secs}s)."
                await self._send_alert(guild, "Rate Limit", message, reason)
                await self._apply_punishment(message, f_rate["punishment"], "rate_limit", reason)
                user_times.clear()
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(Automod(bot))
