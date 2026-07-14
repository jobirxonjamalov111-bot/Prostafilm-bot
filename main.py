import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# 1. Sozlamalar
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = 8003726053          # O'z IDingizni yozing
CHANNEL_ID = -1003988674227    # Kanal IDsi (bot shu yerda ADMIN bo'lishi shart)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ESLATMA: bu oddiy xotiradagi lug'at — bot qayta ishga tushganda (Railway restart
# qilganda) bu ma'lumot O'CHIB KETADI. Haqiqiy loyihada SQLite/PostgreSQL kerak.
MOVIES_DB: dict[str, int] = {}   # {"123": message_id}


class UploadMovie(StatesGroup):
    waiting_for_code = State()


# 2. Videoni qabul qilish (faqat admin)
@dp.message(F.video)
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return  # admin bo'lmasa, e'tiborsiz qoldiramiz

    await state.update_data(video_file_id=message.video.file_id)
    await message.answer("✅ Video qabul qilindi. Endi kino uchun kod yozing (masalan: 123):")
    await state.set_state(UploadMovie.waiting_for_code)


# 3. Kodni qabul qilish, kanalga yuborish va bazaga yozish
@dp.message(UploadMovie.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting!")
        return

    data = await state.get_data()
    video_id = data.get("video_file_id")

    try:
        sent_msg = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=video_id,
            caption=f"Kino kodi: {message.text}"
        )
        # Kod bilan kanal xabarini bog'laymiz
        MOVIES_DB[message.text] = sent_msg.message_id
        await message.answer(f"🎉 Muvaffaqiyatli saqlandi! Kodi: {message.text}")
    except Exception as e:
        logging.exception("Kanalga video yuborishda xato:")
        await message.answer(f"⚠️ Xatolik: {e}")
    finally:
        await state.clear()


# 4. Kino qidirish (Foydalanuvchi uchun)
@dp.message(F.text.isdigit())
async def get_movie(message: types.Message):
    message_id = MOVIES_DB.get(message.text)

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


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
