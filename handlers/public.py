"""Публичные команды по имени — доступны всем участникам чата.

    «Полина, таймер через 4 часа ферма»  — через 4 часа тегнет автора
    «Полина, таймер в 18:30 созвон»      — напоминание на время
    «Полина, таймеры»                    — список активных в чате
    «Полина, отмена 5»                   — отменить таймер №5 (автор или владелец)
    «Полина, погода Москва»              — погода
    «Полина, помощь»                     — краткая справка

Владелец может выключить это в конкретном чате командой `.полина выкл`.
"""
import logging
import re
import time
from datetime import datetime, timedelta

from telethon import events

import config
import db
import timers_core
from timeparse import parse_when, parse_duration, human_delta
from weather_api import get_weather_text
from . import fun

log = logging.getLogger("polina.public")

_last_timer_at = {}  # user_id -> ts, анти-флуд на создание таймеров

TIMER_RE = re.compile(r"^(?:таймер|напомни(?:ть)?|напоминание)\b[,:]?\s*(.+)$", re.I | re.S)
EVERY_RE = re.compile(r"^кажд(?:ые|ый|ую|ое)\b[,:]?\s*(.+)$", re.I | re.S)
LIST_RE = re.compile(r"^таймеры\b", re.I)
CANCEL_RE = re.compile(r"^(?:отмена|отмени|стоп)\s+(?:№\s*)?(\d+)\s*$", re.I)
WEATHER_RE = re.compile(r"^погода\b[,:]?\s*(.*)$", re.I | re.S)
HELP_RE = re.compile(r"^(?:помощь|команды|что\s+умеешь\??|help)\s*$", re.I)

# Заметки
NOTE_SAVE_RE = re.compile(r"^(?:запомни|сохрани)\b[,:]?\s*([\s\S]+)$", re.I)
NOTE_DEL_RE = re.compile(r"^(?:забудь|удали)(?:\s+заметку)?\b[,:]?\s*(.+)$", re.I | re.S)
NOTES_LIST_RE = re.compile(r"^заметки\b", re.I)
NOTE_GET_RE = re.compile(r"^заметка\s+(.+)$", re.I | re.S)
KEY_SPLIT_RE = re.compile(r"^([^\n:=]{1,30})\s*[:=]\s*([\s\S]+)$")

# Развлечения. (?!-) в «кто» — чтобы не ловить «кто-нибудь», «кто-то»
FUN_WHO_RE = re.compile(r"^кто(?!-)\b[,:]?\s*(.*)$", re.I | re.S)
FUN_CHOOSE_RE = re.compile(r"^(?:выбери|выбор)\b[,:]?\s*(.*)$", re.I | re.S)
FUN_DICE_RE = re.compile(r"^(?:брось\s+)?кубик\b", re.I)
FUN_BALL_RE = re.compile(r"^шар\b[,:]?\s*(.*)$", re.I | re.S)


def _public_help():
    n = config.BOT_NAME
    return (
        "👋 Я {0}. Что умею в этом чате:\n"
        "• «{0}, таймер через 4 часа ферма» — напомню и тегну вас\n"
        "• «{0}, каждые 4 часа ферма» — буду напоминать по кругу до отмены\n"
        "• «{0}, таймер в 18:30 созвон» — напоминание на время\n"
        "• «{0}, таймеры» — список активных\n"
        "• «{0}, отмена 5» — отменить таймер №5\n"
        "• «{0}, погода Москва» — текущая погода\n"
        "• «{0}, запомни пароль: 1234» — сохранить заметку\n"
        "• «{0}, заметки» — список · «{0}, заметка пароль» — показать · «{0}, забудь 3» — удалить\n"
        "• «{0}, кто самый умный?» — выберу случайного из чата 🎯\n"
        "• «{0}, выбери: пицца или суши» · «{0}, кубик» 🎲 · «{0}, шар: вопрос?» 🔮"
    ).format(n)


