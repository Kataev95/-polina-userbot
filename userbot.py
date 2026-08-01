"""Полина — Telegram-юзербот на Telethon.

Запуск:  python userbot.py
Нужны переменные окружения: TELEGRAM_API_ID, TELEGRAM_API_HASH, SESSION_STRING.
Как их получить и как задеплоить на Bothost — в README.md.
"""
import asyncio
import logging
import sys
import time

from telethon import TelegramClient
from telethon.sessions import StringSession

import config
import db
import timers_core
from handlers import register_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("polina")


async def main():
    db.init()

    client = TelegramClient(
        StringSession(config.SESSION_STRING),
        config.API_ID,
        config.API_HASH,
        device_model="Polina Userbot",
        app_version="1.0",
    )

    await client.connect()
    if not await client.is_user_authorized():
        log.error(
            "❌ SESSION_STRING недействительна: сессия отозвана, устарела или сгенерирована "
            "с другими api_id/api_hash. Сгенерируйте новую строку: python gen_session.py "
            "(или python convert_session.py из существующего .session-файла) — см. README.md."
        )
        sys.exit(1)

    me = await client.get_me()
    config.OWNER_ID = me.id
    config.STARTED_AT = time.time()

    register_all(client)
    await timers_core.restore(client)

    log.info("✅ %s запущена на аккаунте: %s (id %s)", config.BOT_NAME, me.first_name, me.id)
    log.info("Отправьте .помощь в любой чат, чтобы увидеть список команд.")

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено.")
