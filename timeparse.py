"""Парсер русских указаний времени для таймеров.

Понимает:
    «через 4 часа ферма», «через 30 минут чай», «через 1 час 30 минут»,
    «через полчаса», «через час», «через полтора часа», «через 1.5 часа»,
    «через 2 дня», «через неделю», «в 18:30 созвон»
Слово «через» можно опускать: «таймер 10 минут чай».
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ParsedWhen:
    due_ts: float   # unix-время срабатывания
    seconds: int    # через сколько секунд
    text: str       # текст напоминания (может быть пустым)
    human: str      # человекочитаемо: «4 ч (сегодня в 21:53)»


_NUM_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*")

# Пары «число + единица». Порядок альтернатив внутри — от длинных к коротким.
_UNIT_PATTERNS = [
    (re.compile(r"^(?:сек(?:унд(?:а|у|ы)?)?|с)\.?\b", re.I), 1),
    (re.compile(r"^(?:мин(?:ут(?:а|у|ы|ку)?)?|м)\.?\b", re.I), 60),
    (re.compile(r"^(?:час(?:а|ов|ик(?:а|ов)?)?|ч)\.?\b", re.I), 3600),
    (re.compile(r"^(?:сут(?:ки|ок)|д(?:ень|ня|ней)?)\.?\b", re.I), 86400),
    (re.compile(r"^(?:нед(?:ел(?:я|ю|и)|ель)?)\.?\b", re.I), 604800),
]

# Слова без числа (только в начале): «полчаса», «час», «минуту»…
_SPECIAL = [
    (re.compile(r"^полчаса\b", re.I), 1800),
    (re.compile(r"^полтора\s+часа\b", re.I), 5400),
    (re.compile(r"^час(?:ок|ик)?\b", re.I), 3600),
    (re.compile(r"^минут(?:у|ку)\b", re.I), 60),
    (re.compile(r"^секунду\b", re.I), 1),
    (re.compile(r"^сутки\b", re.I), 86400),
    (re.compile(r"^день\b", re.I), 86400),
    (re.compile(r"^неделю\b", re.I), 604800),
]

_THROUGH_RE = re.compile(r"^через\s+(.+)$", re.I | re.S)
_AT_RE = re.compile(r"^в\s+(\d{1,2})[:.](\d{2})\b[,:]?\s*(.*)$", re.I | re.S)


def _clean_rest(s):
    return s.strip().lstrip(",.;:—–-").strip()


def parse_duration(s):
    """«4 часа ферма» -> (14400, "ферма") или None, если время не распознано."""
    rest = s.strip()
    total = 0.0
    first = True
    while True:
        if first:
            first = False
            hit = False
            for pat, secs in _SPECIAL:
                m = pat.match(rest)
                if m:
                    total += secs
                    rest = rest[m.end():].strip()
                    hit = True
                    break
            if hit:
                continue
        m = _NUM_RE.match(rest)
        if not m:
            break
        tail = rest[m.end():]
        unit = None
        for pat, mult in _UNIT_PATTERNS:
            um = pat.match(tail)
            if um:
                unit = (um.end(), mult)
                break
        if unit is None:
            break
        num = float(m.group(1).replace(",", "."))
        total += num * unit[1]
        rest = tail[unit[0]:].strip()
    if total <= 0:
        return None
    return int(round(total)), _clean_rest(rest)


def human_delta(secs):
    """14400 -> «4 ч», 4800 -> «1 ч 20 мин»"""
    secs = int(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append("{0} дн".format(d))
    if h:
        parts.append("{0} ч".format(h))
    if m:
        parts.append("{0} мин".format(m))
    if s and not d and not h:
        parts.append("{0} сек".format(s))
    return " ".join(parts) or "0 сек"


def _human_when(due, now):
    t = due.strftime("%H:%M")
    if due.date() == now.date():
        return "сегодня в {0}".format(t)
    if due.date() == (now + timedelta(days=1)).date():
        return "завтра в {0}".format(t)
    return "{0} в {1}".format(due.strftime("%d.%m"), t)


def _build(seconds, due, rest, now):
    human = "{0} ({1})".format(human_delta(seconds), _human_when(due, now))
    return ParsedWhen(due_ts=due.timestamp(), seconds=seconds, text=_clean_rest(rest or ""), human=human)


def parse_when(spec, tz):
    """Разбирает строку после слова «таймер»/«напомни».

    Возвращает ParsedWhen или None, если время не понято.
    """
    spec = spec.strip().lstrip(",:").strip()
    now = datetime.now(tz)

    m = _THROUGH_RE.match(spec)
    if m:
        r = parse_duration(m.group(1))
        if not r:
            return None
        seconds, rest = r
        return _build(seconds, now + timedelta(seconds=seconds), rest, now)

    m = _AT_RE.match(spec)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            return None
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        seconds = int((due - now).total_seconds())
        return _build(seconds, due, m.group(3), now)

    # «через» опущено: «таймер 10 минут чай»
    r = parse_duration(spec)
    if r:
        seconds, rest = r
        return _build(seconds, now + timedelta(seconds=seconds), rest, now)
    return None