def register(client):
    name_re = re.compile(
        r"^\s*{0}\b[,!:\s]*(.+)$".format(re.escape(config.BOT_NAME)), re.I | re.S
    )

    @client.on(events.NewMessage())
    async def public_router(event):
        raw = event.raw_text or ""
        m = name_re.match(raw)
        if not m:
            return
        rest = m.group(1).strip()

        if not config.responds_here(event.chat_id, event.is_private, event.sender_id):
            return

        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False):
            return
        is_owner = event.sender_id == config.OWNER_ID
        if not is_owner and not db.is_public_enabled(event.chat_id):
            return

        em = EVERY_RE.match(rest)
        if em:
            await _create_every(event, sender, em.group(1))
            return
        tm = TIMER_RE.match(rest)
        if tm:
            await _create_timer(event, sender, tm.group(1))
            return
        if LIST_RE.match(rest):
            await _list_timers(event)
            return
        cm = CANCEL_RE.match(rest)
        if cm:
            await _cancel_timer(event, int(cm.group(1)), is_owner)
            return
        wm = WEATHER_RE.match(rest)
        if wm:
            await _weather(event, wm.group(1).strip())
            return
        sm = NOTE_SAVE_RE.match(rest)
        if sm:
            await _save_note(event, sender, sm.group(1))
            return
        dm = NOTE_DEL_RE.match(rest)
        if dm:
            await _del_note(event, dm.group(1), is_owner)
            return
        if NOTES_LIST_RE.match(rest):
            await _list_notes(event)
            return
        gm = NOTE_GET_RE.match(rest)
        if gm:
            await _get_note(event, gm.group(1))
            return
        fm = FUN_WHO_RE.match(rest)
        if fm:
            if not fun.on_cooldown(event.sender_id):
                await fun.who(event, fm.group(1))
            return
        fm = FUN_CHOOSE_RE.match(rest)
        if fm:
            if not fun.on_cooldown(event.sender_id):
                await fun.choose(event, fm.group(1))
            return
        if FUN_DICE_RE.match(rest):
            if not fun.on_cooldown(event.sender_id):
                await fun.dice(event)
            return
        fm = FUN_BALL_RE.match(rest)
        if fm:
            if not fun.on_cooldown(event.sender_id):
                await fun.ball(event, fm.group(1))
            return
        if HELP_RE.match(rest):
            await event.reply(_public_help())
            return
        # Остальное игнорируем: возможно, обращались не к боту


async def _create_timer(event, sender, spec):
    # «таймер каждые 4 часа ферма» — тоже повторяющийся
    em = EVERY_RE.match(spec.strip())
    if em:
        await _create_every(event, sender, em.group(1))
        return

    user_id = event.sender_id
    now = time.time()
    if now - _last_timer_at.get(user_id, 0) < config.TIMER_COOLDOWN:
        return  # тихий анти-флуд

    parsed = parse_when(spec, config.TIMEZONE)
    if parsed is None:
        await event.reply(
            "⏰ Не поняла время. Примеры:\n"
            "• {0}, таймер через 4 часа ферма\n"
            "• {0}, таймер через 1 час 30 минут чай\n"
            "• {0}, таймер в 18:30 созвон".format(config.BOT_NAME)
        )
        return
    if parsed.seconds < config.TIMER_MIN_SECONDS:
        await event.reply("⏰ Слишком быстро — минимум {0} секунд.".format(config.TIMER_MIN_SECONDS))
        return
    if parsed.seconds > config.TIMER_MAX_SECONDS:
        await event.reply("⏰ Слишком долго — максимум 60 дней.")
        return
    if db.count_active(event.chat_id, user_id) >= config.TIMERS_PER_USER:
        await event.reply(
            "⏰ У вас уже {0} активных таймеров в этом чате — это максимум.".format(config.TIMERS_PER_USER)
        )
        return

    _last_timer_at[user_id] = now
    text = parsed.text[:config.TIMER_TEXT_MAX]
    user_name = timers_core.clean_name(getattr(sender, "first_name", "") or "")
    timer_id = db.add_timer(event.chat_id, user_id, user_name, event.id, parsed.due_ts, text)
    timers_core.schedule(
        event.client, timer_id, event.chat_id, user_id, user_name, event.id, parsed.due_ts, text
    )
    label = " «{0}»".format(text) if text else ""
    await event.reply("⏰ Принято! Напомню через {0}{1} — таймер №{2}".format(parsed.human, label, timer_id))


