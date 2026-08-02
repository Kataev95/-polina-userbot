"""Регистрация всех обработчиков команд."""
from . import gossip, misc, public, scheduler, tagall, voice, welcome


def register_all(client):
    voice.register(client)      # .гс
    tagall.register(client)     # .все, .стоп
    misc.register(client)       # .пинг .погода .ид .полина вкл/выкл .помощь
    welcome.register(client)    # приветствие новичков + .привет
    scheduler.register(client)  # .отложка — отложенные сообщения Telegram + авто-пополнение
    gossip.register(client)     # .вестник — ИИ-дайджест дня + лог чата
    public.register(client)     # «Полина, таймер/таймеры/отмена/погода/заметки/помощь»
