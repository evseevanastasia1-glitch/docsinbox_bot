import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


logging.basicConfig(level=logging.INFO)

# --- ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip() or "1Mkdpte7ILplqPisRQP98lXFLFEGrdcEY1gRd2iPGzuU"
GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "").strip() or "Лист1"
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

PORT = int(os.getenv("PORT", "10000"))

# Render может НЕ проставлять RENDER_EXTERNAL_URL автоматически, поэтому держим WEBHOOK_BASE.
# Пример: WEBHOOK_BASE = https://docsinbox-bot.onrender.com
WEBHOOK_BASE = (os.getenv("RENDER_EXTERNAL_URL", "").strip() or os.getenv("WEBHOOK_BASE", "").strip()).rstrip("/")
if not WEBHOOK_BASE:
    raise RuntimeError("Нет WEBHOOK_BASE/RENDER_EXTERNAL_URL. Задай WEBHOOK_BASE в Render.")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())


# -------------------- КНОПКИ --------------------
def kb_expectations():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✅ Да", "❌ Нет", "⚖️ Частично")
    return kb


def kb_reasons():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("1. Долгое подключение поставщиков", callback_data="r:1"),
        types.InlineKeyboardButton("2. Тех.поддержка", callback_data="r:2"),
        types.InlineKeyboardButton("3. Функционал", callback_data="r:3"),
        types.InlineKeyboardButton("4. Внедрение", callback_data="r:4"),
        types.InlineKeyboardButton("5. Другое", callback_data="r:5"),
    )
    return kb


def kb_skip():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Пропустить", callback_data="skip"))
    return kb


REASONS = {
    "1": "Долгое подключение поставщиков",
    "2": "Тех.поддержка",
    "3": "Функционал",
    "4": "Внедрение",
    "5": "Другое",
}


# -------------------- FSM --------------------
class FeedbackFSM(StatesGroup):
    expectations = State()
    rating = State()
    reason = State()
    comment = State()
    innkpp = State()


# -------------------- УТИЛИТЫ --------------------
def now_str():
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_rating(text: str) -> Optional[int]:
    t = (text or "").strip()
    if t.isdigit():
        v = int(t)
        if 0 <= v <= 10:
            return v
    return None


def churn_risk(rating: int) -> str:
    if rating >= 9:
        return "5–10%"
    if rating >= 7:
        return "25–40%"
    if rating >= 5:
        return "50–70%"
    return "80%+"


def extract_inn_kpp(text: str) -> Tuple[str, str]:
    """
    Принимаем ИНН/КПП "как угодно":
    - если нашли числа длиной 10/12 -> считаем ИНН
    - если нашли число длиной 9 -> считаем КПП
    - если вообще ничего не нашли -> кладём исходную строку в ИНН, КПП пустой
    """
    raw = (text or "").strip()
    nums = re.findall(r"\d+", raw)
    inn = ""
    kpp = ""

    for n in nums:
        if len(n) in (10, 12):
            inn = n
            break

    for n in nums:
        if len(n) == 9 and n != inn:
            kpp = n
            break

    if not inn and not kpp:
        return raw, ""

    return inn, kpp


# -------------------- Google Sheets --------------------
def get_sheets_service():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON не задан")

    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


async def append_row(row: list):
    def _write():
        service = get_sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{GOOGLE_SHEET_WORKSHEET}!A:I",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    await asyncio.to_thread(_write)


# -------------------- ХЭНДЛЕРЫ БОТА --------------------
@dp.message_handler(commands=["start", "restart"], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        "Добрый день!\n\n"
        "Пожалуйста, оцените ваши впечатления от внедрения DocsInBox.\n"
        "Оправдал ли сервис ваши ожидания? ☺️",
        reply_markup=kb_expectations(),
    )
    await FeedbackFSM.expectations.set()


