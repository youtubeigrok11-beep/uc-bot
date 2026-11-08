import asyncio
import html
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ============ SOZLAMALAR (server o'zgaruvchilaridan olinadi) ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
CARD_NUMBER = os.getenv("CARD_NUMBER", "8600 0000 0000 0000")
CARD_OWNER = os.getenv("CARD_OWNER", "ISM FAMILIYA")
SUPPORT = os.getenv("SUPPORT_USERNAME", "@admin")
DB_PATH = os.getenv("DB_PATH", "orders.db")

# UC miqdori: narx (so'mda). NARXLARNI O'ZINGIZGA MOSLAB O'ZGARTIRING!
PACKAGES = {
    60: 13000,
    325: 65000,
    660: 130000,
    1800: 325000,
    3850: 650000,
    8100: 1300000,
}

MENU_BUY = "🛒 UC sotib olish"
MENU_ORDERS = "📦 Buyurtmalarim"
MENU_HELP = "☎️ Aloqa"

router = Router()


# ============ BAZA ============
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                pubg_id TEXT NOT NULL,
                uc INTEGER NOT NULL,
                price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )"""
        )


def create_order(user_id, username, pubg_id, uc, price) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO orders (user_id, username, pubg_id, uc, price, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, username, pubg_id, uc, price, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        return cur.lastrowid


def get_order(order_id):
    with db() as conn:
        return conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def set_status_if_pending(order_id, status) -> bool:
    """Faqat 'pending' bo'lsa o'zgartiradi (ikki marta bosilishdan himoya)."""
    with db() as conn:
        cur = conn.execute(
            "UPDATE orders SET status=? WHERE id=? AND status='pending'", (status, order_id)
        )
        return cur.rowcount == 1


def user_orders(user_id, limit=10):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()


STATUS_TEXT = {"pending": "⏳ Kutilmoqda", "done": "✅ Bajarildi", "rejected": "❌ Rad etildi"}


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


# ============ HOLATLAR ============
class Order(StatesGroup):
    pubg_id = State()
    receipt = State()


# ============ KLAVIATURALAR ============
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MENU_BUY)], [KeyboardButton(text=MENU_ORDERS), KeyboardButton(text=MENU_HELP)]],
        resize_keyboard=True,
    )


def packages_kb():
    rows = [
        [InlineKeyboardButton(text=f"{uc} UC — {fmt(price)} so'm", callback_data=f"buy:{uc}")]
        for uc, price in PACKAGES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚫 Bekor qilish", callback_data="cancel")]])


def admin_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"done:{order_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject:{order_id}"),
            ]
        ]
    )


# ============ MIJOZ QISMI ============
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Assalomu alaykum! 👋\nPUBG Mobile UC xarid qilish botiga xush kelibsiz.\n\nQuyidagi menyudan tanlang:",
        reply_markup=main_menu(),
    )


@router.message(F.text == MENU_BUY)
async def buy(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Kerakli UC miqdorini tanlang:", reply_markup=packages_kb())


@router.message(F.text == MENU_HELP)
async def help_msg(message: Message):
    await message.answer(f"Savol yoki muammo bo'lsa, admin bilan bog'laning: {SUPPORT}")


@router.message(F.text == MENU_ORDERS)
async def my_orders(message: Message):
    orders = user_orders(message.from_user.id)
    if not orders:
        await message.answer("Sizda hali buyurtmalar yo'q.")
        return
    lines = [
        f"#{o['id']} — {o['uc']} UC — ID: {o['pubg_id']} — {STATUS_TEXT[o['status']]}" for o in orders
    ]
    await message.answer("Oxirgi buyurtmalaringiz:\n\n" + "\n".join(lines))


@router.callback_query(F.data == "cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Buyurtma bekor qilindi.")
    await call.answer()


@router.callback_query(F.data.startswith("buy:"))
async def choose_package(call: CallbackQuery, state: FSMContext):
    uc = int(call.data.split(":")[1])
    if uc not in PACKAGES:
        await call.answer("Bunday paket yo'q", show_alert=True)
        return
    await state.set_state(Order.pubg_id)
    await state.update_data(uc=uc, price=PACKAGES[uc])
    await call.message.edit_text(
        f"Siz tanladingiz: <b>{uc} UC</b> — {fmt(PACKAGES[uc])} so'm\n\n"
        "Endi <b>PUBG Mobile ID</b> raqamingizni yuboring (faqat raqamlar).",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(Order.pubg_id)
async def get_pubg_id(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not (5 <= len(text) <= 14):
        await message.answer("ID noto'g'ri. Faqat raqamlardan iborat PUBG ID yuboring (masalan: 5123456789).")
        return
    await state.update_data(pubg_id=text)
    data = await state.get_data()
    await state.set_state(Order.receipt)
    await message.answer(
        f"✅ ID: <b>{text}</b>\n"
        f"📦 Paket: <b>{data['uc']} UC</b>\n\n"
        f"Endi <b>{fmt(data['price'])} so'm</b> to'lang:\n"
        f"💳 Karta: <code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: {html.escape(CARD_OWNER)}\n\n"
        "To'lovdan keyin <b>chek skrinshotini</b> shu yerga yuboring.\n"
        "⚠️ ID to'g'ri ekanini tekshiring — noto'g'ri ID'ga yuborilgan UC qaytarilmaydi.",
        reply_markup=cancel_kb(),
    )


@router.message(Order.receipt, F.photo)
async def get_receipt(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    user = message.from_user
    order_id = create_order(user.id, user.username, data["pubg_id"], data["uc"], data["price"])
    await state.clear()

    caption = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n"
        f"👤 {html.escape(user.full_name)} "
        f"({'@' + user.username if user.username else 'username yo`q'}, <code>{user.id}</code>)\n"
        f"🎮 PUBG ID: <code>{data['pubg_id']}</code>\n"
        f"📦 {data['uc']} UC\n"
        f"💰 {fmt(data['price'])} so'm"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id, message.photo[-1].file_id, caption=caption, reply_markup=admin_kb(order_id)
            )
        except Exception as e:
            logging.warning("Adminga yuborib bo'lmadi %s: %s", admin_id, e)

    await message.answer(
        f"Rahmat! Buyurtmangiz #{order_id} qabul qilindi. ✅\n"
        "Admin chekni tekshirib, UC'ni yuboradi. Tayyor bo'lgach, sizga xabar keladi.",
        reply_markup=main_menu(),
    )


@router.message(Order.receipt)
async def receipt_not_photo(message: Message):
    await message.answer("Iltimos, to'lov chekining <b>rasmini</b> (skrinshot) yuboring.", reply_markup=cancel_kb())


# ============ ADMIN QISMI ============
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.callback_query(F.data.startswith(("done:", "reject:")))
async def admin_action(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return

    action, oid = call.data.split(":")
    order = get_order(int(oid))
    if not order:
        await call.answer("Buyurtma topilmadi", show_alert=True)
        return

    new_status = "done" if action == "done" else "rejected"
    if not set_status_if_pending(order["id"], new_status):
        await call.answer("Bu buyurtma allaqachon ko'rib chiqilgan", show_alert=True)
        return

    if new_status == "done":
        user_text = (
            f"✅ Buyurtma #{order['id']} bajarildi!\n"
            f"{order['uc']} UC ID <code>{order['pubg_id']}</code> ga yuborildi. O'yindan tekshirib ko'ring. 🎮"
        )
    else:
        user_text = (
            f"❌ Buyurtma #{order['id']} rad etildi (chek tasdiqlanmadi yoki ma'lumot noto'g'ri).\n"
            f"Aniqlik kiritish uchun admin bilan bog'laning: {SUPPORT}"
        )
    try:
        await bot.send_message(order["user_id"], user_text)
    except Exception as e:
        logging.warning("Mijozga xabar ketmadi: %s", e)

    mark = "✅ BAJARILDI" if new_status == "done" else "❌ RAD ETILDI"
    await call.message.edit_caption(caption=(call.message.caption or "") + f"\n\n<b>{mark}</b>", reply_markup=None)
    await call.answer("Mijozga xabar yuborildi")


@router.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) c, COALESCE(SUM(price),0) s FROM orders GROUP BY status"
        ).fetchall()
    if not rows:
        await message.answer("Hali buyurtma yo'q.")
        return
    lines = [f"{STATUS_TEXT[r['status']]}: {r['c']} ta, {fmt(r['s'])} so'm" for r in rows]
    await message.answer("📊 Statistika:\n\n" + "\n".join(lines))


@router.message(Command("pending"))
async def pending(message: Message):
    if not is_admin(message.from_user.id):
        return
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall()
    if not rows:
        await message.answer("Kutilayotgan buyurtma yo'q. 👍")
        return
    lines = [f"#{o['id']} — {o['uc']} UC — ID {o['pubg_id']} — {o['created_at']}" for o in rows]
    await message.answer("⏳ Kutilayotgan buyurtmalar:\n\n" + "\n".join(lines))


# ============ ISHGA TUSHIRISH ============
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. Server o'zgaruvchilariga qo'shing.")
    if not ADMIN_IDS:
        raise SystemExit("ADMIN_IDS topilmadi. O'zingizning Telegram ID'ingizni kiriting.")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
