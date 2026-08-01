"""Развлекательные команды — вызываются из роутера handlers/public.py.

    «Полина, кто самый умный?»       — выбирает случайного участника чата и тегает
    «Полина, выбери: пицца или суши» — случайный выбор из вариантов
    «Полина, кубик»                  — бросает настоящий анимированный кубик Telegram 🎲
    «Полина, шар: идти на ферму?»    — магический шар (да / нет / спроси позже)

Все команды с лёгким кулдауном (3 сек на человека), чтобы чат не заспамили.
"""
import logging
import random
import re
import time

from telethon.tl.types import InputMediaDice

import config
from timers_core import mention, clean_name

log = logging.getLogger("polina.fun")

FUN_COOLDOWN = 3      # сек между развлекательными командами на человека
MEMBERS_TTL = 600     # кэш участников чата, сек
MEMBERS_MAX = 400     # сколько участников максимум берём в розыгрыш

_last_at = {}         # user_id -> ts
_members_cache = {}   # chat_id -> (ts, [(user_id, name), ...])


def on_cooldown(user_id):
    """True = слишком часто, молчим. Иначе фиксирует время вызова."""
    now = time.time()
    if now - _last_at.get(user_id, 0) < FUN_COOLDOWN:
        return True
    _last_at[user_id] = now
    return False


async def _members(event):
    """Участники чата (без ботов, удалённых и самой Полины), с кэшем на 10 минут."""
    cid = event.chat_id
    now = time.time()
    cached = _members_cache.get(cid)
    if cached and now - cached[0] < MEMBERS_TTL:
        return cached[1]
    users = []
    async for u in event.client.iter_participants(cid):
        if getattr(u, "bot", False) or getattr(u, "deleted", False):
            continue
        if u.id == config.SELF_ID:
            continue
        users.append((u.id, clean_name(u.first_name)))
        if len(users) >= MEMBERS_MAX:
            break
    _members_cache[cid] = (now, users)
    return users


# ---------- «Полина, кто самый умный?» ----------

_WHO_PHRASES = [
    "🎯 Думаю, {tail} — {m}",
    "{m} — вот кто {tail} 😎",
    "Все знают: {tail} — это {m} 💅",
    "Мой вердикт: {tail} — {m} 🔮",
    "Даже гадать не надо: {tail} — {m} ✨",
]


async def who(event, tail):
    if not event.is_group:
        await event.reply("🎯 Эта команда работает в группе.")
        return
    tail = (tail or "").strip().strip("?!.,").strip()
    try:
        users = await _members(event)
    except Exception as e:
        log.warning("кто: не смог получить участников: %s", e)
        await event.reply("🤷‍♀️ Не смогла получить список участников.")
        return
    if not users:
        await event.reply("🤷‍♀️ Здесь некого выбирать.")
        return
    uid, name = random.choice(users)
    m = mention(uid, name)
    if tail:
        await event.reply(random.choice(_WHO_PHRASES).format(tail=tail, m=m))
    else:
        await event.reply("🎯 Мой выбор — {0}!".format(m))


# ---------- «Полина, выбери: пицца или суши» ----------

_CHOOSE_PHRASES = [
    "🤔 Однозначно **{c}**!",
    "💅 Конечно **{c}**, даже не сомневайся.",
    "🎲 Пусть будет **{c}**.",
    "✨ Звёзды подсказывают: **{c}**.",
    "😌 Я бы взяла **{c}**.",
]


def split_options(args):
    """«пицца или суши» / «а, б, в» -> список вариантов."""
    args = (args or "").strip()
    parts = re.split(r"\s+или\s+", args, flags=re.I)
    if len(parts) < 2:
        parts = args.split(",")
    parts = [p.strip(" ?!.,") for p in parts]
    return [p for p in parts if p]


async def choose(event, args):
    parts = split_options(args)
    if len(parts) < 2:
        await event.reply("🤔 Дайте варианты: «{0}, выбери: пицца или суши»".format(config.BOT_NAME))
        return
    await event.reply(random.choice(_CHOOSE_PHRASES).format(c=random.choice(parts)))


# ---------- «Полина, кубик» ----------

async def dice(event):
    try:
        await event.client.send_file(event.chat_id, InputMediaDice("🎲"), reply_to=event.id)
    except Exception as e:
        log.warning("кубик: не смог отправить анимацию: %s", e)
        await event.reply("🎲 Выпало: **{0}**".format(random.randint(1, 6)))


# ---------- «Полина, шар: вопрос?» ----------

_BALL = [
    # да
    "Бесспорно 💯", "Определённо да ✅", "Никаких сомнений 👍", "Да!",
    "Звёзды говорят — да ✨", "Мне кажется — да 🙂", "Скорее всего 👌",
    "Знаки говорят — да 🔮",
    # туманно
    "Пока неясно, спроси позже 🌫", "Сконцентрируйся и спроси снова 🧘‍♀️",
    "Лучше тебе не знать 🤫", "Предсказать сложно… 🌀", "Спроси завтра 😴",
    # нет
    "Не рассчитывай на это ❌", "Мой ответ — нет 🙅‍♀️", "По моим данным — нет 📉",
    "Перспективы не очень 😬", "Очень сомнительно 🤨",
]


async def ball(event, question):
    if not (question or "").strip():
        await event.reply("🔮 Задайте вопрос: «{0}, шар: идти сегодня на ферму?»".format(config.BOT_NAME))
        return
    await event.reply("🔮 " + random.choice(_BALL))