@dp.message_handler(state=FeedbackFSM.expectations, content_types=types.ContentTypes.TEXT)
async def on_expectations(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt not in ["✅ Да", "❌ Нет", "⚖️ Частично"]:
        await message.answer("Пожалуйста, выберите вариант кнопкой ниже 🙂", reply_markup=kb_expectations())
        return

    await state.update_data(expectations=txt)
    await message.answer("Спасибо!\nОцените сервис по шкале от 0 до 10", reply_markup=types.ReplyKeyboardRemove())
    await FeedbackFSM.rating.set()


@dp.message_handler(state=FeedbackFSM.rating, content_types=types.ContentTypes.TEXT)
async def on_rating(message: types.Message, state: FSMContext):
    rating = parse_rating(message.text)
    if rating is None:
        await message.answer("Введите число от 0 до 10")
        return

    await state.update_data(rating=rating)

    # 9–10: ИНН/КПП НЕ спрашиваем
    if rating >= 9:
        await message.answer("Спасибо за высокую оценку и что выбрали нас! ❤️")
        await finalize(message, state, inn="", kpp="")
        return

    if rating >= 7:
        await message.answer("Спасибо за оценку!\nПодскажите, пожалуйста, что пошло не так.")
    else:
        await message.answer(
            "Нам очень жаль, что сервис не полностью оправдал ваши ожидания 😔\n"
            "Подскажите, пожалуйста, что пошло не так."
        )

    await message.answer("Выберите причину:", reply_markup=kb_reasons())
    await FeedbackFSM.reason.set()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("r:"), state=FeedbackFSM.reason)
