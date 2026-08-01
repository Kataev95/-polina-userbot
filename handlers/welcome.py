"""Приветствие новых участников группы.

Два режима (на каждый чат отдельно):

1. «Сразу по входу» (по умолчанию) — Полина здоровается, как только человек
   вступил. Работает, если системное событие о входе доходит до Полины.

2. «По триггеру» — для чатов, где бот-охранник (напр. SecurityBermuda)
   удаляет системное сообщение о входе и сам пишет «Добро пожаловать».
   Полина реагирует на сообщение бота-охранника и определяет новичка так:
   a) берёт его ПРЯМО ИЗ СООБЩЕНИЯ охранника — если тот упомянул новичка
      (кликабельное имя, tg://user-ссылка или @username) — самый надёжный путь;
   b) если упоминания нет — берёт из очереди недавно вошедших (Полина
      запоминает входы, пока системное сообщение ещё не удалено).

Настройка — командой `.привет` в самой группе (под аккаунтом Полины или
с вашего основного аккаунта):

    .привет                      — статус и текущий текст/режим
    .привет вкл / .привет выкл    — включить / выключить
    .привет текст <шаблон>        — задать текст ({name}, {chat})
    .привет сразу                 — режим «сразу по входу»
    .привет триггер               — режим «по триггеру»: фраза «добро пожаловать» от любого бота
    .привет триггер @SecurityBermuda  — «по триггеру»: «добро пожаловать» от конкретного бота
    .привет триггер <фраза>       — «по триггеру»: своя фраза (от любого бота)
"""
import logging
import re
import time

from telethon import events
from telethon.tl import types as tl_types

import config
import db
from timers_core import mention, clean_name

log = logging.getLogger("polina.welcome")

DEFAULT = "👋 Добро пожаловать, {name}! Рады видеть тебя в «{chat}»."
DEFAULT_TRIGGER = "добро пожаловать"
PENDING_TTL = 900       # сколько секунд помним вошедшего (15 мин)
PENDING_MAX = 20        # максимум в очереди на чат
GREET_DEDUP_TTL = 600   # не приветствуем одного человека чаще, чем раз в 10 мин

CFG_RE = re.compile(r"^\.привет(?:\s+([\s\S]+))?$", re.I)
_USER_URL_RE = re.compile(r"tg://user\?id=(\d+)")

# Очередь недавно вошедших: chat_id -> [(user_id, name, ts), ...]
_pending = {}
# Кого уже поприветствовали недавно: chat_id -> {user_id: ts}
_greeted = {}


# ---------- Очередь вошедших ----------

def _add_pending(chat_id, user_id, name):
    now = time.time()
    lst = _pending.setdefault(chat_id, [])
    lst[:] = [(u, n, t) for (u, n, t) in lst if now - t < PENDING_TTL and u != user_id]
    lst.append((user_id, name, now))
    if len(lst) > PENDING_MAX:
        del lst[:-PENDING_MAX]


def _pop_one(chat_id):
    """Самый ранний свежий новичок (FIFO). -> (user_id, name) | None"""
    now = time.time()
    lst = _pending.get(chat_id, [])
    while lst:
        user_id, name, ts = lst.pop(0)
        if now - ts < PENDING_TTL:
            return user_id, name
    return None


def _remove_pending(chat_id, user_id):
    lst = _pending.get(chat_id)
    if lst:
        lst[:] = [(u, n, t) for (u, n, t) in lst if u != user_id]


# ---------- Анти-дубль приветствий ----------

def _was_greeted(chat_id, user_id):
    ts = _greeted.get(chat_id, {}).get(user_id)
    return ts is not None and time.time() - ts < GREET_DEDUP_TTL


def _mark_greeted(chat_id, user_id):
    now = time.time()
    d = _greeted.setdefault(chat_id, {})
    for k in [k for k, v in d.items() if now - v > GREET_DEDUP_TTL]:
        del d[k]
    d[user_id] = now


# ---------- Извлечение новичка из сообщения-триггера ----------

def _slice_utf16(text, offset, length):
    """Entity-офсеты Telegram считаются в UTF-16 — режем корректно."""
    try:
        b = text.encode("utf-16-le")
        piece = b[offset * 2:(offset + length) * 2]
        return piece.decode("utf-16-le", errors="ignore").strip()
    except Exception:
        return ""


