import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from aiohttp import web

# -------------------- НАСТРОЙКИ --------------------
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1Mkdpte7ILplqPisRQP98lXFLFEGrdcEY1gRd2iPGzuU",
).strip()

GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "Лист1").strip()

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

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
    if text.isdigit():
        v = int(text)
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
    nums = re.findall(r"\d+", text)
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
        return text.strip(), ""
    return inn, kpp


# -------------------- Google Sheets --------------------
def get_sheets_service():
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
            body={"values": [row]},
        ).execute()

    await asyncio.to_thread(_write)


# -------------------- ХЭНДЛЕРЫ --------------------
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


@dp.message_handler(state=FeedbackFSM.expectations)
async def on_expectations(message: types.Message, state: FSMContext):
    await state.update_data(expectations=message.text)
    await message.answer(
        "Спасибо!\nОцените сервис по шкале от 0 до 10",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await FeedbackFSM.rating.set()


@dp.message_handler(state=FeedbackFSM.rating)
async def on_rating(message: types.Message, state: FSMContext):
    rating = parse_rating(message.text)
    if rating is None:
        await message.answer("Введите число от 0 до 10")
        return

    await state.update_data(rating=rating)

    if rating >= 9:
        await finalize(message, state, "", "", "", "")
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


@dp.callback_query_handler(lambda c: c.data.startswith("r:"), state=FeedbackFSM.reason)
async def on_reason(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":")[1]
    await state.update_data(reason=REASONS[code])
    await call.answer()

    if code == "5":
        await call.message.edit_text("Пожалуйста, напишите комментарий (обязательно):")
    else:
        await call.message.edit_text(
            "Если хотите — оставьте комментарий.\nИли нажмите «Пропустить».",
            reply_markup=kb_skip(),
        )
    await FeedbackFSM.comment.set()


@dp.callback_query_handler(lambda c: c.data == "skip", state=FeedbackFSM.comment)
async def skip(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(comment="")
    await ask_inn(call.message, state)


@dp.message_handler(state=FeedbackFSM.comment)
async def on_comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await ask_inn(message, state)


async def ask_inn(message: types.Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, укажите ИНН (или ИНН/КПП, если есть).\n"
        "Можно писать в любом формате.",
    )
    await FeedbackFSM.innkpp.set()


@dp.message_handler(state=FeedbackFSM.innkpp)
async def on_inn(message: types.Message, state: FSMContext):
    inn, kpp = extract_inn_kpp(message.text)
    await finalize(message, state, inn, kpp)


async def finalize(message, state, inn="", kpp="", *_):
    data = await state.get_data()
    rating = data["rating"]

    row = [
        now_str(),
        str(message.from_user.id),
        data.get("expectations", ""),
        rating,
        data.get("reason", ""),
        data.get("comment", ""),
        inn,
        kpp,
        churn_risk(rating),
    ]

    asyncio.create_task(append_row(row))

    await state.finish()
    await message.answer(
        "Спасибо за обратную связь, ваше мнение поможет нам стать лучше 💙",
        reply_markup=kb_expectations(),
    )
    await FeedbackFSM.expectations.set()


# -------------------- ЗАПУСК --------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
