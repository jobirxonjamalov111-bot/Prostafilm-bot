import asyncio
import os
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# 1. Sozlamalar — hammasi Railway Variables'dan olinadi, kodda hech narsa ochiq yozilmagan
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Baza fayli — Railway'da bu Volume ulangan papkada bo'lishi SHART, aks holda
# qayta deployda baza yana o'chib ketadi
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            series_code TEXT NOT NULL,
            episode_number INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (series_code, episode_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series_info (
            series_code TEXT PRIMARY KEY,
            description TEXT,
            photo_file_id TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_user(user_id: int) -> bool:
    """Foydalanuvchini bazaga qo'shadi. Agar u YANGI bo'lsa True qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )
    conn.commit()
    is_new = cursor.rowcount > 0
    conn.close()
    return is_new


def get_user_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count


def get_all_users() -> list[int]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [row[0] for row in rows]


def save_episode(series_code: str, episode_number: int, message_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO episodes (series_code, episode_number, message_id) VALUES (?, ?, ?)",
        (series_code, episode_number, message_id)
    )
    conn.commit()
    conn.close()


def get_episodes(series_code: str) -> list[tuple[int, int]]:
    """[(episode_number, message_id), ...] tartiblangan holda qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT episode_number, message_id FROM episodes WHERE series_code = ? ORDER BY episode_number",
        (series_code,)
    ).fetchall()
    conn.close()
    return rows


def get_episode_message_id(series_code: str, episode_number: int) -> int | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT message_id FROM episodes WHERE series_code = ? AND episode_number = ?",
        (series_code, episode_number)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def save_series_info(series_code: str, description: str, photo_file_id: str | None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO series_info (series_code, description, photo_file_id) VALUES (?, ?, ?)",
        (series_code, description, photo_file_id)
    )
    conn.commit()
    conn.close()


def get_series_info(series_code: str) -> tuple[str, str] | None:
    """(description, photo_file_id) qaytaradi, yo'q bo'lsa None."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT description, photo_file_id FROM series_info WHERE series_code = ?",
        (series_code,)
    ).fetchone()
    conn.close()
    return row if row else None


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


class BroadcastPost(StatesGroup):
    waiting_for_content = State()


class SeriesUpload(StatesGroup):
    waiting_for_code = State()
    waiting_for_description = State()
    waiting_for_poster = State()
    waiting_for_episode = State()


class EditContent(StatesGroup):
    waiting_for_code = State()
    waiting_for_action = State()
    waiting_for_description = State()
    waiting_for_poster = State()


# 0. Start komandasi
@dp.message(Command("start"))
async def start_command(message: types.Message):
    is_new = save_user(message.from_user.id)

    if is_new and message.from_user.id != ADMIN_ID:
        user = message.from_user
        username = f"@{user.username}" if user.username else "yo'q"
        total = get_user_count()
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🆕 Yangi foydalanuvchi! (#{total})\n\n"
                f"👤 Ism: {user.full_name}\n"
                f"🔗 Username: {username}\n"
                f"🆔 ID: {user.id}"
            )
        except Exception:
            logging.exception("Adminga xabar yuborishda xato:")

    await message.answer("🎬Assalomu alekum Prosta |film  , 🍿botimizga Xush kelibsiz! Kino kodini yuboring 👇 (masalan: 123)")


# 0.1. Admin uchun ommaviy xabar (broadcast) yuborish
@dp.message(Command("post"))
async def post_command(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "📢 Yubormoqchi bo'lgan xabaringizni yuboring "
        "(matn, rasm, video — qanday bo'lsa shunday ko'rinishda ketadi):"
    )
    await state.set_state(BroadcastPost.waiting_for_content)


# 0.2. Xabarni qabul qilib, hammaga tarqatish
@dp.message(BroadcastPost.waiting_for_content)
async def process_broadcast(message: types.Message, state: FSMContext):
    users = get_all_users()
    await state.clear()

    await message.answer(f"⏳ {len(users)} ta foydalanuvchiga yuborilmoqda...")

    success = 0
    failed = 0
    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Telegram limitiga tegib ketmaslik uchun

    await message.answer(f"✅ Yuborildi: {success} ta\n❌ Yuborilmadi: {failed} ta")


# 0.3. Serial yuklashni boshlash (faqat admin)
@dp.message(Command("serial"))
async def serial_command(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer("🎞 Serial uchun kod kiriting (masalan: 77):")
    await state.set_state(SeriesUpload.waiting_for_code)


# 0.4. Serial kodini qabul qilish, so'ng tavsifni so'rash
@dp.message(SeriesUpload.waiting_for_code)
async def process_series_code(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting!")
        return

    await state.update_data(series_code=message.text)
    await message.answer("📝 Serial haqida tavsif yozing (nomi, yili, janri, reytingi va h.k.):")
    await state.set_state(SeriesUpload.waiting_for_description)


# 0.4.1. Tavsifni qabul qilish, so'ng poster (rasm) so'rash
@dp.message(SeriesUpload.waiting_for_description)
async def process_series_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Iltimos, matn ko'rinishida tavsif yuboring!")
        return

    await state.update_data(description=message.text)
    await message.answer("🖼 Endi serial uchun poster (rasm) yuboring:")
    await state.set_state(SeriesUpload.waiting_for_poster)


# 0.4.2. Poster rasmini qabul qilish, so'ng qismlarni so'rash
@dp.message(SeriesUpload.waiting_for_poster, F.photo)
async def process_series_poster(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photo_file_id = message.photo[-1].file_id  # eng katta o'lchamdagisi

    save_series_info(data["series_code"], data["description"], photo_file_id)
    await state.update_data(next_episode=1)

    await message.answer(
        "🎬 1-qism videosini yuboring.\n"
        "Barcha qismlarni yuklab bo'lgach /done deb yozing."
    )
    await state.set_state(SeriesUpload.waiting_for_episode)


# 0.4.3. Poster o'rniga boshqa narsa yuborilsa
@dp.message(SeriesUpload.waiting_for_poster)
async def series_poster_wrong(message: types.Message):
    await message.answer("❗ Iltimos, rasm (poster) yuboring.")


# 0.5. Har bir qism videosini qabul qilib, kanalga yuborish
@dp.message(SeriesUpload.waiting_for_episode, F.video)
async def process_series_episode(message: types.Message, state: FSMContext):
    data = await state.get_data()
    series_code = data.get("series_code")
    episode_number = data.get("next_episode", 1)

    try:
        sent_msg = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=message.video.file_id,
            caption=f"🎬 Serial kodi: {series_code}\n📺 {episode_number}-qism"
        )
        save_episode(series_code, episode_number, sent_msg.message_id)
        await message.answer(
            f"✅ {episode_number}-qism saqlandi!\n"
            f"Keyingi qismni yuboring yoki tugatish uchun /done yozing."
        )
        await state.update_data(next_episode=episode_number + 1)
    except Exception:
        logging.exception("Serial qismini kanalga yuborishda xato:")
        await message.answer("⚠️ Xatolik yuz berdi, keyinroq urinib ko'ring.")


# 0.6. Serial yuklashni tugatish
@dp.message(Command("done"), SeriesUpload.waiting_for_episode)
async def finish_series(message: types.Message, state: FSMContext):
    data = await state.get_data()
    series_code = data.get("series_code")
    total_episodes = data.get("next_episode", 1) - 1
    await state.clear()

    if total_episodes == 0:
        await message.answer("❌ Hech qanday qism yuklanmadi, serial saqlanmadi.")
        return

    await message.answer(
        f"🎉 Serial saqlandi!\n"
        f"🔑 Kodi: {series_code}\n"
        f"📺 Jami qismlar: {total_episodes} ta"
    )


# 0.7. Serial holatida video yoki /done bo'lmagan xabarlar uchun
@dp.message(SeriesUpload.waiting_for_episode)
async def series_wrong_content(message: types.Message):
    await message.answer("❗ Iltimos, video yuboring yoki tugatish uchun /done yozing.")


# 0.8. Mavjud kino/serialni tahrirlashni boshlash (faqat admin)
@dp.message(Command("edit"))
async def edit_command(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer("✏️ Tahrirlamoqchi bo'lgan kino yoki serial kodini kiriting:")
    await state.set_state(EditContent.waiting_for_code)


# 0.9. Kodni tekshirish — kino yoki serial ekanini aniqlash
@dp.message(EditContent.waiting_for_code)
async def process_edit_code(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting!")
        return

    code = message.text
    is_series = bool(get_episodes(code))
    is_movie = get_movie(code) is not None

    if is_series:
        await state.update_data(code=code)
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text="📝 Tavsif/Poster", callback_data=f"edit_action:info:{code}"))
        builder.add(types.InlineKeyboardButton(text="➕ Yangi qism qo'shish", callback_data=f"edit_action:addep:{code}"))
        builder.adjust(1)
        await message.answer("Nimani tahrirlaysiz?", reply_markup=builder.as_markup())
        await state.set_state(EditContent.waiting_for_action)
    elif is_movie:
        await state.update_data(code=code, content_type="movie")
        await message.answer("📝 Yangi tavsif matnini yuboring:")
        await state.set_state(EditContent.waiting_for_description)
    else:
        await message.answer("❌ Bunday kodli kino yoki serial topilmadi!")
        await state.clear()


# 0.9.1. "Tavsif/Poster" tanlanganda
@dp.callback_query(F.data.startswith("edit_action:info:"))
async def edit_action_info(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    await state.update_data(code=code, content_type="series")

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data=f"edit_skip:description:{code}"))
    await callback.message.answer(
        "📝 Yangi tavsif matnini yuboring (o'zgartirmoqchi bo'lmasangiz, o'tkazib yuboring):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(EditContent.waiting_for_description)
    await callback.answer()


# 0.9.2. "Yangi qism qo'shish" tanlanganda
@dp.callback_query(F.data.startswith("edit_action:addep:"))
async def edit_action_addep(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    existing = get_episodes(code)
    next_episode = (existing[-1][0] + 1) if existing else 1

    await state.update_data(series_code=code, next_episode=next_episode)
    await callback.message.answer(
        f"🎬 {next_episode}-qism videosini yuboring.\n"
        f"Barcha yangi qismlarni yuklab bo'lgach /done deb yozing."
    )
    await state.set_state(SeriesUpload.waiting_for_episode)
    await callback.answer()


# 0.10. Yangi tavsifni qabul qilish (matn orqali)
@dp.message(EditContent.waiting_for_description)
async def process_edit_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Iltimos, matn ko'rinishida tavsif yuboring!")
        return

    data = await state.get_data()
    code = data["code"]
    content_type = data["content_type"]

    if content_type == "movie":
        new_caption = f"🎬 {message.text}\n\n🔑 Kino kodi: {code}"
        message_id = get_movie(code)
        try:
            await bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=message_id,
                caption=new_caption
            )
            await message.answer("✅ Tavsif muvaffaqiyatli yangilandi!")
        except Exception:
            logging.exception("Caption yangilashda xato:")
            await message.answer("⚠️ Xatolik yuz berdi, keyinroq urinib ko'ring.")
        await state.clear()
    else:
        await state.update_data(new_description=message.text)
        await ask_for_poster(message, state, code)


# 0.10.1. Tavsifni o'tkazib yuborish (eski tavsif saqlanadi)
@dp.callback_query(F.data.startswith("edit_skip:description:"))
async def edit_skip_description(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    old_info = get_series_info(code)
    old_description = old_info[0] if old_info else ""

    await state.update_data(new_description=old_description)
    await ask_for_poster(callback.message, state, code)
    await callback.answer()


async def ask_for_poster(message: types.Message, state: FSMContext, code: str):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data=f"edit_skip:poster:{code}"))
    await message.answer(
        "🖼 Yangi poster (rasm) yuboring (o'zgartirmoqchi bo'lmasangiz, o'tkazib yuboring):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(EditContent.waiting_for_poster)


# 0.11. Yangi poster qabul qilinganda
@dp.message(EditContent.waiting_for_poster, F.photo)
async def process_edit_poster_new(message: types.Message, state: FSMContext):
    data = await state.get_data()
    code = data["code"]
    new_photo_file_id = message.photo[-1].file_id

    save_series_info(code, data["new_description"], new_photo_file_id)
    await message.answer("✅ Tavsif va poster muvaffaqiyatli yangilandi!")
    await state.clear()


# 0.11.1. Posterni o'tkazib yuborish (eski poster saqlanadi)
@dp.callback_query(F.data.startswith("edit_skip:poster:"))
async def edit_skip_poster(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    code = data["code"]

    old_info = get_series_info(code)
    old_photo_file_id = old_info[1] if old_info else None

    save_series_info(code, data["new_description"], old_photo_file_id)
    await callback.message.answer("✅ Tavsif muvaffaqiyatli yangilandi (poster o'zgarmadi)!")
    await state.clear()
    await callback.answer()


@dp.message(EditContent.waiting_for_poster)
async def process_edit_poster_wrong(message: types.Message):
    await message.answer("❗ Iltimos, rasm yuboring yoki yuqoridagi \"O'tkazib yuborish\" tugmasini bosing.")


# 1. Videoni qabul qilish (faqat admin)
@dp.message(F.video)
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return  # admin bo'lmasa, e'tiborsiz qoldiramiz

    await state.update_data(video_file_id=message.video.file_id)
    await message.answer("✅ Video qabul qilindi. Endi kino uchun kod yozing (masalan: 123):")
    await state.set_state(UploadMovie.waiting_for_code)


# 2. Kodni qabul qilish, so'ng tavsifni so'rash
@dp.message(UploadMovie.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting!")
        return

    await state.update_data(code=message.text)
    await message.answer("📝 Endi kino haqida tavsif yozing (nomi, yili, janri va h.k.):")
    await state.set_state(UploadMovie.waiting_for_description)


# 3. Tavsifni qabul qilish, kanalga yuborish va bazaga yozish
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
        save_movie(code, sent_msg.message_id)
        await message.answer(f"🎉 Muvaffaqiyatli saqlandi! Kodi: {code}")
    except Exception:
        logging.exception("Kanalga video yuborishda xato:")
        await message.answer("⚠️ Xatolik yuz berdi, keyinroq urinib ko'ring.")
    finally:
        await state.clear()


# 4. Kino/serial qidirish (foydalanuvchi uchun)
@dp.message(F.text.isdigit())
async def get_movie_handler(message: types.Message):
    code = message.text
    episodes = get_episodes(code)

    if episodes:
        # Serial topildi — qismlar tugmalarini tayyorlaymiz
        builder = InlineKeyboardBuilder()
        for episode_number, _ in episodes:
            builder.add(types.InlineKeyboardButton(
                text=f"{episode_number}-qism",
                callback_data=f"ep:{code}:{episode_number}"
            ))
        builder.adjust(3)

        info = get_series_info(code)
        if info:
            description, photo_file_id = info
            caption = f"{description}\n\n🔑 Serial kodi: {code}"
            if photo_file_id:
                await message.answer_photo(
                    photo=photo_file_id,
                    caption=caption,
                    reply_markup=builder.as_markup()
                )
            else:
                await message.answer(caption, reply_markup=builder.as_markup())
        else:
            await message.answer("📺 Serial topildi! Qismni tanlang:", reply_markup=builder.as_markup())
        return

    message_id = get_movie(code)
    if not message_id:
        await message.answer("❌ Bunday kodli kino topilmadi!")
        return

    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=CHANNEL_ID,
            message_id=message_id
        )
    except Exception:
        logging.exception("Kino yuborishda xato:")
        await message.answer("❌ Kinoni yuborib bo'lmadi, keyinroq urinib ko'ring.")


# 4.1. Serial qismini tanlaganda yuborish
@dp.callback_query(F.data.startswith("ep:"))
async def send_episode(callback: types.CallbackQuery):
    try:
        _, series_code, episode_number = callback.data.split(":")
        episode_number = int(episode_number)
    except ValueError:
        await callback.answer("❌ Xato ma'lumot.", show_alert=True)
        return

    message_id = get_episode_message_id(series_code, episode_number)
    if not message_id:
        await callback.answer("❌ Bu qism topilmadi.", show_alert=True)
        return

    try:
        await bot.copy_message(
            chat_id=callback.message.chat.id,
            from_chat_id=CHANNEL_ID,
            message_id=message_id
        )
        await callback.answer()
    except Exception:
        logging.exception("Serial qismini yuborishda xato:")
        await callback.answer("❌ Yuborib bo'lmadi, keyinroq urinib ko'ring.", show_alert=True)


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
