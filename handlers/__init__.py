"""Регистрация всех обработчиков команд."""
from . import misc, public, tagall, voice


def register_all(client):
    voice.register(client)    # .гс
    tagall.register(client)   # .все
    misc.register(client)     # .пинг .погода .ид .полина вкл/выкл .помощь
    public.register(client)   # «Полина, таймер/таймеры/отмена/погода/помощь»
