from flask import Flask, request, jsonify
import requests
import pymorphy2
import math
import logging

app = Flask(__name__)

# URL API Open-Meteo
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Состояние диалога
DIALOG_STATE = {}

# Функция для определения категории качества воздуха
def get_air_quality_category(pm25):
    if pm25 <= 12:
        return "хорошее"
    elif 12 < pm25 <= 35:
        return "умеренное"
    elif 35 < pm25 <= 55:
        return "нездоровое для чувствительных групп"
    elif 55 < pm25 <= 150:
        return "нездоровое"
    elif 150 < pm25 <= 250:
        return "очень нездоровое"
    else:
        return "опасное"

# Функция для описания погодных условий
def get_weather_condition(code):
    """
    Возвращает текстовое описание погодных условий на основе кода.
    Коды соответствуют API Open-Meteo.
    """
    weather_conditions = {
        0: "ясно",
        1: "преимущественно ясно",
        2: "переменная облачность",
        3: "пасмурно",
        45: "туман",
        48: "туман с инеем",
        51: "легкая морось",
        53: "умеренная морось",
        55: "сильная морось",
        56: "легкая ледяная морось",
        57: "сильная ледяная морось",
        61: "небольшой дождь",
        63: "умеренный дождь",
        65: "сильный дождь",
        66: "ледяной дождь",
        67: "сильный ледяной дождь",
        71: "небольшой снег",
        73: "умеренный снег",
        75: "сильный снег",
        77: "снежные зерна",
        80: "небольшие ливни",
        81: "умеренные ливни",
        82: "сильные ливни",
        85: "небольшой снегопад",
        86: "сильный снегопад",
        95: "гроза",
        96: "гроза с небольшим градом",
        99: "гроза с сильным градом"
    }
    return weather_conditions.get(code, "неизвестные условия")

# Обработчик запросов от Алисы
@app.route('/', methods=['POST'])
def alice():
    # Получаем JSON-запрос от Алисы
    data = request.get_json()
    if not data:
        return jsonify({
            "version": "1.0",
            "response": {
                "text": "Не удалось обработать запрос.",
                "end_session": False
            }
        })

    # Получаем идентификатор сессии пользователя
    session_id = data['session']['session_id']

    # Проверяем, был ли уже запрошен город
    if session_id not in DIALOG_STATE:
        DIALOG_STATE[session_id] = {"city_requested": False}

    # Извлекаем сущность YANDEX.GEO (город)
    try:
        if 'request' not in data or 'nlu' not in data['request'] or 'entities' not in data['request']['nlu']:
            city = None
        else:
            city_entity = next(
                entity for entity in data['request']['nlu']['entities']
                if entity['type'] == 'YANDEX.GEO'
            )
            city = city_entity['value']['city'].capitalize()  # Извлекаем и форматируем город
    except (KeyError, StopIteration):
        city = None

    # Если город не указан и его ещё не запрашивали
    if not city and not DIALOG_STATE[session_id]["city_requested"]:
        DIALOG_STATE[session_id]["city_requested"] = True
        return jsonify({
            "version": "1.0",
            "response": {
                "text": "Пожалуйста, укажите город, чтобы я могла сообщить погоду.",
                "end_session": False
            }
        })

    # Если город не указан, но его уже запрашивали
    if not city:
        return jsonify({
            "version": "1.0",
            "response": {
                "text": "Город не указан. Попробуйте ещё раз.",
                "end_session": False
            }
        })

    # Склоняем город в предложный падеж
    city_prepositional = get_city_in_prepositional(city)

    # Получаем координаты города
    lat, lon = get_coordinates(city)

    # Запрос к API Open-Meteo для получения погоды
    weather_params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": ["relativehumidity_2m", "cloudcover", "weathercode"],
        "timezone": "auto"
    }

    try:
        weather_response = requests.get(OPEN_METEO_URL, params=weather_params)
        weather_response.raise_for_status()
        weather_data = weather_response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при запросе к Open-Meteo: {e}")
        return jsonify({
            "version": "1.0",
            "response": {
                "text": "Не удалось получить данные о погоде. Попробуйте позже.",
                "end_session": False
            }
        })

    if 'current_weather' not in weather_data:
        return jsonify({
            "version": "1.0",
            "response": {
                "text": "Не удалось получить данные о погоде. Попробуйте позже.",
                "end_session": False
            }
        })

    current_weather = weather_data['current_weather']
    temp = current_weather.get('temperature', 0)
    wind_speed = int(current_weather.get('windspeed', 0))
    wind_dir = get_wind_direction(current_weather.get('winddirection', 0))
    weather_code = current_weather.get('weathercode', 0)  # Код погодных условий

    # Дополнительные данные о погоде
    humidity = weather_data.get('hourly', {}).get('relativehumidity_2m', [None])[0]  # Влажность
    cloud_cover = weather_data.get('hourly', {}).get('cloudcover', [None])[0]  # Облачность
    weather_condition = get_weather_condition(weather_code)  # Погодные условия

    # Запрос к API качества воздуха
    air_quality_params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "pm2_5"
    }

    try:
        air_quality_response = requests.get(AIR_QUALITY_URL, params=air_quality_params)
        air_quality_response.raise_for_status()
        air_quality_data = air_quality_response.json()
        pm25 = air_quality_data.get('hourly', {}).get('pm2_5', [None])[0]  # Текущее значение PM2.5
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при запросе к Air Quality API: {e}")
        pm25 = None

    # Формируем текст ответа в зависимости от температуры
    if temp > 0:
        temperature_message = f"На улице плюс {math.floor(temp)}°C."
    elif temp == 0:
        temperature_message = "На улице ровно ноль градусов."
    else:
        temperature_message = f"На улице минус {math.floor(temp)}°C."

    # Основной текст ответа
    response_text = f"В {city_prepositional} {weather_condition.capitalize()}. {temperature_message}. "
    response_text += f"Ветер {wind_dir}, {wind_speed} м/с. Влажность: {humidity}%, облачность: {cloud_cover}%. "

    if pm25 is not None:
        air_quality_category = get_air_quality_category(pm25)
        response_text += f"Качество воздуха: {air_quality_category} (PM две целых пять десятых равен {pm25} мкг/м³)."
    else:
        response_text += "Данные о качестве воздуха недоступны."

    return jsonify({
        "version": "1.0",
        "response": {
            "text": response_text,
            "end_session": False
        }
    })

