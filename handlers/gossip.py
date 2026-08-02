"""«Вечерний вестник» — экспорт переписки чата за день файлом .md владельцу в ЛС.

Полина тихо запоминает сообщения чата (текст + медиа-пометки, хранится 3 суток),
а вечером (или по команде) присылает владельцу в личку Markdown-файл со всей
перепиской за 24 часа: шапка со статистикой, топ активности и все сообщения
с id участников — удобно скормить любому внешнему сервису для дайджеста.

Команды (в группе или в ЛС Полине; владелец — под Полиной или с основного аккаунта):

    .вестник               — статус: вкл/выкл, время, сколько сообщений накоплено
    .вестник вкл / выкл     — ежедневная отправка файла в ЛС
    .вестник время 21:00    — во сколько присылать
    .вестник сейчас         — прислать файл прямо сейчас

В ЛС команды применяются к рабочему чату из ALLOWED_CHATS.
"""
import asyncio
import logging
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone

from telethon import events

import config
import db
from timers_core import clean_name

log = logging.getLogger("polina.gossip")

CHECK_EVERY = 30          # период проверки времени отправки, сек
FETCH_LIMIT = 100000      # берём ВСЁ за сутки (лимит только защитный)
TOP_N = 10                # размер топа активности в шапке файла

PATTERN = re.compile(r"^\.вестник(?:\s+([\s\S]+))?$", re.I)
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")


# ---------- Чистые функции ----------

def should_fire(now_hm, fire_time, last_date, today):
    return now_hm == fire_time and last_date != today


def build_markdown(title, rows, tz, now_local):
    """rows: [(user_id, user_name, text, ts)] -> содержимое .md файла."""
    counts = Counter()
    names = {}
    for (uid, name, _text, _ts) in rows:
        counts[uid] += 1
        names[uid] = name or "Аноним"

    lines = []
    lines.append("# {0} — переписка за {1}".format(title, now_local.strftime("%d.%m.%Y")))
    lines.append("")
    lines.append("Сообщений: {0} · Участников: {1} · Выгрузка: {2} ({3})".format(
        len(rows), len(counts), now_local.strftime("%H:%M"), str(tz)))
    lines.append("")
    lines.append("Формат: `[ЧЧ:ММ] Имя (id:ЧИСЛО): текст`. Пометки [фото], [стикер], "
                 "[голосовое] и т.п. — отправленные медиа. id пригодятся для кликабельных "
                 "тегов: `[Имя](tg://user?id=ЧИСЛО)`.")
    lines.append("")
    lines.append("## Топ активности")
    lines.append("")
    for i, (uid, n) in enumerate(counts.most_common(TOP_N), 1):
        lines.append("{0}. {1} (id:{2}) — {3}".format(i, names[uid], uid, n))
    lines.append("")
    lines.append("## Сообщения")
    lines.append("")
    for (uid, name, text, ts) in rows:
        hm = datetime.fromtimestamp(ts, tz).strftime("%H:%M")
        text = text.replace("\n", " ").strip()
        lines.append("[{0}] {1} (id:{2}): {3}".format(hm, name or "Аноним", uid, text))
    lines.append("")
    return "\n".join(lines)


# ---------- Экспорт ----------

