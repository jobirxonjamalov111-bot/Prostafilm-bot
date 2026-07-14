import asyncio
import os
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# 1. Sozlamalar
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = 8003726053          # O'z IDingizni yozing
CHANNEL_ID = -1003988674227    # Kanal IDsi (bot shu yerda ADMIN bo'lishi shart)

# Baza fayli — Railway'da bu Volume ulangan papkada bo'lishi SHART, aks holda
# qayta deployda baza yana o'chib ketadi (pastdagi izohni o'qing)
DB_PATH = os.getenv("DB_PATH", "/data/movies.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            code TEXT PRIMARY KEY,
            message_id INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_movie(code: str, message_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO movies (code, message_id) VALUES (?, ?)",
        (code, message_id)
    )
    conn.commit()
    conn.close()


def get_movie(code: str) -> int | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT message_id FROM movies WHERE code = ?", (code,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


class UploadMovie(StatesGroup):
    waiting_for_code = State()
    waiting_for_description = State()


# 0. Start komandasi
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer("Xush kelibsiz! Kino kodini yuboring (masalan: 123).")


# 2. Videoni qabul qilish (faqat admin)
@dp.message(F.video)
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return  # admin bo'lmasa, e'tiborsiz qoldiramiz

    await state.update_data(video_file_id=message.video.file_id)
    await message.answer("✅ Video qabul qilindi. Endi kino uchun kod yozing (masalan: 123):")
    await state.set_state(UploadMovie.waiting_for_code)


# 3. Kodni qabul qilish, so'ng tavsifni so'rash
@dp.message(UploadMovie.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting!")
        return

    await state.update_data(code=message.text)
    await message.answer("📝 Endi kino haqida tavsif yozing (nomi, yili, janri va h.k.):")
    await state.set_state(UploadMovie.waiting_for_description)


# 3.1. Tavsifni qabul qilish, kanalga yuborish va bazaga yozish
@dp.message(UploadMovie.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Iltimos, matn ko'rinishida tavsif yuboring!")
        return

    data = await state.get_data()
    video_id = data.get("video_file_id")
    code = data.get("code")
    tavsif = message.text

    caption = f"🎬 {tavsif}\n\n🔑 Kino kodi: {code}"

    try:
        sent_msg = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=video_id,
            caption=caption
        )
        # Kod bilan kanal xabarini bog'laymiz (SQLite bazaga yozamiz)
        save_movie(code, sent_msg.message_id)
        await message.answer(f"🎉 Muvaffaqiyatli saqlandi! Kodi: {code}")
    except Exception as e:
        logging.exception("Kanalga video yuborishda xato:")
        await message.answer(f"⚠️ Xatolik: {e}")
    finally:
        await state.clear()


# 4. Kino qidirish (Foydalanuvchi uchun)
@dp.message(F.text.isdigit())
async def get_movie_handler(message: types.Message):
    message_id = get_movie(message.text)

    if not message_id:
        await message.answer("❌ Bunday kodli kino topilmadi!")
        return

    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=CHANNEL_ID,
            message_id=message_id
        )
    except Exception as e:
        logging.exception("Kino yuborishda xato:")
        await message.answer("❌ Kinoni yuborib bo'lmadi, keyinroq urinib ko'ring.")


# 5. Boshqa hech qaysi handlerga mos kelmagan xabarlar uchun
@dp.message()
async def fallback_handler(message: types.Message):
    await message.answer("❗ Iltimos, kino kodini raqam bilan yuboring yoki /start bosing.")


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
