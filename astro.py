import os
import logging
import hashlib
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, ReplyKeyboardRemove
import asyncio
import re
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from fastapi import Request

from fastapi import FastAPI

# --- ДОБАВЛЕНЫ НОВЫЕ ИМПОРТЫ ДЛЯ WEBHOOK И AIOHTTP ---
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from urllib.parse import urlparse
# --- КОНЕЦ НОВЫХ ИМПОРТОВ ---

# --- Загрузка переменных окружения для локальной разработки ---
load_dotenv()
app = FastAPI()

# Настройки (теперь читаются из переменных окружения для Render)
TOKEN = os.getenv("BOT_TOKEN")
TON_WALLET = os.getenv("TON_WALLET_ADDRESS")
ADSGRAM_API_KEY = os.getenv("ADSGRAM_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "AstroBotDB")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "users")
WEBHOOK_DOMAIN = "astrox-mfuk.onrender.com"
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "astrox-mfuk.onrender.com")

# Проверка наличия обязательных переменных окружения
if not TOKEN:
    raise ValueError("Environment variable BOT_TOKEN is not set.")
if not TON_WALLET:
    raise ValueError("Environment variable TON_WALLET_ADDRESS is not set.")

# --- НАСТРОЙКИ WEBHOOK ДЛЯ RENDER ---
# WEBHOOK_HOST - это домен вашего сервиса Render (например, my-astro-bot.onrender.com)
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"
# Render предоставляет порт через переменную окружения PORT
WEB_SERVER_PORT = os.getenv('PORT', 8080)
# Полный URL вебхука, который будет установлен в Telegram
# Исправлена опечатка WEBHOO_PATH на WEBHOOK_PATH
WEBHOOK_URL = f"https://{WEBHOOK_HOST}/webhook/{TOKEN}" if WEBHOOK_HOST else None
# Путь, по которому Telegram будет отправлять обновления (часть WEBHOOK_URL)
WEBHOOK_PATH = f"/webhook/{TOKEN}"
# --- КОНЕЦ НАСТРОЕК WEBHOOK ---

# Инициализация логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # Более подробный формат
logger = logging.getLogger(__name__)

# --- Инициализация MongoDB ---
# Клиент MongoDB
mongo_client: AsyncIOMotorClient = None
# База данных и коллекция
db = None
users_collection = None

async def init_mongodb():
    """Инициализация клиента MongoDB и коллекций."""
    global mongo_client, db, users_collection
    if MONGO_URI: # Проверяем, что URI задан, прежде чем пытаться подключиться
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URI)
            db = mongo_client[MONGO_DB_NAME]
            users_collection = db[MONGO_COLLECTION_NAME]
            logger.info(f"MongoDB успешно подключен к базе данных '{MONGO_DB_NAME}'")
        except Exception as e:
            logger.error(f"Ошибка подключения к MongoDB: {e}", exc_info=True)
            # Не поднимаем исключение, чтобы бот мог запуститься без БД для пользователей
            logger.warning("Бот будет работать без сохранения пользовательских данных в MongoDB.")
    else:
        logger.warning("MONGO_URI не установлен. Бот будет работать без сохранения пользовательских данных в MongoDB.")


# Инициализация бота и диспетчера
# Используем MemoryStorage для хранения состояний FSM
storage = MemoryStorage()
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=storage)

# Состояния для FSM (Finite State Machine)
class Form(StatesGroup):
    set_birth_date = State()
    select_sign = State() # Это состояние не используется напрямую, но пусть будет
    select_language = State() # Это состояние не используется напрямую, но пусть будет
    magic_ball_answer = State()