async def _create_every(event, sender, spec):
    """«каждые 4 часа ферма» — повторяющееся напоминание до отмены."""
    user_id = event.sender_id
    now = time.time()
    if now - _last_timer_at.get(user_id, 0) < config.TIMER_COOLDOWN:
        return  # тихий анти-флуд

    r = parse_duration(spec)
    if r is None:
        await event.reply(
            "🔁 Не поняла интервал. Примеры:\n"
            "• {0}, каждые 4 часа ферма\n"
            "• {0}, каждый час вода\n"
            "• {0}, каждые 30 минут проверка".format(config.BOT_NAME)
        )
        return
    interval, text = r
    if interval < config.REPEAT_MIN_SECONDS:
        await event.reply("🔁 Слишком часто — минимальный повтор {0}.".format(
            human_delta(config.REPEAT_MIN_SECONDS)))
        return
    if interval > config.TIMER_MAX_SECONDS:
        await event.reply("🔁 Слишком редко — максимум 60 дней.")
        return
    if db.count_active(event.chat_id, user_id) >= config.TIMERS_PER_USER:
        await event.reply(
            "⏰ У вас уже {0} активных таймеров в этом чате — это максимум.".format(config.TIMERS_PER_USER)
        )
        return

    _last_timer_at[user_id] = now
    text = text.strip()[:config.TIMER_TEXT_MAX]
    user_name = timers_core.clean_name(getattr(sender, "first_name", "") or "")
    due_ts = now + interval
    timer_id = db.add_timer(event.chat_id, user_id, user_name, event.id, due_ts, text,
                            repeat_seconds=interval)
    timers_core.schedule(event.client, timer_id, event.chat_id, user_id, user_name,
                         event.id, due_ts, text, repeat_seconds=interval)
    first = datetime.now(config.TIMEZONE) + timedelta(seconds=interval)
    label = " «{0}»".format(text) if text else ""
    await event.reply(
        "🔁 Принято! Буду напоминать каждые {0}{1} — таймер №{2}.\n"
        "Первое напоминание в {3}. Остановить: «{4}, отмена {2}»".format(
            human_delta(interval), label, timer_id, first.strftime("%H:%M"), config.BOT_NAME)
    )


async def _list_timers(event):
    rows = db.active_timers(event.chat_id)
    if not rows:
        await event.reply("⏰ В этом чате нет активных таймеров.")
        return
    lines = ["⏰ **Активные таймеры:**"]
    for (tid, _chat, _uid, uname, _mid, due_ts, text, repeat_seconds) in rows[:15]:
        left = human_delta(max(0, int(due_ts - time.time())))
        label = " — «{0}»".format(text) if text else ""
        rep = " 🔁(кажд. {0})".format(human_delta(int(repeat_seconds))) if repeat_seconds else ""
        lines.append("№{0}{1}: через {2}{3} · {4}".format(tid, rep, left, label, uname or "кто-то"))
    if len(rows) > 15:
        lines.append("… и ещё {0}".format(len(rows) - 15))
    lines.append("\nОтмена: «{0}, отмена N»".format(config.BOT_NAME))
    await event.reply("\n".join(lines))


async def _cancel_timer(event, timer_id, is_owner):
    row = db.get_timer(timer_id)
    if not row or row[7] != "active" or row[1] != event.chat_id:
        await event.reply("🤷‍♀️ Таймер №{0} не найден в этом чате.".format(timer_id))
        return
    if row[2] != event.sender_id and not is_owner:
        await event.reply("🚫 Отменить таймер может только его автор.")
        return
    timers_core.cancel(timer_id)
    await event.reply("✅ Таймер №{0} отменён.".format(timer_id))


