"""SQLite-хранилище: таймеры и настройки чатов.

Таймеры хранятся в базе, поэтому переживают перезапуск бота:
при старте userbot.py вызывает timers_core.restore(), который
заново планирует все активные таймеры.
"""
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn = None


def init():
    """Открыть базу и создать таблицы. Вызывается один раз при старте."""
    global _conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(config.DATA_DIR / "polina.db"), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT DEFAULT '',
            message_id INTEGER DEFAULT 0,
            due_ts REAL NOT NULL,
            text TEXT DEFAULT '',
            created_ts REAL NOT NULL,
            status TEXT DEFAULT 'active'
        )"""
    )
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            public_enabled INTEGER DEFAULT 1
        )"""
    )
    # Чистим завершённые таймеры старше 30 дней, чтобы база не разрасталась
    _conn.execute(
        "DELETE FROM timers WHERE status != 'active' AND created_ts < ?",
        (time.time() - 30 * 86400,),
    )
    _conn.commit()


# ---------- Таймеры ----------

def add_timer(chat_id, user_id, user_name, message_id, due_ts, text):
    with _lock:
        cur = _conn.execute(
            "INSERT INTO timers (chat_id, user_id, user_name, message_id, due_ts, text, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, user_name, message_id, due_ts, text, time.time()),
        )
        _conn.commit()
        return cur.lastrowid


def get_timer(timer_id):
    """-> (id, chat_id, user_id, user_name, message_id, due_ts, text, status) или None"""
    with _lock:
        return _conn.execute(
            "SELECT id, chat_id, user_id, user_name, message_id, due_ts, text, status "
            "FROM timers WHERE id = ?",
            (timer_id,),
        ).fetchone()


def active_timers(chat_id=None):
    """-> [(id, chat_id, user_id, user_name, message_id, due_ts, text), ...] по времени срабатывания"""
    q = ("SELECT id, chat_id, user_id, user_name, message_id, due_ts, text "
         "FROM timers WHERE status = 'active'")
    args = ()
    if chat_id is not None:
        q += " AND chat_id = ?"
        args = (chat_id,)
    q += " ORDER BY due_ts"
    with _lock:
        return _conn.execute(q, args).fetchall()


def set_status(timer_id, status):
    with _lock:
        _conn.execute("UPDATE timers SET status = ? WHERE id = ?", (status, timer_id))
        _conn.commit()


def count_active(chat_id, user_id):
    with _lock:
        row = _conn.execute(
            "SELECT COUNT(*) FROM timers WHERE status = 'active' AND chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return row[0] if row else 0


# ---------- Настройки чатов ----------

def is_public_enabled(chat_id):
    """Разрешены ли публичные команды («Полина, …») в этом чате. По умолчанию — да."""
    with _lock:
        row = _conn.execute(
            "SELECT public_enabled FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return True if row is None else bool(row[0])


def set_public_enabled(chat_id, enabled):
    with _lock:
        _conn.execute(
            "INSERT INTO chat_settings (chat_id, public_enabled) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET public_enabled = excluded.public_enabled",
            (chat_id, 1 if enabled else 0),
        )
        _conn.commit()
