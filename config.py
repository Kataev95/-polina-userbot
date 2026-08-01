"""Конфигурация юзербота: всё берётся из переменных окружения.

Обязательные переменные (задаются в панели Bothost):
    TELEGRAM_API_ID   — числовой API ID с my.telegram.org
    TELEGRAM_API_HASH — API Hash с my.telegram.org
    SESSION_STRING    — строка сессии Telethon (см. README.md)

Необязательные:
    BOT_NAME     — имя, на которое бот откликается в чатах (по умолчанию «Полина»)
    TTS_VOICE    — голос озвучки (по умолчанию ru-RU-SvetlanaNeural, женский)
    TTS_RATE     — скорость речи, например "+15%" или "-10%" (по умолчанию "+0%")
    TTS_MAX_LEN  — максимум символов на озвучку (по умолчанию 800)
    TIMEZONE     — часовой пояс для «в 18:30» (по умолчанию Europe/Moscow)
    DATA_DIR     — куда класть базу таймеров (по умолчанию /app/data на Bothost)
    TAGALL_LIMIT — максимум людей для .все (по умолчанию 100)
"""
import os
import sys
from pathlib import Path

# Локальный запуск: подхватываем .env, если установлен python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from zoneinfo import ZoneInfo  # Python 3.9+


def _require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        print(
            "❌ Не задана переменная окружения {0}.\n"
            "   Добавьте её в панели Bothost: настройки бота → Переменные окружения.\n"
            "   Обязательные: TELEGRAM_API_ID, TELEGRAM_API_HASH, SESSION_STRING (подробности в README.md).".format(name)
        )
        sys.exit(1)
    return value


API_ID = int(_require("TELEGRAM_API_ID"))
API_HASH = _require("TELEGRAM_API_HASH")
SESSION_STRING = _require("SESSION_STRING")

# Имя, на которое бот откликается в чатах («Полина, таймер …»)
BOT_NAME = os.getenv("BOT_NAME", "Полина").strip() or "Полина"

# Озвучка (.гс). Женский русский голос — Svetlana, мужской — ru-RU-DmitryNeural.
TTS_VOICE = os.getenv("TTS_VOICE", "ru-RU-SvetlanaNeural")
TTS_RATE = os.getenv("TTS_RATE", "+0%")
TTS_MAX_LEN = int(os.getenv("TTS_MAX_LEN", "800"))

# Часовой пояс для «в 18:30» и подтверждений таймеров
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))

# База данных (таймеры, настройки чатов).
# На Bothost код лежит в /app и заменяется при деплое, а /app/data сохраняется —
# поэтому по умолчанию используем /app/data, если запущены в контейнере.
_default_data = Path("/app/data") if Path("/app").is_dir() else Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.getenv("DATA_DIR", str(_default_data)))

# Таймеры
TIMER_MIN_SECONDS = 10                 # минимальный таймер
TIMER_MAX_SECONDS = 60 * 24 * 3600     # максимальный — 60 дней
TIMERS_PER_USER = 10                   # активных таймеров на человека в одном чате
TIMER_TEXT_MAX = 200                   # максимум символов текста напоминания
TIMER_COOLDOWN = 5                     # секунд между созданием таймеров одним человеком

# .все — «тихий призыв»: тег пачками с удалением, запуск владельцем из ЛС
TAGALL_LIMIT = int(os.getenv("TAGALL_LIMIT", "200"))              # максимум людей за вызов
TAGALL_BATCH = int(os.getenv("TAGALL_BATCH", "5"))               # упоминаний в одной пачке
TAGALL_DELETE_DELAY = float(os.getenv("TAGALL_DELETE_DELAY", "1.0"))  # пауза перед удалением пачки, сек
TAGALL_BATCH_PAUSE = float(os.getenv("TAGALL_BATCH_PAUSE", "1.5"))    # пауза между пачками, сек

# ID ОСНОВНОГО аккаунта владельца — с него Полина принимает команду .все в ЛС
# и туда же шлёт прогресс. Если не задан, используется сам аккаунт Полины
# (режим selfbot: команды пишете под самой Полиной).
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Заполняются при старте (userbot.py)
SELF_ID = 0        # id аккаунта, на котором запущена Полина
STARTED_AT = 0.0
