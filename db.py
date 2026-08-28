"""
db.py — Lightweight SQLite data layer for HiveBots.

No ORM, no external dependencies beyond the Python standard library.
This keeps the project trivially deployable on any host that has Python 3.
"""
import sqlite3
import os
import time
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "hivebots.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    points_remaining INTEGER NOT NULL DEFAULT 100000,
    points_total INTEGER NOT NULL DEFAULT 100000,
    points_reset_month TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    username TEXT,
    token TEXT NOT NULL,
    secret TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    avatar_emoji TEXT DEFAULT '🤖',
    created_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    trigger_name TEXT NOT NULL,
    code TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    run_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(bot_id) REFERENCES bots(id)
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    level TEXT NOT NULL,
    trigger_name TEXT,
    message TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(bot_id) REFERENCES bots(id)
);

CREATE TABLE IF NOT EXISTS bot_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    tg_user_id TEXT NOT NULL,
    username TEXT,
    first_name TEXT,
    waiting_command TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    UNIQUE(bot_id, tg_user_id),
    FOREIGN KEY(bot_id) REFERENCES bots(id)
);

CREATE TABLE IF NOT EXISTS user_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    tg_user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    UNIQUE(bot_id, tg_user_id, key)
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    tg_user_id TEXT,
    trigger_name TEXT NOT NULL,
    run_at REAL NOT NULL,
    created_at REAL NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'info',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_commands_bot ON commands(bot_id);
CREATE INDEX IF NOT EXISTS idx_logs_bot ON logs(bot_id);
CREATE INDEX IF NOT EXISTS idx_botusers_bot ON bot_users(bot_id);
CREATE INDEX IF NOT EXISTS idx_userdata_lookup ON user_data(bot_id, tg_user_id);
CREATE INDEX IF NOT EXISTS idx_sched_runat ON scheduled_tasks(run_at, done);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now():
    return time.time()