async def _weather(event, city):
    if not city:
        await event.reply("🌤 Напишите город: «{0}, погода Москва»".format(config.BOT_NAME))
        return
    try:
        await event.reply(await get_weather_text(city))
    except Exception as e:
        log.warning("погода: %s", e)
        await event.reply("⚠️ Не получилось узнать погоду, попробуйте позже.")


# ---------- Заметки ----------

async def _save_note(event, sender, spec):
    spec = spec.strip()
    if not spec:
        await event.reply("📝 Что запомнить? Пример: «{0}, запомни пароль: 1234»".format(config.BOT_NAME))
        return
    key = ""
    content = spec
    km = KEY_SPLIT_RE.match(spec)
    if km:
        key = km.group(1).strip().lower()
        content = km.group(2).strip()
    content = content[:1000]
    if not content:
        await event.reply("📝 Пустая заметка.")
        return
    # Лимит 50 заметок на чат (обновление существующей именованной не считается новой)
    is_update = bool(key) and db.get_note(event.chat_id, key=key) is not None
    if not is_update and db.count_notes(event.chat_id) >= 50:
        await event.reply("📝 Лимит 50 заметок. Удалите лишние: «{0}, забудь <№>».".format(config.BOT_NAME))
        return
    name = timers_core.clean_name(getattr(sender, "first_name", "") or "")
    note_id, updated = db.add_note(event.chat_id, key, content, event.sender_id, name)
    if key:
        await event.reply("✅ {0} «{1}» (№{2}).".format("Обновила" if updated else "Запомнила", key, note_id))
    else:
        await event.reply("✅ Запомнила заметку №{0}.".format(note_id))


async def _list_notes(event):
    rows = db.list_notes(event.chat_id)
    if not rows:
        await event.reply("📝 Заметок пока нет. Добавьте: «{0}, запомни ...»".format(config.BOT_NAME))
        return
    lines = ["📌 **Заметки:**"]
    for (nid, key, content, _aid, _an) in rows[:30]:
        preview = (content if len(content) <= 60 else content[:57] + "…").replace("\n", " ")
        if key:
            lines.append("`#{0}` [{1}]: {2}".format(nid, key, preview))
        else:
            lines.append("`#{0}`: {1}".format(nid, preview))
    if len(rows) > 30:
        lines.append("… и ещё {0}".format(len(rows) - 30))
    lines.append("\nПоказать: «{0}, заметка <ключ или №>» · Удалить: «{0}, забудь <№>»".format(config.BOT_NAME))
    await event.reply("\n".join(lines))


def _find_note(chat_id, ref):
    ref = ref.strip()
    row = None
    if ref.isdigit():
        row = db.get_note(chat_id, note_id=int(ref))
    if row is None:
        row = db.get_note(chat_id, key=ref.lower())
    return row


async def _get_note(event, ref):
    row = _find_note(event.chat_id, ref)
    if row is None:
        await event.reply("🤷‍♀️ Не нашла заметку «{0}».".format(ref.strip()))
        return
    nid, key, content, _aid, _an = row
    head = "[{0}] ".format(key) if key else ""
    await event.reply("📌 {0}#{1}:\n{2}".format(head, nid, content))


async def _del_note(event, ref, is_owner):
    row = _find_note(event.chat_id, ref)
    if row is None:
        await event.reply("🤷‍♀️ Не нашла заметку «{0}».".format(ref.strip()))
        return
    nid, _key, _content, aid, _an = row
    if aid != event.sender_id and not is_owner:
        await event.reply("🚫 Удалить заметку может только её автор.")
        return
    db.delete_note(event.chat_id, nid)
    await event.reply("✅ Заметка №{0} удалена.".format(nid))