async def export_log(client, chat_id, note_when_empty=False):
    """Собрать лог за 24 ч и отправить .md владельцу в ЛС. Возвращает статус-строку."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).timestamp()
    rows = db.get_log(chat_id, since, FETCH_LIMIT)
    local_now = datetime.now(config.TIMEZONE)

    if not rows:
        if note_when_empty:
            try:
                await client.send_message(
                    config.OWNER_ID, "🗞 За сутки в чате нет сообщений — файла не будет. "
                    "(Лог копится с момента включения вестника.)")
            except Exception:
                pass
        return "лог пуст — файл не отправлен"

    try:
        entity = await client.get_entity(chat_id)
        title = getattr(entity, "title", "Чат")
    except Exception:
        title = "Чат"

    md = build_markdown(title, rows, config.TIMEZONE, local_now)
    filename = "log-{0}.md".format(local_now.strftime("%Y-%m-%d"))
    caption = "🗞 Лог «{0}» за {1}: {2} сообщений. Готов для твоего дайджеста!".format(
        title, local_now.strftime("%d.%m.%Y"), len(rows))

    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        await client.send_file(config.OWNER_ID, path, caption=caption, force_document=True)
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except Exception:
            pass

    db.digest_mark_fired(chat_id, local_now.strftime("%Y-%m-%d"))
    db.cleanup_log(now.timestamp() - 3 * 86400)
    return "файл отправлен в ЛС ({0} сообщений)".format(len(rows))


# ---------- Обработчики ----------

def _media_marker(event):
    """Пометка типа медиа для лога — стикеры и гс тоже материал для сплетен."""
    try:
        if getattr(event, "sticker", None) is not None:
            return "[стикер]"
        if getattr(event, "gif", None) is not None:
            return "[гифка]"
        if getattr(event, "photo", None) is not None:
            return "[фото]"
        if getattr(event, "voice", None) is not None:
            return "[голосовое]"
        if getattr(event, "video_note", None) is not None:
            return "[кружок]"
        if getattr(event, "video", None) is not None:
            return "[видео]"
        if getattr(event, "audio", None) is not None:
            return "[аудио]"
        if getattr(event, "document", None) is not None:
            return "[файл]"
    except Exception:
        pass
    return ""


def _default_chat(event):
    """В группе — сама группа; в ЛС — единственный чат из ALLOWED_CHATS."""
    if event.is_group:
        return event.chat_id
    if event.is_private and len(config.ALLOWED_CHATS) == 1:
        return next(iter(config.ALLOWED_CHATS))
    return None


async def _say(event, text):
    if event.out:
        await event.edit(text)
    else:
        await event.reply(text)


def register(client):

    @client.on(events.NewMessage())
    async def chat_logger(event):
        # Тихо копим лог: только группы из белого списка, только сообщения людей
        if not event.is_group or event.out:
            return
        if not config.chat_allowed(event.chat_id):
            return
        text = (event.raw_text or "").strip()
        if text.startswith("."):
            return  # служебные команды в лог не попадают
        marker = _media_marker(event)
        if marker:
            text = (text + " " + marker).strip() if text else marker
        if not text:
            return
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False):
            return
        db.log_message(event.chat_id, event.sender_id,
                       clean_name(getattr(sender, "first_name", "")), text)

    @client.on(events.NewMessage(pattern=PATTERN))
    async def digest_cmd(event):
        if not (event.out or event.sender_id == config.OWNER_ID):
            return
        if not config.responds_here(event.chat_id, event.is_private, event.sender_id):
            return
        cid = _default_chat(event)
        if cid is None:
            await _say(event, "🗞 Используй команду в самой группе (или задай ровно один чат в ALLOWED_CHATS).")
            return

        arg = (event.pattern_match.group(1) or "").strip()
        cfg = db.digest_get(cid)
        enabled = cfg[0] if cfg else False
        fire_time = cfg[1] if cfg else config.DIGEST_TIME_DEFAULT

        if not arg:
            since = datetime.now(timezone.utc).timestamp() - 24 * 3600
            n = db.count_log(cid, since)
            await _say(
                event,
                "🗞 **Вечерний вестник (файл в ЛС)**: {0}\n"
                "Время отправки: {1} ({2})\n"
                "Сообщений за 24 ч в архиве: {3}\n\n"
                "`.вестник вкл/выкл` · `.вестник время 21:00` · `.вестник сейчас`".format(
                    "включён ✅" if enabled else "выключен 🚫",
                    fire_time, str(config.TIMEZONE), n),
            )
            return

        low = arg.lower()
        if low in ("вкл", "on"):
            db.digest_set(cid, enabled=True, fire_time=fire_time)
            await _say(event, "✅ Вестник включён — каждый день в {0} пришлю лог файлом в ЛС.".format(fire_time))
        elif low in ("выкл", "off"):
            db.digest_set(cid, enabled=False)
            await _say(event, "🚫 Вестник выключен (лог продолжает копиться).")
        elif low.startswith("время"):
            t = arg[len("время"):].strip()
            m = TIME_RE.match(t)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                await _say(event, "🕘 Формат: `.вестник время 21:00`")
                return
            ft = "{0:02d}:{1:02d}".format(int(m.group(1)), int(m.group(2)))
            db.digest_set(cid, enabled=True, fire_time=ft)
            await _say(event, "🕘 Лог будет приходить в {0} ({1}), вестник включён.".format(ft, config.TIMEZONE))
        elif low in ("сейчас", "now", "тест"):
            await _say(event, "📤 Собираю лог…")
            try:
                info = await export_log(event.client, cid, note_when_empty=True)
                log.info("вестник: ручной экспорт %s: %s", cid, info)
                if not event.is_private:
                    await event.respond("📬 {0}".format(info))
            except Exception as e:
                log.exception("вестник: ручной экспорт")
                await event.respond("⚠️ Не получилось: {0}".format(str(e)[:150]))
        else:
            await _say(event, "🤔 Не поняла. `.вестник вкл/выкл` · `.вестник время 21:00` · `.вестник сейчас`")

    # Фоновый цикл: следим за временем отправки
    asyncio.get_event_loop().create_task(_digest_loop(client))


async def _digest_loop(client):
    await asyncio.sleep(90)  # даём боту спокойно стартовать
    while True:
        try:
            now_local = datetime.now(config.TIMEZONE)
            now_hm = now_local.strftime("%H:%M")
            today = now_local.strftime("%Y-%m-%d")
            for (chat_id, fire_time, last_date) in db.digest_enabled():
                if not config.chat_allowed(chat_id):
                    continue
                if not should_fire(now_hm, fire_time, last_date, today):
                    continue
                db.digest_mark_fired(chat_id, today)  # сразу, чтобы не задвоить
                try:
                    info = await export_log(client, chat_id, note_when_empty=True)
                    log.info("вестник: %s — %s", chat_id, info)
                except Exception as e:
                    log.exception("вестник: экспорт %s не удался", chat_id)
                    if config.OWNER_ID and config.OWNER_ID != config.SELF_ID:
                        try:
                            await client.send_message(
                                config.OWNER_ID,
                                "⚠️ Вестник: не смогла отправить лог: {0}".format(str(e)[:200]))
                        except Exception:
                            pass
        except Exception:
            log.exception("вестник: сбой цикла")
        await asyncio.sleep(CHECK_EVERY)
