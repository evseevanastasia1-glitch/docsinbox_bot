import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update,
)

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

# Render обычно даёт внешний URL в переменной RENDER_EXTERNAL_URL.
# Если вдруг нет — задай WEBHOOK_BASE вручную.
WEBHOOK_BASE = (os.getenv("RENDER_EXTERNAL_URL", "").strip() or os.getenv("WEBHOOK_BASE", "").strip()).rstrip("/")
if not WEBHOOK_BASE:
    raise RuntimeError("Нет WEBHOOK_BASE/RENDER_EXTERNAL_URL. Задай WEBHOOK_BASE в Render.")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

# --- BOT / DP ---
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())


# -------------------- КНОПКИ --------------------
def kb_expectations() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет"), KeyboardButton(text="⚖️ Частично")],
        ],
        resize_keyboard=True,
    )


def kb_reasons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1. Долгое подключение поставщиков", callback_data="r:1")],
            [InlineKeyboardButton(text="2. Тех.поддержка", callback_data="r:2")],
            [InlineKeyboardButton(text="3. Функционал", callback_data="r:3")],
            [InlineKeyboardButton(text="4. Внедрение", callback_data="r:4")],
            [InlineKeyboardButton(text="5. Другое", callback_data="r:5")],
        ]
    )


def kb_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip")]]
    )


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
def now_str() -> str:
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
        # как и просили — если не нашли цифры, сохраняем "как есть"
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
@dp.message(CommandStart())
@dp.message(Command("restart"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Добрый день!\n\n"
        "Пожалуйста, оцените ваши впечатления от внедрения DocsInBox.\n"
        "Оправдал ли сервис ваши ожидания? ☺️",
        reply_markup=kb_expectations(),
    )
    await state.set_state(FeedbackFSM.expectations)


@dp.message(FeedbackFSM.expectations, F.text)
async def on_expectations(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt not in ["✅ Да", "❌ Нет", "⚖️ Частично"]:
        await message.answer("Пожалуйста, выберите вариант кнопкой ниже 🙂", reply_markup=kb_expectations())
        return

    await state.update_data(expectations=txt)
    await message.answer("Спасибо!\nОцените сервис по шкале от 0 до 10", reply_markup=ReplyKeyboardRemove())
    await state.set_state(FeedbackFSM.rating)


@dp.message(FeedbackFSM.rating, F.text)
async def on_rating(message: Message, state: FSMContext):
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
    await state.set_state(FeedbackFSM.reason)


@dp.callback_query(FeedbackFSM.reason, F.data.startswith("r:"))
async def on_reason(call: CallbackQuery, state: FSMContext):
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

    await state.set_state(FeedbackFSM.comment)


@dp.callback_query(FeedbackFSM.comment, F.data == "skip")
async def skip(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(comment="")
    await ask_inn(call.message, state)


@dp.message(FeedbackFSM.comment, F.text)
async def on_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    reason = data.get("reason", "")
    comment = (message.text or "").strip()

    if reason == REASONS["5"] and not comment:
        await message.answer("Для пункта «Другое» нужен комментарий 🙂 Напишите, пожалуйста, пару слов.")
        return

    await state.update_data(comment=comment)
    await ask_inn(message, state)


async def ask_inn(message: Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, укажите ИНН (или ИНН/КПП, если есть), чтобы мы могли корректно идентифицировать компанию.\n"
        "Можно писать в любом формате: например, «ИНН 770... КПП 770...», «770.../770...», «770... 770...».",
    )
    await state.set_state(FeedbackFSM.innkpp)


@dp.message(FeedbackFSM.innkpp, F.text)
async def on_inn(message: Message, state: FSMContext):
    inn, kpp = extract_inn_kpp(message.text)
    await finalize(message, state, inn=inn, kpp=kpp)


async def finalize(message: Message, state: FSMContext, inn: str = "", kpp: str = ""):
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

    await state.clear()

    # Твоя финальная фраза (как просила раньше — можно поменять тут при желании)
    await message.answer(
        "Спасибо за обратную связь! 🙏 Ваша оценка поможет нам стать лучше!",
        reply_markup=ReplyKeyboardRemove(),
    )


# -------------------- WEB APP (Webhook + Health) --------------------
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()

        # ✅ правильно для aiogram 2.x
        update = types.Update.to_object(data)

        await dp.feed_update(bot, update)
    except Exception:
        logging.exception("Webhook handler crashed")

    return web.Response(text="ok")


async def health(_request: web.Request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    # на всякий — перезаписываем webhook
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook set to %s", WEBHOOK_URL)


async def on_cleanup(app: web.Application):
    # закрываем aiohttp-сессию бота корректно (без депрекейшн-варнинга)
    try:
        session = await bot.get_session()
        await session.close()
    except Exception:
        logging.exception("Failed to close bot session")


async def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    logging.info("Running on http://0.0.0.0:%s", PORT)

    # держим процесс живым
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
