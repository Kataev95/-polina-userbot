"""Приветствие новых участников группы.

Два режима (на каждый чат отдельно):

1. «Сразу по входу» (по умолчанию) — Полина здоровается, как только человек
   вступил. Работает, если системное событие о входе доходит до Полины.

2. «По триггеру» — для чатов, где бот-охранник (напр. SecurityBermuda)
   удаляет системное сообщение о входе и сам пишет обезличенное «Добро
   пожаловать». Тогда Полина ЗАПОМИНАЕТ вошедших (событие входа приходит в
   момент входа, до удаления), а тегает их, когда появляется сообщение
   бота-триггера — отвечая прямо на него.

Настройка — командой `.привет` в самой группе (под аккаунтом Полины или
с вашего основного аккаунта):

    .привет                      — статус и текущий текст/режим
    .привет вкл / .привет выкл    — включить / выключить
    .привет текст <шаблон>        — задать текст ({name}, {chat})
    .привет сразу                 — режим «сразу по входу»
    .привет триггер               — режим «по триггеру» (фраза «добро пожаловать»)
    .привет триггер @SecurityBermuda  — режим «по триггеру» от конкретного бота
    .привет триггер добро пожаловать  — режим «по триггеру» по своей фразе
"""
import logging
import re
import time

from telethon import events

import config
import db
from timers_core import mention, clean_name

log = logging.getLogger("polina.welcome")

DEFAULT = "👋 Добро пожаловать, {name}! Рады видеть тебя в «{chat}»."
DEFAULT_TRIGGER = "добро пожаловать"
PENDING_TTL = 900   # сколько секунд помним вошедшего (15 мин)
PENDING_MAX = 20    # максимум в очереди на чат

CFG_RE = re.compile(r"^\.привет(?:\s+([\s\S]+))?$", re.I)

# Очередь недавно вошедших в памяти: chat_id -> [(user_id, name, ts), ...]
_pending = {}


def _add_pending(chat_id, user_id, name):
    now = time.time()
    lst = _pending.setdefault(chat_id, [])
    # выбрасываем протухших и дубликаты этого же человека
    lst[:] = [(u, n, t) for (u, n, t) in lst if now - t < PENDING_TTL and u != user_id]
    lst.append((user_id, name, now))
    if len(lst) > PENDING_MAX:
        del lst[:-PENDING_MAX]


def _pop_one(chat_id):
    """Забрать самого раннего свежего новичка (FIFO). -> (user_id, name) | None"""
    now = time.time()
    lst = _pending.get(chat_id, [])
    while lst:
        user_id, name, ts = lst.pop(0)
        if now - ts < PENDING_TTL:
            return user_id, name
    return None


async def _chat_title(event):
    try:
        chat = await event.get_chat()
        return getattr(chat, "title", "чат")
    except Exception:
        return "чат"


async def _greet(client, chat_id, pairs, title, reply_to=None):
    """pairs: [(user_id, name), ...]"""
    tmpl = db.get_welcome_text(chat_id) or DEFAULT
    names = ", ".join(mention(uid, nm or "друг") for uid, nm in pairs[:5])
    text = tmpl.replace("{name}", names).replace("{chat}", title)
    try:
        await client.send_message(chat_id, text, reply_to=reply_to)
    except Exception:
        try:
            await client.send_message(chat_id, text)
        except Exception as e:
            log.warning("приветствие: не смог отправить в %s: %s", chat_id, e)


async def _say(event, text):
    if event.out:
        await event.edit(text)
    else:
        await event.reply(text)


