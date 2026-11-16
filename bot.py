import os
import asyncio
import logging
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
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
    db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL and DATABASE_URL.startswith("postgres://") else DATABASE_URL
    db_pool = await asyncpg.create_pool(dsn=db_url)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS event_players (
                tg_id BIGINT PRIMARY KEY,
                xf_nick TEXT NOT NULL,
                balance DOUBLE PRECISION DEFAULT 0
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
    logging.info("База данных PostgreSQL готова к работе!")

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
        user = await conn.fetchrow("SELECT * FROM event_players WHERE tg_id = $1", message.from_user.id)
    await message.answer(
        "❄️ **Добро пожаловать в ивент-бота VISE RUSSIA!** 🎁\n\n"
        "Ищи спрятанные подарки на форуме, вводи коды здесь и обменивай монеты на эксклюзивные награды!\n"
        "🔗 Форум проекта: https://samp.date",
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
    if len(nick) < 2 or len(nick) > 40:
        await message.answer("❌ Некорректная длина ника. Введи свой ник с форума:")
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO event_players (tg_id, xf_nick, balance) VALUES ($1, $2, 0) ON CONFLICT (tg_id) DO UPDATE SET xf_nick = $2",
            message.from_user.id, nick
        )
    await state.clear()
    await message.answer(f"✅ Ник **{nick}** успешно привязан!\nТеперь ты можешь вводить найденные коды.", parse_mode="Markdown")

@dp.message(F.text == "🪙 Мой баланс")
async def show_balance(message: types.Message):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_players WHERE tg_id = $1", message.from_user.id)
        used_count = await conn.fetchval("SELECT COUNT(*) FROM used_codes WHERE tg_id = $1", message.from_user.id)
    if not user:
        await message.answer("⚠️ Сначала привяжи свой ник с форума!")
        return
    bal = int(user["balance"]) if float(user["balance"]).is_integer() else round(user["balance"], 2)
    await message.answer(f"👤 Профиль: `{user['xf_nick']}`\n🪙 Баланс: `{bal} монет`\n🎁 Найдено подарков: `{used_count}/{len(SECRET_CODES)}`", parse_mode="Markdown")

@dp.message(F.text == "🎁 Ввести секретный код")
async def ask_code(message: types.Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_players WHERE tg_id = $1", message.from_user.id)
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
        already = await conn.fetchrow("SELECT id FROM used_codes WHERE tg_id = $1 AND code = $2", tg_id, code)
        if already:
            await message.answer("⚠️ Ты уже активировал этот код ранее!")
            await state.clear()
            return
        reward = SECRET_CODES[code]
        await conn.execute("INSERT INTO used_codes (tg_id, code) VALUES ($1, $2)", tg_id, code)
        new_bal = await conn.fetchval("UPDATE event_players SET balance = balance + $1 WHERE tg_id = $2 RETURNING balance", reward, tg_id)
    formatted_bal = int(new_bal) if float(new_bal).is_integer() else round(new_bal, 2)
    await message.answer(f"🎉 Код `{code}` активирован!\n➕ Начислено: **+{reward} 🪙**\n💰 Новый баланс: **{formatted_bal} 🪙**", parse_mode="Markdown")
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
    title, price = SHOP_ITEMS[item_id]
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM event_players WHERE tg_id = $1", tg_id)
        if not user:
            await callback.answer("Сначала привяжи ник с форума!", show_alert=True)
            return
        
        # Безопасное списание (защита от двойного клика)
        new_bal = await conn.fetchval(
            "UPDATE event_players SET balance = balance - $1 WHERE tg_id = $2 AND balance >= $1 RETURNING balance",
            price, tg_id
        )
        if new_bal is None:
            await callback.answer(f"Недостаточно монет! У тебя {user['balance']} 🪙, нужно {price} 🪙.", show_alert=True)
            return

    await callback.message.answer(f"✅ Куплено: **{title}** за **{price} 🪙**!\nЗаявка отправлена администрации — награда скоро будет выдана.", parse_mode="Markdown")
    try:
        if ADMIN_CHAT_ID:
            username = callback.from_user.username or "нет_юзернейма"
            await bot.send_message(ADMIN_CHAT_ID, f"🛍 **НОВАЯ ПОКУПКА!**\nИгрок: `{user['xf_nick']}`\nTG: @{username} (`{tg_id}`)\nТовар: **{title}** ({price} 🪙)", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления админу: {e}")
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
