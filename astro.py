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

# --- Добавляем необходимые импорты для aiohttp и aiogram webhook ---
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from urllib.parse import urlparse # Добавлено для парсинга URL
# --- Конец новых импортов ---

# --- Загрузка переменных окружения для локальной разработки ---
load_dotenv()

# Настройки (теперь читаются из переменных окружения для Vercel)
TOKEN = os.getenv("BOT_TOKEN")
TON_WALLET = os.getenv("TON_WALLET_ADDRESS")
ADSGRAM_API_KEY = os.getenv("ADSGRAM_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "AstroBotDB")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "users")

# Новые переменные для вебхука на Render
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
# Render предоставляет порт через переменную окружения PORT
WEB_SERVER_PORT = os.getenv('PORT', 8080) # Используем PORT, по умолчанию 8080
WEBHOOK_URL = f"https://{WEBHOOK_HOST}/webhook/{TOKEN}" if WEBHOOK_HOST else None
# Путь для вебхука (часть URL после домена)
WEBHOOK_PATH = f"/webhook/{TOKEN}" # Это обычно используется, или можно парсить из WEBHOOK_URL

# Проверка наличия обязательных переменных окружения
if not TOKEN:
    raise ValueError("Environment variable BOT_TOKEN is not set.")
if not TON_WALLET:
    raise ValueError("Environment variable TON_WALLET_ADDRESS is not set.")
# MONGO_URI обязателен, если вы хотите использовать MongoDB
if not MONGO_URI:
    logging.warning("Environment variable MONGO_URI is not set. MongoDB will not be used.")
    mongo_client = None
    db = None
    users_collection = None
else:
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URI)
        db = mongo_client[MONGO_DB_NAME]
        users_collection = db[MONGO_COLLECTION_NAME]
        logging.info(f"MongoDB успешно подключен к базе данных '{MONGO_DB_NAME}'")
    except Exception as e:
        logging.error(f"Ошибка подключения к MongoDB: {e}")
        mongo_client = None
        db = None
        users_collection = None

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
storage = MemoryStorage() # Используем MemoryStorage, если не хотите использовать Redis/MongoDB для состояний
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=storage)

# --- Ваши обработчики и логика бота (опущены для краткости) ---
# Например, у вас должны быть определены хэндлеры, как:
# @dp.message(Command('start'))
# async def command_start_handler(message: types.Message) -> None:
#     await message.answer("Hello!")

# --- Ваша функция cron_job_handler (обязательно должна быть определена) ---
async def cron_job_handler(request: web.Request):
    # Проверяем секретный ключ
    CRON_SECRET_KEY = os.getenv("CRON_SECRET_KEY")
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
    # Здесь должна быть логика вашей ежедневной рассылки гороскопов
    # Например:
    # await send_daily_horoscopes_to_all_users(bot, users_collection)
    logger.info("Daily horoscope sending logic should be here.")
    return web.Response(text="Cron job executed successfully!")


# --- Функции on_startup и on_shutdown ---
async def on_startup(passed_bot: Bot) -> None:
    logger.info("Инициализация...")
    if WEBHOOK_URL:
        # Устанавливаем вебхук в Telegram
        await passed_bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info(f"Вебхук установлен на: {WEBHOOK_URL}")
    else:
        logger.warning("WEBHOOK_HOST не установлен. Вебхук не будет настроен. Бот будет работать в режиме long-polling.")

async def on_shutdown(passed_bot: Bot) -> None:
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB соединение закрыто.")

# --- Ваша исправленная функция main() ---
async def main():
    await on_startup(bot)

    # Регистрируем on_shutdown для корректного закрытия соединений
    dp.shutdown.register(on_shutdown)

    # Используем WEBHOOK_HOST и WEB_SERVER_PORT для режима вебхука
    if WEBHOOK_HOST and WEB_SERVER_PORT:
        logger.info(f"Запуск бота в режиме вебхука на порту {WEB_SERVER_PORT} (для Render).")

        web_app = web.Application()

        # Правильная настройка вебхука для aiogram 3.x
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            handle_http_errors=True, # Рекомендуется для обработки ошибок
        )
        webhook_requests_handler.register(web_app, path=WEBHOOK_PATH)

        # Добавляем маршрут для cron-задачи
        web_app.add_routes([web.get('/run_daily_horoscopes', cron_job_handler)])

        setup_application(web_app, dp, bot=bot) # Дополнительная настройка (graceful shutdown)

        runner = web.AppRunner(web_app)
        await runner.setup()
        
        # Передаем порт как int
        site = web.TCPSite(runner, host='0.0.0.0', port=int(WEB_SERVER_PORT))
        await site.start()

        logger.info(f"Веб-сервер запущен на порту {WEB_SERVER_PORT}")
        await asyncio.Event().wait() # Держит сервер запущенным
    else:
        logger.error("Переменные окружения WEBHOOK_HOST или PORT не установлены. Невозможно запустить в режиме вебхука.")
        logger.info("Запуск бота в режиме long-polling (для локальной разработки).")
        # Если WEBHOOK_HOST или WEB_SERVER_PORT не установлены, запускаем long-polling
        await dp.start_polling(bot)

if __name__ == "__main__":
    # Убедитесь, что эта часть находится в конце файла
    # и запускает main()
    asyncio.run(main())
