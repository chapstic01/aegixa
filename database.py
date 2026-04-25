"""
Aegixa database layer — async SQLite via aiosqlite.
All functions are safe to call from the bot's async event loop.
"""

import aiosqlite
import os
import secrets
import hashlib
from datetime import datetime
from typing import Any, Optional

DB_PATH = os.getenv("DB_PATH", "aegixa.db")

import logging as _logging
_log = _logging.getLogger(__name__)
if DB_PATH == "aegixa.db":
    _log.warning(
        "DB_PATH is not set — using ephemeral './aegixa.db'. "
        "ALL DATA WILL BE LOST ON REDEPLOY. "
        "Set DB_PATH=/data/aegixa.db and add a Railway Volume mounted at /data to persist data."
    )
else:
    _log.info("Database path: %s", DB_PATH)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS guilds (
    guild_id            INTEGER PRIMARY KEY,
    alert_channel_id    INTEGER,
    announcement_channel_id INTEGER,
    announcement_role_id    INTEGER,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guild_roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    role_type   TEXT NOT NULL CHECK(role_type IN ('staff','config','alert')),
    UNIQUE(guild_id, role_id, role_type)
);

CREATE TABLE IF NOT EXISTS log_channels (
    guild_id    INTEGER NOT NULL,
    log_type    TEXT NOT NULL,
    channel_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, log_type)
);

CREATE TABLE IF NOT EXISTS excluded_channels (
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS banned_words (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    word        TEXT NOT NULL,
    UNIQUE(guild_id, word)
);

CREATE TABLE IF NOT EXISTS warnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channel_blocks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, channel_id, user_id)
);

CREATE TABLE IF NOT EXISTS role_swap_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    trigger_role_id INTEGER NOT NULL,
    remove_role_id  INTEGER NOT NULL,
    note            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS role_grant_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    trigger_role_id INTEGER NOT NULL,
    grant_role_id   INTEGER NOT NULL,
    note            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS features (
    guild_id        INTEGER NOT NULL,
    feature_name    TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, feature_name)
);

CREATE TABLE IF NOT EXISTS commands_config (
    guild_id        INTEGER NOT NULL,
    command_name    TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, command_name)
);

CREATE TABLE IF NOT EXISTS voice_joins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automod_filters (
    guild_id        INTEGER NOT NULL,
    filter_name     TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    punishment      TEXT NOT NULL DEFAULT 'none',
    PRIMARY KEY (guild_id, filter_name)
);

CREATE TABLE IF NOT EXISTS mute_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    reason          TEXT,
    duration_seconds INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id                INTEGER PRIMARY KEY,
    auto_ban_threshold      INTEGER DEFAULT 0,
    raid_mode               INTEGER DEFAULT 0,
    rate_limit_count        INTEGER DEFAULT 5,
    rate_limit_seconds      INTEGER DEFAULT 3,
    caps_percent            INTEGER DEFAULT 70,
    caps_min_length         INTEGER DEFAULT 10
);

CREATE TABLE IF NOT EXISTS temp_bans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    reason          TEXT,
    expires_at      TIMESTAMP NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sticky_messages (
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    content         TEXT,
    last_message_id INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS reaction_roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    emoji       TEXT NOT NULL,
    role_id     INTEGER NOT NULL,
    UNIQUE(message_id, emoji)
);

CREATE TABLE IF NOT EXISTS giveaways (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER,
    prize       TEXT NOT NULL,
    winners     INTEGER DEFAULT 1,
    host_id     INTEGER NOT NULL,
    ends_at     TIMESTAMP NOT NULL,
    ended       INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invite_cache (
    guild_id    INTEGER NOT NULL,
    invite_code TEXT NOT NULL,
    inviter_id  INTEGER,
    uses        INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, invite_code)
);

CREATE TABLE IF NOT EXISTS premium_guilds (
    guild_id      INTEGER PRIMARY KEY,
    activated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMP,
    license_key   TEXT,
    tier          TEXT DEFAULT 'premium'
);

CREATE TABLE IF NOT EXISTS license_keys (
    key_hash      TEXT PRIMARY KEY,
    tier          TEXT    DEFAULT 'premium',
    duration_days INTEGER DEFAULT 30,
    max_uses      INTEGER DEFAULT 1,
    uses          INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by    INTEGER
);

