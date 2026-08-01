"""Приветствие новых участников группы.

Когда кто-то вступает/добавлен в группу, Полина пишет приветствие
(если оно включено для этого чата).

Настройка — командой `.привет` в самой группе. Работает и когда команду
пишет сам аккаунт Полины (исходящее), и когда её пишет владелец со своего
основного аккаунта:

    .привет                     — показать статус и текущий текст
    .привет вкл / .привет выкл   — включить / выключить в этом чате
    .привет текст <шаблон>       — задать текст (плейсхолдеры {name}, {chat})

Пример шаблона: «Добро пожаловать, {name}! Ты в чате «{chat}» 🌴»
"""
import logging
import re

from telethon import events

import config
import db
from timers_core import mention

log = logging.getLogger("polina.welcome")

DEFAULT = "👋 Добро пожаловать, {name}! Рады видеть тебя в «{chat}»."
CFG_RE = re.compile(r"^\.привет(?:\s+([\s\S]+))?$", re.I)


async def _say(event, text):
    """Ответить: редактированием (если команду писала Полина) или reply (если владелец)."""
    if event.out:
        await event.edit(text)
    else:
        await event.reply(text)


def register(client):

    @client.on(events.ChatAction)
    async def on_join(event):
        if not (event.user_joined or event.user_added):
            return
        if not db.welcome_enabled(event.chat_id):
            return

        users = list(event.users or [])
        if not users:
            try:
                u = await event.get_user()
                if u:
                    users = [u]
            except Exception:
                pass
        users = [u for u in users if not getattr(u, "bot", False)]
        # не приветствуем саму Полину, если её добавили
        users = [u for u in users if u.id != config.SELF_ID]
        if not users:
            return

        try:
            chat = await event.get_chat()
            title = getattr(chat, "title", "чат")
        except Exception:
            title = "чат"

        tmpl = db.get_welcome_text(event.chat_id) or DEFAULT
        names = ", ".join(mention(u.id, u.first_name or "друг") for u in users[:5])
        text = tmpl.replace("{name}", names).replace("{chat}", title)

        try:
            await event.reply(text)
        except Exception:
            try:
                await client.send_message(event.chat_id, text)
            except Exception as e:
                log.warning("приветствие: не смог отправить в %s: %s", event.chat_id, e)

    @client.on(events.NewMessage(pattern=CFG_RE))
    async def welcome_cfg(event):
        # настраивать может Полина (исходящее) или владелец
        if not (event.out or event.sender_id == config.OWNER_ID):
            return
        if event.is_private:
            await _say(event, "⚙️ Команду `.привет` используйте в самой группе, для которой настраиваете приветствие.")
            return

        arg = (event.pattern_match.group(1) or "").strip()
        cid = event.chat_id

        if not arg:
            on = db.welcome_enabled(cid)
            tmpl = db.get_welcome_text(cid) or DEFAULT
            await _say(
                event,
                "👋 **Приветствие новичков**: {0}\n"
                "Текст: {1}\n\n"
                "Управление:\n"
                "`.привет вкл` / `.привет выкл`\n"
                "`.привет текст <шаблон>`\n"
                "Плейсхолдеры: `{{name}}` — новичок, `{{chat}}` — название чата.".format(
                    "включено ✅" if on else "выключено 🚫", tmpl
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
        elif low.startswith("текст"):
            tmpl = arg[len("текст"):].strip()
            if not tmpl:
                await _say(event, "✍️ Укажите текст: `.привет текст Добро пожаловать, {name}! 🌴`")
                return
            db.set_welcome(cid, text=tmpl[:500], on=True)
            preview = tmpl.replace("{name}", "Имя").replace("{chat}", "Чат")
            await _say(event, "✅ Шаблон сохранён и приветствие включено.\nПример: {0}".format(preview[:400]))
        else:
            await _say(event, "🤔 Не поняла. `.привет вкл` / `.привет выкл` / `.привет текст <шаблон>`")