# Функция для получения координат города
def get_coordinates(city):
    # URL API Nominatim для геокодирования
    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

    # Параметры запроса
    params = {
        "q": city,  # Название города
        "format": "json",  # Формат ответа
        "limit": 1  # Ограничение на количество результатов
    }

    # Заголовки для корректной работы с Nominatim
    headers = {
        "User-Agent": "MyWeatherApp/1.0"  # Укажите название вашего приложения
    }

    try:
        # Делаем запрос к Nominatim
        response = requests.get(NOMINATIM_URL, params=params, headers=headers)
        response.raise_for_status()  # Проверяем на ошибки

        # Парсим JSON-ответ
        data = response.json()

        if data:  # Если есть результаты
            lat = float(data[0]['lat'])  # Широта
            lon = float(data[0]['lon'])  # Долгота
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                return 55.7558, 37.6176
            return lat, lon
        else:
            # Если город не найден, возвращаем координаты по умолчанию (Москва)
            return 55.7558, 37.6176

    except requests.exceptions.RequestException as e:
        # В случае ошибки запроса возвращаем координаты по умолчанию
        logging.error(f"Ошибка при запросе к Nominatim: {e}")
        return 55.7558, 37.6176

# Функция для преобразования градусов в направление ветра
def get_wind_direction(degrees):
    if not (0 <= degrees <= 360):
        return "неизвестное направление"
    directions = ["северный", "северо-восточный", "восточный", "юго-восточный",
                  "южный", "юго-западный", "западный", "северо-западный"]
    index = round(degrees / 45) % 8
    return directions[index]

# Функция для склонения города
def get_city_in_prepositional(city):
    morph = pymorphy2.MorphAnalyzer()
    parsed_city = morph.parse(city)[0]  # Анализируем слово
    return parsed_city.inflect({'loct'}).word.title()  # Склоняем в предложный падеж

# Запуск сервера
if __name__ == '__main__':
    app.run(port=5000)