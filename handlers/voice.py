"""Команда .гс — голосовое сообщение (озвучка текста женским голосом).

    .гс привет                — голосовое со словом «привет»
    .гс Добро пожаловать в Бермудский чат
    ответить .гс на сообщение — озвучит текст того сообщения

Сообщение-команда удаляется, вместо него отправляется голосовое.
"""
import logging
import re
import tempfile
from pathlib import Path

from telethon import events
from telethon.tl.types import DocumentAttributeAudio

import config
from tts import synth_voice, TTSError

log = logging.getLogger("polina.voice")

PATTERN = re.compile(r"^\.(?:гс|голос)(?:\s+([\s\S]+))?$", re.I)


def register(client):

    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN))
    async def voice_cmd(event):
        text = (event.pattern_match.group(1) or "").strip()
        reply = await event.get_reply_message()
        if not text and reply:
            text = (reply.raw_text or "").strip()
        if not text:
            await event.edit("🎙 Использование: `.гс привет` — или ответьте `.гс` на сообщение с текстом")
            return
        text = text[:config.TTS_MAX_LEN]

        try:
            await event.edit("🎙 Записываю голосовое…")
        except Exception:
            pass

        try:
            with tempfile.TemporaryDirectory() as tmp:
                mp3 = str(Path(tmp) / "tts.mp3")
                ogg = str(Path(tmp) / "voice.ogg")
                duration = await synth_voice(text, mp3, ogg, config.TTS_VOICE, config.TTS_RATE)
                await event.client.send_file(
                    event.chat_id,
                    ogg,
                    voice_note=True,
                    reply_to=reply.id if reply else None,
                    attributes=[DocumentAttributeAudio(duration=duration, voice=True)],
                )
            await event.delete()
        except TTSError as e:
            log.warning(".гс: %s", e)
            await _fail(event, str(e))
        except Exception as e:
            log.exception(".гс: неожиданная ошибка")
            await _fail(event, str(e)[:120])


async def _fail(event, why):
    try:
        await event.edit("⚠️ Голосовое не отправилось: {0}".format(why))
    except Exception:
        pass