CREATE TABLE IF NOT EXISTS mod_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    action_type     TEXT NOT NULL,
    moderator_id    INTEGER NOT NULL,
    target_id       INTEGER,
    reason          TEXT,
    extra           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS join_leave_config (
    guild_id            INTEGER PRIMARY KEY,
    join_channel_id     INTEGER,
    join_message        TEXT DEFAULT 'Welcome {mention} to **{server}**! 👋',
    join_enabled        INTEGER DEFAULT 0,
    leave_channel_id    INTEGER,
    leave_message       TEXT DEFAULT '**{user}** has left the server.',
    leave_enabled       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS autoroles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    role_id         INTEGER NOT NULL,
    delay_seconds   INTEGER DEFAULT 0,
    UNIQUE(guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS starboard_config (
    guild_id        INTEGER PRIMARY KEY,
    channel_id      INTEGER,
    threshold       INTEGER DEFAULT 3,
    emoji           TEXT DEFAULT '⭐',
    enabled         INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS starboard_entries (
    guild_id            INTEGER NOT NULL,
    original_message_id INTEGER NOT NULL,
    starboard_message_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, original_message_id)
);

CREATE TABLE IF NOT EXISTS user_xp (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    xp              INTEGER DEFAULT 0,
    level           INTEGER DEFAULT 0,
    last_xp_at      TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS level_roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    level       INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    UNIQUE(guild_id, level)
);

CREATE TABLE IF NOT EXISTS xp_config (
    guild_id            INTEGER PRIMARY KEY,
    enabled             INTEGER DEFAULT 1,
    xp_min              INTEGER DEFAULT 15,
    xp_max              INTEGER DEFAULT 25,
    cooldown_seconds    INTEGER DEFAULT 60,
    levelup_channel_id  INTEGER,
    levelup_message     TEXT DEFAULT 'GG {mention}, you reached **level {level}**! 🎉',
    voice_xp_enabled    INTEGER DEFAULT 0,
    voice_xp_per_minute INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ticket_config (
    guild_id            INTEGER PRIMARY KEY,
    panel_channel_id    INTEGER,
    log_channel_id      INTEGER,
    support_role_id     INTEGER,
    category_id         INTEGER,
    welcome_message     TEXT DEFAULT 'Support will be with you shortly. Please describe your issue.',
    enabled             INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    ticket_number   INTEGER NOT NULL,
    claimed_by      INTEGER,
    closed          INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stats_channels (
    guild_id        INTEGER NOT NULL,
    stat_type       TEXT NOT NULL,
    channel_id      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, stat_type)
);

CREATE TABLE IF NOT EXISTS custom_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_by  INTEGER,
    UNIQUE(guild_id, name)
);

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    content     TEXT NOT NULL,
    send_at     TIMESTAMP NOT NULL,
    sent        INTEGER DEFAULT 0,
    created_by  INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS polls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER,
    question    TEXT NOT NULL,
    options     TEXT,
    ended       INTEGER DEFAULT 0,
    created_by  INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS anti_nuke_config (
    guild_id    INTEGER PRIMARY KEY,
    enabled     INTEGER DEFAULT 0,
    punishment  TEXT DEFAULT 'kick',
    whitelist   TEXT DEFAULT '[]',
    thresholds  TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS premium_codes (
    guild_id    INTEGER PRIMARY KEY,
    code        TEXT NOT NULL,
    expires_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS gumroad_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    guild_id        INTEGER NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'premium',
    days            INTEGER NOT NULL DEFAULT 30,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS security_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    user_id     INTEGER,
    details     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS honeypot_config (
    guild_id    INTEGER PRIMARY KEY,
    channel_id  INTEGER NOT NULL,
    action      TEXT NOT NULL DEFAULT 'ban'
);

CREATE TABLE IF NOT EXISTS automod_exempt_roles (
    guild_id    INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);
"""


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA)
        # Add columns introduced after initial schema (safe on existing DBs)
        _new_cols = [
            "ALTER TABLE guilds ADD COLUMN verification_enabled INTEGER DEFAULT 0",
            "ALTER TABLE guilds ADD COLUMN verification_channel_id INTEGER",
            "ALTER TABLE guilds ADD COLUMN verified_role_id INTEGER",
            "ALTER TABLE guilds ADD COLUMN unverified_role_id INTEGER",
            "ALTER TABLE guilds ADD COLUMN update_channel_id INTEGER",
            "ALTER TABLE guild_settings ADD COLUMN auto_detect_raids INTEGER DEFAULT 1",
            "ALTER TABLE guild_settings ADD COLUMN raid_join_threshold INTEGER DEFAULT 10",
            "ALTER TABLE guild_settings ADD COLUMN raid_join_window INTEGER DEFAULT 10",
            "ALTER TABLE guild_settings ADD COLUMN raid_action TEXT DEFAULT 'kick'",
            "ALTER TABLE guild_settings ADD COLUMN min_account_age INTEGER DEFAULT 0",
            "ALTER TABLE join_leave_config ADD COLUMN dm_message TEXT DEFAULT 'Welcome to {server}, {user}! We''re glad to have you here.'",
            "ALTER TABLE join_leave_config ADD COLUMN dm_enabled INTEGER DEFAULT 0",
            "ALTER TABLE xp_config ADD COLUMN voice_xp_enabled INTEGER DEFAULT 0",
            "ALTER TABLE xp_config ADD COLUMN voice_xp_per_minute INTEGER DEFAULT 1",
            "ALTER TABLE ticket_config ADD COLUMN ticket_types TEXT",
            "ALTER TABLE ticket_config ADD COLUMN idle_close_hours INTEGER DEFAULT 0",
            "ALTER TABLE tickets ADD COLUMN ticket_type TEXT",
            "ALTER TABLE tickets ADD COLUMN last_message_at TIMESTAMP",
            "ALTER TABLE guild_settings ADD COLUMN raid_lockdown_duration INTEGER DEFAULT 300",
        ]
        for stmt in _new_cols:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fetchall(query: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def _fetchone(query: str, params: tuple = ()) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def _execute(query: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Guilds
# ---------------------------------------------------------------------------

async def ensure_guild(guild_id: int):
    await _execute("INSERT OR IGNORE INTO guilds (guild_id) VALUES (?)", (guild_id,))


async def get_guild(guild_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))


async def set_guild_field(guild_id: int, field: str, value: Any):
    allowed = {"alert_channel_id", "announcement_channel_id", "announcement_role_id", "update_channel_id"}
    if field not in allowed:
        raise ValueError(f"Unknown guild field: {field}")
    await ensure_guild(guild_id)
    await _execute(f"UPDATE guilds SET {field} = ? WHERE guild_id = ?", (value, guild_id))


# ---------------------------------------------------------------------------
# Guild roles
# ---------------------------------------------------------------------------

async def get_guild_roles(guild_id: int, role_type: str) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM guild_roles WHERE guild_id = ? AND role_type = ?",
        (guild_id, role_type),
    )


async def add_guild_role(guild_id: int, role_id: int, role_type: str):
    await _execute(
        "INSERT OR IGNORE INTO guild_roles (guild_id, role_id, role_type) VALUES (?,?,?)",
        (guild_id, role_id, role_type),
    )


async def remove_guild_role(guild_id: int, role_id: int, role_type: str):
    await _execute(
        "DELETE FROM guild_roles WHERE guild_id=? AND role_id=? AND role_type=?",
        (guild_id, role_id, role_type),
    )


# ---------------------------------------------------------------------------
# Log channels
# ---------------------------------------------------------------------------

async def get_log_channel(guild_id: int, log_type: str) -> Optional[int]:
    row = await _fetchone(
        "SELECT channel_id FROM log_channels WHERE guild_id=? AND log_type=?",
        (guild_id, log_type),
    )
    return row["channel_id"] if row else None


async def get_all_log_channels(guild_id: int) -> dict[str, int]:
    rows = await _fetchall("SELECT log_type, channel_id FROM log_channels WHERE guild_id=?", (guild_id,))
    return {r["log_type"]: r["channel_id"] for r in rows}


async def set_log_channel(guild_id: int, log_type: str, channel_id: Optional[int]):
    if channel_id is None:
        await _execute(
            "DELETE FROM log_channels WHERE guild_id=? AND log_type=?",
            (guild_id, log_type),
        )
    else:
        await _execute(
            "INSERT OR REPLACE INTO log_channels (guild_id, log_type, channel_id) VALUES (?,?,?)",
            (guild_id, log_type, channel_id),
        )


# ---------------------------------------------------------------------------
# Excluded channels
# ---------------------------------------------------------------------------

async def get_excluded_channels(guild_id: int) -> set[int]:
    rows = await _fetchall("SELECT channel_id FROM excluded_channels WHERE guild_id=?", (guild_id,))
    return {r["channel_id"] for r in rows}


async def add_excluded_channel(guild_id: int, channel_id: int):
    await _execute(
        "INSERT OR IGNORE INTO excluded_channels (guild_id, channel_id) VALUES (?,?)",
        (guild_id, channel_id),
    )


async def remove_excluded_channel(guild_id: int, channel_id: int):
    await _execute(
        "DELETE FROM excluded_channels WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    )


# ---------------------------------------------------------------------------
# Banned words
# ---------------------------------------------------------------------------

async def get_banned_words(guild_id: int) -> list[str]:
    rows = await _fetchall("SELECT word FROM banned_words WHERE guild_id=?", (guild_id,))
    return [r["word"] for r in rows]


async def add_banned_word(guild_id: int, word: str) -> bool:
    try:
        await _execute(
            "INSERT INTO banned_words (guild_id, word) VALUES (?,?)",
            (guild_id, word.lower()),
        )
        return True
    except Exception:
        return False


async def remove_banned_word(guild_id: int, word: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM banned_words WHERE guild_id=? AND word=?",
            (guild_id, word.lower()),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

async def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
    return await _execute(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?,?,?,?)",
        (guild_id, user_id, moderator_id, reason),
    )


async def get_warnings(guild_id: int, user_id: int) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
        (guild_id, user_id),
    )


async def remove_warning(guild_id: int, warning_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM warnings WHERE id=? AND guild_id=?",
            (warning_id, guild_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def clear_warnings(guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        await db.commit()
        return cur.rowcount


async def get_all_warnings(guild_id: int) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM warnings WHERE guild_id=? ORDER BY created_at DESC",
        (guild_id,),
    )


# ---------------------------------------------------------------------------
# Channel blocks
# ---------------------------------------------------------------------------

async def add_channel_block(guild_id: int, channel_id: int, user_id: int, moderator_id: int):
    await _execute(
        """INSERT OR REPLACE INTO channel_blocks
           (guild_id, channel_id, user_id, moderator_id) VALUES (?,?,?,?)""",
        (guild_id, channel_id, user_id, moderator_id),
    )


async def remove_channel_block(guild_id: int, channel_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM channel_blocks WHERE guild_id=? AND channel_id=? AND user_id=?",
            (guild_id, channel_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Role automation rules
# ---------------------------------------------------------------------------

async def add_role_swap(guild_id: int, trigger: int, remove: int, note: str = "") -> int:
    return await _execute(
        "INSERT INTO role_swap_rules (guild_id, trigger_role_id, remove_role_id, note) VALUES (?,?,?,?)",
        (guild_id, trigger, remove, note),
    )


async def get_role_swaps(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM role_swap_rules WHERE guild_id=?", (guild_id,))


async def remove_role_swap(guild_id: int, rule_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM role_swap_rules WHERE id=? AND guild_id=?",
            (rule_id, guild_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def add_role_grant(guild_id: int, trigger: int, grant: int, note: str = "") -> int:
    return await _execute(
        "INSERT INTO role_grant_rules (guild_id, trigger_role_id, grant_role_id, note) VALUES (?,?,?,?)",
        (guild_id, trigger, grant, note),
    )


async def get_role_grants(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM role_grant_rules WHERE guild_id=?", (guild_id,))


async def remove_role_grant(guild_id: int, rule_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM role_grant_rules WHERE id=? AND guild_id=?",
            (rule_id, guild_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

# Features that are OFF by default — must be explicitly enabled
_FEATURES_DEFAULT_OFF = frozenset({
    "starboard",
    "tickets",
    "join_leave",
    "custom_commands",
    "server_stats",
    "polls",
    "scheduler",
    "levels",
})


async def get_feature(guild_id: int, feature_name: str) -> bool:
    row = await _fetchone(
        "SELECT enabled FROM features WHERE guild_id=? AND feature_name=?",
        (guild_id, feature_name),
    )
    if row:
        return bool(row["enabled"])
    return feature_name not in _FEATURES_DEFAULT_OFF


async def set_feature(guild_id: int, feature_name: str, enabled: bool):
    await _execute(
        "INSERT OR REPLACE INTO features (guild_id, feature_name, enabled) VALUES (?,?,?)",
        (guild_id, feature_name, int(enabled)),
    )


async def get_all_features(guild_id: int) -> dict[str, bool]:
    rows = await _fetchall("SELECT feature_name, enabled FROM features WHERE guild_id=?", (guild_id,))
    return {r["feature_name"]: bool(r["enabled"]) for r in rows}


# ---------------------------------------------------------------------------
# Commands config
# ---------------------------------------------------------------------------

async def get_command_enabled(guild_id: int, command_name: str) -> bool:
    row = await _fetchone(
        "SELECT enabled FROM commands_config WHERE guild_id=? AND command_name=?",
        (guild_id, command_name),
    )
    return bool(row["enabled"]) if row else True


async def set_command_enabled(guild_id: int, command_name: str, enabled: bool):
    await _execute(
        "INSERT OR REPLACE INTO commands_config (guild_id, command_name, enabled) VALUES (?,?,?)",
        (guild_id, command_name, int(enabled)),
    )


async def get_all_commands_config(guild_id: int) -> dict[str, bool]:
    rows = await _fetchall("SELECT command_name, enabled FROM commands_config WHERE guild_id=?", (guild_id,))
    return {r["command_name"]: bool(r["enabled"]) for r in rows}


# ---------------------------------------------------------------------------
# Voice join tracking
# ---------------------------------------------------------------------------

async def record_voice_join(guild_id: int, user_id: int, channel_id: int):
    # Remove any stale entry first
    await _execute(
        "DELETE FROM voice_joins WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    await _execute(
        "INSERT INTO voice_joins (guild_id, user_id, channel_id) VALUES (?,?,?)",
        (guild_id, user_id, channel_id),
    )


async def pop_voice_join(guild_id: int, user_id: int) -> Optional[dict]:
    row = await _fetchone(
        "SELECT * FROM voice_joins WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    if row:
        await _execute(
            "DELETE FROM voice_joins WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
    return row


# ---------------------------------------------------------------------------
# Automod filters
# ---------------------------------------------------------------------------

async def get_filter(guild_id: int, filter_name: str) -> dict:
    row = await _fetchone(
        "SELECT * FROM automod_filters WHERE guild_id=? AND filter_name=?",
        (guild_id, filter_name),
    )
    return row or {"guild_id": guild_id, "filter_name": filter_name, "enabled": 1, "punishment": "none"}


async def set_filter(guild_id: int, filter_name: str, enabled: bool = None, punishment: str = None):
    existing = await get_filter(guild_id, filter_name)
    new_enabled = int(enabled) if enabled is not None else existing["enabled"]
    new_punishment = punishment if punishment is not None else existing["punishment"]
    await _execute(
        "INSERT OR REPLACE INTO automod_filters (guild_id, filter_name, enabled, punishment) VALUES (?,?,?,?)",
        (guild_id, filter_name, new_enabled, new_punishment),
    )


async def get_all_filters(guild_id: int) -> dict[str, dict]:
    rows = await _fetchall("SELECT * FROM automod_filters WHERE guild_id=?", (guild_id,))
    return {r["filter_name"]: r for r in rows}


# ---------------------------------------------------------------------------
# Mute records
# ---------------------------------------------------------------------------

async def add_mute_record(guild_id: int, user_id: int, moderator_id: int, reason: str, duration_seconds: Optional[int]):
    await _execute(
        "INSERT INTO mute_records (guild_id, user_id, moderator_id, reason, duration_seconds) VALUES (?,?,?,?,?)",
        (guild_id, user_id, moderator_id, reason, duration_seconds),
    )


# ---------------------------------------------------------------------------
# Guild settings (raid mode, auto-ban threshold, rate limits, caps)
# ---------------------------------------------------------------------------

async def get_guild_settings(guild_id: int) -> dict:
    row = await _fetchone("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,))
    return row or {
        "guild_id": guild_id, "auto_ban_threshold": 0, "raid_mode": 0,
        "rate_limit_count": 5, "rate_limit_seconds": 3,
        "caps_percent": 70, "caps_min_length": 10,
    }


async def set_guild_setting(guild_id: int, field: str, value):
    allowed = {"auto_ban_threshold", "raid_mode", "rate_limit_count", "rate_limit_seconds",
               "caps_percent", "caps_min_length", "auto_detect_raids", "raid_join_threshold",
               "raid_join_window", "raid_action", "min_account_age", "raid_lockdown_duration"}
    if field not in allowed:
        raise ValueError(f"Unknown setting: {field}")
    await _execute(
        f"INSERT INTO guild_settings (guild_id, {field}) VALUES (?,?) ON CONFLICT(guild_id) DO UPDATE SET {field}=excluded.{field}",
        (guild_id, value),
    )


# ---------------------------------------------------------------------------
# Temp bans
# ---------------------------------------------------------------------------

async def add_temp_ban(guild_id: int, user_id: int, moderator_id: int, reason: str, expires_at: str) -> int:
    return await _execute(
        "INSERT INTO temp_bans (guild_id, user_id, moderator_id, reason, expires_at) VALUES (?,?,?,?,?)",
        (guild_id, user_id, moderator_id, reason, expires_at),
    )


async def get_expired_temp_bans(now: str) -> list[dict]:
    return await _fetchall("SELECT * FROM temp_bans WHERE expires_at <= ? AND ended IS NOT 1", (now,))


async def remove_temp_ban(ban_id: int):
    await _execute("DELETE FROM temp_bans WHERE id=?", (ban_id,))


async def get_temp_bans(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM temp_bans WHERE guild_id=?", (guild_id,))


# ---------------------------------------------------------------------------
# Sticky messages
# ---------------------------------------------------------------------------

async def get_all_stickies(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM sticky_messages WHERE guild_id=? ORDER BY channel_id", (guild_id,))


async def get_sticky(guild_id: int, channel_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM sticky_messages WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))


async def set_sticky(guild_id: int, channel_id: int, content: str, last_message_id: Optional[int] = None):
    await _execute(
        "INSERT OR REPLACE INTO sticky_messages (guild_id, channel_id, content, last_message_id) VALUES (?,?,?,?)",
        (guild_id, channel_id, content, last_message_id),
    )


async def update_sticky_message_id(guild_id: int, channel_id: int, message_id: int):
    await _execute(
        "UPDATE sticky_messages SET last_message_id=? WHERE guild_id=? AND channel_id=?",
        (message_id, guild_id, channel_id),
    )


async def remove_sticky(guild_id: int, channel_id: int):
    await _execute("DELETE FROM sticky_messages WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))


# ---------------------------------------------------------------------------
# Reaction roles
# ---------------------------------------------------------------------------

async def add_reaction_role(guild_id: int, message_id: int, emoji: str, role_id: int):
    await _execute(
        "INSERT OR REPLACE INTO reaction_roles (guild_id, message_id, emoji, role_id) VALUES (?,?,?,?)",
        (guild_id, message_id, emoji, role_id),
    )


async def remove_reaction_role(message_id: int, emoji: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM reaction_roles WHERE message_id=? AND emoji=?", (message_id, emoji))
        await db.commit()
        return cur.rowcount > 0


async def get_reaction_role(message_id: int, emoji: str) -> Optional[dict]:
    return await _fetchone("SELECT * FROM reaction_roles WHERE message_id=? AND emoji=?", (message_id, emoji))


async def get_reaction_roles(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM reaction_roles WHERE guild_id=?", (guild_id,))


# ---------------------------------------------------------------------------
# Giveaways
# ---------------------------------------------------------------------------

async def create_giveaway(guild_id: int, channel_id: int, prize: str, winners: int, host_id: int, ends_at: str) -> int:
    return await _execute(
        "INSERT INTO giveaways (guild_id, channel_id, prize, winners, host_id, ends_at) VALUES (?,?,?,?,?,?)",
        (guild_id, channel_id, prize, winners, host_id, ends_at),
    )


async def set_giveaway_message(giveaway_id: int, message_id: int):
    await _execute("UPDATE giveaways SET message_id=? WHERE id=?", (message_id, giveaway_id))


async def get_active_giveaways(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM giveaways WHERE guild_id=? AND ended=0", (guild_id,))


async def get_expired_giveaways(now: str) -> list[dict]:
    return await _fetchall("SELECT * FROM giveaways WHERE ends_at <= ? AND ended=0", (now,))


async def end_giveaway(giveaway_id: int):
    await _execute("UPDATE giveaways SET ended=1 WHERE id=?", (giveaway_id,))


async def get_giveaway_by_message(message_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM giveaways WHERE message_id=?", (message_id,))


# ---------------------------------------------------------------------------
# Invite cache
# ---------------------------------------------------------------------------

async def upsert_invite(guild_id: int, code: str, inviter_id: Optional[int], uses: int):
    await _execute(
        "INSERT OR REPLACE INTO invite_cache (guild_id, invite_code, inviter_id, uses) VALUES (?,?,?,?)",
        (guild_id, code, inviter_id, uses),
    )


async def get_invites(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM invite_cache WHERE guild_id=?", (guild_id,))


async def delete_invite(guild_id: int, code: str):
    await _execute("DELETE FROM invite_cache WHERE guild_id=? AND invite_code=?", (guild_id, code))


# ---------------------------------------------------------------------------
# Mod actions log
# ---------------------------------------------------------------------------

async def log_mod_action(guild_id: int, action_type: str, moderator_id: int, target_id: Optional[int] = None, reason: str = "", extra: str = ""):
    await _execute(
        "INSERT INTO mod_actions (guild_id, action_type, moderator_id, target_id, reason, extra) VALUES (?,?,?,?,?,?)",
        (guild_id, action_type, moderator_id, target_id, reason, extra),
    )


async def get_mod_actions(guild_id: int, limit: int = 50) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM mod_actions WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
        (guild_id, limit),
    )


# ---------------------------------------------------------------------------
# Premium / license keys
# ---------------------------------------------------------------------------

async def is_premium(guild_id: int) -> bool:
    row = await _fetchone(
        """SELECT 1 FROM premium_guilds
           WHERE guild_id = ?
           AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)""",
        (guild_id,),
    )
    return bool(row)


async def generate_license_key(tier: str, duration_days: int, created_by: int, max_uses: int = 1) -> str:
    raw_key = secrets.token_urlsafe(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await _execute(
        "INSERT INTO license_keys (key_hash, tier, duration_days, max_uses, created_by) VALUES (?,?,?,?,?)",
        (key_hash, tier, duration_days, max_uses, created_by),
    )
    return raw_key


async def grant_premium(guild_id: int, days: int, tier: str = "premium"):
    """Grant or extend premium. If already active, adds days from current expiry."""
    await _execute(
        """INSERT INTO premium_guilds (guild_id, expires_at, license_key, tier)
           VALUES (?, datetime('now', '+' || ? || ' days'), 'GUMROAD', ?)
           ON CONFLICT(guild_id) DO UPDATE SET
               expires_at = CASE
                   WHEN expires_at > datetime('now')
                   THEN datetime(expires_at, '+' || ? || ' days')
                   ELSE datetime('now', '+' || ? || ' days')
               END,
               tier = excluded.tier""",
        (guild_id, days, tier, days, days),
    )


async def redeem_license_key(guild_id: int, raw_key: str) -> tuple[bool, str]:
    key_hash = hashlib.sha256(raw_key.strip().encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM license_keys WHERE key_hash = ?", (key_hash,)) as cur:
            key = await cur.fetchone()
        if not key:
            return False, "Invalid license key."
        if key["uses"] >= key["max_uses"]:
            return False, "This key has already been used the maximum number of times."
        await db.execute(
            """INSERT OR REPLACE INTO premium_guilds (guild_id, expires_at, license_key, tier)
               VALUES (?, datetime('now', '+' || ? || ' days'), ?, ?)""",
            (guild_id, key["duration_days"], raw_key[:8] + "****", key["tier"]),
        )
        await db.execute("UPDATE license_keys SET uses = uses + 1 WHERE key_hash = ?", (key_hash,))
        await db.commit()
    return True, f"Premium activated for **{key['duration_days']} days**!"


# ---------------------------------------------------------------------------
# Verification config (stored in guilds table)
# ---------------------------------------------------------------------------

async def get_verification(guild_id: int) -> dict:
    row = await _fetchone(
        """SELECT verification_enabled, verification_channel_id,
                  verified_role_id, unverified_role_id
           FROM guilds WHERE guild_id = ?""",
        (guild_id,),
    )
    return row or {"verification_enabled": 0, "verification_channel_id": None,
                   "verified_role_id": None, "unverified_role_id": None}


async def set_verification(guild_id: int, **kwargs):
    allowed = {"verification_enabled", "verification_channel_id", "verified_role_id", "unverified_role_id"}
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    if not kwargs:
        return
    await ensure_guild(guild_id)
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    await _execute(
        f"UPDATE guilds SET {set_clause} WHERE guild_id = ?",
        (*kwargs.values(), guild_id),
    )


# ---------------------------------------------------------------------------
# Join / Leave announcements
# ---------------------------------------------------------------------------

async def get_join_leave_config(guild_id: int) -> dict:
    row = await _fetchone("SELECT * FROM join_leave_config WHERE guild_id = ?", (guild_id,))
    return dict(row) if row else {
        "guild_id": guild_id, "join_channel_id": None,
        "join_message": "Welcome {mention} to **{server}**! 👋", "join_enabled": 0,
        "leave_channel_id": None, "leave_message": "**{user}** has left the server.", "leave_enabled": 0,
    }


async def set_join_leave_config(guild_id: int, **kwargs):
    allowed = {"join_channel_id", "join_message", "join_enabled", "leave_channel_id", "leave_message", "leave_enabled"}
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    if not kwargs:
        return
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    await _execute(
        f"INSERT INTO join_leave_config (guild_id, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *kwargs.values()),
    )


# ---------------------------------------------------------------------------
# Autoroles
# ---------------------------------------------------------------------------

async def get_autoroles(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM autoroles WHERE guild_id = ? ORDER BY delay_seconds", (guild_id,))


async def add_autorole(guild_id: int, role_id: int, delay_seconds: int = 0):
    await _execute(
        "INSERT OR REPLACE INTO autoroles (guild_id, role_id, delay_seconds) VALUES (?,?,?)",
        (guild_id, role_id, delay_seconds),
    )


async def remove_autorole(guild_id: int, role_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM autoroles WHERE guild_id=? AND role_id=?", (guild_id, role_id))
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Starboard
# ---------------------------------------------------------------------------

async def get_starboard_config(guild_id: int) -> dict:
    row = await _fetchone("SELECT * FROM starboard_config WHERE guild_id = ?", (guild_id,))
    return dict(row) if row else {"guild_id": guild_id, "channel_id": None, "threshold": 3, "emoji": "⭐", "enabled": 1}


async def set_starboard_config(guild_id: int, **kwargs):
    allowed = {"channel_id", "threshold", "emoji", "enabled"}
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    if not kwargs:
        return
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    await _execute(
        f"INSERT INTO starboard_config (guild_id, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *kwargs.values()),
    )


async def get_starboard_entry(guild_id: int, original_message_id: int) -> Optional[dict]:
    return await _fetchone(
        "SELECT * FROM starboard_entries WHERE guild_id=? AND original_message_id=?",
        (guild_id, original_message_id),
    )


async def set_starboard_entry(guild_id: int, original_message_id: int, starboard_message_id: int):
    await _execute(
        "INSERT OR REPLACE INTO starboard_entries (guild_id, original_message_id, starboard_message_id) VALUES (?,?,?)",
        (guild_id, original_message_id, starboard_message_id),
    )


async def delete_starboard_entry(guild_id: int, original_message_id: int):
    await _execute(
        "DELETE FROM starboard_entries WHERE guild_id=? AND original_message_id=?",
        (guild_id, original_message_id),
    )


# ---------------------------------------------------------------------------
# XP / Levels
# ---------------------------------------------------------------------------

async def get_xp_config(guild_id: int) -> dict:
    row = await _fetchone("SELECT * FROM xp_config WHERE guild_id = ?", (guild_id,))
    return dict(row) if row else {
        "guild_id": guild_id, "enabled": 1, "xp_min": 15, "xp_max": 25,
        "cooldown_seconds": 60, "levelup_channel_id": None,
        "levelup_message": "GG {mention}, you reached **level {level}**! 🎉",
        "voice_xp_enabled": 0, "voice_xp_per_minute": 1,
    }


async def set_xp_config(guild_id: int, **kwargs):
    allowed = {"enabled", "xp_min", "xp_max", "cooldown_seconds", "levelup_channel_id", "levelup_message", "voice_xp_enabled", "voice_xp_per_minute"}
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    if not kwargs:
        return
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    await _execute(
        f"INSERT INTO xp_config (guild_id, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *kwargs.values()),
    )


async def get_user_xp(guild_id: int, user_id: int) -> dict:
    row = await _fetchone("SELECT * FROM user_xp WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    return dict(row) if row else {"guild_id": guild_id, "user_id": user_id, "xp": 0, "level": 0, "last_xp_at": None}


async def add_user_xp(guild_id: int, user_id: int, amount: int) -> dict:
    """Add XP and return updated row."""
    await _execute(
        """INSERT INTO user_xp (guild_id, user_id, xp, last_xp_at)
           VALUES (?,?,?,datetime('now'))
           ON CONFLICT(guild_id, user_id) DO UPDATE SET
           xp=xp+?, last_xp_at=datetime('now')""",
        (guild_id, user_id, amount, amount),
    )
    return await get_user_xp(guild_id, user_id)


async def set_user_xp(guild_id: int, user_id: int, xp: int, level: int):
    await _execute(
        """INSERT INTO user_xp (guild_id, user_id, xp, level)
           VALUES (?,?,?,?)
           ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=?, level=?""",
        (guild_id, user_id, xp, level, xp, level),
    )


async def update_user_level(guild_id: int, user_id: int, level: int):
    await _execute(
        "UPDATE user_xp SET level=? WHERE guild_id=? AND user_id=?",
        (level, guild_id, user_id),
    )


async def get_xp_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM user_xp WHERE guild_id=? ORDER BY xp DESC LIMIT ?",
        (guild_id, limit),
    )


async def get_level_roles(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM level_roles WHERE guild_id=? ORDER BY level", (guild_id,))


async def add_level_role(guild_id: int, level: int, role_id: int):
    await _execute(
        "INSERT OR REPLACE INTO level_roles (guild_id, level, role_id) VALUES (?,?,?)",
        (guild_id, level, role_id),
    )


async def remove_level_role(guild_id: int, level: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM level_roles WHERE guild_id=? AND level=?", (guild_id, level))
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

async def get_ticket_config(guild_id: int) -> dict:
    row = await _fetchone("SELECT * FROM ticket_config WHERE guild_id = ?", (guild_id,))
    return dict(row) if row else {
        "guild_id": guild_id, "panel_channel_id": None, "log_channel_id": None,
        "support_role_id": None, "category_id": None,
        "welcome_message": "Support will be with you shortly. Please describe your issue.",
        "enabled": 1, "ticket_types": None, "idle_close_hours": 0,
    }


async def set_ticket_config(guild_id: int, **kwargs):
    allowed = {
        "panel_channel_id", "log_channel_id", "support_role_id", "category_id",
        "welcome_message", "enabled", "ticket_types", "idle_close_hours",
    }
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    if not kwargs:
        return
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    await _execute(
        f"INSERT INTO ticket_config (guild_id, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *kwargs.values()),
    )


async def create_ticket(guild_id: int, channel_id: int, user_id: int, ticket_type: str = "Support") -> int:
    count_row = await _fetchone("SELECT COUNT(*) as n FROM tickets WHERE guild_id=?", (guild_id,))
    ticket_number = (count_row["n"] if count_row else 0) + 1
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return await _execute(
        "INSERT INTO tickets (guild_id, channel_id, user_id, ticket_number, ticket_type, last_message_at) VALUES (?,?,?,?,?,?)",
        (guild_id, channel_id, user_id, ticket_number, ticket_type, now),
    )


async def get_ticket_by_channel(channel_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM tickets WHERE channel_id=? AND closed=0", (channel_id,))


async def get_open_tickets(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM tickets WHERE guild_id=? AND closed=0 ORDER BY created_at DESC", (guild_id,))


async def close_ticket(channel_id: int):
    await _execute("UPDATE tickets SET closed=1 WHERE channel_id=?", (channel_id,))


async def claim_ticket(channel_id: int, moderator_id: int):
    await _execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?", (moderator_id, channel_id))


async def unclaim_ticket(channel_id: int):
    await _execute("UPDATE tickets SET claimed_by=NULL WHERE channel_id=?", (channel_id,))


async def touch_ticket(channel_id: int):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    await _execute("UPDATE tickets SET last_message_at=? WHERE channel_id=? AND closed=0", (now, channel_id))


async def get_idle_tickets(idle_hours: int) -> list[dict]:
    return await _fetchall(
        """SELECT * FROM tickets WHERE closed=0
           AND last_message_at IS NOT NULL
           AND last_message_at <= datetime('now', '-' || ? || ' hours')""",
        (idle_hours,),
    )


async def get_user_open_ticket(guild_id: int, user_id: int) -> Optional[dict]:
    return await _fetchone(
        "SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND closed=0",
        (guild_id, user_id),
    )


# ---------------------------------------------------------------------------
# Server stats channels
# ---------------------------------------------------------------------------

async def set_stats_channel(guild_id: int, stat_type: str, channel_id: int):
    await _execute(
        "INSERT OR REPLACE INTO stats_channels (guild_id, stat_type, channel_id) VALUES (?,?,?)",
        (guild_id, stat_type, channel_id),
    )


async def get_stats_channels(guild_id: int) -> dict[str, int]:
    rows = await _fetchall("SELECT stat_type, channel_id FROM stats_channels WHERE guild_id=?", (guild_id,))
    return {r["stat_type"]: r["channel_id"] for r in rows}


async def delete_stats_channels(guild_id: int):
    await _execute("DELETE FROM stats_channels WHERE guild_id=?", (guild_id,))


# ---------------------------------------------------------------------------
# Custom commands
# ---------------------------------------------------------------------------

async def get_custom_commands(guild_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM custom_commands WHERE guild_id=? ORDER BY name", (guild_id,))


async def get_custom_command(guild_id: int, name: str) -> Optional[dict]:
    return await _fetchone(
        "SELECT * FROM custom_commands WHERE guild_id=? AND name=?",
        (guild_id, name.lower()),
    )


async def set_custom_command(guild_id: int, name: str, response: str, created_by: int):
    await _execute(
        "INSERT OR REPLACE INTO custom_commands (guild_id, name, response, created_by) VALUES (?,?,?,?)",
        (guild_id, name.lower(), response, created_by),
    )


async def delete_custom_command(guild_id: int, name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM custom_commands WHERE guild_id=? AND name=?",
            (guild_id, name.lower()),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Scheduled messages
# ---------------------------------------------------------------------------

async def add_scheduled_message(guild_id: int, channel_id: int, content: str, send_at: str, created_by: int) -> int:
    return await _execute(
        "INSERT INTO scheduled_messages (guild_id, channel_id, content, send_at, created_by) VALUES (?,?,?,?,?)",
        (guild_id, channel_id, content, send_at, created_by),
    )


async def get_pending_scheduled_messages(now: str) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM scheduled_messages WHERE send_at <= ? AND sent=0",
        (now,),
    )


async def mark_scheduled_sent(msg_id: int):
    await _execute("UPDATE scheduled_messages SET sent=1 WHERE id=?", (msg_id,))


async def get_scheduled_messages(guild_id: int) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM scheduled_messages WHERE guild_id=? AND sent=0 ORDER BY send_at",
        (guild_id,),
    )


async def delete_scheduled_message(msg_id: int, guild_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM scheduled_messages WHERE id=? AND guild_id=? AND sent=0",
            (msg_id, guild_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------

async def create_poll(guild_id: int, channel_id: int, question: str, options: str, created_by: int) -> int:
    return await _execute(
        "INSERT INTO polls (guild_id, channel_id, question, options, created_by) VALUES (?,?,?,?,?)",
        (guild_id, channel_id, question, options, created_by),
    )


async def set_poll_message(poll_id: int, message_id: int):
    await _execute("UPDATE polls SET message_id=? WHERE id=?", (message_id, poll_id))


async def end_poll(poll_id: int):
    await _execute("UPDATE polls SET ended=1 WHERE id=?", (poll_id,))


# ---------------------------------------------------------------------------
# Premium codes & Gumroad subscriptions
# ---------------------------------------------------------------------------

async def create_premium_code(guild_id: int) -> str:
    """Generate a 6-char verification code valid for 60 minutes."""
    code = secrets.token_hex(3).upper()
    await _execute(
        """INSERT OR REPLACE INTO premium_codes (guild_id, code, expires_at)
           VALUES (?, ?, datetime('now', '+60 minutes'))""",
        (guild_id, code),
    )
    return code


async def verify_and_consume_premium_code(guild_id: int, code: str) -> bool:
    """Return True and delete the code if it matches and hasn't expired."""
    row = await _fetchone(
        "SELECT 1 FROM premium_codes WHERE guild_id=? AND code=? AND expires_at > datetime('now')",
        (guild_id, code.strip().upper()),
    )
    if row:
        await _execute("DELETE FROM premium_codes WHERE guild_id=?", (guild_id,))
        return True
    return False


async def store_gumroad_subscription(subscription_id: str, guild_id: int, tier: str, days: int):
    await _execute(
        "INSERT OR REPLACE INTO gumroad_subscriptions (subscription_id, guild_id, tier, days) VALUES (?,?,?,?)",
        (subscription_id, guild_id, tier, days),
    )


async def get_gumroad_subscription(subscription_id: str) -> Optional[dict]:
    return await _fetchone(
        "SELECT * FROM gumroad_subscriptions WHERE subscription_id=?",
        (subscription_id,),
    )


# ---------------------------------------------------------------------------
# Security events
# ---------------------------------------------------------------------------

async def log_security_event(guild_id: int, event_type: str, user_id: Optional[int] = None, details: str = ""):
    await _execute(
        "INSERT INTO security_events (guild_id, event_type, user_id, details) VALUES (?,?,?,?)",
        (guild_id, event_type, user_id, details),
    )


async def get_security_events(guild_id: int, limit: int = 50) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM security_events WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
        (guild_id, limit),
    )


# ---------------------------------------------------------------------------
# Honeypot config
# ---------------------------------------------------------------------------

async def set_honeypot(guild_id: int, channel_id: int, action: str = "ban"):
    await _execute(
        "INSERT INTO honeypot_config (guild_id, channel_id, action) VALUES (?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, action=excluded.action",
        (guild_id, channel_id, action),
    )


async def get_honeypot(guild_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM honeypot_config WHERE guild_id=?", (guild_id,))


async def clear_honeypot(guild_id: int):
    await _execute("DELETE FROM honeypot_config WHERE guild_id=?", (guild_id,))


# ---------------------------------------------------------------------------
# Automod exempt roles
# ---------------------------------------------------------------------------

async def get_automod_exempt_roles(guild_id: int) -> set[int]:
    rows = await _fetchall("SELECT role_id FROM automod_exempt_roles WHERE guild_id=?", (guild_id,))
    return {r["role_id"] for r in rows}


async def add_automod_exempt_role(guild_id: int, role_id: int):
    await _execute(
        "INSERT OR IGNORE INTO automod_exempt_roles (guild_id, role_id) VALUES (?,?)",
        (guild_id, role_id),
    )


async def remove_automod_exempt_role(guild_id: int, role_id: int):
    await _execute(
        "DELETE FROM automod_exempt_roles WHERE guild_id=? AND role_id=?",
        (guild_id, role_id),
    )


# ---------------------------------------------------------------------------
# Anti-nuke config
# ---------------------------------------------------------------------------

async def get_anti_nuke_config(guild_id: int) -> dict:
    import json as _json
    row = await _fetchone("SELECT * FROM anti_nuke_config WHERE guild_id=?", (guild_id,))
    if not row:
        return {"enabled": False, "punishment": "kick", "whitelist": [], "thresholds": {}}
    return {
        "enabled":    bool(row["enabled"]),
        "punishment": row["punishment"] or "kick",
        "whitelist":  _json.loads(row["whitelist"] or "[]"),
        "thresholds": _json.loads(row["thresholds"] or "{}"),
    }


async def set_anti_nuke_config(guild_id: int, cfg: dict):
    import json as _json
    await _execute(
        """INSERT INTO anti_nuke_config (guild_id, enabled, punishment, whitelist, thresholds)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET
             enabled    = excluded.enabled,
             punishment = excluded.punishment,
             whitelist  = excluded.whitelist,
             thresholds = excluded.thresholds""",
        (
            guild_id,
            int(cfg["enabled"]),
            cfg["punishment"],
            _json.dumps(cfg["whitelist"]),
            _json.dumps(cfg["thresholds"]),
        ),
    )