# --- Тексты для разных языков (теперь только русский) ---
TEXTS = {
    "ru": {
        "welcome": "✨ Добро пожаловать в <b>Cosmic Insight</b> - ваш личный астрологический компаньон!\n\n"
                   "Получайте свой ежедневный гороскоп и небесное руководство.",
        "main_menu_horoscope": "🌟 Получить гороскоп",
        "main_menu_settings": "⚙️ Настройки",
        "main_menu_support": "❤️ Поддержать нас",
        "main_menu_entertainment": "🎲 Развлечения",
        "support_us_prompt": "💎 Поддержите наш проект!\n\n"
                             "Пожалуйста, отправьте <b>любую сумму TON</b> на адрес:\n"
                             "<code>{wallet}</code>\n\n"
                             "Для этого:\n"
                             "1. Нажмите кнопку 'Открыть @wallet'.\n"
                             "2. В боте @wallet выберите 'Отправить'.\n"
                             "3. Вставьте указанный выше адрес.\n"
                             "4. Введите желаемую сумму.",
        "settings_menu_choose": "Выберите, что хотите настроить:",
        "settings_change_sign": "Изменить знак зодиака",
        "settings_set_birth_date": "Указать дату рождения",
        "settings_change_language": "Изменить язык",
        "choose_sign": "Выберите ваш знак зодиака:",
        "sign_set_success": "✅ Ваш знак зодиака установлен как <b>{sign}</b>.",
        "sign_changed_answer": "Знак зодиака изменен!",
        "donate_open_wallet": "Открыть @wallet",
        "donate_closed": "Окно доната закрыто.",
        "birth_date_prompt": "Пожалуйста, введите вашу дату рождения в формате ДД.ММ.ГГГГ:",
        "birth_date_invalid_format": "Неверный формат даты. Пожалуйста, введите в формате ДД.ММ.ГГГГ (например, 01.01.2000).",
        "birth_date_future_error": "Дата рождения не может быть в будущем. Пожалуйста, введите корректную дату.",
        "birth_date_success": "✅ Ваша дата рождения <b>{birth_date}</b> успешно сохранена!",
        "birth_date_changed_answer": "Дата рождения сохранена!",
        "choose_language_prompt": "Выберите язык:",
        "language_set_success": "✅ Язык успешно изменен на <b>{lang_name}</b>.",
        "language_changed_answer": "Язык изменен!",
        "ad_text": "✨ <b>Специальное предложение от нашего партнера</b> ✨\n\n"
                   "Ознакомьтесь с этим удивительным продуктом!",
        "ad_button": "Посетить спонсора",
        "horoscope_title": "{emoji} <b>Ваш ежедневный гороскоп</b>",
        "horoscope_sign": "🔮 Знак Зодиака: {sign} ({element})",
        "horoscope_date": "📅 Дата: {date}",
        "horoscope_age": "🎂 Вам {age} {years}!",
        "horoscope_mood": "🌈 Настроение дня: <b>{mood}</b>",
        "horoscope_lucky_color": "🍀 Счастливый цвет: <b>{color}</b>",
        "horoscope_lucky_number": "🔢 Число удачи: <b>{number}</b>",
        "horoscope_ruling_planet": "🪐 Планета-покровитель: <b>{planet}</b>",
        "horoscope_lucky_stone": "💎 Камень удачи: <b>{stone}</b>",
        "horoscope_compatibility": "💞 Совместимость: <b>{compatible_signs}</b>",
        "horoscope_tip": "💡 <b>Совет дня:</b> {tip}",
        "horoscope_love": "💖 Любовь",
        "horoscope_career": "💼 Карьера",
        "horoscope_finance": "💰 Финансы",
        "horoscope_health": "🏥 Здоровье",
        "horoscope_closing_message": "Помните, звезды лишь намекают, а выбор всегда за вами! Желаем вам волшебного дня! ✨",
        "years_singular": "год",
        "years_plural_2_4": "года",
        "years_plural_5_plus": "лет",
        "compatibility_not_defined": "Не определена",
        "horoscope_tips": [
            "Доверьтесь своей интуиции; она приведет вас к правильному решению.",
            "Сегодня отличный день для новых начинаний и смелых идей.",
            "Обратите внимание на детали — в них кроется ключ к успеху.",
            "Постарайтесь провести время с близкими; это принесет вам радость.",
            "Не бойтесь рисковать, но делайте это мудро и осознанно.",
            "Сосредоточьтесь на своих целях, и вы достигнете желаемого.",
            "Практикуйте благодарность – это привлечет больше позитива в вашу жизнь.",
            "Найдите время для отдыха и восстановления, это важно для вашего благополучия."
        ],
        # Переводы для знаков зодиака, элементов, планет, камней
        "sign_aries": "Овен", "sign_taurus": "Телец", "sign_gemini": "Близнецы", "sign_cancer": "Рак",
        "sign_leo": "Лев", "sign_virgo": "Дева", "sign_libra": "Весы", "sign_scorpio": "Скорпион",
        "sign_sagittarius": "Стрелец", "sign_capricorn": "Козерог", "sign_aquarius": "Водолей", "sign_pisces": "Рыбы",
        
        "element_fire": "Огонь", "element_earth": "Земля", "element_air": "Воздух", "element_water": "Вода",

        "planet_mars": "Марс", "planet_venus": "Венера", "planet_mercury": "Меркурий", "planet_moon": "Луна",
        "planet_sun": "Солнце", "planet_pluto": "Плутон", "planet_jupiter": "Юпитер", "planet_saturn": "Сатурн",
        "planet_uranus": "Уран", "planet_neptune": "Нептун", "planet_crystal": "Кристалл (по умолчанию)", # для камня
        
        "stone_diamond": "Алмаз", "stone_emerald": "Изумруд", "stone_agate": "Агат", "stone_pearl": "Жемчуг",
        "stone_ruby": "Рубин", "stone_sapphire": "Сапфир", "stone_opal": "Опал", "stone_topaz": "Топаз",
        "stone_turquoise": "Бирюза", "stone_garnet": "Гранат", "stone_amethyst": "Аметист", "stone_aquamarine": "Аквамарин",

        # Развлекательные функции
        "entertainment_menu_choose": "Выберите развлечение:",
        "cookie_button": "🍪 Печенье с предсказанием",
        "magic_ball_button": "🔮 Шар с ответами",
        "cookie_fortune_message": "🍪 Ваше предсказание: <b>{fortune}</b>",
        "magic_ball_question_prompt": "🔮 Задайте свой вопрос Шару (он ответит 'да' или 'нет'):",
        "magic_ball_answer_message": "🔮 Ответ Шара: <b>{answer}</b>",
        "magic_ball_not_a_question": "Пожалуйста, задайте вопрос."
    }
}

# Текст для кнопки "Поделиться ботом"
SHARE_MESSAGE_RU = "🔮 Хочешь узнать, что ждет тебя сегодня по звездам? Получай свой личный гороскоп каждый день с Cosmic Insight! Это не просто общие фразы, а глубокий взгляд в твою судьбу. Присоединяйся и исследуй свой космический путь! ✨\n\n[Ссылка на бота]"


# --- Вспомогательная функция для получения текста на нужном языке ---
# Теперь она должна получать данные пользователя из БД
# Если MongoDB не подключена, используем In-memory хранилище для пользователей
_user_data_cache = {} # Простой in-memory кэш для пользовательских данных

async def get_user_data(user_id: int):
    """Получает данные пользователя из MongoDB или из кэша."""
    if users_collection is not None: # Corrected line
        user_data = await users_collection.find_one({"_id": user_id})
        if user_data:
            _user_data_cache[user_id] = user_data # Обновляем кэш из БД
            return user_data
    # Если MongoDB не подключена или пользователь не найден в БД, ищем в кэше
    return _user_data_cache.get(user_id, {"_id": user_id, "sign": "aries", "lang": "ru", "birth_date": None})

async def update_user_data(user_id: int, key: str, value):
    """Обновляет данные пользователя в MongoDB и в кэше."""
    user_id_str = str(user_id)
    # Обновляем кэш
    current_data = _user_data_cache.get(user_id_str, {"_id": user_id_str, "sign": "aries", "lang": "ru", "birth_date": None})
    current_data[key] = value
    _user_data_cache[user_id_str] = current_data

    if users_collection is not None:
        await users_collection.update_one(
            {"_id": user_id_str},
            {"$set": {key: value}},
            upsert=True
        )
    else:
        logger.warning(f"MongoDB коллекция не инициализирована, данные пользователя {user_id_str} будут сохранены только в памяти.")


# Функция get_text теперь должна быть асинхронной, чтобы получить язык пользователя
async def get_text_async(user_id: int, key: str) -> str:
    user_data = await get_user_data(user_id)
    lang = user_data.get("lang", "ru")
    return TEXTS.get(lang, TEXTS["ru"]).get(key, f"_{key}_")

