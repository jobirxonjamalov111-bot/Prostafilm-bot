import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# Tokenni Railway'dagi Environment Variables'dan oladi
API_TOKEN = os.getenv("API_TOKEN")

# Bot va Dispatcher obyektlarini yaratish
bot = Bot(token=API_TOKEN)
dp = Dispatcher() # O'zgarish shu yerda: qavs ichi bo'sh bo'ladi

# KANALAR ro'yxati (bu o'zgaruvchini kodning boshida e'lon qiling)
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
    is_subscribed = await check_sub(message.from_user.id)
    
    if not is_subscribed:
        await message.answer(
            "Assalomu alekum! Botdan foydalanish uchun barcha homiy kanallarimizga a'zo bo'lishingiz majburiy!\n\n"
            "Iltimos, pastdagi kanallarga a'zo bo'lib, keyin 'Tekshirish' tugmasini bosing.",
            reply_markup=get_sub_keyboard()
        )
    else:
        await message.answer(
            f"👋 ✨ Salom, {message.from_user.first_name}!\n\n🎬 Prosta Film botimizga xush kelibsiz!",
            reply_markup=get_main_keyboard()
        )

@dp.callback_query(lambda call: call.data == "check_subscription")
async def check_callback(call: types.CallbackQuery):
    is_subscribed = await check_sub(call.from_user.id)
    
    if is_subscribed:
        await call.message.delete()
        await call.message.answer(
            "🎉 Rahmat! Obuna muvaffaqiyatli tekshirildi. Endi botdan foydalanishingiz mumkin!",
            reply_markup=get_main_keyboard()
        )
    else:
        await call.answer("❌ Siz hali hamma kanalga a'zo bo'lmadingiz!", show_alert=True)

@dp.message(lambda message: message.text == "Kino qidirish 🔍")
async def search_movie(message: types.Message):
    if not await check_sub(message.from_user.id):
        return await message.answer("❌ Botdan foydalanish uchun avval barcha kanallarga a'zo bo'ling!", reply_markup=get_sub_keyboard())
    await message.answer("🔢 **Kino kodini kiriting:**")

@dp.message(lambda message: message.text == "Yordam ❓")
async def help_command(message: types.Message):
    await message.answer("🆘 Yordam bo‘limi: Adminga yozing: @Tezrideadmin")

async def main():
    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
