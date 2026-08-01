"""Служебные команды владельца: .пинг, .погода, .ид, .полина вкл/выкл, .помощь."""
import asyncio
import logging
import re
import time

from telethon import events

import config
import db
import timers_core
from weather_api import get_weather_text

log = logging.getLogger("polina.misc")


def _human_uptime(secs):
    secs = int(secs)
    d, rem = divmod(secs, 86400)
    h, m = divmod(rem // 60, 60)
    if d:
        return "{0}д {1}ч".format(d, h)
    if h:
        return "{0}ч {1}м".format(h, m)
    return "{0}м".format(m)


def _owner_help():
    n = config.BOT_NAME
    ln = n.lower()
    return (
        "🤖 **{n} — команды**\n\n"
        "**Только для вас (с точкой):**\n"
        "`.гс текст` — голосовое женским голосом (или ответьте `.гс` на сообщение)\n"
        "`.все @группа [текст]` — тихий тег (пишите Полине В ЛС): по 5 и удаляет, прогресс в ЛС\n"
        "`.погода город` — погода\n"
        "`.пинг` — жив ли бот, аптайм\n"
        "`.ид` — ID чата (ответом на сообщение — и ID человека)\n"
        "`.{ln} вкл/выкл` — публичные команды в этом чате\n"
        "`.помощь` — это сообщение (удалится через минуту)\n\n"
        "**Для всех в чате (по имени):**\n"
        "«{n}, таймер через 4 часа ферма» — напомнит и тегнет автора\n"
        "«{n}, таймер в 18:30 созвон»\n"
        "«{n}, таймеры» — список · «{n}, отмена 5» — отмена\n"
        "«{n}, погода Москва» · «{n}, помощь»"
    ).format(n=n, ln=ln)


def register(client):

    @client.on(events.NewMessage(outgoing=True, pattern=re.compile(r"^\.(?:пинг|ping)$", re.I)))
    async def ping_cmd(event):
        t0 = time.perf_counter()
        msg = await event.edit("🏓 …")
        dt = (time.perf_counter() - t0) * 1000
        uptime = _human_uptime(time.time() - config.STARTED_AT)
        await msg.edit(
            "🏓 Понг! `{0:.0f} мс` · аптайм {1} · активных таймеров: {2}".format(
                dt, uptime, timers_core.active_count()
            )
        )

    @client.on(events.NewMessage(outgoing=True, pattern=re.compile(r"^\.погода(?:\s+(.+))?$", re.I)))
    async def weather_cmd(event):
        city = (event.pattern_match.group(1) or "").strip()
        if not city:
            await event.edit("🌤 Использование: `.погода Москва`")
            return
        await event.edit("🌤 Смотрю погоду…")
        try:
            await event.edit(await get_weather_text(city))
        except Exception as e:
            log.warning(".погода: %s", e)
            await event.edit("⚠️ Не получилось узнать погоду, попробуйте позже.")

    @client.on(events.NewMessage(outgoing=True, pattern=re.compile(r"^\.(?:ид|id)$", re.I)))
    async def id_cmd(event):
        reply = await event.get_reply_message()
        lines = ["ℹ️ Чат: `{0}`".format(event.chat_id)]
        if reply and reply.sender_id:
            lines.append("Пользователь: `{0}`".format(reply.sender_id))
        await event.edit("\n".join(lines))

    @client.on(events.NewMessage(outgoing=True, pattern=re.compile(
        r"^\.{0}\s+(вкл|выкл)\s*$".format(re.escape(config.BOT_NAME)), re.I)))
    async def toggle_cmd(event):
        on = event.pattern_match.group(1).lower() == "вкл"
        db.set_public_enabled(event.chat_id, on)
        state = "включены ✅" if on else "выключены 🚫"
        await event.edit(
            "⚙️ Публичные команды («{0}, таймер/погода…») в этом чате {1}".format(config.BOT_NAME, state)
        )

    @client.on(events.NewMessage(outgoing=True, pattern=re.compile(r"^\.(?:помощь|хелп|help)$", re.I)))
    async def help_cmd(event):
        await event.edit(_owner_help())
        await asyncio.sleep(60)
        try:
            await event.delete()
        except Exception:
            pass