# Генератор гороскопов
class HoroscopeGenerator:
    SIGNS = {
        "aries": {"emoji": "♈️", "element_key": "element_fire", "character": ["энергичный", "смелый", "импульсивный"], "planet_key": "planet_mars", "lucky_number": [9], "lucky_stone_key": "stone_diamond"},
        "taurus": {"emoji": "♉️", "element_key": "element_earth", "character": ["стабильный", "терпеливый", "упрямый"], "planet_key": "planet_venus", "lucky_number": [6], "lucky_stone_key": "stone_emerald"},
        "gemini": {"emoji": "♊️", "element_key": "element_air", "character": ["адаптивный", "любопытный", "нерешительный"], "planet_key": "planet_mercury", "lucky_number": [5], "lucky_stone_key": "stone_agate"},
        "cancer": {"emoji": "♋️", "element_key": "element_water", "character": ["заботливый", "эмоциональный", "защитник"], "planet_key": "planet_moon", "lucky_number": [2], "lucky_stone_key": "stone_pearl"},
        "leo": {"emoji": "♌️", "element_key": "element_fire", "character": ["уверенный", "щедрый", "властный"], "planet_key": "planet_sun", "lucky_number": [1], "lucky_stone_key": "stone_ruby"},
        "virgo": {"emoji": "♍️", "element_key": "element_earth", "character": ["аналитический", "практичный", "критичный"], "planet_key": "planet_mercury", "lucky_number": [5], "lucky_stone_key": "stone_sapphire"},
        "libra": {"emoji": "♎️", "element_key": "element_air", "character": ["гармоничный", "дипломатичный", "нерешительный"], "planet_key": "planet_venus", "lucky_number": [6], "lucky_stone_key": "stone_opal"},
        "scorpio": {"emoji": "♏️", "element_key": "element_water", "character": ["интенсивный", "страстный", "скрытный"], "planet_key": "planet_pluto", "lucky_number": [8], "lucky_stone_key": "stone_topaz"},
        "sagittarius": {"emoji": "♐️", "element_key": "element_fire", "character": ["авантюрный", "оптимистичный", "беспокойный"], "planet_key": "planet_jupiter", "lucky_number": [3], "lucky_stone_key": "stone_turquoise"},
        "capricorn": {"emoji": "♑️", "element_key": "element_earth", "character": ["дисциплинированный", "ответственный", "пессимистичный"], "planet_key": "planet_saturn", "lucky_number": [8], "lucky_stone_key": "stone_garnet"},
        "aquarius": {"emoji": "♒️", "element_key": "element_air", "character": ["инновационный", "независимый", "безэмоциональный"], "planet_key": "planet_uranus", "lucky_number": [4], "lucky_stone_key": "stone_amethyst"},
        "pisces": {"emoji": "♓️", "element_key": "element_water", "character": ["сострадательный", "артистичный", "склонный к эскапизму"], "planet_key": "planet_neptune", "lucky_number": [7], "lucky_stone_key": "stone_aquamarine"},
    }

    MOODS = {
        "ru": ["Оптимистичное", "Задумчивое", "Энергичное", "Спокойное", "Осторожное", "Радостное", "Вдохновленное", "Гармоничное", "Надежное"],
    }
    LUCKY_COLORS = {
        "ru": ["Синий", "Зеленый", "Золотой", "Серебряный", "Красный", "Фиолетовый", "Розовый", "Желтый", "Бирюзовый", "Лавандовый"],
    }
    COMPATIBILITY = {
        "aries": ["leo", "sagittarius", "gemini", "aquarius"], "taurus": ["virgo", "capricorn", "cancer", "pisces"],
        "gemini": ["libra", "aquarius", "aries", "leo"], "cancer": ["scorpio", "pisces", "taurus", "virgo"],
        "leo": ["aries", "sagittarius", "gemini", "libra"], "virgo": ["taurus", "capricorn", "cancer", "scorpio"],
        "libra": ["gemini", "aquarius", "leo", "sagittarius"], "scorpio": ["cancer", "pisces", "virgo", "capricorn"],
        "sagittarius": ["aries", "leo", "libra", "aquarius"], "capricorn": ["taurus", "virgo", "scorpio", "pisces"],
        "aquarius": ["gemini", "libra", "aries", "sagittarius"], "pisces": ["cancer", "scorpio", "taurus", "capricorn"],
    }

    # Предсказания для "Печенья с предсказаниями"
    COOKIE_FORTUNES = [
        "Вас ждет неожиданная удача.",
        "Прислушайтесь к своему внутреннему голосу.",
        "Сегодня идеальный день для новых начинаний.",
        "Ваша доброта будет вознаграждена.",
        "Не бойтесь перемен, они к лучшему.",
        "Вас ждет приятная встреча.",
        "Сосредоточьтесь на своих мечтах.",
        "Ваше упорство принесет плоды.",
        "Найдите радость в мелочах.",
        "Впереди вас ждет увлекательное приключение.",
        "День принесет новые возможности.",
        "Будьте открыты для новых идей."
    ]

    # Ответы для "Шара с ответами"
    MAGIC_BALL_ANSWERS = {
        "positive": [
            "Безусловно!", "Да.", "Весьма вероятно.", "Можете быть уверены в этом.", "Да, определенно.",
            "Без сомнения.", "Как я вижу, да."
        ],
        "negative": [
            "Мои источники говорят нет.", "Очень сомнительно.", "Не рассчитывайте на это.", "Нет.", "Перспективы не очень хорошие."
        ],
        "neutral": [
            "Сконцентрируйся и спроси снова.", "Ответ неясен, попробуй еще раз.", "Лучше не говорить тебе сейчас.",
            "Не могу предсказать сейчас."
        ]
    }


    @staticmethod
    async def _generate_aspect_description(aspect: str, score: int, rng: random.Random, lang: str) -> str:
        descriptions = {
            TEXTS[lang]["horoscope_love"]: {
                (8, 10): {
                    "ru": ["✨ Сегодня ваша харизма на пике! Отличный день для романтических приключений и углубления связей. Откройтесь новым чувствам, и пусть ваша душа расцветет!",
                           "💖 Сердце наполнено теплом и гармонией. Возможны приятные сюрпризы от близких или новые вдохновляющие знакомства. Позвольте себе быть счастливой!"]
                },
                (5, 7): {
                    "ru": ["🌟 В любви ожидается стабильность. Не бойтесь проявлять инициативу, даже небольшие жесты внимания принесут радость и укрепят ваши отношения.",
                           "❤️ Отношения требуют вашего нежного внимания. Проведите время с теми, кто вам дорог, и вы почувствуете прилив позитивной энергии и взаимопонимания."]
                },
                (3, 4): {
                    "ru": ["⚠️ Будьте осторожны в словах, чтобы ненароком не ранить чувства близких. Небольшие недопонимания могут возникнуть, но их легко преодолеть искренним разговором и нежностью.",
                           "💔 Возможны эмоциональные качели. Постарайтесь найти время для себя, чтобы восстановить внутренний баланс и обрести спокойствие."]
                },
                (1, 2): {
                    "ru": ["🚨 Сегодня лучше избегать серьезных выяснений отношений. Сосредоточьтесь на саморазвитии и дайте себе время для глубоких размышлений и восстановления.",
                           "😔 Могут возникнуть трудности в общении. Важно помнить, что каждый имеет право на свое мнение. Не принимайте все слишком близко к сердцу, сохраняйте внутренний покой."]
                }
            },
            TEXTS[lang]["horoscope_career"]: {
                (8, 10): {
                    "ru": ["🚀 Ваша энергия и целеустремленность принесут невероятные плоды! Отличный день для новых проектов, важных переговоров и стремительного карьерного роста. Вселенная на вашей стороне!",
                           "📈 Смело беритесь за самые сложные задачи — успех гарантирован. Ваша продуктивность сегодня поражает, и ваши усилия будут щедро вознаграждены!"]
                },
                (5, 7): {
                    "ru": ["💼 Рабочие вопросы решаются без особых затруднений. Возможно, появятся новые, очень интересные предложения, которые стоит рассмотреть внимательнее.",
                           "📚 Благоприятный период для обучения и повышения квалификации. Новые знания, полученные сегодня, обязательно пригодятся вам в будущем, открывая новые горизонны."]
                },
                (3, 4): {
                    "ru": ["📉 Могут возникнуть небольшие препятствия или задержки. Сохраняйте спокойствие, доверяйте своему внутреннему голосу и тщательно планируйте свои действия, чтобы преодолеть их.",
                           "⏳ Сегодня лучше сосредоточиться на рутинных задачах и не начинать ничего слишком амбициозного. Терпение и методичность принесут лучшие результаты."]
                },
                (1, 2): {
                    "ru": ["🚧 Возможны разногласия с коллегами или начальством. Постарайтесь быть максимально дипломатичной и избегать конфликтов, чтобы сохранить гармонию.",
                           "🙅‍♀️ День не подходит для принятия важных карьерных решений. Лучше отложить их на потом, когда звезды будут более благосклонны."]
                }
            },
            TEXTS[lang]["horoscope_finance"]: {
                (8, 10): {
                    "ru": ["💰 Вас ждет финансовый успех! Возможно неожиданное поступление денег, очень выгодные инвестиции или удачные сделки. Доверьтесь своей интуиции!",
                           "💸 Отличный день для планирования бюджета и крупных, давно желанных покупок. Ваше финансовое чутье сегодня обострено, используйте его мудро."]
                },
                (5, 7): {
                    "ru": ["💳 Финансовая ситуация стабильна. Небольшие траты не повлияют на ваш бюджет. Возможно, стоит рассмотреть новые источники дохода, чтобы приумножить свои накопления.",
                           "📈 Есть шанс найти очень выгодное предложение. Будьте внимательны к деталям и не упустите свою возможность."]
                },
                (3, 4): {
                    "ru": ["📉 Сегодня стоит быть экономнее и внимательнее к расходам. Избегайте необдуманных трат и крупных покупок. Возможны непредвиденные, но управляемые расходы.",
                           "🧐 Пересмотрите свои финансовые планы. Возможно, есть слабые места, требующие вашего пристального внимания и коррекции."]
                }
            },
            TEXTS[lang]["horoscope_health"]: {
                (8, 10): {
                    "ru": ["🌸 Чувствуете себя полной энергии и жизненных сил! Отличный день для активного отдыха, спорта и начала новых здоровых привычек. Ваше тело благодарит вас!",
                           "💪 Ваше самочувствие прекрасно. Это идеальный день для занятий, которые приносят вам физическое и ментальное удовольствие и наполняют вас радостью."]
                },
                (5, 7): {
                    "ru": ["🌿 Здоровье в норме. Уделите внимание правильному питанию и умеренным физическим нагрузкам. Не забывайте о полноценном сне, он — ваш лучший помощник.",
                           "💧 Пейте больше чистой воды и прислушивайтесь к сигналам своего тела. Оно всегда подскажет, что ему нужно."]
                },
                (3, 4): {
                    "ru": ["😴 Возможна легкая усталость или снижение тонуса. Позвольте себе отдохнуть и избегайте переутомления. Возможно, стоит пересмотреть режим дня и добавить больше релаксации.",
                           "🤒 Незначительные недомогания могут напомнить о себе. Дайте своему организму время на восстановление и не игнорируйте его потребности."]
                }
            }
        }
        for (min_score, max_score), desc_dict in descriptions[aspect].items():
            if min_score <= score <= max_score:
                return rng.choice(desc_dict["ru"])
        return ""

    @staticmethod
    def calculate_age(birth_date: datetime) -> int:
        today = datetime.now()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age

    @staticmethod
    def get_zodiac_sign(birth_date: datetime) -> str:
        month = birth_date.month
        day = birth_date.day

        if (month == 3 and day >= 21) or (month == 4 and day <= 19): return "aries"
        if (month == 4 and day >= 20) or (month == 5 and day <= 20): return "taurus"
        if (month == 5 and day >= 21) or (month == 6 and day <= 20): return "gemini"
        if (month == 6 and day >= 21) or (month == 7 and day <= 22): return "cancer"
        if (month == 7 and day >= 23) or (month == 8 and day <= 22): return "leo"
        if (month == 8 and day >= 23) or (month == 9 and day <= 22): return "virgo"
        if (month == 9 and day >= 23) or (month == 10 and day <= 22): return "libra"
        if (month == 10 and day >= 23) or (month == 11 and day <= 21): return "scorpio"
        if (month == 11 and day >= 22) or (month == 12 and day <= 21): return "sagittarius"
        if (month == 12 and day >= 22) or (month == 1 and day <= 19): return "capricorn"
        if (month == 1 and day >= 20) or (month == 2 and day <= 18): return "aquarius"
        if (month == 2 and day >= 19) or (month == 3 and day <= 20): return "pisces"
        return "aries" # Возврат по умолчанию

    @staticmethod
    def get_year_ending(age: int, lang: str) -> str:
        if age % 10 == 1 and age % 100 != 11:
            return TEXTS["ru"]["years_singular"]
        elif age % 10 >= 2 and age % 10 <= 4 and (age % 100 < 10 or age % 100 >= 20):
            return TEXTS["ru"]["years_plural_2_4"]
        else:
            return TEXTS["ru"]["years_plural_5_plus"]

    @staticmethod
    async def generate(user_id: int) -> str:
        user_data = await get_user_data(user_id)
        lang = user_data.get("lang", "ru")
        
        sign_key = user_data.get('sign', HoroscopeGenerator.get_zodiac_sign(datetime.now()) if user_data.get('birth_date') else "aries")
        today = datetime.now()

        # Уникальный seed для дня и пользователя
        seed_str = f"{user_id}_{today.strftime('%Y%m%d')}_{sign_key}".encode()
        seed = int(hashlib.md5(seed_str).hexdigest()[:8], 16)
        rng = random.Random(seed)

        # Генерация аспектов
        aspects = {
            await get_text_async(user_id, "horoscope_love"): rng.randint(1, 10),
            await get_text_async(user_id, "horoscope_career"): rng.randint(1, 10),
            await get_text_async(user_id, "horoscope_finance"): rng.randint(1, 10),
            await get_text_async(user_id, "horoscope_health"): rng.randint(1, 10)
        }

        # Дополнительные элементы прогноза
        sign_info = HoroscopeGenerator.SIGNS.get(sign_key, {})
        mood = rng.choice(HoroscopeGenerator.MOODS[lang])
        lucky_color = rng.choice(HoroscopeGenerator.LUCKY_COLORS[lang])
        lucky_number = rng.choice(sign_info.get("lucky_number", [rng.randint(1, 9)]))
        
        # Используем get_text_async для локализованных значений
        ruling_planet = await get_text_async(user_id, sign_info.get("planet_key", "planet_crystal"))
        lucky_stone = await get_text_async(user_id, sign_info.get("lucky_stone_key", "stone_crystal"))

        available_compatible_signs = HoroscopeGenerator.COMPATIBILITY.get(sign_key, [])
        compatible_signs_keys = rng.sample(available_compatible_signs, k=min(2, len(available_compatible_signs)))
        # Переводим названия знаков для совместимости
        compatible_signs = [await get_text_async(user_id, f"sign_{s}") for s in compatible_signs_keys]

        if not compatible_signs:
            compatible_signs.append(await get_text_async(user_id, "compatibility_not_defined"))

        tips = TEXTS[lang].get("horoscope_tips", [])
        if not tips:
            tips = ["Сегодня отличный день!", "Будьте внимательны к деталям!"]
        
        # Форматирование
        horoscope_text = (await get_text_async(user_id, "horoscope_title")).format(emoji=sign_info.get('emoji', '✨')) + "\n\n"
        # Используем get_text_async для названия знака и элемента
        horoscope_text += (await get_text_async(user_id, "horoscope_sign")).format(sign=await get_text_async(user_id, f"sign_{sign_key}"), element=await get_text_async(user_id, sign_info.get('element_key', ''))) + "\n"
        horoscope_text += (await get_text_async(user_id, "horoscope_date")).format(date=today.strftime('%d %b %Y')) + "\n"

        birth_date_str = user_data.get("birth_date")
        if birth_date_str:
            try:
                birth_date_obj = datetime.strptime(birth_date_str, "%d.%m.%Y")
                age = HoroscopeGenerator.calculate_age(birth_date_obj)
                years_ending = HoroscopeGenerator.get_year_ending(age, lang)
                horoscope_text += (await get_text_async(user_id, "horoscope_age")).format(age=age, years=years_ending) + "\n"
            except ValueError:
                logger.error(f"Неверный формат даты рождения для пользователя {user_id}: {birth_date_str}")
        
        horoscope_text += "\n"

        for aspect_name, score in aspects.items():
            rating_emoji = ""
            if score >= 8: rating_emoji = "🌟"
            elif score >= 5: rating_emoji = "✨"
            elif score >= 3: rating_emoji = "⚠️"
            else: rating_emoji = "🚨"

            description = await HoroscopeGenerator._generate_aspect_description(
                aspect_name,
                score,
                rng,
                lang
            )
            horoscope_text += f"{aspect_name}: {rating_emoji} {score}/10\n<i>\" {description} \"</i>\n\n"

        horoscope_text += (
            (await get_text_async(user_id, "horoscope_mood")).format(mood=mood) + "\n"
            f"{(await get_text_async(user_id, 'horoscope_lucky_color')).format(color=lucky_color)}\n"
            f"{(await get_text_async(user_id, 'horoscope_lucky_number')).format(number=lucky_number)}\n"
            f"{(await get_text_async(user_id, 'horoscope_ruling_planet')).format(planet=ruling_planet)}\n"
            f"{(await get_text_async(user_id, 'horoscope_lucky_stone')).format(stone=lucky_stone)}\n"
            f"{(await get_text_async(user_id, 'horoscope_compatibility')).format(compatible_signs=', '.join(compatible_signs))}\n\n"
            f"{(await get_text_async(user_id, 'horoscope_tip')).format(tip=rng.choice(tips))}\n\n"
            f"<i>{(await get_text_async(user_id, 'horoscope_closing_message'))}</i>"
        )
        return horoscope_text

