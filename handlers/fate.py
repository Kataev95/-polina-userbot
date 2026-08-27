"""Команды .судьба и .судьба я.

.судьба   — выбирает случайного участника текущего чата и даёт ему задание.
.судьба я — даёт задание автору команды.

В пределах текущего запуска процесса задания не повторяются, пока не будут
использованы все 200. После исчерпания базы цикл начинается заново.
"""
import random
import re

from telethon import events
from telethon.tl.types import User

import config
from timers_core import mention
from data.fate_tasks import FATE_TASKS

COMMAND_RE = re.compile(r"^\.судьба(?:\s+(я))?\s*$", re.I)

_unused = list(range(len(FATE_TASKS)))


def _next_task():
    global _unused
    if not _unused:
        _unused = list(range(len(FATE_TASKS)))
    index = random.choice(_unused)
    _unused.remove(index)
    return FATE_TASKS[index]


async def _random_member(client, chat_id):
    """Берём случайного участника без загрузки всех участников в память."""
    try:
        participants = []
        async for user in client.iter_participants(chat_id):
            if getattr(user, "bot", False) or getattr(user, "deleted", False):
                continue
            if user.id in (config.SELF_ID, config.OWNER_ID):
                continue
            participants.append(user)
        if not participants:
            return None
        return random.choice(participants)
    except Exception:
        return None


def register(client):
    @client.on(events.NewMessage(pattern=COMMAND_RE))
    async def fate_cmd(event):
        if event.is_private:
            await event.respond("🔮 Команда `.судьба` работает в чате.\n`.судьба` — выбрать участника\n`.судьба я` — судьба для себя.")
            return

        if not config.chat_allowed(event.chat_id):
            return

        own = bool(event.pattern_match.group(1))
        target = None
        if not own:
            target = await _random_member(event.client, event.chat_id)
            if target is None:
                await event.respond("🔮 Судьба не смогла найти подходящего участника.")
                return

        task = _next_task()

        if own:
            target_id = event.sender_id
            name = "тебе"
            text = "🔮 **СУДЬБА РЕШИЛА…**\n\n{0}\n\n🍀 Удачи!".format(task)
        else:
            target_id = target.id
            name = mention(target.id, target.first_name or "участнику")
            text = "🔮 **СУДЬБА РЕШИЛА…**\n\n{0}\n\n🎯 Сегодня судьба выбрала {1}!".format(task, name)

        await event.respond(text)

        # Для режима «судьба я» имя участника можно дополнительно выделить тегом,
        # но не отправляем второе сообщение, чтобы команда оставалась компактной.
