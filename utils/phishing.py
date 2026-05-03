"""Phishing, scam link, and IP-logger detection (Premium feature)."""

import re
from urllib.parse import urlparse

PHISHING_DOMAINS: set[str] = {
    # Discord Nitro scams
    "discordgifts.site", "discordnitro.gift", "discord-nitro.ru",
    "discordapp.gifts", "discord-gift.site", "getnitro.gg",
    "discord.gifts", "discordnitro.me", "nitro-discord.ru",
    "discordnitro.online", "discord-nitro.gift", "freeddiscord.com",
    "discordairdrop.com", "discordnitros.com", "nitroland.org",
    "discord-promo.com", "discordfree.gift", "discordboost.gift",
    "discordgift.co", "nitroforever.com", "discord-gifter.com",
    "discordgiftcard.com", "discord-gifts.ru", "discord-nitro.net",
    "free-discord-nitro.com", "claimdiscordnitro.com",
    "discordnitrogift.com", "discord-free-nitro.com",
    # Steam scams
    "stearm.com", "steamcornmunity.com", "st3amcommunity.com",
    "steamcommuntiy.com", "steamcommunuty.com", "steamcommuity.com",
    "steam-trade.ru", "steamfreegift.com", "steamgifts.ru",
    "steemcommunity.com", "steamcommunity.gift", "steam-giveaway.ru",
    "steamgiift.com", "st3am.com", "ssteam.com", "steampowerd.com",
    "steamcommunlty.com", "steam-community.ru", "free-steam.win",
    "steamwallet-gift.com", "steamdesktop.com",
    # IP loggers / grabbers
    "grabify.link", "iplogger.com", "iplogger.org", "iplogger.ru",
    "iplogger.co", "2no.co", "yip.su", "ps3cfw.com",
    "blasze.tk", "blasze.com", "lovebird.guru", "yourip.link",
    "ip-tracker.org", "ipgrabber.ru", "ipgrab.me", "maper.info",
    "stickr.co", "ezstat.ru", "geolocation.ws", "loc8tor.com",
    "track.ip-tracker.org", "api.grabify.link", "redirect.grabify.link",
    "grabify.gg", "ipt.pw", "csgocases.com.iplogger.org",
    "gyazo.com.iplogger.org", "spylink.net", "ipgrab.net",
    "bmwforum.co", "leancoding.co", "datauth.io",
    "headshot.monster", "gaming-at-my.best", "discord.id.pro",
    "freegiftcards.co", "joinmy.site", "shrekis.life",
    # Crypto / airdrop scams
    "free-crypto.win", "bitcoin-generator.org", "claimcrypto.xyz",
    "crypto-airdrop.win", "claimethereum.com", "freebitcoin.io",
    "nft-mint.xyz", "nft-claim.org", "nftairdrop.xyz",
    "cryptodrop.win", "ethereumairdrop.io", "btcgiveaway.org",
    "metamask-airdrop.com", "uniswap-airdrop.com", "nft-free.io",
    "opensea-nft.com", "claimbtc.xyz", "cryptogive.win",
    "walletconnect.ru", "metamaskapp.net", "trustwallet-airdrop.com",
    # Generic gift / reward scams
    "gift-link.ru", "gift-discord.ru", "freegifts.site",
    "freegiftcard.win", "claimyourgift.ru", "gift-cards.ru",
    "amazon-gift.ru", "roblox-free.com", "freerobux.win",
    "robux-generator.com", "robloxrobux.com", "robloxfree.xyz",
    "freeamazon.win", "getrobux.win",
    # Fake Discord verification / support scams
    "discord-verify.com", "discord-safety.com", "discord-secure.net",
    "discord-support.ru", "discordverify.net", "verify-discord.com",
    "discord-help.net", "discordteam.net", "discordapp-verify.com",
    "discord-confirmation.com", "discordprotect.com",
    # Malware / CDN impersonators
    "cdn-discord.com", "discordcdn.ru", "discordfiles.ru",
    "discord-media.ru", "discordapp.ru", "discord-app.ru",
    "discordattachments.com", "discordfile.com",
}

# Domains used as URL shorteners / redirectors for IP logging
_IP_LOGGER_DOMAINS = {
    "grabify.link", "2no.co", "yip.su", "blasze.tk", "blasze.com",
    "stickr.co", "lovebird.guru", "iplogger.org", "iplogger.com",
    "bmwforum.co", "leancoding.co", "joinmy.site", "spylink.net",
    "datauth.io", "headshot.monster", "gaming-at-my.best",
    "shrekis.life", "discord.id.pro",
}

SUSPICIOUS_PATTERNS = [
    # Scam TLDs combined with bait words
    r"(?:discord|nitro|steam|free|gift|crypto|nft|robux|wallet|claim)"
    r".*\.(ru|xyz|win|tk|ml|ga|cf|gq|pw|top|click|download|zip|icu|cyou)\b",
    # Raw IP addresses in URLs (always suspicious)
    r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
    # Discord typosquats
    r"discord(?:app)?\.(?!com|gg|dev|media|js|net|new)\w{2,}",
    # Steam typosquats
    r"st[e3]am(?:comm?unity|power[e3]d|wallet).*\.(ru|xyz|win|net|org)\b",
    # URL shorteners commonly used by IP loggers
    r"(?:grabify|iplogger|blasze|stickr|yip\.su|2no\.co|lovebird\.guru)",
    # Suspicious path patterns (claim/verify/free + random tokens)
    r"(?:claim|verify|free|gift|airdrop)/[A-Za-z0-9]{8,}",
]

PHISHING_KEYWORDS = [
    "free nitro", "steam gift", "free steam", "airdrop claim",
    "claim your prize", "you have won", "click here to claim",
    "free robux", "limited time offer", "verify your account to claim",
    "free nft", "nft giveaway", "crypto giveaway", "wallet connect",
    "connect your wallet", "metamask required", "your account will be banned",
    "unusual activity detected", "confirm your discord account",
    "get free discord nitro", "nitro giveaway", "steam wallet code",
    "gift card generator", "account suspended", "verify your identity",
    "free gift card", "you've been selected", "congratulations you won",
    "claim your free", "limited offer expires", "discord mod application",
    "discord partnership", "vote for our server", "boosting reward",
]

_compiled = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]
_url_re   = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)
_shorturl_re = re.compile(
    r"(?:bit\.ly|tinyurl\.com|t\.co|ow\.ly|is\.gd|buff\.ly|rb\.gy)/\S+",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    return _url_re.findall(text)


def _is_phishing_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.").split(":")[0]
        # Direct domain hit
        if domain in PHISHING_DOMAINS:
            return True
        # Subdomain of known phishing domain
        for phish in PHISHING_DOMAINS:
            if domain.endswith("." + phish):
                return True
        # IP logger domains
        if domain in _IP_LOGGER_DOMAINS:
            return True
        # Pattern matching
        return any(p.search(url) for p in _compiled)
    except Exception:
        return False


def has_shorturl(text: str) -> bool:
    """Detect common URL shorteners (often used to hide phishing links)."""
    return bool(_shorturl_re.search(text))


def scan_message(text: str) -> tuple[bool, str]:
    """Returns (is_phishing, reason)."""
    for url in extract_urls(text):
        if _is_phishing_url(url):
            return True, f"Phishing/scam URL detected: `{url[:80]}`"
    if has_shorturl(text):
        return True, "URL shortener detected (commonly used to mask phishing links)"
    text_lower = text.lower()
    for kw in PHISHING_KEYWORDS:
        if kw in text_lower:
            return True, f"Scam keyword detected: `{kw}`"
    return False, ""
