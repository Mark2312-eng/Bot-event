import os
import asyncio
import logging
import requests
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
XF_API_URL = os.getenv("XF_API_URL", "https://samp.date").rstrip("/")
XF_API_KEY = os.getenv("XF_API_KEY")
XF_API_USER = os.getenv("XF_API_USER", "1")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
CUSTOM_FIELD_ID = os.getenv("CUSTOM_FIELD_ID", "vise_coins")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vise-bot")

db_pool: asyncpg.Pool | None = None
bot: Bot | None = None
dp = Dispatcher()

# ---------- БД ----------

async def init_db():
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан")
    db_url = DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    db_pool = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=5)
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
    log.info("PostgreSQL подключена")

async def close_db():
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None

# ---------- XenForo ----------

def xf_headers():
    return {"XF-Api-Key": XF_API_KEY or "", "XF-Api-User": XF_API_USER}

def find_xf_user(username: str):
    try:
        resp = requests.get(
            f"{XF_API_URL}/users/find-name",
            params={"username": username},
            headers=xf_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("exact")
        log.warning(f"XF find-name {username}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"XF search error: {e}")
    return None

def get_xf_user_balance(user_id: int) -> float:
    try:
        resp = requests.get(f"{XF_API_URL}/users/{user_id}", headers=xf_headers(), timeout=10)
        if resp.status_code == 200:
            val = (
                resp.json().get("user", {})
                .get("custom_fields", {})
                .get(CUSTOM_FIELD_ID, "0")
            )
            if val is None or val == "":
                return 0.0
            return float(str(val).replace(",", "."))
    except Exception as e:
        log.error(f"XF read error: {e}")
    return 0.0

def set_xf_user_balance(user_id: int, new_balance: float) -> bool:
    try:
        if new_balance == int(new_balance):
            formatted = str(int(new_balance))
        else:
            formatted = str(round(new_balance, 2))
        resp = requests.post(
            f"{XF_API_URL}/users/{user_id}",
            data={f"custom_fields[{CUSTOM_FIELD_ID}]": formatted},
            headers=xf_headers(),
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f"XF write error: {e}")
    return False

def change_xf_balance(user_id: int, delta: float) -> float | None:
    """Атомарно (в рамках одного процесса) меняет баланс. Возвращает новый баланс или None."""
    cur = get_xf_user_balance(user_id)
    new = cur + delta
    if new < 0:
        return None
    if not set_xf_user_balance(user_id, new):
        return None
    return new

# ---------- FSM ----------

class EventStates(StatesGroup):
    waiting_for_nick = State()
    waiting_for_code = State()

# ---------- Клавиатуры ----------

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎁 Ввести код"), KeyboardButton(text="💰 Баланс")],
            [KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="👤 Мой профиль")],
        ],
        resize_keyboard=True,
    )

def shop_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, (title, price) in SHOP_ITEMS.items():
        rows.append([InlineKeyboardButton(text=f"{title} — {price:g}", callback_data=f"buy:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- Хендлеры ----------

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это бот новогоднего ивента VISE 🎄\n\n"
        "Привяжи свой игровой ник, чтобы получать и тратить VISE-коины.",
        reply_markup=main_kb(),
    )
    await message.answer("Введи свой игровой ник:")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_kb())

@dp.message(F.text == "👤 Мой профиль")
async def profile(message: types.Message):
    if not db_pool:
        await message.answer("БД не готова, попробуй позже.")
        return
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT xf_id, xf_nick FROM event_users WHERE tg_id=$1", message.from_user.id
        )
    if not row:
        await message.answer("Профиль не привязан. Отправь /start и введи ник.")
        return
    bal = await asyncio.to_thread(get_xf_user_balance, row["xf_id"])
    await message.answer(
        f"👤 Ник: <b>{row['xf_nick']}</b>\n"
        f"🆔 XF ID: <code>{row['xf_id']}</code>\n"
        f"💰 Баланс: <b>{bal:g}</b> VISE",
        parse_mode="HTML",
    )

@dp.message(F.text == "💰 Баланс")
async def balance(message: types.Message):
    if not db_pool:
        await message.answer("БД не готова, попробуй позже.")
        return
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT xf_id FROM event_users WHERE tg_id=$1", message.from_user.id
        )
    if not row:
        await message.answer("Сначала привяжи ник через /start.")
        return
    bal = await asyncio.to_thread(get_xf_user_balance, row["xf_id"])
    await message.answer(f"💰 Твой баланс: <b>{bal:g}</b> VISE", parse_mode="HTML")

@dp.message(F.text == "🎁 Ввести код")
async def ask_code(message: types.Message, state: FSMContext):
    if not db_pool:
        await message.answer("БД не готова, попробуй позже.")
        return
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT xf_id FROM event_users WHERE tg_id=$1", message.from_user.id
        )
    if not row:
        await message.answer("Сначала привяжи ник через /start.")
        return
    await state.set_state(EventStates.waiting_for_code)
    await message.answer("Введи секретный код из письма/поста:")