# Клавиатуры
class Keyboard:
    @staticmethod
    async def main_menu(user_id: int) -> ReplyKeyboardMarkup:
        builder = ReplyKeyboardBuilder()
        builder.button(text=await get_text_async(user_id, "main_menu_horoscope"))
        builder.button(text=await get_text_async(user_id, "main_menu_settings"))
        builder.button(text=await get_text_async(user_id, "main_menu_support"))
        builder.button(text=await get_text_async(user_id, "main_menu_entertainment"))
        builder.adjust(2)
        return builder.as_markup(resize_keyboard=True)

    @staticmethod
    async def settings_menu(user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text=await get_text_async(user_id, "settings_change_sign"), callback_data="change_sign")
        builder.button(text=await get_text_async(user_id, "settings_set_birth_date"), callback_data="set_birth_date")
        builder.button(text=await get_text_async(user_id, "settings_change_language"), callback_data="change_language")
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    async def sign_selection_menu(user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        signs_list = list(HoroscopeGenerator.SIGNS.keys())
        for i in range(0, len(signs_list), 3):
            row_buttons = []
            for sign_key in signs_list[i:i+3]:
                sign_info = HoroscopeGenerator.SIGNS[sign_key]
                row_buttons.append(types.InlineKeyboardButton(text=f"{sign_info['emoji']} {await get_text_async(user_id, f'sign_{sign_key}')}", callback_data=f"set_sign_{sign_key}"))
            builder.row(*row_buttons)
        return builder.as_markup()

    @staticmethod
    async def language_selection_menu(user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="🇷🇺 Русский", callback_data="set_lang_ru")
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    async def entertainment_menu(user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text=await get_text_async(user_id, "cookie_button"), callback_data="get_cookie_fortune")
        builder.button(text=await get_text_async(user_id, "magic_ball_button"), callback_data="ask_magic_ball")
        builder.adjust(1)
        return builder.as_markup()

# --- Вспомогательная функция для определения языка (упрощена, т.к. только RU) ---
def get_user_initial_language_code(message: types.Message) -> str:
    return "ru"

# Обработчики
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id) 

    # Если это новый пользователь или данные неполны, инициализируем
    if not user_data.get("sign") or not user_data.get("lang") or not user_data.get("birth_date"):
        initial_lang = get_user_initial_language_code(message)
        # Убедимся, что user_id хранится как строка, если это новый пользователь для MongoDB
        user_id_str = str(user_id)
        
        # Создаем или обновляем запись пользователя
        if users_collection:
            # Проверяем наличие реферала, если команда /start с аргументом
            referrer_id = None
            if message.text and len(message.text.split()) > 1:
                potential_referrer_id = message.text.split()[1]
                if potential_referrer_id != user_id_str: # Чтобы пользователь не мог быть своим рефералом
                    referrer_exists = await users_collection.find_one({"_id": potential_referrer_id})
                    if referrer_exists:
                        referrer_id = potential_referrer_id
                        # Добавляем нового пользователя как реферала к рефереру
                        await users_collection.update_one(
                            {"_id": referrer_id},
                            {"$addToSet": {"referrals": user_id_str}}
                        )
                        logger.info(f"Пользователь {user_id_str} пришел по реферальной ссылке {referrer_id}")
                    else:
                        logger.warning(f"Реферер {potential_referrer_id} не найден.")
                else:
                    logger.warning(f"Пользователь {user_id_str} попытался быть своим собственным рефералом.")

            await users_collection.update_one(
                {"_id": user_id_str},
                {"$setOnInsert": { # Устанавливаем только при первой вставке
                    "username": message.from_user.username,
                    "first_name": message.from_user.first_name,
                    "last_name": message.from_user.last_name,
                    "registration_date": datetime.now(),
                    "balance": 0,
                    "referrals": [],
                    "referrer_id": referrer_id, # Устанавливаем реферера
                    "sign": user_data.get("sign", "aries"), # Берем из кэша, если уже есть
                    "lang": initial_lang,
                    "birth_date": user_data.get("birth_date", None)
                }},
                upsert=True # Вставить, если не существует
            )
            # Если это новый пользователь, попросим дату рождения
            if not user_data.get("birth_date"):
                await message.answer(
                    "Привет! Я Астро-бот. Отправь мне свою дату рождения в формате ДД.ММ.ГГГГ для получения гороскопа.",
                    reply_markup=await Keyboard.main_menu(user_id)
                )
                await message.answer("Для удобства, пожалуйста, укажите вашу дату рождения. Введите ее в формате ДД.ММ.ГГГГ (например, 01.01.2000).")
                await dp.get_current().fsm_context.set_state(Form.set_birth_date)
            else:
                await message.answer(
                    await get_text_async(user_id, "welcome"),
                    reply_markup=await Keyboard.main_menu(user_id),
                    parse_mode="HTML"
                )
        else:
            # Если MongoDB не подключена, просто отвечаем
            await message.answer(
                await get_text_async(user_id, "welcome"),
                reply_markup=await Keyboard.main_menu(user_id),
                parse_mode="HTML"
            )
            logger.warning(f"MongoDB не подключен. Функционал для пользователя {user_id} ограничен. Данные не сохраняются.")
    else:
        # Существующий пользователь
        await message.answer(
            await get_text_async(user_id, "welcome"),
            reply_markup=await Keyboard.main_menu(user_id),
            parse_mode="HTML"
        )


