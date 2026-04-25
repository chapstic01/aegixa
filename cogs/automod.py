"""
Automod cog — all content filters with per-filter punishments.
Filters: spam, word, image, sticker, external_emoji, link, invite, caps,
         rate_limit, mentions, zalgo, repeated_chars, emoji_spam, phishing
"""

import unicodedata
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

DISCORD_INVITE_RE  = re.compile(r"(discord\.gg|discord\.com/invite|discordapp\.com/invite)/\S+", re.IGNORECASE)
REPEATED_CHARS_RE  = re.compile(r"(.)\1{8,}", re.UNICODE)
CUSTOM_EMOJI_RE    = re.compile(r"<a?:\w+:\d+>")
EMOJI_SPAM_THRESHOLD = 8  # default; configurable via guild_settings in future

FILTER_SPAM           = "spam"
FILTER_WORD           = "word"
FILTER_IMAGE          = "image"
FILTER_STICKER        = "sticker"
FILTER_EXTERNAL_EMOJI = "external_emoji"
FILTER_LINK           = "link"
FILTER_MENTIONS       = "mentions"
FILTER_ZALGO          = "zalgo"
FILTER_REPEATED       = "repeated_chars"
FILTER_EMOJI_SPAM     = "emoji_spam"
MENTION_THRESHOLD     = 5

# Alert deduplication window (seconds): max one alert per guild+filter per window
ALERT_DEDUP_WINDOW = 30


def _is_zalgo(text: str) -> bool:
    """Detect zalgo / combining-mark spam."""
    if not text:
        return False
    combining = sum(1 for c in text if unicodedata.category(c) == "Mn")
    letters   = sum(1 for c in text if c.isalpha())
    if letters < 5:
        return False
    return combining >= max(5, letters * 0.4)


def _has_repeated_chars(text: str) -> bool:
    """Detect 9+ consecutive identical characters: 'aaaaaaaaaa'."""
    return bool(REPEATED_CHARS_RE.search(text))


def _count_emoji(message: discord.Message) -> int:
    """Count total emoji (custom + Unicode) in a message."""
    text = message.content or ""
    custom = len(CUSTOM_EMOJI_RE.findall(text))
    unicode_em = sum(
        1 for c in text
        if "\U0001F300" <= c <= "\U0001FAFF"
        or "\U00002600" <= c <= "\U000027BF"
        or "\U0001F000" <= c <= "\U0001F02F"
    )
    return custom + unicode_em


