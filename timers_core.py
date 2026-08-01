"""Логика таймеров: планирование, срабатывание, восстановление после рестарта."""
import asyncio
import logging
import time

import db
from timeparse import human_delta  # noqa: F401 (используется обработчиками)

log = logging.getLogger("polina.timers")

_tasks = {}  # timer_id -> asyncio.Task


def clean_name(name):
    """Убираем из имени символы, ломающие markdown-упоминание."""
    name = (name or "").strip()
    for ch in "[]()`*_":
        name = name.replace(ch, "")
    return name.strip() or "друг"


def mention(user_id, name):
    """Кликабельное упоминание, работает даже без @username."""
    return "[{0}](tg://user?id={1})".format(clean_name(name), user_id)


def active_count():
    return len(_tasks)


async def _fire(client, timer_id, chat_id, user_id, user_name, message_id, text, late=False):
    body = text or "время вышло!"
    msg = "⏰ {0}, напоминаю: **{1}**".format(mention(user_id, user_name), body)
    if late:
        msg += "\n_(сработал с задержкой: бот перезапускался)_"
    try:
        await client.send_message(chat_id, msg, reply_to=message_id or None)
    except Exception:
        # Исходное сообщение могли удалить — шлём без reply
        try:
            await client.send_message(chat_id, msg)
        except Exception as e:
            log.warning("Таймер №%s: не смог отправить в чат %s: %s", timer_id, chat_id, e)
    db.set_status(timer_id, "done")
    _tasks.pop(timer_id, None)


async def _wait_and_fire(client, timer_id, chat_id, user_id, user_name, message_id, due_ts, text):
    try:
        # Спим кусками не длиннее 30 минут — так надёжнее для очень долгих таймеров
        while True:
            remaining = due_ts - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 1800))
        await _fire(client, timer_id, chat_id, user_id, user_name, message_id, text)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Таймер №%s: ошибка", timer_id)


def schedule(client, timer_id, chat_id, user_id, user_name, message_id, due_ts, text):
    task = asyncio.get_event_loop().create_task(
        _wait_and_fire(client, timer_id, chat_id, user_id, user_name, message_id, due_ts, text)
    )
    _tasks[timer_id] = task


def cancel(timer_id):
    task = _tasks.pop(timer_id, None)
    if task:
        task.cancel()
    db.set_status(timer_id, "cancelled")


async def restore(client):
    """Восстановить активные таймеры из базы после рестарта бота."""
    rows = db.active_timers()
    now = time.time()
    restored = 0
    late = 0
    for (timer_id, chat_id, user_id, user_name, message_id, due_ts, text) in rows:
        if due_ts <= now:
            asyncio.get_event_loop().create_task(
                _fire(client, timer_id, chat_id, user_id, user_name, message_id, text, late=True)
            )
            late += 1
        else:
            schedule(client, timer_id, chat_id, user_id, user_name, message_id, due_ts, text)
            restored += 1
    if restored or late:
        log.info("Таймеры: восстановлено %s, сработало с опозданием %s", restored, late)
