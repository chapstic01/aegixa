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
            "ALTER TABLE guild_settings ADD COLUMN auto_detect_raids INTEGER DEFAULT 1",
            "ALTER TABLE guild_settings ADD COLUMN raid_join_threshold INTEGER DEFAULT 10",
            "ALTER TABLE guild_settings ADD COLUMN raid_join_window INTEGER DEFAULT 10",
            "ALTER TABLE guild_settings ADD COLUMN raid_action TEXT DEFAULT 'kick'",
            "ALTER TABLE guild_settings ADD COLUMN min_account_age INTEGER DEFAULT 0",
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
    allowed = {"alert_channel_id", "announcement_channel_id", "announcement_role_id"}
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

async def get_feature(guild_id: int, feature_name: str) -> bool:
    row = await _fetchone(
        "SELECT enabled FROM features WHERE guild_id=? AND feature_name=?",
        (guild_id, feature_name),
    )
    return bool(row["enabled"]) if row else True  # default ON


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
               "raid_join_window", "raid_action", "min_account_age"}
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
    """Grant premium to a guild without a license key (owner gifting)."""
    await _execute(
        """INSERT OR REPLACE INTO premium_guilds (guild_id, expires_at, license_key, tier)
           VALUES (?, datetime('now', '+' || ? || ' days'), 'GIFTED', ?)""",
        (guild_id, days, tier),
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
