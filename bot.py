import os
import re
import json
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Для Google Sheets (нужно добавить в requirements.txt: google-api-python-client google-auth)
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from aiohttp import web


# -------------------- НАСТРОЙКИ --------------------
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

# Google Sheets
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1Mkdpte7ILplqPisRQP98lXFLFEGrdcEY1gRd2iPGzuU").strip()
GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "Лист1").strip()

# Service Account JSON: рекомендую хранить целиком в ENV (Render)
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()  # опционально (если хочешь через файл)

# Render healthcheck
ENABLE_HEALTHCHECK = os.getenv("ENABLE_HEALTHCHECK", "1").strip() == "1"

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())


# -------------------- КНОПКИ --------------------
def kb_expectations():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("✅ Да"))
    kb.add(types.KeyboardButton("❌ Нет"))
    kb.add(types.KeyboardButton("⚖️ Частично"))
    return kb


def kb_reasons():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("1. Долгое подключение поставщиков", callback_data="reason:1"),
        types.InlineKeyboardButton("2. Тех.поддержка", callback_data="reason:2"),
        types.InlineKeyboardButton("3. Функционал", callback_data="reason:3"),
        types.InlineKeyboardButton("4. Внедрение", callback_data="reason:4"),
        types.InlineKeyboardButton("5. Другое", callback_data="reason:5"),
    )
    return kb


def kb_skip_comment():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Пропустить", callback_data="comment:skip"))
    return kb


REASON_LABELS = {
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
def now_warsaw_str() -> str:
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_rating(text: str) -> Optional[int]:
    if text is None:
        return None
    t = text.strip()
    if not re.fullmatch(r"\d{1,2}", t):
        return None
    v = int(t)
    if 0 <= v <= 10:
        return v
    return None


def churn_risk_percent(rating: int) -> str:
    # 9–10 - 5–10%
    # 7–8 - 25–40%
    # 5–6 - 50–70%
    # 0–4 - 80%+
    if rating >= 9:
        return "5–10%"
    if rating >= 7:
        return "25–40%"
    if rating >= 5:
        return "50–70%"
    return "80%+"


def extract_inn_kpp_loose(text: str) -> Tuple[str, str]:
    """
    Принимаем любой формат (как просили).
    Пытаемся извлечь ИНН (10/12 цифр) и КПП (9 цифр).
    Если не получилось — кладём максимум в ИНН, КПП пустой, чтобы не потерять ввод.
    """
    if not text:
        return "", ""

    raw = text.strip()
    groups = re.findall(r"\d+", raw)

    inn = ""
    kpp = ""

    # ИНН: 10 или 12
    for g in groups:
        if len(g) in (10, 12):
            inn = g
            break

    # КПП: 9
    for g in groups:
        if len(g) == 9 and g != inn:
            kpp = g
            break

    # Если ни ИНН, ни КПП не нашли — сохраняем весь ввод в ИНН (как есть)
    if not inn and not kpp:
        return raw, ""

    # Если нашли КПП, но ИНН нет — тоже не теряем ввод: сохраняем весь текст в ИНН
    if not inn and kpp:
        return raw, kpp

    return inn, kpp


# -------------------- Google Sheets writer --------------------
@dataclass
class SheetsClient:
    sheet_id: str
    worksheet: str
    service: object  # googleapiclient service

    async def append_row(self, values: list):
        """
        values: list of 9 elements matching columns:
        Дата | Telegram ID | Ожидания | Оценка | Причина | Комментарий | ИНН | КПП | Риск оттока
        """
        rng = f"{self.worksheet}!A:I"

        def _append():
            body = {"values": [values]}
            return (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=self.sheet_id,
                    range=rng,
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                )
                .execute()
            )

        # чтобы не блокировать polling
        await asyncio.to_thread(_append)


_sheets_client: Optional[SheetsClient] = None


