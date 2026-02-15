import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


logging.basicConfig(level=logging.INFO)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
if not GOOGLE_SHEET_ID:
    raise RuntimeError("GOOGLE_SHEET_ID не задан")

GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "").strip() or "Лист1"
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
if not GOOGLE_SERVICE_ACCOUNT_JSON:
    raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON не задан")

PORT = int(os.getenv("PORT", "10000"))

# Render может не проставлять RENDER_EXTERNAL_URL автоматически — оставляем WEBHOOK_BASE как запасной вариант.
WEBHOOK_BASE = (os.getenv("RENDER_EXTERNAL_URL", "").strip() or os.getenv("WEBHOOK_BASE", "").strip()).rstrip("/")
if not WEBHOOK_BASE:
    raise RuntimeError(
        "Нет WEBHOOK_BASE/RENDER_EXTERNAL_URL. "
        "Задай WEBHOOK_BASE в Render (например https://xxx.onrender.com)"
    )

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

TZ = ZoneInfo("Europe/Warsaw")  # можно поменять, если нужно

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# -------------------- ТЕКСТЫ --------------------
TXT_START = (
    "Добрый день!\n"
    "Оправдал ли сервис DocsInBox ваши ожидания? ☺️"
)

TXT_YES_ASK_COMMENT = (
    "Нам очень приятно это слышать 💙\n"
    "Если у вас есть идеи или предложения по улучшению — будем рады обратной связи.\n"
    "Можно написать комментарий или нажать «Пропустить»."
)

TXT_YES_FINAL = "Спасибо за доверие и что выбрали DocsInBox 🙏"

TXT_NO_ASK_REASON = (
    "Нам жаль, что сервис не оправдал ожидания 😔\n"
    "Подскажите, пожалуйста, что пошло не так?"
)

TXT_OTHER_MANDATORY_COMMENT = "Пожалуйста, напишите комментарий — это обязательное поле."

TXT_OPT_COMMENT = (
    "При необходимости вы можете уточнить детали.\n"
    "Или нажмите «Пропустить»."
)

# Важно: без обещаний "оперативно свяжемся" — это для идентификации/статистики
TXT_ID_REQUIRED = (
    "Для корректной обработки обратной связи, пожалуйста, укажите\n"
    "ИНН или номер телефона компании.\n\n"
    "Достаточно одного из вариантов."
)

TXT_ID_INVALID = (
    "Пожалуйста, укажите корректный ИНН (10 или 12 цифр)\n"
    "или номер телефона (например +79991234567)."
)

TXT_NO_FINAL = "Спасибо за обратную связь 🙏\nЭто поможет нам стать лучше."

# -------------------- КНОПКИ --------------------
def kb_expectations():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✅ Да", "❌ Нет")
    return kb


def kb_reasons():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("1️⃣ Долгое подключение поставщиков", callback_data="r:1"),
        types.InlineKeyboardButton("2️⃣ Техподдержка", callback_data="r:2"),
        types.InlineKeyboardButton("3️⃣ Функционал", callback_data="r:3"),
        types.InlineKeyboardButton("4️⃣ Внедрение", callback_data="r:4"),
        types.InlineKeyboardButton("5️⃣ Другое", callback_data="r:5"),
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

# -------------------- FSM --------------------
class FeedbackFSM(StatesGroup):
    expectations = State()  # Да/Нет
    reason = State()        # если Нет
    comment = State()       # optional, но если reason=Другое — обязательный
    ident = State()         # обязательный, если Нет: ИНН или телефон


# -------------------- УТИЛИТЫ --------------------
def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def normalize_inn(text: str) -> Optional[str]:
    t = (text or "").strip()
    if re.fullmatch(r"\d{10}|\d{12}", t):
        return t
    return None


def normalize_phone(text: str) -> Optional[str]:
    """
    Принимаем телефон в виде:
      +7XXXXXXXXXX / 8XXXXXXXXXX / 79XXXXXXXXX / 9XXXXXXXXX (10 цифр)
    Нормализуем к +7XXXXXXXXXX
    """
    raw = (text or "").strip()

    # выкидываем всё кроме цифр
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    if len(digits) == 10:
        # без кода страны
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits

    return None


def has_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]", text or ""))


# -------------------- Google Sheets --------------------
def get_sheets_service():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


async def append_row(row: list):
    """
    Всегда добавляет НОВУЮ строку (не заменяет существующие).
    Диапазон под 8 колонок: A:H
    """
    def _write():
        service = get_sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{GOOGLE_SHEET_WORKSHEET}!A:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    await asyncio.to_thread(_write)


