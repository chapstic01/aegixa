FEATURES = [
    "spam_filter",
    "word_filter",
    "image_block",
    "sticker_block",
    "external_emoji_block",
    "logging",
    "role_automation",
    "message_management",
    "automod",
    "dashboard",
    "announcements",
    "raid_mode",
    "reaction_roles",
    "giveaways",
    "sticky_messages",
    "invite_tracking",
]

PUNISHMENTS = ["none", "warn", "mute", "kick", "ban"]

LOG_TYPES = [
    "general",
    "spam",
    "member",
    "edit",
    "delete",
    "voice",
    "roles",
    "channels",
    "modactions",
]

FILTER_NAMES = [
    "spam",
    "word",
    "image",
    "sticker",
    "external_emoji",
    "link",
    "invite",
    "caps",
    "rate_limit",
]

PROTECTED_COMMANDS = ["setup", "cmds", "about"]

COLORS = {
    "red": 0xED4245,
    "green": 0x57F287,
    "yellow": 0xFEE75C,
    "blue": 0x5865F2,
    "orange": 0xFFA500,
    "purple": 0x9B59B6,
    "white": 0xFFFFFF,
    "dark": 0x2F3136,
    "blurple": 0x5865F2,
}

LOG_COLORS = {
    "general": 0x5865F2,
    "spam": 0xED4245,
    "member": 0x57F287,
    "edit": 0xFEE75C,
    "delete": 0xFFA500,
    "voice": 0x9B59B6,
    "roles": 0x00B0F4,
    "channels": 0xEB459E,
    "modactions": 0xFF6B35,
}

# Default rate-limit: 5 messages in 3 seconds
DEFAULT_RATE_LIMIT_COUNT = 5
DEFAULT_RATE_LIMIT_SECONDS = 3

# Default caps filter: 70% uppercase, min 10 chars
DEFAULT_CAPS_PERCENT = 70
DEFAULT_CAPS_MIN_LENGTH = 10
