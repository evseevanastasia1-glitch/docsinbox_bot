import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

try:
    import uvloop  # ускоряет event loop на Linux (Render)
    uvloop.install()
except Exception:
    pass


# -------------------- НАСТРОЙКИ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID", "").strip()  # можно не указывать
DATA_FILE = os.getenv("DATA_FILE", "feedback.jsonl")  # куда писать ответы (на Render файл временный, но для отладки ок)

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

logging.basicConfig(level=logging.WARNING)

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())


# -------------------- УТИЛИТЫ --------------------
def extract_inn_kpp(text: str):
    """
    Достаём ИНН (10 или 12 цифр) и КПП (обычно 9, но будем терпеть 8-10, чтобы 'как угодно').
    Работает с форматами типа:
    - 7813550941 / 78130100
    - ИНН 7813550941 КПП 781301009
    - 7813550941 781301009
    - любые символы/пробелы/слеши
    """
    if not text:
        return None, None

    digits_groups = re.findall(r"\d+", text)
    inn = None
    kpp = None

    # Сначала ищем ИНН как группу 10 или 12
    for g in digits_groups:
        if len(g) in (10, 12):
            inn = g
            break

    # КПП: обычно 9, но будем более мягкими (8-10),
    # чтобы не ругаться на "78130100" как на скрине
    for g in digits_groups:
        if len(g) in (8, 9, 10) and g != inn:
            kpp = g
            break

    return inn, kpp


async def append_jsonl(path: str, payload: dict):
    """
    Асинхронная запись одной строки JSONL.
    Чтобы не лагало — пишем через to_thread (не блокируем event loop).
    """
    line = json.dumps(payload, ensure_ascii=False) + "\n"

    def _write():
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    await asyncio.to_thread(_write)


def kb_topic():
    return types.ReplyKeyboardMarkup(resize_keyboard=True).add(
        types.KeyboardButton("Процесс внедрения"),
        types.KeyboardButton("Работа менеджера"),
        types.KeyboardButton("Поддержка / сопровождение"),
    )


def kb_rating():
    kb = types.InlineKeyboardMarkup(row_width=5)
    kb.add(
        types.InlineKeyboardButton("1", callback_data="rate:1"),
        types.InlineKeyboardButton("2", callback_data="rate:2"),
        types.InlineKeyboardButton("3", callback_data="rate:3"),
        types.InlineKeyboardButton("4", callback_data="rate:4"),
        types.InlineKeyboardButton("5", callback_data="rate:5"),
    )
    return kb


def kb_reason():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("Все хорошо ✅", callback_data="reason:ok"),
        types.InlineKeyboardButton("Долго / затянуто ⏳", callback_data="reason:slow"),
        types.InlineKeyboardButton("Сложно / непонятно 🤯", callback_data="reason:hard"),
        types.InlineKeyboardButton("Были ошибки / баги 🐛", callback_data="reason:bugs"),
        types.InlineKeyboardButton("Другое ✍️", callback_data="reason:other"),
    )
    return kb


def kb_skip_comment():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Пропустить", callback_data="comment:skip"))
    return kb


REASON_LABELS = {
    "ok": "Все хорошо",
    "slow": "Долго / затянуто",
    "hard": "Сложно / непонятно",
    "bugs": "Были ошибки / баги",
    "other": "Другое",
}


# -------------------- СОСТОЯНИЯ --------------------
class FeedbackFSM(StatesGroup):
    topic = State()
    inn = State()
    rating = State()
    reason = State()
    comment = State()


# -------------------- ХЭНДЛЕРЫ --------------------
@dp.message_handler(commands=["start", "restart"], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        "Привет! 👋\n"
        "Собираю обратную связь по внедрению DocsInBox.\n\n"
        "Выберите, о чем хотите оставить отзыв:",
        reply_markup=kb_topic(),
    )
    await FeedbackFSM.topic.set()


