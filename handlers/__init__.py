"""Регистрация всех обработчиков команд."""
from . import fun, gossip, misc, public, quiz, scheduler, tagall, tagquiz, voice, welcome


def register_all(client):
    voice.register(client)      # .гс
    tagall.register(client)     # .все, .стоп
    tagquiz.register(client)    # .тег — вопросы + 5 тегов каждые 30 минут
    misc.register(client)       # .пинг .погода .ид .полина вкл/выкл .помощь
    welcome.register(client)    # приветствие новичков + .привет
    scheduler.register(client)  # .отложка — отложенные сообщения Telegram + авто-пополнение
    gossip.register(client)     # .вестник — лог чата файлом .md в ЛС
    quiz.register(client)       # .квиз — итоги викторин (кто ответил правильнее всех)
    fun.register(client)        # .кубик <1-6> — «удачливый» кубик
    public.register(client)     # «Полина, таймер/таймеры/отмена/погода/заметки/развлечения»
