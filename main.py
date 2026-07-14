import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# 1. Tokenni Railway'dagi "Variables" qismidan oladi
API_TOKEN = os.getenv("API_TOKEN")

# 2. Logging — xatolarni to'liq ko'rish uchun
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 3. Bot va Dispatcher yaratish
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# KANALLAR ro'yxati — O'ZINGIZNIKIGA ALMASHTIRING!
# Bot bu kanallarga ADMIN qilib qo'shilgan bo'lishi shart, aks holda tekshiruv ishlamaydi
KANALLAR = ["@sizning_kanal1", "@sizning_kanal2"]

# Salomlashuv rasmi — O'ZINGIZNIKIGA ALMASHTIRING (haqiqiy to'liq URL bo'lishi kerak)
# Agar rasm kerak bo'lmasa, PHOTO_URL = None qoldiring
PHOTO_URL = None  # masalan: "https://i.imgur.com/abcd123.jpg"


async def check_sub(user_id: int) -> bool:
    """Foydalanuvchi barcha kanallarga a'zo bo'lganini tekshiradi."""
    for kanal in KANALLAR:
        try:
            member = await bot.get_chat_member(chat_id=kanal, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            # Kanal topilmasa yoki bot admin bo'lmasa — shu yerda bilib olamiz
            logging.error(f"Kanal tekshiruvida xato ({kanal}): {e}")
            return False
    return True


def get_sub_keyboard():
    builder = InlineKeyboardBuilder()
    for kanal in KANALLAR:
        builder.add(types.InlineKeyboardButton(
            text=f"Kanalga o'tish: {kanal}",
            url=f"https://t.me/{kanal.replace('@', '')}"
        ))
    builder.add(types.InlineKeyboardButton(text="Tekshirish ✅", callback_data="check_subscription"))
    builder.adjust(1)
    return builder.as_markup()


def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Kino qidirish 🔍"))
    builder.add(types.KeyboardButton(text="Yordam ❓"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


@dp.message(Command("start"))
async def start_command(message: types.Message):
    try:
        welcome_text = (
            f"👋 ✨ Salom, {message.from_user.first_name}!\n\n"
            "🎬 Prosta Film botimizga xush kelibsiz!\n\n"
            "👇 Istalgan kino kodini kiriting. Masalan: 33"
        )

        if not await check_sub(message.from_user.id):
            await message.answer(
                "Assalomu alekum! Botdan foydalanish uchun kanallarga a'zo bo'ling!",
                reply_markup=get_sub_keyboard()
            )
            return

        if PHOTO_URL:
            await message.answer_photo(photo=PHOTO_URL, caption=welcome_text, reply_markup=get_main_keyboard())
        else:
            await message.answer(welcome_text, reply_markup=get_main_keyboard())

    except Exception as e:
        logging.exception("start_command da xato:")
        await message.answer(f"⚠️ Xatolik yuz berdi: {e}")


@dp.callback_query(lambda call: call.data == "check_subscription")
async def check_callback(call: types.CallbackQuery):
    try:
        if await check_sub(call.from_user.id):
            await call.message.delete()
            await call.message.answer("🎉 Rahmat! Obuna tekshirildi.", reply_markup=get_main_keyboard())
        else:
            await call.answer("❌ Siz hali hamma kanalga a'zo bo'lmadingiz!", show_alert=True)
    except Exception as e:
        logging.exception("check_callback da xato:")
        await call.answer("⚠️ Xatolik yuz berdi, qayta urinib ko'ring.", show_alert=True)


@dp.message(lambda message: message.text == "Kino qidirish 🔍")
async def search_movie(message: types.Message):
    await message.answer("🔢 Kino kodini kiriting:")


@dp.message(lambda message: message.text == "Yordam ❓")
async def help_command(message: types.Message):
    await message.answer(
        "ℹ️ Botdan foydalanish:\n\n"
        "1️⃣ Kanallarga a'zo bo'ling\n"
        "2️⃣ 'Kino qidirish' tugmasini bosing yoki to'g'ridan-to'g'ri kino kodini kiriting\n"
        "3️⃣ Kod raqamini yuboring, masalan: 33"
    )


@dp.message(lambda message: message.text and message.text.isdigit())
async def movie_code_handler(message: types.Message):
    kino_kod = message.text
    # Bu yerga o'z kino bazangizni ulaysiz (masalan JSON, SQLite yoki boshqa manba)
    await message.answer(f"🔎 '{kino_kod}' kodli kino qidirilmoqda...\n\n(Bu yerga kino bazasi ulanishi kerak)")


@dp.message()
async def fallback_handler(message: types.Message):
    await message.answer("❗ Tushunarsiz buyruq. Iltimos, menyudan foydalaning yoki kino kodini kiriting.")


async def main():
    # Muhim: agar avval webhook ishlatilgan bo'lsa, uni o'chirish shart,
    # aks holda polling ishlamaydi
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
