"""Конвертация существующего .session-файла (Telethon) в SESSION_STRING.

Подходит, если вы уже запускали Telethon-скрипты и у вас остался файл
вида my_account.session — повторный вход по коду НЕ понадобится.

⚠️ Запускать ЛОКАЛЬНО там, где лежит ваш .session-файл:

    pip install telethon
    python convert_session.py

⚠️ Строка сессии даёт ПОЛНЫЙ доступ к аккаунту. Никому не показывайте её,
не отправляйте в чаты и не коммитьте в Git.
"""
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID (с my.telegram.org): ").strip())
api_hash = input("API Hash: ").strip()
path = input("Файл сессии (например, my_account.session): ").strip()

if path.endswith(".session"):
    path = path[: -len(".session")]

if not os.path.exists(path + ".session"):
    raise SystemExit("❌ Файл {0}.session не найден".format(path))

client = TelegramClient(path, api_id, api_hash)
client.connect()
try:
    if not client.is_user_authorized():
        raise SystemExit(
            "❌ Эта сессия не авторизована. Проще сгенерировать новую: python gen_session.py"
        )
    print("\nSESSION_STRING (скопируйте целиком в панель Bothost):\n")
    print(StringSession.save(client.session))
    print("\nГотово. Никому не показывайте эту строку!")
finally:
    client.disconnect()
