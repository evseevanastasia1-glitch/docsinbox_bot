{\rtf1\ansi\ansicpg1251\cocoartf2867
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fnil\fcharset0 HelveticaNeue;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\deftab560
\pard\pardeftab560\slleading20\partightenfactor0

\f0\fs26 \cf0 import os\
import re\
import json\
import asyncio\
import logging\
from datetime import datetime\
from zoneinfo import ZoneInfo\
from typing import Optional, Tuple\
\
from aiohttp import web\
\
from aiogram import Bot, Dispatcher, types\
from aiogram.contrib.fsm_storage.memory import MemoryStorage\
from aiogram.dispatcher import FSMContext\
from aiogram.dispatcher.filters.state import State, StatesGroup\
\
from google.oauth2.service_account import Credentials\
from googleapiclient.discovery import build\
\
\
logging.basicConfig(level=logging.INFO)\
\
# --- ENV ---\
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()\
if not BOT_TOKEN:\
    raise RuntimeError("BOT_TOKEN \uc0\u1085 \u1077  \u1079 \u1072 \u1076 \u1072 \u1085 ")\
\
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip() or "1Mkdpte7ILplqPisRQP98lXFLFEGrdcEY1gRd2iPGzuU"\
GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "").strip() or "\uc0\u1051 \u1080 \u1089 \u1090 1"\
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()\
\
PORT = int(os.getenv("PORT", "10000"))\
\
# Render \uc0\u1084 \u1086 \u1078 \u1077 \u1090  \u1053 \u1045  \u1087 \u1088 \u1086 \u1089 \u1090 \u1072 \u1074 \u1083 \u1103 \u1090 \u1100  RENDER_EXTERNAL_URL \u1072 \u1074 \u1090 \u1086 \u1084 \u1072 \u1090 \u1080 \u1095 \u1077 \u1089 \u1082 \u1080 , \u1087 \u1086 \u1101 \u1090 \u1086 \u1084 \u1091  \u1076 \u1077 \u1088 \u1078 \u1080 \u1084  WEBHOOK_BASE.\
# \uc0\u1055 \u1088 \u1080 \u1084 \u1077 \u1088 : WEBHOOK_BASE = https://docsinbox-bot.onrender.com\
WEBHOOK_BASE = (os.getenv("RENDER_EXTERNAL_URL", "").strip() or os.getenv("WEBHOOK_BASE", "").strip()).rstrip("/")\
if not WEBHOOK_BASE:\
    raise RuntimeError("\uc0\u1053 \u1077 \u1090  WEBHOOK_BASE/RENDER_EXTERNAL_URL. \u1047 \u1072 \u1076 \u1072 \u1081  WEBHOOK_BASE \u1074  Render.")\
\
WEBHOOK_PATH = "/webhook"\
WEBHOOK_URL = f"\{WEBHOOK_BASE\}\{WEBHOOK_PATH\}"\
\
WARSAW_TZ = ZoneInfo("Europe/Warsaw")\
\
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)\
dp = Dispatcher(bot, storage=MemoryStorage())\
\
\
# -------------------- \uc0\u1050 \u1053 \u1054 \u1055 \u1050 \u1048  --------------------\
def kb_expectations():\
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)\
    kb.add("\uc0\u9989  \u1044 \u1072 ", "\u10060  \u1053 \u1077 \u1090 ", "\u9878 \u65039  \u1063 \u1072 \u1089 \u1090 \u1080 \u1095 \u1085 \u1086 ")\
    return kb\
\
\
def kb_reasons():\
    kb = types.InlineKeyboardMarkup(row_width=1)\
    kb.add(\
        types.InlineKeyboardButton("1. \uc0\u1044 \u1086 \u1083 \u1075 \u1086 \u1077  \u1087 \u1086 \u1076 \u1082 \u1083 \u1102 \u1095 \u1077 \u1085 \u1080 \u1077  \u1087 \u1086 \u1089 \u1090 \u1072 \u1074 \u1097 \u1080 \u1082 \u1086 \u1074 ", callback_data="r:1"),\
        types.InlineKeyboardButton("2. \uc0\u1058 \u1077 \u1093 .\u1087 \u1086 \u1076 \u1076 \u1077 \u1088 \u1078 \u1082 \u1072 ", callback_data="r:2"),\
        types.InlineKeyboardButton("3. \uc0\u1060 \u1091 \u1085 \u1082 \u1094 \u1080 \u1086 \u1085 \u1072 \u1083 ", callback_data="r:3"),\
        types.InlineKeyboardButton("4. \uc0\u1042 \u1085 \u1077 \u1076 \u1088 \u1077 \u1085 \u1080 \u1077 ", callback_data="r:4"),\
        types.InlineKeyboardButton("5. \uc0\u1044 \u1088 \u1091 \u1075 \u1086 \u1077 ", callback_data="r:5"),\
    )\
    return kb\
