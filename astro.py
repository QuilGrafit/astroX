# -*- coding: utf-8 -*-
import os
import logging
import asyncio
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Request
import uvicorn

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

from motor.motor_asyncio import AsyncIOMotorClient

# =================================================================================
# 1. КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# =================================================================================

# Настройка логирования для вывода информации в консоль
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

# Загрузка переменных окружения из файла .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN") # Например, 'astrox-mfuk.onrender.com'
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", 8080)) # Render предоставляет порт через переменную PORT

# Проверка наличия обязательных переменных
if not all([BOT_TOKEN, MONGO_URL, WEBHOOK_DOMAIN]):
    logging.critical("Одна или несколько переменных окружения (BOT_TOKEN, MONGO_URL, WEBHOOK_DOMAIN) не установлены.")
    exit()

# Пути для вебхука
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"
ALLOWED_UPDATES = ["message", "callback_query"]

# Инициализация объектов
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()
app = FastAPI()
db_client = None
db = None

# =================================================================================
# 2. РАБОТА С БАЗОЙ ДАННЫХ (MongoDB)
# =================================================================================

async def init_db():
    """Инициализирует подключение к базе данных MongoDB."""
    global db_client, db
    try:
        db_client = AsyncIOMotorClient(MONGO_URL)
        db = db_client.sample_mflix # Используем базу данных sample_mflix
        # Проверка соединения
        await db_client.admin.command('ping')
        logging.info("Успешное подключение к MongoDB.")
    except Exception as e:
        logging.error(f"Не удалось подключиться к MongoDB: {e}")
        raise

async def close_db():
    """Закрывает соединение с базой данных."""
    if db_client:
        db_client.close()
        logging.info("Соединение с MongoDB закрыто.")

async def get_user_data(user_id: int):
    """Получает данные пользователя из коллекции 'users'."""
    if db:
        return await db.users.find_one({"user_id": user_id})
    return None

async def update_user_data(user_id: int, data: dict):
    """Обновляет или создает данные пользователя в коллекции 'users'."""
    if db:
        await db.users.update_one({"user_id": user_id}, {"$set": data}, upsert=True)

# =================================================================================
# 3. ЛОГИКА ЖИЗНЕННОГО ЦИКЛА И ВЕБХУКА (FastAPI)
# =================================================================================

@app.on_event("startup")
async def on_startup():
    """Выполняется при старте сервера."""
    await init_db()
    logging.info("Установка вебхука...")
    await bot.set_webhook(
        url=WEBHOOK_URL,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=True # Удаляем накопившиеся обновления
    )
    logging.info(f"Вебхук успешно установлен на: {WEBHOOK_URL}")

@app.on_event("shutdown")
async def on_shutdown():
    """Выполняется при остановке сервера."""
    logging.info("Удаление вебхука...")
    await bot.delete_webhook()
    await close_db()
    logging.info("Вебхук удален. Сервер остановлен.")

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    """
    Принимает входящие обновления от Telegram,
    преобразует их и передает в диспетчер aiogram.
    """
    try:
        telegram_update = types.Update(**await request.json())
        await dp.feed_update(bot=bot, update=telegram_update)
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка при обработке вебхук-запроса: {e}")
        return {"ok": False, "error": str(e)}, 500

@app.get("/")
async def root():
    """Корневой роут для проверки работы сервера."""
    return {"message": "AstroX Bot API is running"}

# =================================================================================
# 4. КЛАВИАТУРЫ
# =================================================================================
# Здесь собраны все ваши функции для создания клавиатур

def get_main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔮 Гадание", callback_data="divination_menu")],
        [InlineKeyboardButton(text="✨ Персональный гороскоп", callback_data="personal_horoscope")],
        [InlineKeyboardButton(text="🌟 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="⭐ Оценить бота", callback_data="rate_bot")]
    ])

def get_divination_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Карта дня", callback_data="card_of_the_day")],
        [InlineKeyboardButton(text="Да или Нет", callback_data="yes_no")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")]
    ])

# ... и так далее для всех остальных ваших клавиатур ...
# Я не буду дублировать их все здесь, предполагая, что они у вас уже есть.
# Просто убедитесь, что они определены в этой секции.

# =================================================================================
# 5. МАШИНЫ СОСТОЯНИЙ (FSM)
# =================================================================================

class UserProfile(StatesGroup):
    waiting_for_name = State()
    waiting_for_dob = State()
    waiting_for_gender = State()