class Automod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, filter) last-alert timestamp for deduplication
        self._alert_cooldown: dict[tuple[int, str], float] = {}
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
        # Deduplication: skip if same guild+filter alerted recently
        key = (guild.id, filter_name)
        now = time.monotonic()
        if now - self._alert_cooldown.get(key, 0) < ALERT_DEDUP_WINDOW:
            return
        self._alert_cooldown[key] = now

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
        guild  = message.guild
        if not isinstance(member, discord.Member):
            return
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

    async def _is_exempt(self, message: discord.Message) -> bool:
        """Return True if the message author holds an automod-exempt role."""
        member = message.author
        if not isinstance(member, discord.Member):
            return False
        exempt_ids = await db.get_automod_exempt_roles(message.guild.id)
        if not exempt_ids:
            return False
        return any(r.id in exempt_ids for r in member.roles)

    async def _feature_enabled(self, guild_id: int) -> bool:
        return await db.get_feature(guild_id, "automod")

    # -----------------------------------------------------------------------
    # Main listeners
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
        if before.content == after.content:
            return
        if await self._is_excluded(after):
            return
        if not await self._feature_enabled(after.guild.id):
            return
        # Run all filters on edited messages (is_edit=True skips rate-limit counting)
        await self._run_filters(after, is_edit=True)

    # -----------------------------------------------------------------------
    # Filter runner
    # -----------------------------------------------------------------------

    async def _run_filters(self, message: discord.Message, *, is_edit: bool = False):
        guild  = message.guild
        member = message.author

        if member.bot:
            return

        # Skip admins and staff
        if isinstance(member, discord.Member) and member.guild_permissions.administrator:
            return

        # Skip exempt roles
        if await self._is_exempt(message):
            return

        # ---- Spam filter (links + mass mentions) ----
        f_spam = await db.get_filter(guild.id, FILTER_SPAM)
        if f_spam["enabled"]:
            triggered, reason = False, ""
            if message_has_links(message):
                triggered, reason = True, "Message contained a link."
            elif mention_count(message) >= MENTION_THRESHOLD:
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

        # ---- Link filter ----
        f_link = await db.get_filter(guild.id, FILTER_LINK)
        if f_link["enabled"] and message_has_links(message):
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
        if f_image["enabled"] and message_has_media(message):
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
        if f_sticker["enabled"] and message_has_sticker(message):
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
        if f_emoji["enabled"] and message_has_external_emoji(message):
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
        if f_invite["enabled"] and message.content and DISCORD_INVITE_RE.search(message.content):
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
            settings   = await db.get_guild_settings(guild.id)
            min_len    = settings.get("caps_min_length", DEFAULT_CAPS_MIN_LENGTH)
            caps_pct   = settings.get("caps_percent", DEFAULT_CAPS_PERCENT)
            letters    = [c for c in message.content if c.isalpha()]
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

        # ---- Zalgo / Unicode-combining spam ----
        f_zalgo = await db.get_filter(guild.id, FILTER_ZALGO)
        if f_zalgo["enabled"] and message.content and _is_zalgo(message.content):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            reason = "Message contained zalgo / Unicode combining-mark spam."
            await self._send_alert(guild, "Zalgo", message, reason)
            await self._apply_punishment(message, f_zalgo["punishment"], FILTER_ZALGO, reason)
            return

        # ---- Repeated character spam ----
        f_rep = await db.get_filter(guild.id, FILTER_REPEATED)
        if f_rep["enabled"] and message.content and _has_repeated_chars(message.content):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            reason = "Message contained repeated character spam (9+ consecutive identical characters)."
            await self._send_alert(guild, "Repeated Chars", message, reason)
            await self._apply_punishment(message, f_rep["punishment"], FILTER_REPEATED, reason)
            return

        # ---- Emoji spam ----
        f_emoji_spam = await db.get_filter(guild.id, FILTER_EMOJI_SPAM)
        if f_emoji_spam["enabled"]:
            emoji_count = _count_emoji(message)
            if emoji_count >= EMOJI_SPAM_THRESHOLD:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = f"Emoji spam: {emoji_count} emoji in one message (limit: {EMOJI_SPAM_THRESHOLD})."
                await self._send_alert(guild, "Emoji Spam", message, reason)
                await self._apply_punishment(message, f_emoji_spam["punishment"], FILTER_EMOJI_SPAM, reason)
                return

        # ---- Phishing / scam detection (Premium) ----
        if not is_edit and await db.is_premium(guild.id) and message.content:
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

        # ---- Mention spam filter ----
        f_mentions = await db.get_filter(guild.id, FILTER_MENTIONS)
        if f_mentions["enabled"]:
            mc = mention_count(message)
            if mc >= MENTION_THRESHOLD:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                reason = f"Mass mention: {mc} users mentioned in one message."
                await self._send_alert(guild, "Mention Spam", message, reason)
                await self._apply_punishment(message, f_mentions["punishment"], FILTER_MENTIONS, reason)
                return

        # ---- Rate limit filter (skip on edits — edits aren't new messages) ----
        if not is_edit:
            f_rate = await db.get_filter(guild.id, "rate_limit")
            if f_rate["enabled"]:
                settings    = await db.get_guild_settings(guild.id)
                limit_count = settings.get("rate_limit_count", DEFAULT_RATE_LIMIT_COUNT)
                limit_secs  = settings.get("rate_limit_seconds", DEFAULT_RATE_LIMIT_SECONDS)
                now         = time.monotonic()
                user_times  = self._rate_cache[guild.id][member.id]
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Automod(bot))