@dp.message(F.text.in_({TEXTS["ru"]["main_menu_horoscope"]}))
async def send_horoscope(message: types.Message):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    if not user_data.get("birth_date"):
        await message.answer("Для получения гороскопа, пожалуйста, сначала укажите вашу дату рождения в формате ДД.ММ.ГГГГ.")
        await dp.get_current().fsm_context.set_state(Form.set_birth_date)
        return

    horoscope = await HoroscopeGenerator.generate(user_id)

    bottom_buttons_builder = InlineKeyboardBuilder()
    bottom_buttons_builder.button(text=await get_text_async(user_id, "main_menu_support"), callback_data="show_donate_from_horoscope")
    
    me = await bot.get_me()
    bot_username = me.username
    share_text_encoded = SHARE_MESSAGE_RU.format(url=f"https://t.me/{bot_username}").replace(" ", "%20").replace("\n", "%0A")

    bottom_buttons_builder.button(
        text="💌 Поделиться ботом", 
        url=f"https://t.me/share/url?url=https://t.me/{bot_username}&text={share_text_encoded}"
    )
    
    bottom_buttons_builder.adjust(2)

    await message.answer(
        horoscope,
        parse_mode="HTML",
        reply_markup=bottom_buttons_builder.as_markup()
    )


@dp.message(F.text.in_({TEXTS["ru"]["main_menu_settings"]}))
async def settings_menu(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        await get_text_async(user_id, "settings_menu_choose"),
        reply_markup=await Keyboard.settings_menu(user_id)
    )