# =================================================================================
# 6. ОБРАБОТЧИКИ AIOGRAM (Хендлеры)
# =================================================================================

# --- Обработчики команд ---

@dp.message(Command("start"))
async def handle_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    
    if user_data:
        await message.answer(f"С возвращением, {user_data.get('name', 'Пользователь')}!", reply_markup=get_main_menu_keyboard())
    else:
        # Начинаем процесс регистрации
        await state.set_state(UserProfile.waiting_for_name)
        await message.answer("Добро пожаловать в AstroX! Давайте познакомимся. Как вас зовут?")

# --- Обработчики FSM для регистрации ---

@dp.message(UserProfile.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(UserProfile.waiting_for_dob)
    await message.answer("Приятно познакомиться! Теперь введите вашу дату рождения в формате ДД.ММ.ГГГГ (например, 21.06.2025).")

@dp.message(UserProfile.waiting_for_dob)
async def process_dob(message: Message, state: FSMContext):
    try:
        # Простая валидация формата даты
        datetime.strptime(message.text, "%d.%m.%Y")
        await state.update_data(dob=message.text)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Мужской", callback_data="gender_male")],
            [InlineKeyboardButton(text="Женский", callback_data="gender_female")]
        ])
        await state.set_state(UserProfile.waiting_for_gender)
        await message.answer("Отлично. Теперь выберите ваш пол.", reply_markup=keyboard)
    except ValueError:
        await message.answer("Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ.")

@dp.callback_query(StateFilter(UserProfile.waiting_for_gender))
async def process_gender(callback_query: CallbackQuery, state: FSMContext):
    gender = "Мужской" if callback_query.data == "gender_male" else "Женский"
    user_data = await state.get_data()
    
    # Сохраняем все данные в БД
    final_user_data = {
        "user_id": callback_query.from_user.id,
        "username": callback_query.from_user.username,
        "name": user_data.get('name'),
        "dob": user_data.get('dob'),
        "gender": gender,
        "registration_date": datetime.now()
    }
    await update_user_data(callback_query.from_user.id, final_user_data)
    
    await state.clear() # Завершаем FSM
    await callback_query.message.edit_text("Регистрация завершена! Добро пожаловать в мир звезд ✨")
    await callback_query.message.answer("Вот ваше главное меню:", reply_markup=get_main_menu_keyboard())
    await callback_query.answer()


# --- Обработчики колбэков (нажатий на инлайн-кнопки) ---

@dp.callback_query(lambda c: c.data == 'main_menu')
async def go_to_main_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text("Главное меню:", reply_markup=get_main_menu_keyboard())
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == 'divination_menu')
async def show_divination_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text("Выберите тип гадания:", reply_markup=get_divination_menu_keyboard())
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == 'card_of_the_day')
async def get_card_of_the_day(callback_query: CallbackQuery):
    # Здесь должна быть ваша логика для гадания "Карта дня"
    await callback_query.answer("Вытягиваем карту дня...", show_alert=True)
    # Пример ответа
    await callback_query.message.edit_text("Ваша карта дня: 'Солнце'. Это означает успех и радость! \n\nХотите еще гадание?", reply_markup=get_divination_menu_keyboard())

@dp.callback_query(lambda c: c.data == 'profile')
async def show_profile(callback_query: CallbackQuery):
    user_data = await get_user_data(callback_query.from_user.id)
    if user_data:
        profile_text = (
            f"👤 **Ваш профиль**\n\n"
            f"**Имя:** {user_data.get('name', 'Не указано')}\n"
            f"**Дата рождения:** {user_data.get('dob', 'Не указана')}\n"
            f"**Пол:** {user_data.get('gender', 'Не указан')}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="main_menu")]])
        await callback_query.message.edit_text(profile_text, reply_markup=keyboard)
    else:
        await callback_query.answer("Не удалось найти ваш профиль. Попробуйте перезапустить бота /start", show_alert=True)
    await callback_query.answer()


# Добавьте сюда все остальные ваши обработчики...
# ...


# =================================================================================
# 7. ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА
# =================================================================================

def main():
    """Основная точка входа для запуска веб-сервера."""
    logging.info("Запуск веб-сервера Uvicorn...")
    uvicorn.run(
        app,
        host=WEB_SERVER_HOST,
        port=WEB_SERVER_PORT
    )

if __name__ == "__main__":
    main()