@dp.message_handler(lambda m: m.text in ["Процесс внедрения", "Работа менеджера", "Поддержка / сопровождение"], state=FeedbackFSM.topic)
async def on_topic(message: types.Message, state: FSMContext):
    await state.update_data(topic=message.text)

    await message.answer(
        "Спасибо!\n"
        "Пожалуйста, укажите ИНН (или ИНН/КПП, если есть), чтобы мы могли корректно идентифицировать компанию.\n"
        "Можно писать в любом формате: например, «ИНН 770… КПП 770…», «770…/770…», «770… 770…».",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await FeedbackFSM.inn.set()


@dp.message_handler(state=FeedbackFSM.inn, content_types=types.ContentTypes.TEXT)
async def on_inn(message: types.Message, state: FSMContext):
    inn, kpp = extract_inn_kpp(message.text)

    if not inn:
        await message.answer(
            "Не получилось распознать ИНН 😔\n"
            "Пожалуйста, отправьте ИНН (10 или 12 цифр). Если есть КПП — можно добавить рядом."
        )
        return

    await state.update_data(inn=inn, kpp=kpp)

    await message.answer(
        "Поставьте оценку от 1 до 5:",
        reply_markup=kb_rating(),
    )
    await FeedbackFSM.rating.set()


@dp.callback_query_handler(lambda c: c.data.startswith("rate:"), state=FeedbackFSM.rating)
async def on_rating(call: types.CallbackQuery, state: FSMContext):
    rating = call.data.split(":")[1]
    await state.update_data(rating=int(rating))
    await call.answer()

    await call.message.edit_text(
        "Спасибо! А теперь выберите причину/контекст оценки:",
        reply_markup=kb_reason(),
    )
    await FeedbackFSM.reason.set()


@dp.callback_query_handler(lambda c: c.data.startswith("reason:"), state=FeedbackFSM.reason)
async def on_reason(call: types.CallbackQuery, state: FSMContext):
    reason_code = call.data.split(":")[1]
    await state.update_data(reason=reason_code)
    await call.answer()

    if reason_code == "other":
        await call.message.edit_text(
            "Пожалуйста, напишите комментарий (для пункта «Другое» он обязателен):"
        )
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
    await state.update_data(comment="")
    await call.answer()
    await finalize_feedback(call.message, state)


@dp.message_handler(state=FeedbackFSM.comment, content_types=types.ContentTypes.TEXT)
async def on_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    reason = data.get("reason")

    comment = (message.text or "").strip()
    if reason == "other" and not comment:
        await message.answer("Для пункта «Другое» нужен комментарий 🙂 Напишите, пожалуйста, пару слов.")
        return

    await state.update_data(comment=comment)
    await finalize_feedback(message, state)


async def finalize_feedback(message: types.Message, state: FSMContext):
    data = await state.get_data()

    payload = {
        "ts": int(time.time()),
        "datetime": datetime.now().isoformat(timespec="seconds"),
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "full_name": message.from_user.full_name,
        "topic": data.get("topic"),
        "inn": data.get("inn"),
        "kpp": data.get("kpp"),
        "rating": data.get("rating"),
        "reason_code": data.get("reason"),
        "reason_label": REASON_LABELS.get(data.get("reason"), data.get("reason")),
        "comment": data.get("comment", ""),
    }

    # 1) Сохраним локально (не блокируя)
    try:
        await append_jsonl(DATA_FILE, payload)
    except Exception:
        pass

    # 2) Тихо отправим менеджеру (если задан MANAGER_CHAT_ID)
    if MANAGER_CHAT_ID:6538931451
        try:
            text = (
                "📝 <b>Новая обратная связь</b>\n"
                f"Тема: <b>{payload['topic']}</b>\n"
                f"ИНН: <code>{payload['inn']}</code>\n"
                f"КПП: <code>{payload['kpp'] or '-'}</code>\n"
                f"Оценка: <b>{payload['rating']}</b>\n"
                f"Причина: <b>{payload['reason_label']}</b>\n"
                f"Комментарий: {payload['comment'] or '—'}\n\n"
                f"От: {payload['full_name']} (@{payload['username'] or '-'}) | id={payload['user_id']}"
            )
            await bot.send_message(int(MANAGER_CHAT_ID), text)
        except Exception:
            pass

    await state.finish()

    # Финальный текст, который ты просила:
    await message.answer(
        "Спасибо за обратную связь! 🙏 Ваша оценка поможет нам стать лучше!",
        reply_markup=kb_topic(),
    )
    await FeedbackFSM.topic.set()


# -------------------- ЗАПУСК --------------------
if __name__ == "__main__":
    # skip_updates=True ускоряет старт, чтобы бот не пытался обработать старые апдейты
    executor.start_polling(dp, skip_updates=True)