\
\
def kb_skip():\
    kb = types.InlineKeyboardMarkup()\
    kb.add(types.InlineKeyboardButton("\uc0\u1055 \u1088 \u1086 \u1087 \u1091 \u1089 \u1090 \u1080 \u1090 \u1100 ", callback_data="skip"))\
    return kb\
\
\
REASONS = \{\
    "1": "\uc0\u1044 \u1086 \u1083 \u1075 \u1086 \u1077  \u1087 \u1086 \u1076 \u1082 \u1083 \u1102 \u1095 \u1077 \u1085 \u1080 \u1077  \u1087 \u1086 \u1089 \u1090 \u1072 \u1074 \u1097 \u1080 \u1082 \u1086 \u1074 ",\
    "2": "\uc0\u1058 \u1077 \u1093 .\u1087 \u1086 \u1076 \u1076 \u1077 \u1088 \u1078 \u1082 \u1072 ",\
    "3": "\uc0\u1060 \u1091 \u1085 \u1082 \u1094 \u1080 \u1086 \u1085 \u1072 \u1083 ",\
    "4": "\uc0\u1042 \u1085 \u1077 \u1076 \u1088 \u1077 \u1085 \u1080 \u1077 ",\
    "5": "\uc0\u1044 \u1088 \u1091 \u1075 \u1086 \u1077 ",\
\}\
\
\
# -------------------- FSM --------------------\
class FeedbackFSM(StatesGroup):\
    expectations = State()\
    rating = State()\
    reason = State()\
    comment = State()\
    innkpp = State()\
