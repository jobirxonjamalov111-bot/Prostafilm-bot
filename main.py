import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# 1. Tokenni Railway'dagi "Variables" qismidan oladi
API_TOKEN = os.getenv("API_TOKEN")

# 2. Logging
logging.basicConfig(level=logging.INFO)

# 3. Bot va Dispatcher yaratish (Dispatcher'ga bot yozilmaydi!)
bot = Bot(token=API_TOKEN)
dp = Dispatcher() 

# KANALLAR ro'yxati (o'zgartiring!)
KANALLAR = ["@sizning_kanal1", "@sizning_kanal2"]

async def check_sub(user_id: int) -> bool:
    for kanal in KANALLAR:
        try:
            member = await bot.get_chat_member(chat_id=kanal, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            continue
    return True

def get_sub_keyboard():
    builder = InlineKeyboardBuilder()
    for kanal in KANALLAR:
        builder.add(types.InlineKeyboardButton(text=f"Kanalga o'tish: {kanal}", url=f"https://t.me/{kanal.replace('@', '')}"))
    builder.add(types.InlineKeyboardButton(text="Tekshirish ✅", callback_data="check_subscription"))
    builder.adjust(1)
    return builder.as_markup()

def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Kino qidirish 🔍"))
    builder.add(types.KeyboardButton(text="Yordam ❓"))
    return builder.as_markup(resize_keyboard=True)

@dp.message(Command("start"))
async def start_command(message: types.Message):
    # Rasm linkini o'zingiznikiga almashtiring
    photo_url = "https://telegra.ph/file/..." 
    welcome_text = (
        f"👋 ✨ Salom, {message.from_user.first_name}!\n\n"
        "🎬 Prosta Film botimizga xush kelibsiz!\n\n"
        "👇 Istalgan kino kodini kiriting. Masalan: 33"
    )
    
    if not await check_sub(message.from_user.id):
        await message.answer("Assalomu alekum! Botdan foydalanish uchun kanallarga a'zo bo'ling!", reply_markup=get_sub_keyboard())
    else:
        await message.answer_photo(photo=photo_url, caption=welcome_text, reply_markup=get_main_keyboard())

@dp.callback_query(lambda call: call.data == "check_subscription")
async def check_callback(call: types.CallbackQuery):
    if await check_sub(call.from_user.id):
        await call.message.delete()
        await call.message.answer("🎉 Rahmat! Obuna tekshirildi.", reply_markup=get_main_keyboard())
    else:
        await call.answer("❌ Siz hali hamma kanalga a'zo bo'lmadingiz!", show_alert=True)

@dp.message(lambda message: message.text == "Kino qidirish 🔍")
async def search_movie(message: types.Message):
    await message.answer("🔢 Kino kodini kiriting:")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
