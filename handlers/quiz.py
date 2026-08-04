"""Подсчёт результатов викторин (Telegram quiz-опросов) — команда .квиз.

Владелец чата постит квизы, а Полина по команде сама считает, кто ответил
правильно больше всех, и выдаёт таблицу лидеров.

    .квиз                          — итоги за последние 24 часа
    .квиз 12                       — за последние 12 часов (1–72)
    .квиз https://t.me/c/…/123     — от указанного сообщения (первого квиза) и до конца.
                                     Самый надёжный способ: пришли ссылку на ПЕРВЫЙ квиз,
                                     остальные Полина найдёт сама.

Где запускать:
- в ГРУППЕ — результат публикуется в группе (все видят);
- в ЛС Полине — результат приходит тихо в ЛС (чат берётся из ALLOWED_CHATS).

Нюансы:
- правильный вариант виден, только если Полина сама проголосовала в квизе;
- голоса видны только в НЕанонимных опросах;
- голос самой Полины в зачёт не идёт.
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

# Реальную границу задаёт ссылка (min_id) или срез по времени — сканирование
# останавливается на них само. SCAN_LIMIT — только аварийный предохранитель,
# чтобы не листать историю за годы (50000 сообщений = ~500 запросов к API).
SCAN_LIMIT = 50000
VOTES_PAGE = 100     # голосов за один запрос GetPollVotes
BOARD_LIMIT = 20     # строк в таблице лидеров
PROGRESS_EVERY = 1000  # раз в сколько сообщений обновлять прогресс
MEDALS = ["🥇", "🥈", "🥉"]

PATTERN = re.compile(r"^\.(?:квиз|викторина)(?:\s+(\S+))?$", re.I)
LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/(\d+)|([A-Za-z0-9_]{4,}))/(\d+)", re.I)


# ---------- Чистая логика (тестируется отдельно) ----------

def parse_arg(arg):
    """Аргумент команды -> ('hours', N) | ('link', internal_id|username, msg_id) | None."""
    arg = (arg or "").strip()
    if not arg:
        return ("hours", 24)
    if arg.isdigit():
        return ("hours", max(1, min(int(arg), 72)))
    m = LINK_RE.search(arg)
    if m:
        internal, username, msg_id = m.group(1), m.group(2), int(m.group(3))
        if internal:
            return ("link", -(1000000000000 + int(internal)), msg_id)
        return ("link", username, msg_id)
    return None


def classify_vote(vote_options, correct_set):
    """Голос засчитывается как правильный, если выбран верный вариант."""
    return any(bytes(o) in correct_set for o in vote_options)


def format_board(correct, answered, names, counted, period_label,
                 skipped_anon=0, skipped_unknown=0, mention_fn=mention):
    """Собрать текст таблицы лидеров."""
    lines = ["🏆 **Итоги викторин {0}** — вопросов: {1}".format(period_label, counted), ""]
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
                opts = []
            votes.append((uid, opts))
        offset = getattr(res, "next_offset", None) or ""
        if not offset or not res.votes:
            break
        await asyncio.sleep(0.3)
    return votes, names


async def tally(client, chat_id, hours=None, from_msg_id=None, status=None):
    """Просканировать чат и посчитать итоги. Возвращает текст для публикации."""
    cutoff = None
    kwargs = {}
    if from_msg_id:
        # Граница — сама ссылка: Telethon сам остановится, дойдя до min_id.
        kwargs["min_id"] = from_msg_id - 1   # включая само сообщение-ссылку
        period_label = "с указанного сообщения"
    else:
        hours = hours or 24
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        period_label = "за {0} ч".format(hours)

    quizzes = []
    scanned = 0
    oldest = None
    async for msg in client.iter_messages(chat_id, limit=SCAN_LIMIT, **kwargs):
        scanned += 1
        oldest = msg.date
        if cutoff is not None and msg.date < cutoff:
            break
        media = getattr(msg, "media", None)
        if isinstance(media, types.MessageMediaPoll) and getattr(media.poll, "quiz", False):
            quizzes.append(msg)
        if status and scanned % PROGRESS_EVERY == 0:
            try:
                await status.edit("🧮 Листаю историю… {0} сообщений, найдено квизов: {1}".format(
                    scanned, len(quizzes)))
            except Exception:
                pass

    if not quizzes:
        diag = "просмотрела {0} сообщений".format(scanned)
        if oldest is not None:
            diag += " (до {0})".format(oldest.astimezone(config.TIMEZONE).strftime("%d.%m %H:%M"))
        return ("🤷‍♀️ Квизов не нашла — {0}.\n"
                "Если викторина была раньше, пришли ссылку на ПЕРВЫЙ квиз: "
                "`.квиз https://t.me/c/…/номер` — посчитаю от него.".format(diag))

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
        return "🤷‍♀️ Нашла {0} квизов, но посчитать нечего: {1}.".format(
            len(quizzes), "; ".join(why) or "нет данных")

    if not answered:
        return "🤷‍♀️ В {0} квизах никто, кроме Полины, не голосовал.".format(counted)

    return format_board(correct, answered, names, counted, period_label,
                        skipped_anon, skipped_unknown)


# ---------- Команда ----------

def _target_chat(event):
    """В группе — сама группа; в ЛС — единственный чат из ALLOWED_CHATS."""
    if event.is_group:
        return event.chat_id
    if event.is_private and len(config.ALLOWED_CHATS) == 1:
        return next(iter(config.ALLOWED_CHATS))
    return None


def register(client):

    @client.on(events.NewMessage(pattern=PATTERN))
    async def quiz_cmd(event):
        if not (event.out or event.sender_id == config.OWNER_ID):
            return
        if not config.responds_here(event.chat_id, event.is_private, event.sender_id):
            return

        parsed = parse_arg(event.pattern_match.group(1))
        if parsed is None:
            await event.respond("🧮 Не поняла. Примеры: `.квиз`, `.квиз 12`, "
                                "`.квиз https://t.me/c/…/номер` (ссылка на первый квиз).")
            return

        hours = None
        from_msg_id = None
        target = None
        if parsed[0] == "hours":
            hours = parsed[1]
            target = _target_chat(event)
        else:
            link_chat, from_msg_id = parsed[1], parsed[2]
            if isinstance(link_chat, str):
                # публичная ссылка t.me/username/123 — резолвим
                try:
                    from telethon import utils as tl_utils
                    entity = await event.client.get_entity(link_chat)
                    target = tl_utils.get_peer_id(entity)
                except Exception:
                    await event.respond("🚫 Не смогла открыть чат из ссылки.")
                    return
            else:
                target = link_chat

        if target is None:
            await event.respond("🧮 Запусти `.квиз` в группе с викторинами "
                                "(или задай ровно один чат в ALLOWED_CHATS).")
            return
        if not config.chat_allowed(target):
            await event.respond("🚫 Этот чат не в списке разрешённых (ALLOWED_CHATS).")
            return

        wait_text = "🧮 Считаю результаты викторин…"
        if event.out:
            status = await event.edit(wait_text)
        elif event.is_private:
            status = await event.respond(wait_text)   # результат придёт сюда же, в ЛС
        else:
            status = await event.reply(wait_text)

        try:
            text = await tally(event.client, target, hours=hours, from_msg_id=from_msg_id,
                               status=status)
        except Exception as e:
            log.exception("квиз: ошибка подсчёта")
            text = "⚠️ Не получилось посчитать: `{0}`".format(str(e)[:150])
        try:
            await status.edit(text)
        except Exception:
            await event.respond(text)