\
\
# -------------------- \uc0\u1059 \u1058 \u1048 \u1051 \u1048 \u1058 \u1067  --------------------\
def now_str():\
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")\
\
\
def parse_rating(text: str) -> Optional[int]:\
    t = (text or "").strip()\
    if t.isdigit():\
        v = int(t)\
        if 0 <= v <= 10:\
            return v\
    return None\
\
\
def churn_risk(rating: int) -> str:\
    if rating >= 9:\
        return "5\'9610%"\
    if rating >= 7:\
        return "25\'9640%"\
    if rating >= 5:\
        return "50\'9670%"\
    return "80%+"\
\
\
def extract_inn_kpp(text: str) -> Tuple[str, str]:\
    """\
    \uc0\u1055 \u1088 \u1080 \u1085 \u1080 \u1084 \u1072 \u1077 \u1084  \u1048 \u1053 \u1053 /\u1050 \u1055 \u1055  "\u1082 \u1072 \u1082  \u1091 \u1075 \u1086 \u1076 \u1085 \u1086 ":\
    - \uc0\u1077 \u1089 \u1083 \u1080  \u1085 \u1072 \u1096 \u1083 \u1080  \u1095 \u1080 \u1089 \u1083 \u1072  \u1076 \u1083 \u1080 \u1085 \u1086 \u1081  10/12 -> \u1089 \u1095 \u1080 \u1090 \u1072 \u1077 \u1084  \u1048 \u1053 \u1053 \
    - \uc0\u1077 \u1089 \u1083 \u1080  \u1085 \u1072 \u1096 \u1083 \u1080  \u1095 \u1080 \u1089 \u1083 \u1086  \u1076 \u1083 \u1080 \u1085 \u1086 \u1081  9 -> \u1089 \u1095 \u1080 \u1090 \u1072 \u1077 \u1084  \u1050 \u1055 \u1055 \
    - \uc0\u1077 \u1089 \u1083 \u1080  \u1074 \u1086 \u1086 \u1073 \u1097 \u1077  \u1085 \u1080 \u1095 \u1077 \u1075 \u1086  \u1085 \u1077  \u1085 \u1072 \u1096 \u1083 \u1080  -> \u1082 \u1083 \u1072 \u1076 \u1105 \u1084  \u1080 \u1089 \u1093 \u1086 \u1076 \u1085 \u1091 \u1102  \u1089 \u1090 \u1088 \u1086 \u1082 \u1091  \u1074  \u1048 \u1053 \u1053 , \u1050 \u1055 \u1055  \u1087 \u1091 \u1089 \u1090 \u1086 \u1081 \
    """\
    raw = (text or "").strip()\
    nums = re.findall(r"\\d+", raw)\
    inn = ""\
    kpp = ""\
\
    for n in nums:\
        if len(n) in (10, 12):\
            inn = n\
            break\
\
    for n in nums:\
        if len(n) == 9 and n != inn:\
            kpp = n\
            break\
\
    if not inn and not kpp:\
        return raw, ""\
\
    return inn, kpp\
\
\
# -------------------- Google Sheets --------------------\
def get_sheets_service():\
    if not GOOGLE_SERVICE_ACCOUNT_JSON:\
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON \uc0\u1085 \u1077  \u1079 \u1072 \u1076 \u1072 \u1085 ")\
\
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)\
    creds = Credentials.from_service_account_info(\
        info,\
        scopes=["https://www.googleapis.com/auth/spreadsheets"],\
    )\
    return build("sheets", "v4", credentials=creds, cache_discovery=False)\
\
\
async def append_row(row: list):\
    def _write():\
        service = get_sheets_service()\
        service.spreadsheets().values().append(\
            spreadsheetId=GOOGLE_SHEET_ID,\
            range=f"\{GOOGLE_SHEET_WORKSHEET\}!A:I",\
            valueInputOption="USER_ENTERED",\
            insertDataOption="INSERT_ROWS",\
            body=\{"values": [row]\},\
        ).execute()\
\
    await asyncio.to_thread(_write)\
\
\
# -------------------- \uc0\u1061 \u1069 \u1053 \u1044 \u1051 \u1045 \u1056 \u1067  \u1041 \u1054 \u1058 \u1040  --------------------\
@dp.message_handler(commands=["start", "restart"], state="*")\
async def start(message: types.Message, state: FSMContext):\
    await state.finish()\
    await message.answer(\
        "\uc0\u1044 \u1086 \u1073 \u1088 \u1099 \u1081  \u1076 \u1077 \u1085 \u1100 !\\n\\n"\
        "\uc0\u1055 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1086 \u1094 \u1077 \u1085 \u1080 \u1090 \u1077  \u1074 \u1072 \u1096 \u1080  \u1074 \u1087 \u1077 \u1095 \u1072 \u1090 \u1083 \u1077 \u1085 \u1080 \u1103  \u1086 \u1090  \u1074 \u1085 \u1077 \u1076 \u1088 \u1077 \u1085 \u1080 \u1103  DocsInBox.\\n"\
        "\uc0\u1054 \u1087 \u1088 \u1072 \u1074 \u1076 \u1072 \u1083  \u1083 \u1080  \u1089 \u1077 \u1088 \u1074 \u1080 \u1089  \u1074 \u1072 \u1096 \u1080  \u1086 \u1078 \u1080 \u1076 \u1072 \u1085 \u1080 \u1103 ? \u9786 \u65039 ",\
        reply_markup=kb_expectations(),\
    )\
    await FeedbackFSM.expectations.set()\
\
\
@dp.message_handler(state=FeedbackFSM.expectations, content_types=types.ContentTypes.TEXT)\
async def on_expectations(message: types.Message, state: FSMContext):\
    txt = (message.text or "").strip()\
    if txt not in ["\uc0\u9989  \u1044 \u1072 ", "\u10060  \u1053 \u1077 \u1090 ", "\u9878 \u65039  \u1063 \u1072 \u1089 \u1090 \u1080 \u1095 \u1085 \u1086 "]:\
        await message.answer("\uc0\u1055 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1074 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1074 \u1072 \u1088 \u1080 \u1072 \u1085 \u1090  \u1082 \u1085 \u1086 \u1087 \u1082 \u1086 \u1081  \u1085 \u1080 \u1078 \u1077  \u55357 \u56898 ", reply_markup=kb_expectations())\
        return\
\
    await state.update_data(expectations=txt)\
    await message.answer("\uc0\u1057 \u1087 \u1072 \u1089 \u1080 \u1073 \u1086 !\\n\u1054 \u1094 \u1077 \u1085 \u1080 \u1090 \u1077  \u1089 \u1077 \u1088 \u1074 \u1080 \u1089  \u1087 \u1086  \u1096 \u1082 \u1072 \u1083 \u1077  \u1086 \u1090  0 \u1076 \u1086  10", reply_markup=types.ReplyKeyboardRemove())\
    await FeedbackFSM.rating.set()\
\
\
@dp.message_handler(state=FeedbackFSM.rating, content_types=types.ContentTypes.TEXT)\
async def on_rating(message: types.Message, state: FSMContext):\
    rating = parse_rating(message.text)\
    if rating is None:\
        await message.answer("\uc0\u1042 \u1074 \u1077 \u1076 \u1080 \u1090 \u1077  \u1095 \u1080 \u1089 \u1083 \u1086  \u1086 \u1090  0 \u1076 \u1086  10")\
        return\
\
    await state.update_data(rating=rating)\
\
    # 9\'9610: \uc0\u1048 \u1053 \u1053 /\u1050 \u1055 \u1055  \u1053 \u1045  \u1089 \u1087 \u1088 \u1072 \u1096 \u1080 \u1074 \u1072 \u1077 \u1084 \
    if rating >= 9:\
        await message.answer("\uc0\u1057 \u1087 \u1072 \u1089 \u1080 \u1073 \u1086  \u1079 \u1072  \u1074 \u1099 \u1089 \u1086 \u1082 \u1091 \u1102  \u1086 \u1094 \u1077 \u1085 \u1082 \u1091  \u1080  \u1095 \u1090 \u1086  \u1074 \u1099 \u1073 \u1088 \u1072 \u1083 \u1080  \u1085 \u1072 \u1089 ! \u10084 \u65039 ")\
        await finalize(message, state, inn="", kpp="")\
        return\
\
    if rating >= 7:\
        await message.answer("\uc0\u1057 \u1087 \u1072 \u1089 \u1080 \u1073 \u1086  \u1079 \u1072  \u1086 \u1094 \u1077 \u1085 \u1082 \u1091 !\\n\u1055 \u1086 \u1076 \u1089 \u1082 \u1072 \u1078 \u1080 \u1090 \u1077 , \u1087 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1095 \u1090 \u1086  \u1087 \u1086 \u1096 \u1083 \u1086  \u1085 \u1077  \u1090 \u1072 \u1082 .")\
    else:\
        await message.answer(\
            "\uc0\u1053 \u1072 \u1084  \u1086 \u1095 \u1077 \u1085 \u1100  \u1078 \u1072 \u1083 \u1100 , \u1095 \u1090 \u1086  \u1089 \u1077 \u1088 \u1074 \u1080 \u1089  \u1085 \u1077  \u1087 \u1086 \u1083 \u1085 \u1086 \u1089 \u1090 \u1100 \u1102  \u1086 \u1087 \u1088 \u1072 \u1074 \u1076 \u1072 \u1083  \u1074 \u1072 \u1096 \u1080  \u1086 \u1078 \u1080 \u1076 \u1072 \u1085 \u1080 \u1103  \u55357 \u56852 \\n"\
            "\uc0\u1055 \u1086 \u1076 \u1089 \u1082 \u1072 \u1078 \u1080 \u1090 \u1077 , \u1087 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1095 \u1090 \u1086  \u1087 \u1086 \u1096 \u1083 \u1086  \u1085 \u1077  \u1090 \u1072 \u1082 ."\
        )\
\
    await message.answer("\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1087 \u1088 \u1080 \u1095 \u1080 \u1085 \u1091 :", reply_markup=kb_reasons())\
    await FeedbackFSM.reason.set()\
\
\
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("r:"), state=FeedbackFSM.reason)\
async def on_reason(call: types.CallbackQuery, state: FSMContext):\
    code = call.data.split(":", 1)[1]\
    await state.update_data(reason=REASONS.get(code, ""))\
    await call.answer()\
\
    if code == "5":\
        await call.message.edit_text("\uc0\u1055 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1085 \u1072 \u1087 \u1080 \u1096 \u1080 \u1090 \u1077  \u1082 \u1086 \u1084 \u1084 \u1077 \u1085 \u1090 \u1072 \u1088 \u1080 \u1081  (\u1076 \u1083 \u1103  \u1087 \u1091 \u1085 \u1082 \u1090 \u1072  \'ab\u1044 \u1088 \u1091 \u1075 \u1086 \u1077 \'bb \u1086 \u1085  \u1086 \u1073 \u1103 \u1079 \u1072 \u1090 \u1077 \u1083 \u1077 \u1085 ):")\
    else:\
        await call.message.edit_text(\
            "\uc0\u1045 \u1089 \u1083 \u1080  \u1093 \u1086 \u1090 \u1080 \u1090 \u1077  \'97 \u1086 \u1089 \u1090 \u1072 \u1074 \u1100 \u1090 \u1077  \u1082 \u1086 \u1084 \u1084 \u1077 \u1085 \u1090 \u1072 \u1088 \u1080 \u1081  (\u1085 \u1077 \u1086 \u1073 \u1103 \u1079 \u1072 \u1090 \u1077 \u1083 \u1100 \u1085 \u1086 ).\\n\u1048 \u1083 \u1080  \u1085 \u1072 \u1078 \u1084 \u1080 \u1090 \u1077  \'ab\u1055 \u1088 \u1086 \u1087 \u1091 \u1089 \u1090 \u1080 \u1090 \u1100 \'bb.",\
            reply_markup=kb_skip(),\
        )\
    await FeedbackFSM.comment.set()\
\
\
@dp.callback_query_handler(lambda c: c.data == "skip", state=FeedbackFSM.comment)\
async def skip(call: types.CallbackQuery, state: FSMContext):\
    await call.answer()\
    await state.update_data(comment="")\
    await ask_inn(call.message, state)\
\
\
@dp.message_handler(state=FeedbackFSM.comment, content_types=types.ContentTypes.TEXT)\
async def on_comment(message: types.Message, state: FSMContext):\
    data = await state.get_data()\
    reason = data.get("reason", "")\
    comment = (message.text or "").strip()\
\
    if reason == REASONS["5"] and not comment:\
        await message.answer("\uc0\u1044 \u1083 \u1103  \u1087 \u1091 \u1085 \u1082 \u1090 \u1072  \'ab\u1044 \u1088 \u1091 \u1075 \u1086 \u1077 \'bb \u1085 \u1091 \u1078 \u1077 \u1085  \u1082 \u1086 \u1084 \u1084 \u1077 \u1085 \u1090 \u1072 \u1088 \u1080 \u1081  \u55357 \u56898  \u1053 \u1072 \u1087 \u1080 \u1096 \u1080 \u1090 \u1077 , \u1087 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1087 \u1072 \u1088 \u1091  \u1089 \u1083 \u1086 \u1074 .")\
        return\
\
    await state.update_data(comment=comment)\
    await ask_inn(message, state)\
\
\
async def ask_inn(message: types.Message, state: FSMContext):\
    await message.answer(\
        "\uc0\u1055 \u1086 \u1078 \u1072 \u1083 \u1091 \u1081 \u1089 \u1090 \u1072 , \u1091 \u1082 \u1072 \u1078 \u1080 \u1090 \u1077  \u1048 \u1053 \u1053  (\u1080 \u1083 \u1080  \u1048 \u1053 \u1053 /\u1050 \u1055 \u1055 , \u1077 \u1089 \u1083 \u1080  \u1077 \u1089 \u1090 \u1100 ), \u1095 \u1090 \u1086 \u1073 \u1099  \u1084 \u1099  \u1084 \u1086 \u1075 \u1083 \u1080  \u1082 \u1086 \u1088 \u1088 \u1077 \u1082 \u1090 \u1085 \u1086  \u1080 \u1076 \u1077 \u1085 \u1090 \u1080 \u1092 \u1080 \u1094 \u1080 \u1088 \u1086 \u1074 \u1072 \u1090 \u1100  \u1082 \u1086 \u1084 \u1087 \u1072 \u1085 \u1080 \u1102 .\\n"\
        "\uc0\u1052 \u1086 \u1078 \u1085 \u1086  \u1087 \u1080 \u1089 \u1072 \u1090 \u1100  \u1074  \u1083 \u1102 \u1073 \u1086 \u1084  \u1092 \u1086 \u1088 \u1084 \u1072 \u1090 \u1077 : \u1085 \u1072 \u1087 \u1088 \u1080 \u1084 \u1077 \u1088 , \'ab\u1048 \u1053 \u1053  770... \u1050 \u1055 \u1055  770...\'bb, \'ab770.../770...\'bb, \'ab770... 770...\'bb.",\
    )\
    await FeedbackFSM.innkpp.set()\
\
\
@dp.message_handler(state=FeedbackFSM.innkpp, content_types=types.ContentTypes.TEXT)\
async def on_inn(message: types.Message, state: FSMContext):\
    inn, kpp = extract_inn_kpp(message.text)\
    await finalize(message, state, inn=inn, kpp=kpp)\
\
\
async def finalize(message: types.Message, state: FSMContext, inn: str = "", kpp: str = ""):\
    data = await state.get_data()\
    rating = int(data.get("rating", 0))\
\
    row = [\
        now_str(),                    # \uc0\u1044 \u1072 \u1090 \u1072 \
        str(message.from_user.id),     # Telegram ID\
        data.get("expectations", ""),  # \uc0\u1054 \u1078 \u1080 \u1076 \u1072 \u1085 \u1080 \u1103 \
        rating,                        # \uc0\u1054 \u1094 \u1077 \u1085 \u1082 \u1072 \
        data.get("reason", ""),        # \uc0\u1055 \u1088 \u1080 \u1095 \u1080 \u1085 \u1072 \
        data.get("comment", ""),       # \uc0\u1050 \u1086 \u1084 \u1084 \u1077 \u1085 \u1090 \u1072 \u1088 \u1080 \u1081 \
        inn,                           # \uc0\u1048 \u1053 \u1053 \
        kpp,                           # \uc0\u1050 \u1055 \u1055 \
        churn_risk(rating),            # \uc0\u1056 \u1080 \u1089 \u1082  \u1086 \u1090 \u1090 \u1086 \u1082 \u1072 \
    ]\
\
    # \uc0\u1079 \u1072 \u1087 \u1080 \u1089 \u1100  \u1074  Google Sheets (\u1074  \u1092 \u1086 \u1085 \u1077 )\
    asyncio.create_task(append_row(row))\
\
    await state.finish()\
\
    await message.answer(\
        "\uc0\u1057 \u1087 \u1072 \u1089 \u1080 \u1073 \u1086  \u1079 \u1072  \u1086 \u1073 \u1088 \u1072 \u1090 \u1085 \u1091 \u1102  \u1089 \u1074 \u1103 \u1079 \u1100 ! \u55357 \u56911  \u1042 \u1072 \u1096 \u1072  \u1086 \u1094 \u1077 \u1085 \u1082 \u1072  \u1087 \u1086 \u1084 \u1086 \u1078 \u1077 \u1090  \u1085 \u1072 \u1084  \u1089 \u1090 \u1072 \u1090 \u1100  \u1083 \u1091 \u1095 \u1096 \u1077 !",\
        reply_markup=types.ReplyKeyboardRemove(),\
    )\
\
\
# -------------------- WEB APP (Webhook + Health) --------------------\
async def handle_webhook(request: web.Request):\
    try:\
        data = await request.json()\
\
        # \uc0\u9989  aiogram 2.x\
        update = types.Update.to_object(data)\
\
        # \uc0\u9989  \u1095 \u1090 \u1086 \u1073 \u1099  FSM \u1088 \u1072 \u1073 \u1086 \u1090 \u1072 \u1083  \u1074  webhook-\u1088 \u1077 \u1078 \u1080 \u1084 \u1077 \
        Bot.set_current(bot)\
        Dispatcher.set_current(dp)\
\
        await dp.process_update(update)\
    except Exception:\
        logging.exception("Webhook handler crashed")\
\
    return web.Response(text="ok")\
\
\
async def health(_request: web.Request):\
    return web.Response(text="ok")\
\
\
async def on_startup(app: web.Application):\
    await bot.delete_webhook(drop_pending_updates=True)\
    await bot.set_webhook(WEBHOOK_URL)\
    logging.info("Webhook set to %s", WEBHOOK_URL)\
\
\
async def on_shutdown(app: web.Application):\
    await bot.delete_webhook()\
\
    await dp.storage.close()\
    await dp.storage.wait_closed()\
\
    # \uc0\u1082 \u1086 \u1088 \u1088 \u1077 \u1082 \u1090 \u1085 \u1086  \u1079 \u1072 \u1082 \u1088 \u1099 \u1074 \u1072 \u1077 \u1084  session (\u1073 \u1077 \u1079  DeprecationWarning)\
    try:\
        session = await bot.get_session()\
        await session.close()\
    except Exception:\
        logging.exception("Failed to close bot session")\
\
\
def main():\
    app = web.Application()\
    app.router.add_post(WEBHOOK_PATH, handle_webhook)\
    app.router.add_get("/", health)\
    app.router.add_get("/health", health)\
\
    app.on_startup.append(on_startup)\
    app.on_shutdown.append(on_shutdown)\
\
    web.run_app(app, host="0.0.0.0", port=PORT)\
\
\
if __name__ == "__main__":\
    main()}