"""Погода через Open-Meteo: бесплатно, без API-ключей и регистрации."""
import aiohttp

_CODES = {
    0: ("ясно", "☀️"), 1: ("в основном ясно", "🌤"), 2: ("переменная облачность", "⛅️"),
    3: ("пасмурно", "☁️"), 45: ("туман", "🌫"), 48: ("изморозь", "🌫"),
    51: ("лёгкая морось", "🌦"), 53: ("морось", "🌦"), 55: ("сильная морось", "🌧"),
    56: ("ледяная морось", "🌧"), 57: ("ледяная морось", "🌧"),
    61: ("небольшой дождь", "🌦"), 63: ("дождь", "🌧"), 65: ("сильный дождь", "🌧"),
    66: ("ледяной дождь", "🌧"), 67: ("ледяной дождь", "🌧"),
    71: ("небольшой снег", "🌨"), 73: ("снег", "🌨"), 75: ("сильный снег", "❄️"),
    77: ("снежная крупа", "🌨"), 80: ("небольшой ливень", "🌦"), 81: ("ливень", "🌧"),
    82: ("сильный ливень", "⛈"), 85: ("снегопад", "🌨"), 86: ("сильный снегопад", "❄️"),
    95: ("гроза", "⛈"), 96: ("гроза с градом", "⛈"), 99: ("сильная гроза с градом", "⛈"),
}


async def get_weather_text(city):
    """Возвращает готовый текст с погодой для города (или сообщение, что город не найден)."""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "ru", "format": "json"},
        ) as r:
            geo = await r.json()
        results = geo.get("results") or []
        if not results:
            return "🤷‍♀️ Не нашла город «{0}». Попробуйте написать иначе.".format(city)
        place = results[0]
        name = place.get("name", city)
        country = place.get("country", "")

        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                           "wind_speed_10m,weather_code",
                "wind_speed_unit": "ms",
            },
        ) as r:
            data = await r.json()

    cur = data.get("current") or {}
    desc, emoji = _CODES.get(cur.get("weather_code", -1), ("", "🌍"))
    title = name + (", " + country if country else "")
    lines = ["{0} **Погода: {1}**".format(emoji, title)]
    t = cur.get("temperature_2m")
    feels = cur.get("apparent_temperature")
    if t is not None:
        line = "🌡 {0:+.0f}°C".format(t)
        if feels is not None:
            line += " (ощущается {0:+.0f}°C)".format(feels)
        lines.append(line)
    if desc:
        lines.append("☁️ " + desc.capitalize())
    wind = cur.get("wind_speed_10m")
    if wind is not None:
        lines.append("💨 Ветер {0:.0f} м/с".format(wind))
    hum = cur.get("relative_humidity_2m")
    if hum is not None:
        lines.append("💧 Влажность {0:.0f}%".format(hum))
    return "\n".join(lines)