@dp.callback_query(F.data == "change_sign")
async def request_sign_change(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        await get_text_async(user_id, "choose_sign"),
        reply_markup=await Keyboard.sign_selection_menu(user_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("set_sign_"))
async def set_user_sign(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_sign = callback.data.split("_")[2]
    
    await update_user_data(user_id, "sign", new_sign)

    await callback.message.edit_text(
        (await get_text_async(user_id, "sign_set_success")).format(sign=await get_text_async(user_id, f"sign_{new_sign}")),
        parse_mode="HTML"
    )
    await callback.answer(await get_text_async(user_id, "sign_changed_answer"))

@dp.callback_query(F.data == "set_birth_date")
async def request_birth_date(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        await get_text_async(user_id, "birth_date_prompt")
    )
    await state.set_state(Form.set_birth_date)
    await callback.answer()

@dp.message(Form.set_birth_date)
async def process_birth_date(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    birth_date_str = message.text

    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", birth_date_str):
        await message.answer(await get_text_async(user_id, "birth_date_invalid_format"))
        return

    try:
        birth_date_obj = datetime.strptime(birth_date_str, "%d.%m.%Y")
        if birth_date_obj > datetime.now():
            await message.answer(await get_text_async(user_id, "birth_date_future_error"))
            return
        
        await update_user_data(user_id, "birth_date", birth_date_str)
        calculated_sign = HoroscopeGenerator.get_zodiac_sign(birth_date_obj)
        await update_user_data(user_id, "sign", calculated_sign)

        await message.answer(
            (await get_text_async(user_id, "birth_date_success")).format(birth_date=birth_date_str),
            parse_mode="HTML",
            reply_markup=await Keyboard.main_menu(user_id)
        )
        await state.clear()
    except ValueError:
        await message.answer(await get_text_async(user_id, "birth_date_invalid_format"))

@dp.callback_query(F.data == "change_language")
async def request_language_change(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        await get_text_async(user_id, "choose_language_prompt"),
        reply_markup=await Keyboard.language_selection_menu(user_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("set_lang_"))
async def set_user_language(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_lang = callback.data.split("_")[2]
    
    await update_user_data(user_id, "lang", new_lang)
    
    lang_name = "Русский" # Так как только RU

    await callback.message.edit_text(
        (await get_text_async(user_id, "language_set_success")).format(lang_name=lang_name),
        parse_mode="HTML"
    )
    # Отправляем новое сообщение с основным меню после изменения языка
    await callback.message.answer(
        await get_text_async(user_id, "welcome"),
        reply_markup=await Keyboard.main_menu(user_id),
        parse_mode="HTML"
    )
    await callback.answer(await get_text_async(user_id, "language_changed_answer"))

# Обработчик для кнопки "Поддержать нас" в главном меню
@dp.message(F.text.in_({TEXTS["ru"]["main_menu_support"]}))
@dp.callback_query(F.data == "show_donate_from_horoscope")
async def support_us_menu(update: types.Message | types.CallbackQuery):
    user_id = update.from_user.id
    
    message_to_edit = None
    if isinstance(update, types.CallbackQuery):
        message_to_edit = update.message
        await update.answer() # Отвечаем на колбэк

    builder = InlineKeyboardBuilder()
    builder.button(text=await get_text_async(user_id, "donate_open_wallet"), url="https://t.me/wallet")
    builder.button(text="📋 Копировать адрес TON", callback_data="copy_ton_wallet") 
    builder.button(text=await get_text_async(user_id, "donate_closed"), callback_data="close_donate_message")
    builder.adjust(1)

    text = (await get_text_async(user_id, "support_us_prompt")).format(wallet=TON_WALLET)

    if message_to_edit:
        await message_to_edit.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    else:
        await update.answer(
            text,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )

@dp.callback_query(F.data == "copy_ton_wallet")
async def copy_ton_wallet(callback: types.CallbackQuery):
    await callback.answer(f"Адрес кошелька TON:\n{TON_WALLET}\n\n(Нажмите на этот текст, чтобы скопировать)", show_alert=True)

@dp.callback_query(F.data == "close_donate_message")
async def close_donate_message(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    try:
        await callback.message.delete() # Удаляем сообщение с донатом
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение доната для {user_id}: {e}")
    await callback.answer(await get_text_async(user_id, "donate_closed"))


# --- Обработчики для развлекательных функций ---
@dp.message(F.text.in_({TEXTS["ru"]["main_menu_entertainment"]}))
async def entertainment_menu(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        await get_text_async(user_id, "entertainment_menu_choose"),
        reply_markup=await Keyboard.entertainment_menu(user_id)
    )

@dp.callback_query(F.data == "get_cookie_fortune")
async def get_cookie_fortune(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    fortune = random.choice(HoroscopeGenerator.COOKIE_FORTUNES)
    await callback.message.edit_text(
        (await get_text_async(user_id, "cookie_fortune_message")).format(fortune=fortune),
        parse_mode="HTML",
        reply_markup=None # Убираем кнопки, чтобы не загромождать
    )
    await callback.answer("Ваше предсказание готово!")

@dp.callback_query(F.data == "ask_magic_ball")
async def ask_magic_ball(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        await get_text_async(user_id, "magic_ball_question_prompt"),
        reply_markup=None # Убираем кнопки
    )
    await state.set_state(Form.magic_ball_answer)
    await callback.answer("Шар готов ответить!")

@dp.message(Form.magic_ball_answer)
async def process_magic_ball_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    question = message.text
    if not question.strip().endswith("?"):
        await message.answer(await get_text_async(user_id, "magic_ball_not_a_question"))
        return

    answer_type = random.choices(["positive", "negative", "neutral"], weights=[0.5, 0.3, 0.2], k=1)[0]
    answer = random.choice(HoroscopeGenerator.MAGIC_BALL_ANSWERS[answer_type])

    await message.answer(
        (await get_text_async(user_id, "magic_ball_answer_message")).format(answer=answer),
        parse_mode="HTML",
        reply_markup=await Keyboard.main_menu(user_id) # Возвращаем основное меню
    )
    await state.clear()


# Реклама
async def show_ads(user_id: int):
    try:
        if ADSGRAM_API_KEY and random.randint(1, 5) == 1:
            builder = InlineKeyboardBuilder()
            builder.button(text=await get_text_async(user_id, "ad_button"), url="https://example.com") # Замените на реальную ссылку

            await bot.send_message(
                user_id,
                await get_text_async(user_id, "ad_text"),
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        elif not ADSGRAM_API_KEY:
            logger.debug("ADSGRAM_API_KEY не установлен, реклама не показывается.")
    except Exception as e:
        logger.error(f"Ошибка при показе рекламы пользователю {user_id}: {e}")

# Ежедневная рассылка гороскопов (логика)
async def scheduled_tasks():
    logger.info("Запускаю запланированные задачи: отправка ежедневных гороскопов.")
    # Этот блок будет работать только если MONGO_URI задан и MongoDB подключена
    if users_collection:
        users_cursor = users_collection.find({})
        users_list = await users_cursor.to_list(length=None) # Получаем всех пользователей
        
        for user_doc in users_list:
            user_id = int(user_doc["_id"]) # Конвертируем обратно в int для aiogram
            try:
                horoscope = await HoroscopeGenerator.generate(user_id)
                
                bottom_buttons_builder = InlineKeyboardBuilder()
                bottom_buttons_builder.button(text=await get_text_async(user_id, "main_menu_support"), callback_data="show_donate_from_horoscope")
                
                me = await bot.get_me()
                bot_username = me.username
                share_text_encoded = SHARE_MESSAGE_RU.format(url=f"https://t.me/{bot_username}").replace(" ", "%20").replace("\n", "%0A")
                bottom_buttons_builder.button(
                    text="💌 Поделиться ботом", 
                    url=f"https://t.me/share/url?url=https://t.me/{bot_username}&text={share_text_encoded}"
                )
                bottom_buttons_builder.adjust(2)

                await bot.send_message(user_id, horoscope, parse_mode="HTML", reply_markup=bottom_buttons_builder.as_markup())
                await show_ads(user_id)
                await asyncio.sleep(0.1) # Небольшая задержка, чтобы не превышать лимиты Telegram API
            except Exception as e:
                logger.error(f"Ошибка при отправке гороскопа пользователю {user_id}: {e}", exc_info=True)
    else:
        logger.warning("MongoDB users_collection не инициализирована. Ежедневная рассылка гороскопов не будет работать.")

# --- HTTP-эндпоинт для cron-задачи ---
async def cron_job_handler(request: web.Request):
    CRON_SECRET_KEY = os.getenv("CRON_SECRET_KEY") # Убедитесь, что эта переменная задана на Render
    if not CRON_SECRET_KEY:
        logger.error("CRON_SECRET_KEY не установлен. Доступ к cron_job_handler запрещен.")
        return web.Response(status=403, text="Forbidden: CRON_SECRET_KEY not set.")

    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        logger.warning("Попытка доступа к /run_daily_horoscopes без Bearer токена.")
        return web.Response(status=401, text="Unauthorized: Bearer token missing.")

    provided_key = auth_header.split(' ')[1]
    if provided_key != CRON_SECRET_KEY:
        logger.warning(f"Неверный CRON_SECRET_KEY. Предоставлено: {provided_key[:5]}... Ожидалось: {CRON_SECRET_KEY[:5]}...")
        return web.Response(status=403, text="Forbidden: Invalid secret key.")

    logger.info("Cron job handler triggered!")
    # Вызываем вашу функцию рассылки гороскопов
    try:
        await scheduled_tasks()
        return web.Response(text="Cron job executed successfully!")
    except Exception as e:
        logger.error(f"Ошибка выполнения запланированных задач: {e}", exc_info=True)
        return web.Response(status=500, text=f"Internal Server Error: {e}")


# Функция для установки вебхука и инициализации БД (будет вызвана при деплое на Render)
async def on_startup(passed_bot: Bot) -> None:
    logger.info("Инициализация...")
    await init_mongodb() # Инициализируем MongoDB при старте
    logger.info("Установка вебхука...")
    if WEBHOOK_URL:
        try:
            await passed_bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
            logger.info(f"Вебхук установлен на: {WEBHOOK_URL}")
        except Exception as e:
            logger.error(f"Ошибка при установке вебхука: {e}", exc_info=True)
            # Не фатально, если вебхук уже установлен или есть временные проблемы
    else:
        logger.error("WEBHOOK_HOST не установлен. Вебхук не будет настроен. Убедитесь, что переменная окружения WEBHOOK_HOST задана на Render.")

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    telegram_update = types.Update(**await request.json())
    await dp.feed_update(bot=bot, update=telegram_update)
    return {'ok': True}

@app.get("/")
async def root():
    return {"message": "AstroX API is running"}


# Закрытие соединения с БД при завершении работы бота
async def on_shutdown(passed_bot: Bot) -> None:
    logger.info("Завершение работы бота...")
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB соединение закрыто.")


# --- ГЛАВНАЯ ТОЧКА ВХОДА ДЛЯ RENDER (WEBHOOK/AIOHTTP) И ЛОКАЛЬНОЙ РАЗРАБОТКИ (LONG-POLLING) ---
async def main():
    await on_startup(bot) # Выполняем инициализацию и устанавливаем вебхук

    # Регистрируем on_shutdown для корректного закрытия соединений
    dp.shutdown.register(on_shutdown)

    # Запускаем в режиме вебхука, если WEBHOOK_HOST и WEB_SERVER_PORT установлены
    if WEBHOOK_HOST and WEB_SERVER_PORT:
        logger.info(f"Запуск бота в режиме вебхука на порту {WEB_SERVER_PORT} (для Render).")

        web_app = web.Application()

        # --- НАСТРОЙКА WEBHOOK ДЛЯ AIOGRAM 3.X С AIOHTTP ---
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            handle_http_errors=True, # Рекомендуется для обработки ошибок
        )
        # Регистрируем обработчик вебхука по WEBHOOK_PATH
        webhook_requests_handler.register(web_app, path=WEBHOOK_PATH)
        # --- КОНЕЦ НАСТРОЙКИ WEBHOOK ---

        # Добавляем маршрут для cron-задачи
        web_app.add_routes([web.get('/run_daily_horoscopes', cron_job_handler)])
        
        # Дополнительная настройка приложения aiohttp (например, graceful shutdown)
        setup_application(web_app, dp, bot=bot) 

        runner = web.AppRunner(web_app)
        await runner.setup()
        
        # Запуск веб-сервера на всех доступных интерфейсах (0.0.0.0) и порту от Render
        site = web.TCPSite(runner, host='0.0.0.0', port=int(WEB_SERVER_PORT))
        await site.start()

        logger.info(f"Веб-сервер запущен на порту {WEB_SERVER_PORT}")
        # Держим сервер запущенным, предотвращая завершение
        await asyncio.Event().wait()
    else:
        logger.warning("Переменные окружения WEBHOOK_HOST или PORT не установлены. Запуск в режиме long-polling.")
        # Если WEBHOOK_HOST или WEB_SERVER_PORT не установлены (например, при локальном запуске),
        # запускаем long-polling
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
        except Exception as e:
            logger.error(f"Ошибка при запуске long-polling: {e}", exc_info=True)


if __name__ == "__main__":
    # Эта часть должна быть в самом конце файла для запуска main()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.error(f"Произошла фатальная ошибка при запуске: {e}", exc_info=True)
