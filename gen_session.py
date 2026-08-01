"""Генерация SESSION_STRING с нуля (вход по номеру телефона и коду).

⚠️ Запускать ЛОКАЛЬНО на своём компьютере, НЕ на хостинге:

    pip install telethon
    python gen_session.py

Скрипт спросит api_id/api_hash (с my.telegram.org), номер телефона и код
из Telegram, затем напечатает строку сессии. Вставьте её в переменную
SESSION_STRING в панели Bothost.

⚠️ Строка сессии даёт ПОЛНЫЙ доступ к аккаунту. Никому не показывайте её,
не отправляйте в чаты и не коммитьте в Git.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID (с my.telegram.org): ").strip())
api_hash = input("API Hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nSESSION_STRING (скопируйте целиком в панель Bothost):\n")
    print(client.session.save())
    print("\nГотово. Никому не показывайте эту строку!")
