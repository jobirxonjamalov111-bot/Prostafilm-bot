import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# 1. Sozlamalar
API_TOKEN = os.getenv("API_TOKEN")   # Railway'dagi Variable
ADMIN_ID = 8003726053                 # O'z Telegram IDingizni yozing (@userinfobot orqali bilib oling)
CHANNEL_ID = -1004335196627           # Kino turadigan yopiq kanal IDsi (bot shu kanalda ADMIN bo'lishi shart)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()


# 2. Asosiy tugmalar
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Kino qidirish 🔍"))
    return builder.as_markup(resize_keyboard=True)


# 3. Start komandasi
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer("Xush kelibsiz! Kino kodini yuboring.", reply_markup=get_main_keyboard())


# 4. "Kino qidirish" tugmasi bosilganda
@dp.message(F.text == "Kino qidirish 🔍")
async def ask_movie_code(message: types.Message):
    await message.answer("🔢 Kino kodini kiriting:")


# 5. Admin uchun kino yuklash (Botga yuborsangiz kanalga tashlaydi)
@dp.message(F.video)
async def upload_movie(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Siz admin emassiz!")
        return

    try:
        sent_msg = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=message.video.file_id,
            caption=message.caption
        )
        await message.answer(f"✅ Kino saqlandi! Kodi: {sent_msg.message_id}")
    except Exception as e:
        logging.exception("Kanalga video yuborishda xato:")
        await message.answer(f"⚠️ Kanalga yuborib bo'lmadi: {e}")


# 6. Kino qidirish (Kanal IDsi orqali)
@dp.message(F.text.isdigit())
async def get_movie(message: types.Message):
    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=CHANNEL_ID,
            message_id=int(message.text)
        )
    except Exception as e:
        logging.error(f"Kino topilmadi (kod: {message.text}): {e}")
        await message.answer("❌ Kino topilmadi yoki kod noto'g'ri!")


# 7. Tushunarsiz xabarlar uchun
@dp.message()
async def fallback_handler(message: types.Message):
    await message.answer("❗ Iltimos, kino kodini raqam bilan yuboring yoki menyudan foydalaning.")


async def main():
    # Eski webhook/polling to'qnashuvining oldini olish
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