def build_sheets_client() -> SheetsClient:
    global _sheets_client
    if _sheets_client:
        return _sheets_client

    if GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    elif GOOGLE_SERVICE_ACCOUNT_FILE:
        with open(GOOGLE_SERVICE_ACCOUNT_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
    else:
        # локально можно положить файл service_account.json рядом с bot.py (НЕ коммитить)
        with open("service_account.json", "r", encoding="utf-8") as f:
            info = json.load(f)

    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    _sheets_client = SheetsClient(sheet_id=GOOGLE_SHEET_ID, worksheet=GOOGLE_SHEET_WORKSHEET, service=service)
    return _sheets_client


async def append_to_sheet(row: list):
    try:
        client = build_sheets_client()
        await client.append_row(row)
    except (HttpError, Exception) as e:
        logging.exception("Google Sheets append failed: %s", e)


# -------------------- HANDLERS --------------------
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


@dp.message_handler(
    lambda m: (m.text or "").strip() in ["✅ Да", "❌ Нет", "⚖️ Частично"],
    state=FeedbackFSM.expectations,
)
async def on_expectations(message: types.Message, state: FSMContext):
    await state.update_data(expectations=message.text.strip())
    await message.answer(
        "Спасибо!\n"
        "Оцените сервис по шкале от 0 до 10",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await FeedbackFSM.rating.set()


@dp.message_handler(state=FeedbackFSM.rating, content_types=types.ContentTypes.TEXT)
async def on_rating(message: types.Message, state: FSMContext):
    rating = parse_rating(message.text)
    if rating is None:
        await message.answer("Пожалуйста, введите число от 0 до 10.")
        return

    await state.update_data(rating=rating)

    # 9–10: сразу финал, ИНН/КПП не просим
    if rating >= 9:
        await message.answer("Спасибо за высокую оценку и что выбрали нас! ❤️")
        await finalize_and_write(message, state, reason="", comment="", inn="", kpp="")
        return

    # 7–8: причины, дальше логика комментария и ИНН/КПП
    if rating >= 7:
        await message.answer(
            "Спасибо за оценку!\n"
            "Подскажите, пожалуйста, что пошло не так?"
        )
        await message.answer("Выберите причину:", reply_markup=kb_reasons())
        await FeedbackFSM.reason.set()
        return

    # 0–6: тоже причины, но другой текст
    await message.answer(
        "Нам очень жаль, что сервис не полностью оправдал ваши ожидания 😔\n"
        "Подскажите, пожалуйста, что пошло не так."
    )
    await message.answer("Выберите причину:", reply_markup=kb_reasons())
    await FeedbackFSM.reason.set()


@dp.callback_query_handler(lambda c: c.data.startswith("reason:"), state=FeedbackFSM.reason)
async def on_reason(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":")[1]
    await state.update_data(reason_code=code, reason_label=REASON_LABELS.get(code, ""))

    await call.answer()

    # Комментарий обязателен только для "Другое" (5)
    if code == "5":
        await call.message.edit_text("Пожалуйста, напишите комментарий (для пункта «Другое» он обязателен):")
        await FeedbackFSM.comment.set()
    else:
        await call.message.edit_text(
            "Если хотите — оставьте комментарий (необязательно).\n"
            "Если комментарий не нужен — нажмите «Пропустить».",
            reply_markup=kb_skip_comment(),
        )
        await FeedbackFSM.comment.set()


@dp.callback_query_handler(lambda c: c.data == "comment:skip", state=FeedbackFSM.comment)
async def skip_comment(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(comment="")
    await ask_inn_kpp(call.message, state)


@dp.message_handler(state=FeedbackFSM.comment, content_types=types.ContentTypes.TEXT)
async def on_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    reason_code = data.get("reason_code", "")

    comment = (message.text or "").strip()

    if reason_code == "5" and not comment:
        await message.answer("Для пункта «Другое» нужен комментарий 🙂 Напишите, пожалуйста, пару слов.")
        return

    await state.update_data(comment=comment)
    await ask_inn_kpp(message, state)


async def ask_inn_kpp(message: types.Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, укажите ИНН (или ИНН/КПП, если есть), чтобы мы могли корректно идентифицировать компанию.\n"
        "Можно писать в любом формате: например, «ИНН 770... КПП 770...», «770.../770...», «770... 770...».",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await FeedbackFSM.innkpp.set()


@dp.message_handler(state=FeedbackFSM.innkpp, content_types=types.ContentTypes.TEXT)
async def on_inn_kpp(message: types.Message, state: FSMContext):
    inn, kpp = extract_inn_kpp_loose(message.text)
    await finalize_and_write(message, state, inn=inn, kpp=kpp)


async def finalize_and_write(
    message: types.Message,
    state: FSMContext,
    reason: Optional[str] = None,
    comment: Optional[str] = None,
    inn: Optional[str] = None,
    kpp: Optional[str] = None,
):
    data = await state.get_data()

    expectations = data.get("expectations", "")
    rating = int(data.get("rating", 0))

    reason_label = data.get("reason_label", "")
    comment_val = data.get("comment", "")

    # для ветки 9–10 мы передаём пустые reason/comment/inn/kpp
    if reason is not None:
        reason_label = reason
    if comment is not None:
        comment_val = comment

    inn_val = inn if inn is not None else ""
    kpp_val = kpp if kpp is not None else ""

    risk = churn_risk_percent(rating)

    # строка под СТРОГО заданные столбцы (9 значений)
    row = [
        now_warsaw_str(),                 # Дата
        str(message.from_user.id),        # Telegram ID
        expectations,                     # Ожидания
        rating,                           # Оценка
        reason_label,                     # Причина
        comment_val,                      # Комментарий
        inn_val,                          # ИНН
        kpp_val,                          # КПП
        risk,                             # Риск оттока
    ]

    # пишем в таблицу (не блокируя бота)
    asyncio.create_task(append_to_sheet(row))

    await state.finish()
    await message.answer(
        "Спасибо за обратную связь, ваше мнение поможет нам стать лучше 💙",
        reply_markup=kb_expectations(),
    )
    await FeedbackFSM.expectations.set()


# -------------------- HEALTHCHECK ДЛЯ RENDER WEB SERVICE --------------------
async def health_server():
    app = web.Application()

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Health server started on port %s", port)


async def on_startup(_dp: Dispatcher):
    # Запускаем healthcheck сервер параллельно (чтобы Render Web Service был "живой")
    if ENABLE_HEALTHCHECK:
        asyncio.create_task(health_server())


# -------------------- ЗАПУСК --------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
