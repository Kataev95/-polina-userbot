"""Озвучка текста и конвертация в голосовое сообщение Telegram.

Синтез — edge-tts (нейроголоса Microsoft, бесплатно, без API-ключей).
Чтобы Telegram показал файл как «настоящее» голосовое (с волной),
аудио должно быть в контейнере OGG с кодеком Opus — конвертируем ffmpeg'ом.
"""
import asyncio

import edge_tts


class TTSError(Exception):
    pass


async def _run(cmd):
    """Запустить внешнюю команду, вернуть stdout. Ошибка -> TTSError."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise TTSError(
            "{0} не найден. На Bothost пересоберите бота (ffmpeg ставится автоматически, "
            "т.к. в requirements.txt есть ffmpeg-python); локально установите ffmpeg.".format(cmd[0])
        )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise TTSError("{0}: {1}".format(cmd[0], err.decode(errors="ignore")[:300]))
    return out


async def synth_voice(text, mp3_path, ogg_path, voice, rate="+0%"):
    """Синтезирует text в ogg_path (OGG/Opus). Возвращает длительность в секундах."""
    try:
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        await communicate.save(mp3_path)
    except Exception as e:
        raise TTSError("синтез речи не удался: {0}".format(str(e)[:200]))

    await _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", mp3_path,
        "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1",
        "-application", "voip",
        ogg_path,
    ])

    out = await _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        ogg_path,
    ])
    try:
        duration = int(float(out.decode().strip()))
    except ValueError:
        duration = 0
    return max(duration, 1)
