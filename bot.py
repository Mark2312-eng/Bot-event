import os
import asyncio
import logging
import requests
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
XF_API_URL = os.getenv("XF_API_URL", "https://samp.date")
XF_API_KEY = os.getenv("XF_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
CUSTOM_FIELD_ID = os.getenv("CUSTOM_FIELD_ID", "vise_coins")
DATABASE_URL = os.getenv("DATABASE_URL")

SECRET_CODES = {
    "VISE-NY-01": 1.5, "VISE-NY-02": 1.5, "VISE-NY-03": 2.0,
    "VISE-NY-04": 1.5, "VISE-NY-05": 2.0, "VISE-NY-06": 2.5,
    "VISE-NY-07": 1.5, "VISE-NY-08": 2.0, "VISE-NY-09": 2.5,
    "VISE-NY-10": 3.0, "VISE-2026": 5.0,
}

SHOP_ITEMS = {
    "snow": ("❄️ Краска на форуме «Снежок»", 5.0),
    "sign": ("✍️ Роспись от Руководства в профиль", 4.0),
    "magnat": ("🎄 Краска на форуме «Новогодний Магнат»", 12.0),
    "vip": ("👑 VIP-статус на сервере (7 дней)", 15.0),
}

logging.basicConfig(level=logging.INFO)
db_pool = None

async def init_db():
    global db_pool
    db_url = DATABASE_URL
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    db_pool = await asyncpg.create_pool(dsn=db_url)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS event_users (
                tg_id BIGINT PRIMARY KEY,
                xf_id INTEGER NOT NULL,
                xf_nick TEXT NOT NULL
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS used_codes (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL,
                code TEXT NOT NULL,
                UNIQUE(tg_id, code)
            );
        """)
    logging.info("PostgreSQL подключена!")

def xf_headers():
    return {
        "XF-Api-Key": XF_API_KEY,
        "XF-Api-User": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

def find_xf_user(username: str):
    urls = [
        f"{XF_API_URL.rstrip('/')}/users/find-name",
        "https://samp.date"
    ]
    for url in urls:
        try:
            resp = requests.get(url, params={"username": username}, headers=xf_headers(), timeout=10)
            logging.info(f"API {url} -> {resp.status_code} | Тело: {resp.text[:200]}")
            if resp.status_code == 200 and resp.text.strip().startswith("{"):
                data = resp.json()
                if data.get("exact"):
                    return data["exact"]
                if data.get("recommendations"):
                    # Если API вернул список — берём первый элемент, чтобы не упасть ниже в коде
                    return data["recommendations"][0] if isinstance(data["recommendations"], list) else data["recommendations"]
        except Exception as e:
            logging.error(f"XF search error ({url}): {e}")
    return None

def get_xf_user_balance(user_id: int) -> float:
    urls = [
        f"{XF_API_URL.rstrip('/')}/users/{user_id}",
        f"https://samp.date{user_id}"
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=xf_headers(), timeout=10)
            if resp.status_code == 200 and resp.text.strip().startswith("{"):
                val = resp.json().get("user", {}).get("custom_fields", {}).get(CUSTOM_FIELD_ID, "0")
                return float(str(val).replace(",", ".")) if val else 0.0
        except Exception as e:
            logging.error(f"XF read error: {e}")
    return 0.0

def set_xf_user_balance(user_id: int, new_balance: float) -> bool:
    # Защита от передачи int вместо float
    try:
        new_balance = float(new_balance)
        formatted = int(new_balance) if new_balance.is_integer() else round(new_balance, 2)
    except Exception as e:
        logging.error(f"Balance format error: {e}")
        return False

    urls = [
        f"{XF_API_URL.rstrip('/')}/users/{user_id}",
        f"https://samp.date{user_id}"
    ]
    for url in urls:
        try:
            resp = requests.post(url, data={f"custom_fields[{CUSTOM_FIELD_ID}]": str(formatted)}, headers=xf_headers(), timeout=10)
            if resp.status_code == 200 and resp.text.strip().startswith("{"):
                return True
        except Exception as e:
            logging.error(f"XF write error: {e}")
    return False

class EventStates(StatesGroup):
    waiting_for_nick = State()
    waiting_for_code = State()

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎁 Ввести секретный код")],
            [KeyboardButton(text="🪙 Мой баланс"), KeyboardButton(text="🛒 Магазин наград")],
            [KeyboardButton(text="👤 Привязать / сменить ник"), KeyboardButton(text="ℹ️ Правила ивента")]
        ],
        resize_keyboard=True
    )

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_users WHERE tg_id = $1", message.from_user.id)
    await message.answer(
        "❄️ **Добро пожаловать в ивент-бота VISE RUSSIA!** 🎁\n\n"
        "Ищи спрятанные подарки на форуме, вводи коды здесь и получай монеты прямо на свой профиль!\n"
        "🔗 Форум: https://samp.date",
        parse_mode="Markdown", reply_markup=main_keyboard()
    )
    if not user:
        await message.answer("👇 Введи свой **точный никнейм с форума**:", parse_mode="Markdown")
        await state.set_state(EventStates.waiting_for_nick)

@dp.message(F.text == "👤 Привязать / сменить ник")
async def ask_nick(message: types.Message, state: FSMContext):
    await message.answer("✍️ Введи свой **точный никнейм** с форума:", parse_mode="Markdown")
    await state.set_state(EventStates.waiting_for_nick)

@dp.message(EventStates.waiting_for_nick)
async def process_nick(message: types.Message, state: FSMContext):
    nick = message.text.strip()
    wait_msg = await message.answer("🔎 Ищу профиль на форуме...")
    xf_user = await asyncio.to_thread(find_xf_user, nick)
    if not xf_user:
        await wait_msg.edit_text(f"❌ Ник **{nick}** не найден на форуме! Проверь буквы и попробуй снова:", parse_mode="Markdown")
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO event_users (tg_id, xf_id, xf_nick) VALUES ($1, $2, $3) ON CONFLICT (tg_id) DO UPDATE SET xf_id = $2, xf_nick = $3",
            message.from_user.id, xf_user["user_id"], xf_user["username"]
        )
    await state.clear()
    bal = await asyncio.to_thread(get_xf_user_balance, xf_user["user_id"])
    await wait_msg.edit_text(f"✅ Профиль **{xf_user['username']}** привязан!\n🪙 Баланс: **{bal} монет**", parse_mode="Markdown")

@dp.message(F.text == "🪙 Мой баланс")
async def show_balance(message: types.Message):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_users WHERE tg_id = $1", message.from_user.id)
        used_count = await conn.fetchval("SELECT COUNT(*) FROM used_codes WHERE tg_id = $1", message.from_user.id)
    if not user:
        await message.answer("⚠️ Сначала привяжи свой ник с форума!")
        return
    bal = await asyncio.to_thread(get_xf_user_balance, user["xf_id"])
    await message.answer(f"👤 Профиль: `{user['xf_nick']}`\n🪙 Баланс: `{bal}`\n🎁 Найдено подарков: `{used_count}/{len(SECRET_CODES)}`", parse_mode="Markdown")

@dp.message(F.text == "🎁 Ввести секретный код")
async def ask_code(message: types.Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_users WHERE tg_id = $1", message.from_user.id)
    if not user:
        await message.answer("⚠️ Сначала привяжи свой ник с форума!")
        return
    await message.answer("🔑 Введи секретный код (например, `VISE-NY-01`):", parse_mode="Markdown")
    await state.set_state(EventStates.waiting_for_code)

@dp.message(EventStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    tg_id = message.from_user.id
    if code not in SECRET_CODES:
        await message.answer("❌ Неверный код! Поищи другой подарок на форуме.")
        await state.clear()
        return
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_users WHERE tg_id = $1", tg_id)
        already = await conn.fetchrow("SELECT id FROM used_codes WHERE tg_id = $1 AND code = $2", tg_id, code)
    if not user:
        await message.answer("⚠️ Сначала привяжи свой ник с форума!")
        await state.clear()
        return
    if already:
        await message.answer("⚠️ Ты уже активировал этот код ранее!")
        await state.clear()
        return
    reward = SECRET_CODES[code]
    wait_msg = await message.answer("⏳ Начисляю монеты на форум...")
    new_bal = (await asyncio.to_thread(get_xf_user_balance, user["xf_id"])) + reward
    if await asyncio.to_thread(set_xf_user_balance, user["xf_id"], new_bal):
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO used_codes (tg_id, code) VALUES ($1, $2)", tg_id, code)
        await wait_msg.edit_text(f"🎉 Код `{code}` активирован!\n➕ Начислено: **+{reward} 🪙**\n💰 Новый баланс: **{new_bal} 🪙**", parse_mode="Markdown")
    else:
        await wait_msg.edit_text("❌ Ошибка связи с форумом. Попробуй позже.")
    await state.clear()

@dp.message(F.text == "🛒 Магазин наград")
async def show_shop(message: types.Message):
    buttons = [[InlineKeyboardButton(text=f"{t} ({p} 🪙)", callback_data=f"buy_{i}")] for i, (t, p) in SHOP_ITEMS.items()]
    await message.answer("🛒 **МАГАЗИН НАГРАД VISE RUSSIA**\nВыбери товар для обмена монет:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    item_id = callback.data.replace("buy_", "")
    if item_id not in SHOP_ITEMS:
        return
    tg_id = callback.from_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_users WHERE tg_id = $1", tg_id)
    if not user:
        await callback.answer("Сначала привяжи ник с форума!", show_alert=True)
        return
    title, price = SHOP_ITEMS[item_id]
    bal = await asyncio.to_thread(get_xf_user_balance, user["xf_id"])
    if bal < price:
        await callback.answer(f"Недостаточно монет! У тебя {bal} 🪙, нужно {price} 🪙.", show_alert=True)
        return
    if await asyncio.to_thread(set_xf_user_balance, user["xf_id"], bal - price):
        await callback.message.answer(f"✅ Куплено: **{title}** за **{price} 🪙**! Заявка отправлена администрации.", parse_mode="Markdown")
        try:
            if ADMIN_CHAT_ID:
                await bot.send_message(ADMIN_CHAT_ID, f"🛍 **ПОКУПКА!**\nИгрок: `{user['xf_nick']}`\nTG: @{callback.from_user.username} (`{tg_id}`)\nТовар: **{title}** ({price} 🪙)", parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Admin notify error: {e}")
    await callback.answer()

@dp.message(F.text == "ℹ️ Правила ивента")
async def show_rules(message: types.Message):
    await message.answer("❄️ Ищи спрятанные подарки на форуме, вводи коды в боте и обменивай монеты в магазине! Каждый код работает 1 раз.")

async def health_check(request):
    return web.Response(text="VISE RUSSIA Bot is running!")

async def main():
    await init_db()
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
