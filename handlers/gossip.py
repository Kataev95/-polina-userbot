"""«Вечерний вестник» — ИИ-сплетник: дайджест дня в стиле жёлтой прессы.

Полина тихо запоминает сообщения чата (текст, автор, время; хранится 3 суток),
а вечером отправляет переписку за день в AITunnel и публикует выпуск:
«СКАНДАЛ: Петя опять опоздал!» — с кликабельными тегами героев дня.

Команды (в самой группе, владелец — под Полиной или с основного аккаунта):

    .вестник               — статус: вкл/выкл, время, сколько сообщений накоплено
    .вестник вкл / выкл     — включить / выключить ежедневный выпуск
    .вестник время 21:00    — во сколько выходит выпуск
    .вестник сейчас         — выпустить немедленно (тест)

Переменные окружения (панель Bothost):
    AITUNNEL_API_KEY — ключ aitunnel.ru (обязательно для вестника)
    AI_MODEL         — модель, по умолчанию "auto" (AITunnel выберет сам)
    AI_URL           — эндпоинт, по умолчанию https://api.aitunnel.ru/v1/chat/completions
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import aiohttp
from telethon import events

import config
import db
from timers_core import clean_name

log = logging.getLogger("polina.gossip")

MIN_MESSAGES = 5        # меньше — выпуск не делаем (день был мёртвый)
CHUNK_LIMIT = 3900      # безопасный размер одного сообщения Telegram
CHECK_EVERY = 30        # период проверки времени выпуска, сек
# Лимиты подачи в ИИ берутся из config: DIGEST_MAX_MESSAGES / DIGEST_MAX_CHARS
# (по умолчанию 3000 сообщений / 300К символов — claude-sonnet-4.5 вмещает весь день)

PATTERN = re.compile(r"^\.вестник(?:\s+([\s\S]+))?$", re.I)
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")
MENTION_TOKEN_RE = re.compile(r"\{id:(\d+)\|([^}]{1,64})\}")

SYSTEM_PROMPT = (
    "Ты — редактор ежедневного юмористического вестника группового Telegram-чата. "
    "Твоя задача — превратить обычную переписку за день в яркий, живой дайджест в стиле "
    "жёлтой прессы, который все ждут по вечерам.\n\n"
    "Язык: только русский, разговорный, живой.\n"
    "Тон: умный юмор, ирония, сарказм, лёгкая дерзость. Подкалывай участников за то, что "
    "они сами писали, но без жестокости: не бей по здоровью, семье и деньгам, не оскорбляй "
    "всерьёз и не выдумывай фактов, которых не было в переписке.\n\n"
    "Структура выпуска:\n"
    "1. Громкое название выпуска с датой и эмодзи.\n"
    "2. 3–6 «новостей»: КРИЧАЩИЙ ЗАГОЛОВОК + 1–3 предложения сути с шуткой. Опирайся на "
    "реальные сообщения, цитируй самые смешные фразы дословно.\n"
    "3. В конце одна рубрика на выбор: «Цитата дня», «Герой дня» или «Драма дня».\n\n"
    "ОЧЕНЬ ВАЖНО — упоминание участников: каждый раз, когда называешь участника, пиши его "
    "строго в формате {id:ЧИСЛО|Имя} — ровно с тем id, что стоит у него в логе. "
    "Это превратится в кликабельный тег.\n\n"
    "Оформление: заголовки жирным (**текст**), эмодзи в меру. Весь выпуск — не длиннее "
    "2500 символов. Если день был тихим — обыграй это с иронией, короткий выпуск тоже норм."
)


class GossipError(Exception):
    pass


# ---------- Чистые функции ----------

def build_log_text(rows, tz, max_chars=None):
    """rows: [(user_id, user_name, text, ts)] -> (текст лога для ИИ, сколько вошло).

    Если день гигантский и не влезает в max_chars — старое отбрасывается,
    остаются самые свежие сообщения.
    """
    if max_chars is None:
        max_chars = config.DIGEST_MAX_CHARS
    lines = []
    for (uid, name, text, ts) in rows:
        hm = datetime.fromtimestamp(ts, tz).strftime("%H:%M")
        text = text.replace("\n", " ").strip()
        lines.append("[{0}] {{id:{1}|{2}}}: {3}".format(hm, uid, name or "Аноним", text))
    total = 0
    kept = []
    for line in reversed(lines):  # оставляем самые свежие
        total += len(line) + 1
        if total > max_chars:
            break
        kept.append(line)
    return "\n".join(reversed(kept)), len(kept)


def render_mentions(text):
    """{id:123|Петя} -> [Петя](tg://user?id=123)"""
    return MENTION_TOKEN_RE.sub(lambda m: "[{0}](tg://user?id={1})".format(
        clean_name(m.group(2)), m.group(1)), text)


def split_chunks(text, limit=CHUNK_LIMIT):
    """Режем длинный выпуск по абзацам под лимит Telegram."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for para in text.split("\n\n"):
        while len(para) > limit:  # совсем гигантский абзац
            chunks.append(para[:limit])
            para = para[limit:]
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) > limit:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def should_fire(now_hm, fire_time, last_date, today):
    return now_hm == fire_time and last_date != today


# ---------- ИИ ----------

async def _ask_ai(user_content):
    if not config.AITUNNEL_API_KEY:
        raise GossipError(
            "не задан AITUNNEL_API_KEY — добавь его в панели Bothost (Переменные окружения) "
            "и передеплой")
    payload = {
        "model": config.AI_MODEL,
        "max_tokens": 2000,
        "temperature": 0.9,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": "Bearer " + config.AITUNNEL_API_KEY}
    timeout = aiohttp.ClientTimeout(total=300)  # большой лог + Claude = может думать долго
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(config.AI_URL, json=payload, headers=headers) as r:
                data = await r.json(content_type=None)
                status = r.status
    except Exception as e:
        raise GossipError("сеть/AITunnel недоступен: {0}".format(str(e)[:150]))
    if status != 200:
        err = ""
        if isinstance(data, dict):
            err = str((data.get("error") or {}).get("message") or data)[:200]
        raise GossipError("AITunnel ответил {0}: {1}".format(status, err))
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise GossipError("неожиданный ответ AITunnel")
    if not content or not content.strip():
        raise GossipError("ИИ вернул пустой выпуск")
    return content.strip()


async def make_digest(client, chat_id, force=False):
    """Собрать и опубликовать выпуск. Возвращает текст статуса для владельца."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).timestamp()
    total_day = db.count_log(chat_id, since)
    rows = db.get_log(chat_id, since, config.DIGEST_MAX_MESSAGES)
    if len(rows) < MIN_MESSAGES and not force:
        return "за день мало сообщений ({0}) — выпуск пропущен".format(len(rows))
    if not rows:
        raise GossipError("в логе нет сообщений за сутки (лог копится с момента включения)")

    try:
        entity = await client.get_entity(chat_id)
        title = getattr(entity, "title", "чат")
    except Exception:
        entity = chat_id
        title = "чат"

    local_now = datetime.now(config.TIMEZONE)
    log_text, used = build_log_text(rows, config.TIMEZONE)
    if used < total_day:
        count_line = "всего за день {0} сообщений, ниже — последние {1}".format(total_day, used)
    else:
        count_line = "{0} сообщений".format(used)
    user_content = (
        "Сегодня {0}. Ниже — переписка чата «{1}» за день ({2}).\n"
        "Формат строк: [ЧЧ:ММ] {{id:ЧИСЛО|Имя}}: текст. Пометки вроде [фото], [стикер], "
        "[голосовое] означают отправленные медиа.\n\n{3}\n\n"
        "Составь вечерний выпуск вестника по правилам из системного промпта.".format(
            local_now.strftime("%d.%m.%Y"), title, count_line, log_text)
    )

    content = await _ask_ai(user_content)
    content = render_mentions(content)

    for chunk in split_chunks(content):
        await client.send_message(entity, chunk)
        await asyncio.sleep(0.7)

    db.digest_mark_fired(chat_id, local_now.strftime("%Y-%m-%d"))
    db.cleanup_log(now.timestamp() - 3 * 86400)
    return "выпуск опубликован ({0} сообщений в основе)".format(used)


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
            return  # служебные команды в вестник не попадают
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
        if event.is_private:
            await _say(event, "🗞 Команда `.вестник` работает в самой группе.")
            return

        arg = (event.pattern_match.group(1) or "").strip()
        cid = event.chat_id
        cfg = db.digest_get(cid)
        enabled = cfg[0] if cfg else False
        fire_time = cfg[1] if cfg else config.DIGEST_TIME_DEFAULT

        if not arg:
            since = datetime.now(timezone.utc).timestamp() - 24 * 3600
            n = db.count_log(cid, since)
            key = "задан ✅" if config.AITUNNEL_API_KEY else "НЕ задан ❌ (переменная AITUNNEL_API_KEY)"
            await _say(
                event,
                "🗞 **Вечерний вестник**: {0}\n"
                "Время выпуска: {1} ({2})\n"
                "Сообщений за 24 ч в архиве: {3} (в выпуск берётся до {4})\n"
                "Ключ AITunnel: {5} · модель: {6}\n\n"
                "`.вестник вкл/выкл` · `.вестник время 21:00` · `.вестник сейчас`".format(
                    "включён ✅" if enabled else "выключен 🚫",
                    fire_time, str(config.TIMEZONE), n, config.DIGEST_MAX_MESSAGES,
                    key, config.AI_MODEL),
            )
            return

        low = arg.lower()
        if low in ("вкл", "on"):
            db.digest_set(cid, enabled=True, fire_time=fire_time)
            note = "" if config.AITUNNEL_API_KEY else ("\n⚠️ Не задан AITUNNEL_API_KEY — добавь в панели "
                                                       "Bothost, иначе выпуск не выйдет.")
            await _say(event, "✅ Вестник включён — выпуск ежедневно в {0}.{1}".format(fire_time, note))
        elif low in ("выкл", "off"):
            db.digest_set(cid, enabled=False)
            await _say(event, "🚫 Вестник выключен.")
        elif low.startswith("время"):
            t = arg[len("время"):].strip()
            m = TIME_RE.match(t)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                await _say(event, "🕘 Формат: `.вестник время 21:00`")
                return
            ft = "{0:02d}:{1:02d}".format(int(m.group(1)), int(m.group(2)))
            db.digest_set(cid, enabled=True, fire_time=ft)
            await _say(event, "🕘 Выпуск теперь в {0} ({1}), вестник включён.".format(ft, config.TIMEZONE))
        elif low in ("сейчас", "now", "тест"):
            await _say(event, "🗞 Готовлю экстренный выпуск…")
            try:
                info = await make_digest(event.client, cid, force=True)
                log.info("вестник: ручной выпуск в %s: %s", cid, info)
            except GossipError as e:
                await event.respond("⚠️ Выпуск не вышел: {0}".format(e))
            except Exception as e:
                log.exception("вестник: ручной выпуск")
                await event.respond("⚠️ Выпуск не вышел: {0}".format(str(e)[:150]))
        else:
            await _say(event, "🤔 Не поняла. `.вестник вкл/выкл` · `.вестник время 21:00` · `.вестник сейчас`")

    # Фоновый цикл: следим за временем выпуска
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
                    info = await make_digest(client, chat_id)
                    log.info("вестник: %s — %s", chat_id, info)
                except Exception as e:
                    log.exception("вестник: выпуск в %s не вышел", chat_id)
                    if config.OWNER_ID and config.OWNER_ID != config.SELF_ID:
                        try:
                            await client.send_message(
                                config.OWNER_ID,
                                "⚠️ Вестник сегодня не вышел: {0}".format(str(e)[:200]))
                        except Exception:
                            pass
        except Exception:
            log.exception("вестник: сбой цикла")
        await asyncio.sleep(CHECK_EVERY)
