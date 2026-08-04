"""Подсчёт результатов викторин (Telegram quiz-опросов) — команда .квиз.

Владелец чата постит квизы, а Полина по команде сама считает, кто ответил
правильно больше всех, и публикует таблицу лидеров.

    .квиз          — итоги викторин за последние 24 часа
    .квиз 12       — за последние 12 часов (1–72)

Как это работает:
- Полина сканирует историю чата и находит quiz-опросы;
- правильный вариант виден, только если Полина сама проголосовала в квизе
  (поэтому владелец отвечает на все вопросы с аккаунта Полины);
- списки голосовавших доступны только в НЕанонимных опросах;
- голос самой Полины в зачёт не идёт.

Команда — владельца, в самой группе (под Полиной или с основного аккаунта).
"""
import asyncio
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl import functions, types

import config
from timers_core import mention, clean_name

log = logging.getLogger("polina.quiz")

SCAN_LIMIT = 800     # максимум сообщений истории за один подсчёт
VOTES_PAGE = 100     # голосов за один запрос GetPollVotes
BOARD_LIMIT = 20     # строк в таблице лидеров
MEDALS = ["🥇", "🥈", "🥉"]

PATTERN = re.compile(r"^\.(?:квиз|викторина)(?:\s+(\d{1,3}))?$", re.I)


# ---------- Чистая логика (тестируется отдельно) ----------

def classify_vote(vote_options, correct_set):
    """Голос засчитывается как правильный, если выбран верный вариант."""
    return any(bytes(o) in correct_set for o in vote_options)


def format_board(correct, answered, names, counted, hours,
                 skipped_anon=0, skipped_unknown=0, mention_fn=mention):
    """Собрать текст таблицы лидеров."""
    lines = ["🏆 **Итоги викторин за {0} ч** — вопросов: {1}".format(hours, counted), ""]
    board = sorted(answered.keys(), key=lambda u: (-correct.get(u, 0), -answered[u], u))
    for idx, uid in enumerate(board[:BOARD_LIMIT]):
        score = correct.get(uid, 0)
        name = names.get(uid, "игрок")
        # топ-3 тегаем (пусть получат свои медали), остальных — текстом
        who = mention_fn(uid, name) if idx < 3 else name
        prefix = MEDALS[idx] if idx < 3 else "{0}.".format(idx + 1)
        lines.append("{0} {1} — {2}/{3}".format(prefix, who, score, counted))
    if len(board) > BOARD_LIMIT:
        lines.append("… и ещё {0} участников".format(len(board) - BOARD_LIMIT))
    notes = []
    if skipped_unknown:
        notes.append("{0} квиз(а) пропущено — Полина в них не голосовала, "
                     "правильный ответ не виден".format(skipped_unknown))
    if skipped_anon:
        notes.append("{0} анонимных — голоса не видны".format(skipped_anon))
    if notes:
        lines.append("")
        lines.append("_({0})_".format("; ".join(notes)))
    return "\n".join(lines)


# ---------- Работа с Telegram ----------

async def _fetch_votes(client, chat_id, msg_id):
    """Все голоса по опросу: [(user_id, [option_bytes, ...])], имена из ответа."""
    votes = []
    names = {}
    offset = ""
    while True:
        try:
            res = await client(functions.messages.GetPollVotesRequest(
                peer=chat_id, id=msg_id, limit=VOTES_PAGE,
                offset=offset or None))
        except FloodWaitError as e:
            if e.seconds > 60:
                raise
            await asyncio.sleep(e.seconds + 1)
            continue
        for u in (res.users or []):
            names[u.id] = clean_name(getattr(u, "first_name", ""))
        for v in (res.votes or []):
            peer = getattr(v, "peer", None)
            uid = getattr(peer, "user_id", None)
            if uid is None:
                continue
            if isinstance(v, types.MessagePeerVoteMultiple):
                opts = list(v.options or [])
            elif isinstance(v, types.MessagePeerVote):
                opts = [v.option]
            else:
                # MessagePeerVoteInputOption — без фильтра по опции не приходит
                opts = []
            votes.append((uid, opts))
        offset = getattr(res, "next_offset", None) or ""
        if not offset or not res.votes:
            break
        await asyncio.sleep(0.3)
    return votes, names


async def tally(client, chat_id, hours):
    """Просканировать чат и посчитать итоги. Возвращает текст для публикации."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    quizzes = []
    async for msg in client.iter_messages(chat_id, limit=SCAN_LIMIT):
        if msg.date < cutoff:
            break
        media = getattr(msg, "media", None)
        if isinstance(media, types.MessageMediaPoll) and getattr(media.poll, "quiz", False):
            quizzes.append(msg)

    if not quizzes:
        return ("🤷‍♀️ За последние {0} ч не нашла ни одного квиза "
                "(смотрю последние {1} сообщений).".format(hours, SCAN_LIMIT))

    correct = Counter()
    answered = Counter()
    names = {}
    counted = 0
    skipped_anon = 0
    skipped_unknown = 0

    for msg in reversed(quizzes):  # от старых к новым
        poll = msg.media.poll
        results = getattr(msg.media.results, "results", None) or []
        correct_set = set(bytes(r.option) for r in results if getattr(r, "correct", False))
        if not correct_set:
            skipped_unknown += 1
            continue
        if not getattr(poll, "public_voters", False):
            skipped_anon += 1
            continue
        try:
            votes, vote_names = await _fetch_votes(client, chat_id, msg.id)
        except Exception as e:
            log.warning("квиз: не смогла получить голоса msg %s: %s", msg.id, e)
            continue
        names.update(vote_names)
        for (uid, opts) in votes:
            if uid == config.SELF_ID:
                continue  # голос Полины — только чтобы раскрыть ответ
            answered[uid] += 1
            if classify_vote(opts, correct_set):
                correct[uid] += 1
        counted += 1
        await asyncio.sleep(0.4)

    if counted == 0:
        why = []
        if skipped_unknown:
            why.append("в {0} квизах Полина не голосовала (правильный ответ не виден)".format(skipped_unknown))
        if skipped_anon:
            why.append("{0} анонимных".format(skipped_anon))
        return "🤷‍♀️ Квизы нашла, но посчитать нечего: {0}.".format("; ".join(why) or "нет данных")

    if not answered:
        return "🤷‍♀️ В {0} квизах никто, кроме Полины, не голосовал.".format(counted)

    return format_board(correct, answered, names, counted, hours,
                        skipped_anon, skipped_unknown)


# ---------- Команда ----------

def register(client):

    @client.on(events.NewMessage(pattern=PATTERN))
    async def quiz_cmd(event):
        if not (event.out or event.sender_id == config.OWNER_ID):
            return
        if not config.responds_here(event.chat_id, event.is_private, event.sender_id):
            return
        if not event.is_group:
            if event.is_private and event.sender_id == config.OWNER_ID:
                await event.respond("🧮 Команду `.квиз` запускай в самой группе с викторинами.")
            return

        hours = int(event.pattern_match.group(1) or 24)
        hours = max(1, min(hours, 72))

        if event.out:
            status = await event.edit("🧮 Считаю результаты викторин за {0} ч…".format(hours))
        else:
            status = await event.reply("🧮 Считаю результаты викторин за {0} ч…".format(hours))

        try:
            text = await tally(event.client, event.chat_id, hours)
        except Exception as e:
            log.exception("квиз: ошибка подсчёта")
            text = "⚠️ Не получилось посчитать: `{0}`".format(str(e)[:150])
        try:
            await status.edit(text)
        except Exception:
            await event.respond(text)
