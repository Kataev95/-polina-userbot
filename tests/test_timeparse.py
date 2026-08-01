"""Мини-тесты парсера времени. Запуск: python3 tests/test_timeparse.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zoneinfo import ZoneInfo

from timeparse import parse_duration, parse_when

tz = ZoneInfo("Europe/Moscow")
failures = []


def check(cond, msg):
    if cond:
        print("  ok:", msg)
    else:
        failures.append(msg)
        print("FAIL:", msg)


# --- parse_duration ---
r = parse_duration("4 часа ферма")
check(r == (14400, "ферма"), "4 часа ферма -> {0}".format(r))

r = parse_duration("30 минут чай")
check(r == (1800, "чай"), "30 минут чай -> {0}".format(r))

r = parse_duration("час обед")
check(r == (3600, "обед"), "час обед -> {0}".format(r))

r = parse_duration("полчаса")
check(r == (1800, ""), "полчаса -> {0}".format(r))

r = parse_duration("полтора часа тест")
check(r == (5400, "тест"), "полтора часа тест -> {0}".format(r))

r = parse_duration("1.5 часа тест")
check(r == (5400, "тест"), "1.5 часа тест -> {0}".format(r))

r = parse_duration("1,5 часа")
check(r == (5400, ""), "1,5 часа -> {0}".format(r))

r = parse_duration("2 дня платёж")
check(r == (172800, "платёж"), "2 дня платёж -> {0}".format(r))

r = parse_duration("1 час 20 минут разминка")
check(r == (4800, "разминка"), "1 час 20 минут разминка -> {0}".format(r))

r = parse_duration("10 сек го")
check(r == (10, "го"), "10 сек го -> {0}".format(r))

r = parse_duration("5м пауза")
check(r == (300, "пауза"), "5м пауза -> {0}".format(r))

r = parse_duration("2 недели отчёт")
check(r == (1209600, "отчёт"), "2 недели отчёт -> {0}".format(r))

r = parse_duration("4 часа, ферма")
check(r == (14400, "ферма"), "4 часа, ферма (запятая) -> {0}".format(r))

r = parse_duration("сутки")
check(r == (86400, ""), "сутки -> {0}".format(r))

r = parse_duration("ферма")
check(r is None, "ферма (нет времени) -> None: {0}".format(r))

r = parse_duration("4 ферма")
check(r is None, "4 ферма (число без единицы) -> None: {0}".format(r))

# «чай» не должен распознаваться как «ч» (час)
r = parse_duration("30 минут чай попить")
check(r == (1800, "чай попить"), "30 минут чай попить -> {0}".format(r))

# --- parse_when ---
p = parse_when("через 4 часа ферма", tz)
check(p is not None and p.seconds == 14400 and p.text == "ферма",
      "через 4 часа ферма -> {0}".format(p))

p = parse_when("через полтора часа", tz)
check(p is not None and p.seconds == 5400 and p.text == "",
      "через полтора часа -> {0}".format(p))

p = parse_when("30 минут чай", tz)  # «через» опущено
check(p is not None and p.seconds == 1800 and p.text == "чай",
      "30 минут чай (без «через») -> {0}".format(p))

p = parse_when("в 18:30 созвон", tz)
check(p is not None and p.text == "созвон" and 0 < p.seconds <= 86400,
      "в 18:30 созвон -> seconds={0}".format(p.seconds if p else None))

p = parse_when("в 25:99 бред", tz)
check(p is None, "в 25:99 -> None: {0}".format(p))

p = parse_when("завтра ферма", tz)
check(p is None, "завтра ферма (не поддерживается) -> None: {0}".format(p))

print()
if failures:
    print("❌ Провалено тестов: {0}".format(len(failures)))
    sys.exit(1)
print("✅ Все тесты прошли")