def register(client):

    @client.on(events.ChatAction)
    async def on_join(event):
        if not (event.user_joined or event.user_added):
            return

        users = list(event.users or [])
        if not users:
            try:
                u = await event.get_user()
                if u:
                    users = [u]
            except Exception:
                pass
        users = [u for u in users
                 if not getattr(u, "bot", False) and u.id != config.SELF_ID]
        if not users:
            return

        for u in users:
            _add_pending(event.chat_id, u.id, clean_name(u.first_name))

        enabled = db.welcome_enabled(event.chat_id)
        mode = db.get_welcome_mode(event.chat_id)
        # Диагностика: видно в логах Bothost, что вход пойман
        log.info("вход: %s чел. в чате %s (приветствие=%s, режим=%s)",
                 len(users), event.chat_id, "вкл" if enabled else "выкл",
                 "триггер" if mode == 1 else "сразу")

        if not enabled or mode == 1:
            return  # в режиме триггера ждём сообщения бота-приветствия

        title = await _chat_title(event)
        pairs = [(u.id, clean_name(u.first_name)) for u in users]
        await _greet(client, event.chat_id, pairs, title, reply_to=event.action_message.id if event.action_message else None)

    @client.on(events.NewMessage)
    async def on_trigger(event):
        # Приветствие по сообщению бота-триггера (напр. SecurityBermuda)
        cid = event.chat_id
        if not event.is_group:
            return
        if not db.welcome_enabled(cid) or db.get_welcome_mode(cid) != 1:
            return

        sender = await event.get_sender()
        if sender is None or not getattr(sender, "bot", False):
            return  # триггеримся только на сообщения ботов

        trig = db.get_welcome_trigger(cid)
        matched = False
        if trig.startswith("@"):
            uname = (getattr(sender, "username", "") or "").lower()
            matched = uname == trig[1:].lower()
        else:
            phrase = (trig or DEFAULT_TRIGGER).lower()
            matched = phrase in (event.raw_text or "").lower()
        if not matched:
            return

        newbie = _pop_one(cid)
        if newbie is None:
            log.info("триггер приветствия сработал в %s, но очередь новичков пуста "
                     "(событие входа не поймано?).", cid)
            return

        title = await _chat_title(event)
        await _greet(client, cid, [newbie], title, reply_to=event.id)

    @client.on(events.NewMessage(pattern=CFG_RE))
    async def welcome_cfg(event):
        if not (event.out or event.sender_id == config.OWNER_ID):
            return
        if event.is_private:
            await _say(event, "⚙️ Команду `.привет` используйте в самой группе, для которой настраиваете приветствие.")
            return

        arg = (event.pattern_match.group(1) or "").strip()
        cid = event.chat_id

        if not arg:
            on = db.welcome_enabled(cid)
            mode = db.get_welcome_mode(cid)
            trig = db.get_welcome_trigger(cid) or DEFAULT_TRIGGER
            tmpl = db.get_welcome_text(cid) or DEFAULT
            mode_str = "сразу по входу" if mode == 0 else "по сообщению-триггеру («{0}»)".format(trig)
            await _say(
                event,
                "👋 **Приветствие новичков**: {0}\n"
                "Режим: {1}\n"
                "Текст: {2}\n\n"
                "Управление:\n"
                "`.привет вкл` / `.привет выкл`\n"
                "`.привет текст <шаблон>`  (плейсхолдеры `{{name}}`, `{{chat}}`)\n"
                "`.привет сразу` — здороваться сразу по входу\n"
                "`.привет триггер` — ждать «{3}» от бота-охранника\n"
                "`.привет триггер @SecurityBermuda` — ждать сообщение конкретного бота".format(
                    "включено ✅" if on else "выключено 🚫", mode_str, tmpl, DEFAULT_TRIGGER
                ),
            )
            return

        low = arg.lower()
        if low in ("вкл", "on", "включить"):
            db.set_welcome(cid, on=True)
            await _say(event, "✅ Приветствие новичков включено в этом чате.")
        elif low in ("выкл", "off", "выключить"):
            db.set_welcome(cid, on=False)
            await _say(event, "🚫 Приветствие новичков выключено.")
        elif low in ("сразу", "вход", "поумолчанию"):
            db.set_welcome(cid, mode=0, on=True)
            await _say(event, "✅ Режим: приветствую сразу по входу.")
        elif low.startswith("триггер"):
            rest = arg[len("триггер"):].strip()
            db.set_welcome(cid, mode=1, on=True, trigger=rest)
            if rest.startswith("@"):
                how = "жду сообщение от {0}".format(rest)
            elif rest:
                how = "жду сообщение с фразой «{0}»".format(rest)
            else:
                how = "жду сообщение с фразой «{0}»".format(DEFAULT_TRIGGER)
            await _say(
                event,
                "✅ Режим «по триггеру» включён — {0} и тегаю нового участника в ответ.\n"
                "Полина запоминает вошедших сама; убедись, что она видит входы "
                "(проверь логи в панели Bothost: строка «вход: …»).".format(how),
            )
        elif low.startswith("текст"):
            tmpl = arg[len("текст"):].strip()
            if not tmpl:
                await _say(event, "✍️ Укажите текст: `.привет текст Добро пожаловать, {name}! 🌴`")
                return
            db.set_welcome(cid, text=tmpl[:500], on=True)
            preview = tmpl.replace("{name}", "Имя").replace("{chat}", "Чат")
            await _say(event, "✅ Шаблон сохранён и приветствие включено.\nПример: {0}".format(preview[:400]))
        else:
            await _say(event, "🤔 Не поняла. `.привет вкл` / `выкл` / `сразу` / `триггер [@бот]` / `текст <шаблон>`")
