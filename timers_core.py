"""Логика таймеров: планирование, срабатывание, повторы, восстановление после рестарта."""
import asyncio
import logging
import time

import config
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


def _next_due(due_ts, repeat_seconds, now=None):
    """Следующее срабатывание строго в будущем (догоняем расписание без дрейфа)."""
    now = time.time() if now is None else now
    due_ts += repeat_seconds
    while due_ts <= now:
        due_ts += repeat_seconds
    return due_ts


async def _send_reminder(client, timer_id, chat_id, user_id, user_name, message_id,
                         text, repeat_seconds=0, late=False):
    body = text or "время вышло!"
    msg = "⏰ {0}, напоминаю: **{1}**".format(mention(user_id, user_name), body)
    if repeat_seconds:
        msg += "\n🔁 Каждые {0} · отмена: «{1}, отмена {2}»".format(
            human_delta(repeat_seconds), config.BOT_NAME, timer_id)
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


async def _wait_and_fire(client, timer_id, chat_id, user_id, user_name, message_id,
                         due_ts, text, repeat_seconds=0):
    try:
        while True:
            # Спим кусками не длиннее 30 минут — так надёжнее для очень долгих таймеров
            while True:
                remaining = due_ts - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 1800))

            await _send_reminder(client, timer_id, chat_id, user_id, user_name,
                                 message_id, text, repeat_seconds)

            if not repeat_seconds:
                db.set_status(timer_id, "done")
                _tasks.pop(timer_id, None)
                return

            # Повторяющийся: планируем следующий запуск и продолжаем цикл
            due_ts = _next_due(due_ts, repeat_seconds)
            db.reschedule_timer(timer_id, due_ts)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Таймер №%s: ошибка", timer_id)


def schedule(client, timer_id, chat_id, user_id, user_name, message_id, due_ts, text,
             repeat_seconds=0):
    task = asyncio.get_event_loop().create_task(
        _wait_and_fire(client, timer_id, chat_id, user_id, user_name, message_id,
                       due_ts, text, repeat_seconds)
    )
    _tasks[timer_id] = task


def cancel(timer_id):
    task = _tasks.pop(timer_id, None)
    if task:
        task.cancel()
    db.set_status(timer_id, "cancelled")


async def _fire_once_late(client, timer_id, chat_id, user_id, user_name, message_id, text):
    """Разовый просроченный таймер после рестарта: срабатывает сразу с пометкой."""
    await _send_reminder(client, timer_id, chat_id, user_id, user_name, message_id,
                         text, repeat_seconds=0, late=True)
    db.set_status(timer_id, "done")


async def restore(client):
    """Восстановить активные таймеры из базы после рестарта бота."""
    rows = db.active_timers()
    now = time.time()
    restored = 0
    late = 0
    for (timer_id, chat_id, user_id, user_name, message_id, due_ts, text, repeat_seconds) in rows:
        repeat_seconds = int(repeat_seconds or 0)
        if due_ts <= now:
            if repeat_seconds:
                # Догоняем расписание. Если опоздали немного — напомним разок с пометкой.
                late_by = now - due_ts
                threshold = min(repeat_seconds / 2.0, 3600)
                due_ts = _next_due(due_ts, repeat_seconds, now)
                db.reschedule_timer(timer_id, due_ts)
                if late_by <= threshold:
                    asyncio.get_event_loop().create_task(
                        _send_reminder(client, timer_id, chat_id, user_id, user_name,
                                       message_id, text, repeat_seconds, late=True)
                    )
                schedule(client, timer_id, chat_id, user_id, user_name, message_id,
                         due_ts, text, repeat_seconds)
                restored += 1
            else:
                asyncio.get_event_loop().create_task(
                    _fire_once_late(client, timer_id, chat_id, user_id, user_name,
                                    message_id, text)
                )
                late += 1
        else:
            schedule(client, timer_id, chat_id, user_id, user_name, message_id,
                     due_ts, text, repeat_seconds)
            restored += 1
    if restored or late:
        log.info("Таймеры: восстановлено %s, просрочено при рестарте %s", restored, late)
