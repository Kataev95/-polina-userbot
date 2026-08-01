"""Команда .все — «тихий призыв» участников группы.

Запускается ВЛАДЕЛЬЦЕМ в личке с Полиной (со своего основного аккаунта):

    .все @bermuda_chat                 — тегнуть участников группы
    .все -1001234567890 Сбор на ферму! — с текстом в первой пачке
    .все                                — покажет подсказку

Как работает:
- Полина тегает участников пачками по 5 в ЦЕЛЕВОЙ группе;
- каждое сообщение с тегами через ~1 сек удаляется (в группе не остаётся следов,
  но люди успевают получить уведомление);
- прогресс Полина шлёт владельцу в ЛС (редактирует одно сообщение).

Требования:
- Полина должна состоять в целевой группе (для больших групп — лучше админом);
- команду принимает ТОЛЬКО от OWNER_ID (владельца) и только в личке.
"""
import asyncio
import logging
import re

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

import config
from timers_core import mention

log = logging.getLogger("polina.tagall")

# group(1) — цель (@username или id), group(2) — необязательный текст
PATTERN = re.compile(r"^\.(?:все|всех|all)(?:\s+(\S+))?(?:\s+([\s\S]+))?$", re.I)


def _help():
    return (
        "👥 **Тег участников (тихий призыв)**\n"
        "Формат: `.все <@username или ID группы> [текст]`\n\n"
        "Примеры:\n"
        "`.все @bermuda_chat`\n"
        "`.все -1001234567890 Сбор на ферму!`\n\n"
        "Полина тегает по {0} чел., каждое сообщение сразу удаляет, "
        "а прогресс шлёт сюда. Она должна состоять в группе "
        "(для больших групп — лучше администратором)."
    ).format(config.TAGALL_BATCH)


async def _resolve(client, target):
    """@username / id -> entity (или None)."""
    try:
        key = int(target) if target.lstrip("-").isdigit() else target
        return await client.get_entity(key)
    except Exception as e:
        log.warning(".все: не удалось найти чат %r: %s", target, e)
        return None


async def _collect(client, entity):
    """Список участников без ботов, удалённых, самой Полины и владельца."""
    users = []
    async for u in client.iter_participants(entity):
        if getattr(u, "bot", False) or getattr(u, "deleted", False):
            continue
        if u.id in (config.SELF_ID, config.OWNER_ID):
            continue
        users.append(u)
        if len(users) >= config.TAGALL_LIMIT:
            break
    return users


def register(client):

    @client.on(events.NewMessage(pattern=PATTERN))
    async def tagall_cmd(event):
        # Только личка и только владелец — иначе кто угодно запустит массовый тег
        if not event.is_private or event.sender_id != config.OWNER_ID:
            return

        target = event.pattern_match.group(1)
        extra = (event.pattern_match.group(2) or "").strip()
        if not target:
            await event.respond(_help())
            return

        entity = await _resolve(event.client, target)
        if entity is None:
            await event.respond(
                "🚫 Не нашла чат «{0}».\nУбедись, что Полина состоит в нём, "
                "и укажи @username или числовой ID.".format(target)
            )
            return
        if isinstance(entity, User):
            await event.respond("🚫 «{0}» — это пользователь, а не группа.".format(target))
            return

        title = getattr(entity, "title", str(target))

        try:
            users = await _collect(event.client, entity)
        except Exception as e:
            log.warning(".все: не смог получить участников: %s", e)
            await event.respond(
                "🚫 Не смогла получить участников «{0}». "
                "Возможно, Полине нужны права администратора.\n`{1}`".format(title, str(e)[:120])
            )
            return

        if not users:
            await event.respond("🤷‍♀️ В «{0}» некого тегать.".format(title))
            return

        total = len(users)
        progress = await event.respond("📢 Тегаю в «{0}»…\nПрогресс: 0/{1}".format(title, total))
        done = 0
        sent = 0

        for i in range(0, total, config.TAGALL_BATCH):
            batch = users[i:i + config.TAGALL_BATCH]
            mentions = " ".join(mention(u.id, u.first_name or "друг") for u in batch)
            body = "{0}\n{1}".format(extra, mentions) if (extra and i == 0) else mentions

            try:
                msg = await event.client.send_message(entity, body)
            except FloodWaitError as e:
                await progress.edit(
                    "⏳ Telegram просит паузу {0} c. Остановилась на {1}/{2}.".format(e.seconds, done, total)
                )
                return
            except Exception as e:
                log.warning(".все: ошибка отправки: %s", e)
                await progress.edit(
                    "🚫 Ошибка при отправке: `{0}`\nОстановилась на {1}/{2}.".format(str(e)[:120], done, total)
                )
                return

            sent += 1
            # пауза, чтобы уведомление успело дойти, затем удаляем сообщение
            await asyncio.sleep(config.TAGALL_DELETE_DELAY)
            try:
                await msg.delete()
            except Exception:
                pass

            done += len(batch)
            try:
                await progress.edit("📢 Тегаю в «{0}»…\nПрогресс: {1}/{2}".format(title, done, total))
            except Exception:
                pass

            if i + config.TAGALL_BATCH < total:
                await asyncio.sleep(config.TAGALL_BATCH_PAUSE)

        await progress.edit(
            "✅ Готово: «{0}»\nУпомянула {1} чел. за {2} сообщений — все удалены.".format(title, total, sent)
        )