# -------------------- ХЭНДЛЕРЫ БОТА --------------------
@dp.message_handler(commands=["start", "restart"], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(TXT_START, reply_markup=kb_expectations())
    await FeedbackFSM.expectations.set()


@dp.message_handler(state=FeedbackFSM.expectations, content_types=types.ContentTypes.TEXT)
async def on_expectations(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt not in ("✅ Да", "❌ Нет"):
        await message.answer("Пожалуйста, выберите вариант кнопкой ниже 🙂", reply_markup=kb_expectations())
        return

    await state.update_data(expectations=txt)

    # Ветка "Да"
    if txt == "✅ Да":
        await state.update_data(flow="yes", comment_required=False, reason="")
        await message.answer(TXT_YES_ASK_COMMENT, reply_markup=types.ReplyKeyboardRemove())
        await message.answer(" ", reply_markup=kb_skip())  # показать inline-кнопку "Пропустить"
        await FeedbackFSM.comment.set()
        return

    # Ветка "Нет"
    await state.update_data(flow="no")
    await message.answer(TXT_NO_ASK_REASON, reply_markup=types.ReplyKeyboardRemove())
    await message.answer("Выберите причину:", reply_markup=kb_reasons())
    await FeedbackFSM.reason.set()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("r:"), state=FeedbackFSM.reason)
async def on_reason(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    reason_text = REASONS.get(code, "")
    await state.update_data(reason=reason_text)
    await call.answer()

    # Если "Другое" — комментарий обязателен, без кнопки "Пропустить"
    if code == "5":
        await state.update_data(comment_required=True)
        await call.message.edit_text(TXT_OTHER_MANDATORY_COMMENT)
    else:
        await state.update_data(comment_required=False)
        await call.message.edit_text(TXT_OPT_COMMENT, reply_markup=kb_skip())

    await FeedbackFSM.comment.set()


@dp.callback_query_handler(lambda c: c.data == "skip", state=FeedbackFSM.comment)
async def on_skip_comment(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    required = bool(data.get("comment_required", False))

    # Если комментарий обязателен (Другое) — игнорируем пропуск
    if required:
        await call.answer("Комментарий обязателен 🙂", show_alert=False)
        return

    await call.answer()
    await state.update_data(comment="")

    flow = data.get("flow", "")
    if flow == "yes":
        await finalize(call.message, state, inn="", phone="", risk="нет", final_text=TXT_YES_FINAL)
        return

    # flow == "no"
    await ask_ident(call.message, state)


@dp.message_handler(state=FeedbackFSM.comment, content_types=types.ContentTypes.TEXT)
async def on_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comment = (message.text or "").strip()

    if bool(data.get("comment_required", False)) and not comment:
        await message.answer("Комментарий обязателен 🙂 Напишите, пожалуйста, пару слов.")
        return

    await state.update_data(comment=comment)

    flow = data.get("flow", "")
    if flow == "yes":
        await finalize(message, state, inn="", phone="", risk="нет", final_text=TXT_YES_FINAL)
        return

    await ask_ident(message, state)


async def ask_ident(message: types.Message, state: FSMContext):
    await message.answer(TXT_ID_REQUIRED)
    await FeedbackFSM.ident.set()


@dp.message_handler(state=FeedbackFSM.ident, content_types=types.ContentTypes.TEXT)
async def on_ident(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()

    # Нельзя писать буквами (и ИНН, и телефон) — чтобы не было "ИНН: 77..."
    if has_letters(text):
        await message.answer(TXT_ID_INVALID)
        return

    inn = normalize_inn(text)
    phone = None if inn else normalize_phone(text)

    if not inn and not phone:
        await message.answer(TXT_ID_INVALID)
        return

    await finalize(message, state, inn=inn or "", phone=phone or "", risk="есть", final_text=TXT_NO_FINAL)


async def finalize(
    message: types.Message,
    state: FSMContext,
    inn: str = "",
    phone: str = "",
    risk: str = "",
    final_text: str = "",
):
    data = await state.get_data()

    # Колонки (A:H):
    # Дата | Telegram ID | Ожидания | Причина | Комментарий | ИНН | Телефон | Риск оттока
    row = [
        now_str(),
        str(message.from_user.id),
        data.get("expectations", ""),
        data.get("reason", ""),
        data.get("comment", ""),
        inn,
        phone,
        risk,
    ]

    asyncio.create_task(append_row(row))

    await state.finish()
    await message.answer(final_text, reply_markup=types.ReplyKeyboardRemove())


# -------------------- WEB APP (Webhook + Health) --------------------
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()

        # aiogram 2.x
        update = types.Update.to_object(data)

        # чтобы FSM работал в webhook-режиме
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


def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_get("/", health)
    app.on_startup.append(on_startup)
    web.run_app(app, port=PORT)


if __name__ == "__main__":
    main()
