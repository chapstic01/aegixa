"""General-purpose helpers shared across cogs."""

import re
import discord
from typing import Optional


URL_RE = re.compile(
    r"(https?://|www\.)\S+",
    re.IGNORECASE,
)

TENOR_RE = re.compile(r"https?://tenor\.com/\S+", re.IGNORECASE)
GIPHY_RE = re.compile(r"https?://giphy\.com/\S+", re.IGNORECASE)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
IMAGE_MIMETYPES = {"image/", "video/gif"}


def message_has_links(message: discord.Message) -> bool:
    return bool(URL_RE.search(message.content))


def message_has_media(message: discord.Message) -> bool:
    """True if message contains image/GIF attachments or embeds."""
    for att in message.attachments:
        ext = "." + att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else ""
        if ext in IMAGE_EXTENSIONS:
            return True
        if att.content_type and any(att.content_type.startswith(m) for m in IMAGE_MIMETYPES):
            return True
    for embed in message.embeds:
        if embed.image or embed.thumbnail:
            return True
    if TENOR_RE.search(message.content) or GIPHY_RE.search(message.content):
        return True
    return False


def message_has_sticker(message: discord.Message) -> bool:
    return bool(message.stickers)


def message_has_external_emoji(message: discord.Message) -> bool:
    """Detect <:name:id> emoji NOT from this guild."""
    pattern = re.compile(r"<a?:(\w+):(\d+)>")
    guild_emoji_ids = {str(e.id) for e in message.guild.emojis} if message.guild else set()
    for match in pattern.finditer(message.content):
        if match.group(2) not in guild_emoji_ids:
            return True
    return False


def mention_count(message: discord.Message) -> int:
    return len(message.mentions)


async def resolve_member(
    guild: discord.Guild, query: str
) -> Optional[discord.Member]:
    """
    Resolve a member from a free-text query: mention, ID, username, or display name.
    """
    query = query.strip()

    # Mention: <@123456789>
    mention_match = re.match(r"<@!?(\d+)>", query)
    if mention_match:
        uid = int(mention_match.group(1))
        return guild.get_member(uid) or await guild.fetch_member(uid)

    # Numeric ID
    if query.isdigit():
        uid = int(query)
        return guild.get_member(uid) or await guild.fetch_member(uid)

    # Username / display name search (case-insensitive)
    query_lower = query.lower()
    for member in guild.members:
        if member.name.lower() == query_lower:
            return member
        if member.display_name.lower() == query_lower:
            return member

    # Partial match fallback
    for member in guild.members:
        if query_lower in member.name.lower() or query_lower in member.display_name.lower():
            return member

    return None


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def parse_duration(text: str) -> Optional[int]:
    """Parse '10m', '2h', '1d' etc. into seconds. Returns None on failure."""
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", text.strip().lower())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2) or "s"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def error_embed(description: str) -> discord.Embed:
    return discord.Embed(description=f":x: {description}", color=0xED4245)


def success_embed(description: str) -> discord.Embed:
    return discord.Embed(description=f":white_check_mark: {description}", color=0x57F287)


def info_embed(title: str, description: str = "", color: int = 0x5865F2) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)
