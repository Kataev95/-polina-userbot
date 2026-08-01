"""Регистрация всех обработчиков команд."""
from . import misc, public, tagall, voice, welcome


def register_all(client):
    voice.register(client)    # .гс
    tagall.register(client)   # .все, .стоп
    misc.register(client)     # .пинг .погода .ид .полина вкл/выкл .помощь
    welcome.register(client)  # приветствие новичков + .привет
    public.register(client)   # «Полина, таймер/таймеры/отмена/погода/заметки/помощь»