@dp.message(EventStates.waiting_for_code)
async def handle_code(message: types.Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    await state.clear()
    if code not in SECRET_CODES:
        await message.answer("❌ Такого кода нет. Проверь написание.")
        return
    reward = SECRET_CODES[code]
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT xf_id, xf_nick FROM event_users WHERE tg_id=$1", message.from_user.id
        )
        if not row:
            await message.answer("Сначала привяжи ник через /start.")
            return
        try:
            await conn.execute(
                "INSERT INTO used_codes(tg_id, code) VALUES($1, $2)",
                message.from_user.id, code,
            )
        except asyncpg.UniqueViolationError:
            await message.answer("⚠️ Этот код ты уже активировал.")
            return
    new_bal = await asyncio.to_thread(change_xf_balance, row["xf_id"], reward)
    if new_bal is None:
        # откат — если не удалось начислить, разрешаем повторно
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM used_codes WHERE tg_id=$1 AND code=$2",
                message.from_user.id, code,
            )
        await message.answer("❌ Не удалось начислить награду. Попробуй позже.")
        return
    await message.answer(
        f"✅ Код <code>{code}</code> активирован!\n"
        f"Начислено: <b>+{reward:g}</b> VISE\n"
        f"Новый баланс: <b>{new_bal:g}</b>",
        parse_mode="HTML",
    )
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"🎁 {row['xf_nick']} (tg {message.from_user.id}) активировал {code} (+{reward:g})",
            )
        except Exception as e:
            log.warning(f"admin notify: {e}")

@dp.message(F.text == "🛒 Магазин")
async def shop(message: types.Message):
    if not db_pool:
        await message.answer("БД не готова, попробуй позже.")
        return
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT xf_id FROM event_users WHERE tg_id=$1", message.from_user.id
        )
    if not row:
        await message.answer("Сначала привяжи ник через /start.")
        return
    bal = await asyncio.to_thread(get_xf_user_balance, row["xf_id"])
    await message.answer(
        f"🛒 Магазин (баланс: <b>{bal:g}</b> VISE):",
        parse_mode="HTML",
        reply_markup=shop_kb(),
    )

@dp.callback_query(F.data.startswith("buy:"))
async def buy_cb(cb: types.CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in SHOP_ITEMS:
        await cb.answer("Товар не найден", show_alert=True)
        return
    title, price = SHOP_ITEMS[key]
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT xf_id, xf_nick FROM event_users WHERE tg_id=$1", cb.from_user.id
        )
    if not row:
        await cb.answer("Сначала привяжи ник через /start.", show_alert=True)
        return
    new_bal = await asyncio.to_thread(change_xf_balance, row["xf_id"], -price)
    if new_bal is None:
        await cb.answer("Недостаточно средств или ошибка сервера.", show_alert=True)
        return
    await cb.message.edit_text(
        f"✅ Куплено: <b>{title}</b>\n"
        f"Списано: <b>{price:g}</b> VISE\n"
        f"Остаток: <b>{new_bal:g}</b>",
        parse_mode="HTML",
    )
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"🛒 {row['xf_nick']} (tg {cb.from_user.id}) купил {title} за {price:g}",
            )
        except Exception as e:
            log.warning(f"admin notify: {e}")
    await cb.answer("Готово")

@dp.message(EventStates.waiting_for_nick)
async def handle_nick(message: types.Message, state: FSMContext):
    nick = (message.text or "").strip()
    if not nick or len(nick) < 2 or len(nick) > 32:
        await message.answer("Ник должен быть от 2 до 32 символов. Попробуй ещё:")
        return
    if not db_pool:
        await message.answer("БД не готова, попробуй позже.")
        return
    await message.answer("🔎 Ищу пользователя на форуме...")
    xf_user = await asyncio.to_thread(find_xf_user, nick)
    if not xf_user:
        await message.answer(
            "❌ Пользователь с таким ником не найден на форуме.\n"
            "Проверь написание и попробуй снова:"
        )
        return
    xf_id = int(xf_user["user_id"])
    xf_nick = xf_user["username"]
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO event_users(tg_id, xf_id, xf_nick)
               VALUES($1, $2, $3)
               ON CONFLICT (tg_id) DO UPDATE
               SET xf_id = EXCLUDED.xf_id, xf_nick = EXCLUDED.xf_nick""",
            message.from_user.id, xf_id, xf_nick,
        )
    await state.clear()
    await message.answer(
        f"✅ Привязано: <b>{xf_nick}</b> (ID {xf_id})\n"
        f"Можешь вводить коды и покупать товары.",
        parse_mode="HTML",
        reply_markup=main_kb(),
    )

@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current == EventStates.waiting_for_nick.state:
        await handle_nick(message, state)
        return
    if current == EventStates.waiting_for_code.state:
        await handle_code(message, state)
        return
    # если ничего не ждём — начинаем привязку ника
    await state.set_state(EventStates.waiting_for_nick)
    await message.answer("Введи свой игровой ник (как на форуме):")

# ---------- Web healthcheck ----------

async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "vise-bot"})

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Web healthcheck на :{PORT}")

# ---------- main ----------

async def main():
    global bot
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    if not XF_API_KEY:
        raise RuntimeError("XF_API_KEY не задан")
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    await start_web()
    log.info("Бот запускается (polling)...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await close_db()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлен")