async def on_reason(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(reason=REASONS.get(code, ""))
    await call.answer()

    if code == "5":
        await call.message.edit_text("Пожалуйста, напишите комментарий (для пункта «Другое» он обязателен):")
    else:
        await call.message.edit_text(
            "Если хотите — оставьте комментарий (необязательно).\nИли нажмите «Пропустить».",
            reply_markup=kb_skip(),
        )
    await FeedbackFSM.comment.set()


@dp.callback_query_handler(lambda c: c.data == "skip", state=FeedbackFSM.comment)
async def skip(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(comment="")
    await ask_inn(call.message, state)


@dp.message_handler(state=FeedbackFSM.comment, content_types=types.ContentTypes.TEXT)
async def on_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    reason = data.get("reason", "")
    comment = (message.text or "").strip()

    if reason == REASONS["5"] and not comment:
        await message.answer("Для пункта «Другое» нужен комментарий 🙂 Напишите, пожалуйста, пару слов.")
        return

    await state.update_data(comment=comment)
    await ask_inn(message, state)


async def ask_inn(message: types.Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, укажите ИНН (или ИНН/КПП, если есть), чтобы мы могли корректно идентифицировать компанию.\n"
        "Можно писать в любом формате: например, «ИНН 770... КПП 770...», «770.../770...», «770... 770...».",
    )
    await FeedbackFSM.innkpp.set()


@dp.message_handler(state=FeedbackFSM.innkpp, content_types=types.ContentTypes.TEXT)
async def on_inn(message: types.Message, state: FSMContext):
    inn, kpp = extract_inn_kpp(message.text)
    await finalize(message, state, inn=inn, kpp=kpp)


async def finalize(message: types.Message, state: FSMContext, inn: str = "", kpp: str = ""):
    data = await state.get_data()
    rating = int(data.get("rating", 0))

    row = [
        now_str(),                    # Дата
        str(message.from_user.id),     # Telegram ID
        data.get("expectations", ""),  # Ожидания
        rating,                        # Оценка
        data.get("reason", ""),        # Причина
        data.get("comment", ""),       # Комментарий
        inn,                           # ИНН
        kpp,                           # КПП
        churn_risk(rating),            # Риск оттока
    ]

    # запись в Google Sheets (в фоне)
    asyncio.create_task(append_row(row))

    await state.finish()

    await message.answer(
        "Спасибо за обратную связь! 🙏 Ваша оценка поможет нам стать лучше!",
        reply_markup=types.ReplyKeyboardRemove(),
    )


# -------------------- WEB APP (Webhook + Health) --------------------
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()

        # ✅ aiogram 2.x
        update = types.Update.to_object(data)

        # ✅ чтобы FSM работал в webhook-режиме
        Bot.set_current(bot)
        Dispatcher.set_current(dp)

        await dp.process_update(update)
    except Exception:
        logging.exception("Webhook handler crashed")

    return web.Response(text="ok")


async def health(_request: web.Request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook set to %s", WEBHOOK_URL)


import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "Лист1")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

PORT = int(os.getenv("PORT", "10000"))

WEBHOOK_BASE = (
    os.getenv("RENDER_EXTERNAL_URL", "")
    or os.getenv("WEBHOOK_BASE", "")
).rstrip("/")

if not WEBHOOK_BASE:
    raise RuntimeError("WEBHOOK_BASE / RENDER_EXTERNAL_URL is missing")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

TZ = ZoneInfo("Europe/Warsaw")

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# ---------- FSM ----------
class FeedbackFSM(StatesGroup):
    expectations = State()
    rating = State()
    reason = State()
    comment = State()
    innkpp = State()

# ---------- KEYBOARDS ----------
def kb_expectations():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✅ Да", "❌ Нет", "⚖️ Частично")
    return kb

def kb_reasons():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("1. Долгое подключение поставщиков", callback_data="r:1"),
        types.InlineKeyboardButton("2. Техподдержка", callback_data="r:2"),
        types.InlineKeyboardButton("3. Функционал", callback_data="r:3"),
        types.InlineKeyboardButton("4. Внедрение", callback_data="r:4"),
        types.InlineKeyboardButton("5. Другое", callback_data="r:5"),
    )
    return kb

def kb_skip():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Пропустить", callback_data="skip"))
    return kb

REASONS = {
    "1": "Долгое подключение поставщиков",
    "2": "Техподдержка",
    "3": "Функционал",
    "4": "Внедрение",
    "5": "Другое",
}

# ---------- UTILS ----------
def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def extract_inn_kpp(text: str) -> Tuple[str, str]:
    nums = re.findall(r"\d+", text or "")
    inn = next((n for n in nums if len(n) in (10, 12)), "")
    kpp = next((n for n in nums if len(n) == 9 and n != inn), "")
    return inn or text, kpp

# ---------- GOOGLE SHEETS ----------
def get_sheets():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

async def append_row(row):
    def _write():
        service = get_sheets()
        service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{GOOGLE_SHEET_WORKSHEET}!A:I",
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

    await asyncio.to_thread(_write)

# ---------- HANDLERS ----------
@dp.message_handler(commands=["start"], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        "Добрый день!\n\nОправдал ли сервис ваши ожидания?",
        reply_markup=kb_expectations(),
    )
    await FeedbackFSM.expectations.set()

@dp.message_handler(state=FeedbackFSM.expectations)
async def expectations(message: types.Message, state: FSMContext):
    await state.update_data(expectations=message.text)
    await message.answer("Оцените сервис от 0 до 10", reply_markup=types.ReplyKeyboardRemove())
    await FeedbackFSM.rating.set()

@dp.message_handler(state=FeedbackFSM.rating)
async def rating(message: types.Message, state: FSMContext):
    await state.update_data(rating=message.text)
    await message.answer("Выберите причину:", reply_markup=kb_reasons())
    await FeedbackFSM.reason.set()

@dp.callback_query_handler(lambda c: c.data.startswith("r:"), state=FeedbackFSM.reason)
async def reason(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(reason=REASONS.get(call.data[2:], ""))
    await call.message.answer("Комментарий (или пропустить):", reply_markup=kb_skip())
    await FeedbackFSM.comment.set()

@dp.callback_query_handler(lambda c: c.data == "skip", state=FeedbackFSM.comment)
async def skip(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(comment="")
    await call.message.answer("ИНН / КПП:")
    await FeedbackFSM.innkpp.set()

@dp.message_handler(state=FeedbackFSM.comment)
async def comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("ИНН / КПП:")
    await FeedbackFSM.innkpp.set()

@dp.message_handler(state=FeedbackFSM.innkpp)
async def finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    inn, kpp = extract_inn_kpp(message.text)

    row = [
        now_str(),
        message.from_user.id,
        data.get("expectations"),
        data.get("rating"),
        data.get("reason"),
        data.get("comment"),
        inn,
        kpp,
    ]

    asyncio.create_task(append_row(row))
    await state.finish()
    await message.answer("Спасибо за обратную связь! 🙏")

# ---------- WEB ----------
async def webhook(request):
    data = await request.json()
    update = types.Update.to_object(data)

    Bot.set_current(bot)
    Dispatcher.set_current(dp)

    await dp.process_update(update)
    return web.Response(text="ok")

async def health(_):
    return web.Response(text="ok")

async def on_startup(app):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook set to %s", WEBHOOK_URL)

def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook)
    app.router.add_get("/", health)
    app.on_startup.append(on_startup)
    web.run_app(app, port=PORT)

if __name__ == "__main__":
    main()