async def _extract_mentioned(event):
    """Первый упомянутый в сообщении человек. -> (user_id, name) | None"""
    msg = event.message
    text = msg.raw_text or ""
    ents = msg.entities or []

    # 1) Кликабельное имя без @ (text mention) — так тегает SecurityBermuda
    for e in ents:
        if isinstance(e, tl_types.MessageEntityMentionName):
            return e.user_id, _slice_utf16(text, e.offset, e.length)

    # 2) Ссылка вида tg://user?id=123
    for e in ents:
        if isinstance(e, tl_types.MessageEntityTextUrl):
            m = _USER_URL_RE.match(getattr(e, "url", "") or "")
            if m:
                return int(m.group(1)), _slice_utf16(text, e.offset, e.length)

    # 3) Обычный @username в тексте
    for e in ents:
        if isinstance(e, tl_types.MessageEntityMention):
            uname = _slice_utf16(text, e.offset, e.length)
            if not uname:
                continue
            try:
                u = await event.client.get_entity(uname)
                if isinstance(u, tl_types.User) and not u.bot:
                    return u.id, (u.first_name or uname)
            except Exception:
                pass
    return None


# ---------- Отправка приветствия ----------

async def _chat_title(event):
    try:
        chat = await event.get_chat()
        return getattr(chat, "title", "чат")
    except Exception:
        return "чат"


async def _greet(client, chat_id, pairs, title, reply_to=None):
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
        if not config.chat_allowed(event.chat_id):
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
        pairs = []
        for u in users:
            if not _was_greeted(event.chat_id, u.id):
                _mark_greeted(event.chat_id, u.id)
                pairs.append((u.id, clean_name(u.first_name)))
        if not pairs:
            return
        reply_to = event.action_message.id if event.action_message else None
        await _greet(client, event.chat_id, pairs, title, reply_to=reply_to)

    @client.on(events.NewMessage)
    async def on_trigger(event):
        # Приветствие по сообщению бота-охранника (напр. SecurityBermuda)
        cid = event.chat_id
        if not event.is_group or event.out:
            return
        if not config.chat_allowed(cid):
            return
        if not db.welcome_enabled(cid) or db.get_welcome_mode(cid) != 1:
            return

        sender = await event.get_sender()
        if sender is None:
            return

        trig = db.get_welcome_trigger(cid)
        text_low = (event.raw_text or "").lower()

        if trig.startswith("@"):
            # от конкретного бота + обязательно фраза «добро пожаловать»
            uname = (getattr(sender, "username", "") or "").lower()
            if uname != trig[1:].lower() or DEFAULT_TRIGGER not in text_low:
                return
        else:
            # по фразе — только от ботов, чтобы обычные люди не триггерили
            if not getattr(sender, "bot", False):
                return
            phrase = (trig or DEFAULT_TRIGGER).lower()
            if phrase not in text_low:
                return

        # 1) новичок прямо из сообщения охранника (надёжно)
        pair = await _extract_mentioned(event)
        via = "по упоминанию в сообщении охранника"
        # 2) запасной путь — очередь недавно вошедших
        if pair is None:
            pair = _pop_one(cid)
            via = "из очереди входов"
        if pair is None:
            log.info("триггер в %s: в сообщении нет упоминания и очередь входов пуста — пропускаю", cid)
            return

        uid, name = pair
        if uid in (config.SELF_ID, config.OWNER_ID):
            return
        # уточняем имя и отсекаем ботов
        try:
            u = await event.client.get_entity(uid)
            if getattr(u, "bot", False):
                return
            name = clean_name(getattr(u, "first_name", "") or name)
        except Exception:
            name = clean_name(name)

        if _was_greeted(cid, uid):
            return
        _mark_greeted(cid, uid)
        _remove_pending(cid, uid)

        title = await _chat_title(event)
        log.info("приветствую %s (id %s) в %s — %s", name, uid, cid, via)
        await _greet(client, cid, [(uid, name)], title, reply_to=event.id)

    @client.on(events.NewMessage(pattern=CFG_RE))
    async def welcome_cfg(event):
        if not (event.out or event.sender_id == config.OWNER_ID):
            return
        if not config.responds_here(event.chat_id, event.is_private, event.sender_id):
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
                "`.привет триггер` — по «{3}» от бота-охранника (новичка беру из его сообщения)\n"
                "`.привет триггер @SecurityBermuda` — только от конкретного бота".format(
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
                how = "жду «{0}» от {1}".format(DEFAULT_TRIGGER, rest)
            elif rest:
                how = "жду сообщение бота с фразой «{0}»".format(rest)
            else:
                how = "жду сообщение бота с фразой «{0}»".format(DEFAULT_TRIGGER)
            await _say(
                event,
                "✅ Режим «по триггеру» включён — {0}.\n"
                "Новичка возьму из упоминания в сообщении охранника "
                "(или из очереди входов, если упоминания нет) и отвечу на его сообщение.".format(how),
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
