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
            status TEXT DEFAULT 'active',
            repeat_seconds INTEGER DEFAULT 0
        )"""
    )
    # Миграция для старых баз: добавляем колонку повтора, если её нет
    tcols = {r[1] for r in _conn.execute("PRAGMA table_info(timers)")}
    if "repeat_seconds" not in tcols:
        _conn.execute("ALTER TABLE timers ADD COLUMN repeat_seconds INTEGER DEFAULT 0")
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            public_enabled INTEGER DEFAULT 1
        )"""
    )
    # Миграция: добавляем колонки приветствия, если их ещё нет
    cols = {r[1] for r in _conn.execute("PRAGMA table_info(chat_settings)")}
    if "welcome_on" not in cols:
        _conn.execute("ALTER TABLE chat_settings ADD COLUMN welcome_on INTEGER DEFAULT 0")
    if "welcome_text" not in cols:
        _conn.execute("ALTER TABLE chat_settings ADD COLUMN welcome_text TEXT DEFAULT ''")
    if "welcome_mode" not in cols:
        # 0 = сразу по входу, 1 = по сообщению-триггеру (напр. от SecurityBermuda)
        _conn.execute("ALTER TABLE chat_settings ADD COLUMN welcome_mode INTEGER DEFAULT 0")
    if "welcome_trigger" not in cols:
        # @username бота-триггера ИЛИ фраза; пусто = дефолтная фраза «добро пожаловать»
        _conn.execute("ALTER TABLE chat_settings ADD COLUMN welcome_trigger TEXT DEFAULT ''")

    _conn.execute(
        """CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            key TEXT DEFAULT '',
            content TEXT NOT NULL,
            author_id INTEGER,
            author_name TEXT DEFAULT '',
            created_ts REAL NOT NULL
        )"""
    )
    # Чистим завершённые таймеры старше 30 дней, чтобы база не разрасталась
    _conn.execute(
        "DELETE FROM timers WHERE status != 'active' AND created_ts < ?",
        (time.time() - 30 * 86400,),
    )
    _conn.commit()


# ---------- Таймеры ----------

def add_timer(chat_id, user_id, user_name, message_id, due_ts, text, repeat_seconds=0):
    with _lock:
        cur = _conn.execute(
            "INSERT INTO timers (chat_id, user_id, user_name, message_id, due_ts, text, created_ts, repeat_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, user_name, message_id, due_ts, text, time.time(), int(repeat_seconds)),
        )
        _conn.commit()
        return cur.lastrowid


def get_timer(timer_id):
    """-> (id, chat_id, user_id, user_name, message_id, due_ts, text, status, repeat_seconds) или None"""
    with _lock:
        return _conn.execute(
            "SELECT id, chat_id, user_id, user_name, message_id, due_ts, text, status, repeat_seconds "
            "FROM timers WHERE id = ?",
            (timer_id,),
        ).fetchone()


def active_timers(chat_id=None):
    """-> [(id, chat_id, user_id, user_name, message_id, due_ts, text, repeat_seconds), ...]"""
    q = ("SELECT id, chat_id, user_id, user_name, message_id, due_ts, text, repeat_seconds "
         "FROM timers WHERE status = 'active'")
    args = ()
    if chat_id is not None:
        q += " AND chat_id = ?"
        args = (chat_id,)
    q += " ORDER BY due_ts"
    with _lock:
        return _conn.execute(q, args).fetchall()


def reschedule_timer(timer_id, due_ts):
    """Перенести срабатывание (для повторяющихся таймеров)."""
    with _lock:
        _conn.execute("UPDATE timers SET due_ts = ? WHERE id = ?", (due_ts, timer_id))
        _conn.commit()


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


# ---------- Приветствие новичков ----------

def welcome_enabled(chat_id):
    with _lock:
        row = _conn.execute(
            "SELECT welcome_on FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return bool(row[0]) if row else False


def get_welcome_text(chat_id):
    with _lock:
        row = _conn.execute(
            "SELECT welcome_text FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row[0] if row and row[0] else ""


def get_welcome_mode(chat_id):
    """0 = сразу по входу, 1 = по сообщению-триггеру."""
    with _lock:
        row = _conn.execute(
            "SELECT welcome_mode FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def get_welcome_trigger(chat_id):
    with _lock:
        row = _conn.execute(
            "SELECT welcome_trigger FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row[0] if row and row[0] else ""


def set_welcome(chat_id, on=None, text=None, mode=None, trigger=None):
    with _lock:
        _conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
        if on is not None:
            _conn.execute("UPDATE chat_settings SET welcome_on = ? WHERE chat_id = ?",
                          (1 if on else 0, chat_id))
        if text is not None:
            _conn.execute("UPDATE chat_settings SET welcome_text = ? WHERE chat_id = ?",
                          (text, chat_id))
        if mode is not None:
            _conn.execute("UPDATE chat_settings SET welcome_mode = ? WHERE chat_id = ?",
                          (int(mode), chat_id))
        if trigger is not None:
            _conn.execute("UPDATE chat_settings SET welcome_trigger = ? WHERE chat_id = ?",
                          (trigger, chat_id))
        _conn.commit()


# ---------- Заметки ----------

def add_note(chat_id, key, content, author_id, author_name):
    """Именованная (key) — перезаписывается; безымянная — всегда новая.
    -> (note_id, updated_bool)"""
    with _lock:
        if key:
            existing = _conn.execute(
                "SELECT id FROM notes WHERE chat_id = ? AND key = ?", (chat_id, key)
            ).fetchone()
            if existing:
                _conn.execute(
                    "UPDATE notes SET content = ?, author_id = ?, author_name = ?, created_ts = ? "
                    "WHERE id = ?",
                    (content, author_id, author_name, time.time(), existing[0]),
                )
                _conn.commit()
                return existing[0], True
        cur = _conn.execute(
            "INSERT INTO notes (chat_id, key, content, author_id, author_name, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, key, content, author_id, author_name, time.time()),
        )
        _conn.commit()
        return cur.lastrowid, False


def list_notes(chat_id):
    """-> [(id, key, content, author_id, author_name), ...]"""
    with _lock:
        return _conn.execute(
            "SELECT id, key, content, author_id, author_name FROM notes "
            "WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()


def get_note(chat_id, key=None, note_id=None):
    with _lock:
        if note_id is not None:
            return _conn.execute(
                "SELECT id, key, content, author_id, author_name FROM notes "
                "WHERE chat_id = ? AND id = ?",
                (chat_id, note_id),
            ).fetchone()
        if key:
            return _conn.execute(
                "SELECT id, key, content, author_id, author_name FROM notes "
                "WHERE chat_id = ? AND key = ?",
                (chat_id, key),
            ).fetchone()
        return None


def delete_note(chat_id, note_id):
    with _lock:
        _conn.execute("DELETE FROM notes WHERE chat_id = ? AND id = ?", (chat_id, note_id))
        _conn.commit()


def count_notes(chat_id):
    with _lock:
        row = _conn.execute(
            "SELECT COUNT(*) FROM notes WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row else 0
