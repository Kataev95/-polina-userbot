"""Команда .все — упомянуть участников группы.

    .все               — просто тегнуть всех
    .все Собрание в 8!  — с текстом в первом сообщении

Упоминания идут пачками по 5 с паузой, чтобы не ловить флуд-лимиты.
⚠️ Используйте умеренно: массовые теги раздражают людей и привлекают
внимание антиспама Telegram.
"""
import asyncio
import logging
import re

from telethon import events
from telethon.errors import FloodWaitError

import config
from timers_core import mention

log = logging.getLogger("polina.tagall")

PATTERN = re.compile(r"^\.(?:все|всех|all)(?:\s+([\s\S]+))?$", re.I)


def register(client):

    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN))
    async def tagall_cmd(event):
        if not event.is_group:
            await event.edit("👥 Команда `.все` работает только в группах.")
            return
        text = (event.pattern_match.group(1) or "").strip()

        try:
            await event.delete()
        except Exception:
            pass

        users = []
        try:
            async for u in event.client.iter_participants(event.chat_id):
                if getattr(u, "bot", False) or getattr(u, "deleted", False):
                    continue
                if getattr(u, "is_self", False) or u.id == config.OWNER_ID:
                    continue
                users.append(u)
                if len(users) >= config.TAGALL_LIMIT:
                    break
        except Exception as e:
            log.warning(".все: не смог получить участников: %s", e)
            return
        if not users:
            return

        header_sent = False
        for i in range(0, len(users), config.TAGALL_BATCH):
            batch = users[i:i + config.TAGALL_BATCH]
            mentions = " ".join(mention(u.id, u.first_name or "user") for u in batch)
            if text and not header_sent:
                msg = "📢 {0}\n{1}".format(text, mentions)
            else:
                msg = mentions
            header_sent = True
            try:
                await event.client.send_message(event.chat_id, msg)
            except FloodWaitError as e:
                log.warning(".все: FloodWait %s сек — останавливаюсь", e.seconds)
                break
            except Exception as e:
                log.warning(".все: %s", e)
                break
            if i + config.TAGALL_BATCH < len(users):
                await asyncio.sleep(config.TAGALL_DELAY)
