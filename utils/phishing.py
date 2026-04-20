"""Phishing and scam link/keyword detection (Premium feature)."""

import re
from urllib.parse import urlparse

PHISHING_DOMAINS: set[str] = {
    # Discord Nitro scams
    "discordgifts.site", "discordnitro.gift", "discord-nitro.ru",
    "discordapp.gifts", "discord-gift.site", "getnitro.gg",
    "discord.gifts", "discordnitro.me", "nitro-discord.ru",
    "discordnitro.online", "discord-nitro.gift", "freeddiscord.com",
    # Steam scams
    "stearm.com", "steamcornmunity.com", "st3amcommunity.com",
    "steamcommuntiy.com", "steamcommunuty.com",
    # Crypto / airdrop scams
    "free-crypto.win", "bitcoin-generator.org", "claimcrypto.xyz",
    # Generic gift/reward scams
    "gift-link.ru", "gift-discord.ru", "freegifts.site",
}

SUSPICIOUS_PATTERNS = [
    r"(?:discord|nitro|steam|free|gift).*\.(ru|xyz|win|tk|ml|ga|cf|gq)\b",
    r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",   # raw IP links
    r"discord(?:app)?\.(?!com|gg|dev|media)\w+",         # discord typosquats
]

PHISHING_KEYWORDS = [
    "free nitro", "steam gift", "free steam", "airdrop claim",
    "claim your prize", "you have won", "click here to claim",
    "free robux", "limited time offer", "verify your account to claim",
]

_compiled = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]
_url_re   = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    return _url_re.findall(text)


def _is_phishing_url(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower()
        domain = domain.lstrip("www.").split(":")[0]
        if domain in PHISHING_DOMAINS:
            return True
        return any(p.search(url) for p in _compiled)
    except Exception:
        return False


def scan_message(text: str) -> tuple[bool, str]:
    """Returns (is_phishing, reason)."""
    for url in extract_urls(text):
        if _is_phishing_url(url):
            return True, f"Phishing/scam URL detected: `{url[:60]}`"
    text_lower = text.lower()
    for kw in PHISHING_KEYWORDS:
        if kw in text_lower:
            return True, f"Scam keyword detected: `{kw}`"
    return False, ""
