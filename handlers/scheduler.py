"""Отложенные сообщения Telegram: «ферма» каждые 4 ч 01 мин силами самого Telegram.

Идея: вместо того чтобы Полина писала в чат по внутреннему таймеру (и зависела
от хостинга), она заполняет очередь ОТЛОЖЕННЫХ сообщений Telegram — лимит 100
на чат, горизонт до года. Доставляет их сам Telegram, даже если бот лежит.
Полина лишь дозаполняет очередь (авто-пополнение раз в 6 часов).

Команды — в ЛС Полине от владельца (как .все):

    .отложка @чат 4 часа 1 минута ферма   — заполнить очередь до упора (первое через ~1 мин)
    .отложка @чат                          — статус очереди
    .отложка стоп @чат                     — выключить авто-пополнение
    .отложка очистить @чат                 — удалить все отложенные и выключить авто
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from telethon import events
from telethon import utils as tl_utils
from telethon.errors import FloodWaitError
from telethon.tl import functions
from telethon.tl.types import User

import config
import db
from timeparse import parse_duration, human_delta

log = logging.getLogger("polina.sched")

MAX_SCHEDULED = 100        # лимит Telegram на отложенные в одном чате
FIRST_DELAY = 60           # первое сообщение через минуту после команды
SEND_PAUSE = 0.4           # пауза между запросами планирования, сек
MAX_INTERVAL = 7 * 86400   # максимум между сообщениями — 7 дней
TOPUP_EVERY = 6 * 3600     # период авто-пополнения
TOPUP_START_DELAY = 120    # пауза после старта бота перед первой проверкой
HORIZON_DAYS = 364         # Telegram позволяет планировать до 365 дней

PATTERN = re.compile(r"^\.отложка(?:\s+([\s\S]+))?$", re.I)


# ---------- Чистые функции (тестируются отдельно) ----------

def parse_cmd(args):
    """-> (action, target, spec): help | clear | stop | status | fill"""
    args = (args or "").strip()
    if not args:
        return ("help", None, None)
    parts = args.split(None, 1)
    head = parts[0].lower()
    tail = parts[1].strip() if len(parts) > 1 else ""
    if head in ("очистить", "clear"):
        return ("clear", tail or None, None)
    if head in ("стоп", "stop", "выкл"):
        return ("stop", tail or None, None)
    if not tail:
        return ("status", parts[0], None)
    return ("fill", parts[0], tail)


def plan_times(existing_max, free, interval, now, first_delay=FIRST_DELAY):
    """Моменты для новых отложенных: продолжаем цепочку или начинаем новую.

    existing_max — дата последнего уже запланированного (aware UTC) или None.
    """
    times = []
    if existing_max is not None:
        t = existing_max + timedelta(seconds=interval)
        while t <= now:
            t += timedelta(seconds=interval)
    else:
        t = now + timedelta(seconds=first_delay)
    horizon = now + timedelta(days=HORIZON_DAYS)
    for _ in range(max(0, int(free))):
        if t > horizon:
            break
        times.append(t)
        t += timedelta(seconds=interval)
    return times


def _fmt(dt):
    return dt.astimezone(config.TIMEZONE).strftime("%d.%m %H:%M")


def _help():
    return (
        "⏳ **Отложенные сообщения (доставляет сам Telegram)**\n"
        "`.отложка @чат 4 часа 1 минута ферма` — заполнить очередь (до {0} шт., "
        "первое через ~1 мин)\n"
        "`.отложка @чат` — статус очереди\n"
        "`.отложка стоп @чат` — выключить авто-пополнение\n"
        "`.отложка очистить @чат` — удалить всё и выключить авто\n\n"
        "🔁 После заполнения авто-пополнение держит очередь полной (проверка "
        "каждые 6 ч) — «ферма» не закончится."
    ).format(MAX_SCHEDULED)


# ---------- Работа с Telegram ----------

async def _resolve(client, target):
    """@username / id -> entity группы (или None)."""
    try:
        key = int(target) if target.lstrip("-").isdigit() else target
        entity = await client.get_entity(key)
    except Exception as e:
        log.warning("отложка: не нашла чат %r: %s", target, e)
        return None
    if isinstance(entity, User):
        return None
    return entity


async def _scheduled(client, entity):
    """[(msg_id, date_utc), ...] — уже запланированные в чате."""
    res = await client(functions.messages.GetScheduledHistoryRequest(peer=entity, hash=0))
    out = []
    for m in getattr(res, "messages", []) or []:
        d = getattr(m, "date", None)
        if d:
            out.append((m.id, d))
    return out


async def _fill(client, entity, interval, text, progress=None):
    """Дозаполнить очередь. -> (added, total_after, first_dt, last_dt)"""
    existing = await _scheduled(client, entity)
    free = MAX_SCHEDULED - len(existing)
    now = datetime.now(timezone.utc)
    existing_max = max((d for _i, d in existing), default=None)
    times = plan_times(existing_max, free, interval, now)

    added = 0
    first_dt = None
    last_dt = existing_max
    for dt in times:
        try:
            await client.send_message(entity, text, schedule=dt)
        except FloodWaitError as e:
            if e.seconds > 90:
                log.warning("отложка: FloodWait %s сек — останавливаюсь на %s шт.", e.seconds, added)
                break
            await asyncio.sleep(e.seconds + 1)
            try:
                await client.send_message(entity, text, schedule=dt)
            except Exception as e2:
                log.warning("отложка: повтор после FloodWait не удался: %s", e2)
                break
        except Exception as e:
            log.warning("отложка: ошибка планирования: %s", e)
            break
        added += 1
        if first_dt is None:
            first_dt = dt
        last_dt = dt
        if progress and added % 10 == 0:
            try:
                await progress.edit("📤 Планирую… {0}/{1}".format(added, len(times)))
            except Exception:
                pass
        await asyncio.sleep(SEND_PAUSE)
    return added, len(existing) + added, first_dt, last_dt


# ---------- Команды ----------

async def _cmd_fill(event, target, spec):
    r = parse_duration(spec)
    if r is None:
        await event.respond(
            "🤔 Не поняла интервал. Пример: `.отложка @чат 4 часа 1 минута ферма`")
        return
    interval, text = r
    text = (text or "").strip()
    if not text:
        await event.respond("✍️ Добавь текст сообщения: `.отложка @чат 4 часа 1 минута ферма`")
        return
    if interval < config.REPEAT_MIN_SECONDS:
        await event.respond("⏱ Слишком часто — минимум {0}.".format(human_delta(config.REPEAT_MIN_SECONDS)))
        return
    if interval > MAX_INTERVAL:
        await event.respond("⏱ Слишком редко — максимум 7 дней между сообщениями.")
        return

    entity = await _resolve(event.client, target)
    if entity is None:
        await event.respond("🚫 Не нашла группу «{0}». Укажи @username или ID.".format(target))
        return
    peer_id = tl_utils.get_peer_id(entity)
    if not config.chat_allowed(peer_id):
        await event.respond("🚫 Чат «{0}» не в списке разрешённых (ALLOWED_CHATS).".format(target))
        return

    title = getattr(entity, "title", str(target))
    progress = await event.respond(
        "📤 Планирую «{0}» каждые {1} в «{2}»…".format(text, human_delta(interval), title))

    added, total, first_dt, last_dt = await _fill(event.client, entity, interval, text, progress)
    db.set_sched_config(peer_id, interval, text, enabled=True)

    if added == 0 and total >= MAX_SCHEDULED:
        msg = ("ℹ️ Очередь уже полная: {0}/{1}. Авто-пополнение включено 🔁".format(total, MAX_SCHEDULED))
    elif added == 0:
        msg = "⚠️ Не получилось добавить отложенные — попробуй ещё раз (детали в логах)."
    else:
        msg = (
            "✅ Запланировала {0} шт. «{1}» каждые {2} — в очереди {3}/{4}.\n"
            "Первое: {5} · последнее: {6}.\n"
            "🔁 Авто-пополнение включено: очередь не иссякнет (проверка каждые 6 ч).".format(
                added, text, human_delta(interval), total, MAX_SCHEDULED,
                _fmt(first_dt), _fmt(last_dt))
        )
    try:
        await progress.edit(msg)
    except Exception:
        await event.respond(msg)


async def _cmd_status(event, target):
    entity = await _resolve(event.client, target)
    if entity is None:
        await event.respond("🚫 Не нашла группу «{0}».".format(target))
        return
    peer_id = tl_utils.get_peer_id(entity)
    title = getattr(entity, "title", str(target))
    try:
        sched = await _scheduled(event.client, entity)
    except Exception as e:
        await event.respond("⚠️ Не смогла прочитать очередь: `{0}`".format(str(e)[:120]))
        return
    cfg = db.get_sched_config(peer_id)
    lines = ["📋 **Отложка в «{0}»**: {1}/{2}".format(title, len(sched), MAX_SCHEDULED)]
    if sched:
        dates = sorted(d for _i, d in sched)
        lines.append("Первое: {0} · последнее: {1}".format(_fmt(dates[0]), _fmt(dates[-1])))
    if cfg:
        interval, text, enabled = cfg
        state = "вкл 🔁" if enabled else "выкл ⏸"
        lines.append("Авто-пополнение: {0} (каждые {1}, «{2}»)".format(state, human_delta(interval), text))
    else:
        lines.append("Авто-пополнение: не настроено")
    await event.respond("\n".join(lines))


async def _cmd_clear(event, target):
    if not target:
        await event.respond("Укажи чат: `.отложка очистить @чат`")
        return
    entity = await _resolve(event.client, target)
    if entity is None:
        await event.respond("🚫 Не нашла группу «{0}».".format(target))
        return
    peer_id = tl_utils.get_peer_id(entity)
    title = getattr(entity, "title", str(target))
    try:
        sched = await _scheduled(event.client, entity)
    except Exception as e:
        await event.respond("⚠️ Не смогла прочитать очередь: `{0}`".format(str(e)[:120]))
        return
    db.set_sched_enabled(peer_id, False)
    if not sched:
        await event.respond("ℹ️ В «{0}» отложенных нет. Авто-пополнение выключено.".format(title))
        return
    ids = [i for i, _d in sched]
    deleted = 0
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        try:
            await event.client(functions.messages.DeleteScheduledMessagesRequest(peer=entity, id=chunk))
            deleted += len(chunk)
        except Exception as e:
            log.warning("отложка: не смогла удалить пачку: %s", e)
            break
    await event.respond(
        "🗑 Удалила {0} отложенных из «{1}». Авто-пополнение выключено ⏸".format(deleted, title))


async def _cmd_stop(event, target):
    if not target:
        await event.respond("Укажи чат: `.отложка стоп @чат`")
        return
    entity = await _resolve(event.client, target)
    if entity is None:
        await event.respond("🚫 Не нашла группу «{0}».".format(target))
        return
    peer_id = tl_utils.get_peer_id(entity)
    if db.set_sched_enabled(peer_id, False):
        await event.respond("⏸ Авто-пополнение выключено. Уже запланированные сообщения останутся "
                            "(удалить всё: `.отложка очистить {0}`).".format(target))
    else:
        await event.respond("ℹ️ Для этого чата авто-пополнение и не было настроено.")


# ---------- Авто-пополнение ----------

async def _topup_pass(client):
    for (chat_id, interval, text) in db.enabled_sched_configs():
        if not config.chat_allowed(chat_id):
            continue
        try:
            entity = await client.get_entity(chat_id)
        except Exception as e:
            log.warning("отложка: авто — не получила чат %s: %s", chat_id, e)
            continue
        try:
            added, total, _first, _last = await _fill(client, entity, interval, text)
        except Exception as e:
            log.warning("отложка: авто — ошибка пополнения в %s: %s", chat_id, e)
            continue
        if added:
            log.info("отложка: авто — добавила %s (теперь %s/%s) в %s",
                     added, total, MAX_SCHEDULED, chat_id)
        # После долгого простоя (много добавили) — сообщим владельцу
        if added >= 10 and config.OWNER_ID and config.OWNER_ID != config.SELF_ID:
            try:
                await client.send_message(
                    config.OWNER_ID,
                    "🔁 Отложка: дозаполнила {0} шт. в «{1}» (теперь {2}/{3}).".format(
                        added, getattr(entity, "title", "чат"), total, MAX_SCHEDULED))
            except Exception:
                pass


async def _topup_loop(client):
    await asyncio.sleep(TOPUP_START_DELAY)
    while True:
        try:
            await _topup_pass(client)
        except Exception:
            log.exception("отложка: сбой цикла авто-пополнения")
        await asyncio.sleep(TOPUP_EVERY)


def register(client):

    @client.on(events.NewMessage(pattern=PATTERN))
    async def sched_cmd(event):
        # только личка и только владелец
        if not event.is_private or event.sender_id != config.OWNER_ID:
            return
        action, target, spec = parse_cmd(event.pattern_match.group(1))
        if action == "help":
            await event.respond(_help())
        elif action == "clear":
            await _cmd_clear(event, target)
        elif action == "stop":
            await _cmd_stop(event, target)
        elif action == "status":
            await _cmd_status(event, target)
        else:
            await _cmd_fill(event, target, spec)

    # фоновое авто-пополнение
    asyncio.get_event_loop().create_task(_topup_loop(client))
