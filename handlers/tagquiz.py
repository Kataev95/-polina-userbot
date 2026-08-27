"""Команда .тег — вопросы с тегом участников пачками по 5 каждые 30 минут.

Запуск только владельцем в ЛС:
    .тег <@username или ID чата>

После подсчёта участников Полина просит нужное число вопросов.
Каждый фрагмент текста, заканчивающийся на ?, считается отдельным вопросом.
Каждый вопрос отправляется вместе со следующими 5 кликабельными тегами.
Сообщения в целевом чате НЕ удаляются.
"""
import asyncio
import logging
import math
import re

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

import config
from timers_core import mention

log = logging.getLogger("polina.tagquiz")

START_RE = re.compile(r"^\.тег(?:\s+(\S+))?\s*$", re.I)
CANCEL_RE = re.compile(r"^\.(?:тегстоп|стоптег)$", re.I)

_active = None


def _help():
    return (
        "🏷 **Тег-вопросы**\n"
        "Запуск: `.тег <@username или ID чата>`\n\n"
        "Я посчитаю участников, разобью их по {0} человек и скажу, сколько вопросов прислать. "
        "Каждый новый вопрос должен заканчиваться знаком `?`.\n\n"
        "После получения всех вопросов отправлю первый сразу, затем — по одному каждые 30 минут. "
        "Сообщения в чате остаются.\n"
        "Отмена: `.тегстоп`"
    ).format(config.TAGALL_BATCH)


async def _resolve(client, target):
    try:
        key = int(target) if target.lstrip("-").isdigit() else target
        return await client.get_entity(key)
    except Exception as e:
        log.warning(".тег: не удалось найти чат %r: %s", target, e)
        return None


async def _collect(client, entity):
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


def _extract_questions(text):
    # Вопрос — любой фрагмент, завершённый ?. Поддерживаются несколько вопросов в одном сообщении.
    return [q.strip() for q in re.findall(r"[^?]+\?", text, flags=re.S) if q.strip()]


async def _run(client, state):
    state["running"] = True
    try:
        total_batches = state["needed"]
        for index in range(total_batches):
            if state["cancel"]:
                await client.send_message(config.OWNER_ID, "⛔️ Тег-вопросы остановлены.")
                return

            batch = state["users"][index * config.TAGALL_BATCH:(index + 1) * config.TAGALL_BATCH]
            tags = " ".join(mention(u.id, "🔥") for u in batch)
            question = state["questions"][index]
            body = "{0}\n\n{1}".format(question, tags)

            try:
                await client.send_message(state["entity"], body)
            except FloodWaitError as e:
                await client.send_message(config.OWNER_ID, "⏳ Telegram просит паузу {0} сек. Тег-вопросы остановлены.".format(e.seconds))
                return
            except Exception as e:
                log.warning(".тег: ошибка отправки: %s", e)
                await client.send_message(config.OWNER_ID, "🚫 Ошибка отправки: `{0}`".format(str(e)[:150]))
                return

            state["sent"] = index + 1
            await client.send_message(
                config.OWNER_ID,
                "🏷 Отправлено {0}/{1}. Следующее через 30 минут.".format(state["sent"], total_batches)
            )

            if index + 1 < total_batches:
                await asyncio.sleep(30 * 60)

        await client.send_message(
            config.OWNER_ID,
            "🏁 Тег-вопросы завершены. Отправлено {0} сообщений, охвачено {1} участников.".format(
                total_batches, len(state["users"])
            )
        )
    finally:
        global _active
        _active = None


def register(client):

    @client.on(events.NewMessage(pattern=CANCEL_RE))
    async def cancel_cmd(event):
        if not event.is_private or event.sender_id != config.OWNER_ID:
            return
        if not _active:
            await event.respond("ℹ️ Сейчас нет активного тег-вопроса.")
            return
        _active["cancel"] = True
        await event.respond("⛔️ Останавливаю тег-вопросы…")

    @client.on(events.NewMessage(pattern=START_RE))
    async def start_cmd(event):
        global _active
        if not event.is_private or event.sender_id != config.OWNER_ID:
            return

        target = event.pattern_match.group(1)
        if not target:
            await event.respond(_help())
            return
        if _active:
            await event.respond("⏳ Уже есть активный тег-вопрос. Отмена — `.тегстоп`.")
            return

        entity = await _resolve(event.client, target)
        if entity is None:
            await event.respond("🚫 Не нашла этот чат. Укажи @username или числовой ID и убедись, что Полина состоит в группе.")
            return
        if isinstance(entity, User):
            await event.respond("🚫 Указан пользователь, а нужен чат или группа.")
            return

        try:
            users = await _collect(event.client, entity)
        except Exception as e:
            log.warning(".тег: не смог получить участников: %s", e)
            await event.respond("🚫 Не смогла получить участников. Возможно, Полине нужны права администратора. `{0}`".format(str(e)[:120]))
            return

        if not users:
            await event.respond("🤷‍♀️ В этом чате некого тегать.")
            return

        needed = math.ceil(len(users) / config.TAGALL_BATCH)
        title = getattr(entity, "title", str(target))
        _active = {
            "entity": entity,
            "title": title,
            "users": users,
            "needed": needed,
            "questions": [],
            "cancel": False,
            "running": False,
            "sent": 0,
        }
        await event.respond(
            "📊 Чат «{0}»: {1} участников.\n"
            "🏷 По {2} человек = **{3} сообщений**.\n\n"
            "Пришли мне **{3} вопросов**. Каждый вопрос обязательно заканчивай `?`. "
            "Можно отправлять несколькими сообщениями.\n"
            "Получено: 0/{3}.\n"
            "Отмена: `.тегстоп`".format(title, len(users), config.TAGALL_BATCH, needed)
        )

    @client.on(events.NewMessage)
    async def receive_questions(event):
        global _active
        if not _active or _active.get("running"):
            return
        if not event.is_private or event.sender_id != config.OWNER_ID:
            return

        text = (event.raw_text or "").strip()
        if not text or text.startswith(".тег") or text.startswith(".тегстоп"):
            return

        questions = _extract_questions(text)
        if not questions:
            await event.respond("❗️Не нашла вопрос со знаком `?`. Каждый вопрос должен заканчиваться вопросительным знаком.")
            return

        remaining = _active["needed"] - len(_active["questions"])
        _active["questions"].extend(questions[:remaining])
        got = len(_active["questions"])
        needed = _active["needed"]

        if got < needed:
            await event.respond("📝 Получено вопросов: {0}/{1}. Осталось: {2}.".format(got, needed, needed - got))
            return

        await event.respond("✅ Получено {0}/{0} вопросов. Запускаю: первый вопрос сейчас, затем каждые 30 минут.".format(needed))
        asyncio.create_task(_run(event.client, _active))
