import asyncio
import os
import logging
import sqlite3
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

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
BOT_USERNAME = None  # main() ichida to'ldiriladi


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

    user_columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "joined_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN joined_at TEXT")
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            code TEXT PRIMARY KEY,
            downloads INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Eski bazalarda "description" / "poster_file_id" ustunlari bo'lmasligi mumkin — migratsiya
    existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(movies)").fetchall()]
    if "description" not in existing_columns:
        conn.execute("ALTER TABLE movies ADD COLUMN description TEXT")
    if "poster_file_id" not in existing_columns:
        conn.execute("ALTER TABLE movies ADD COLUMN poster_file_id TEXT")

    conn.commit()
    conn.close()


def save_user(user_id: int) -> bool:
    """Foydalanuvchini bazaga qo'shadi. Agar u YANGI bo'lsa True qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "INSERT OR IGNORE INTO users (user_id, joined_at) VALUES (?, ?)",
        (user_id, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    is_new = cursor.rowcount > 0
    conn.close()
    return is_new


def get_user_joined(user_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT joined_at FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


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


def increment_downloads(code: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO stats (code, downloads) VALUES (?, 1) "
        "ON CONFLICT(code) DO UPDATE SET downloads = downloads + 1",
        (code,)
    )
    conn.commit()
    conn.close()


def get_downloads(code: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT downloads FROM stats WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row[0] if row else 0


def save_movie(code: str, message_id: int, description: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    if description is None:
        # Faqat message_id yangilanmoqchi bo'lsa, mavjud tavsifni saqlab qolamiz
        existing = conn.execute("SELECT description FROM movies WHERE code = ?", (code,)).fetchone()
        description = existing[0] if existing else None
    conn.execute(
        "INSERT OR REPLACE INTO movies (code, message_id, description) VALUES (?, ?, ?)",
        (code, message_id, description)
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


def set_movie_poster(code: str, poster_file_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE movies SET poster_file_id = ? WHERE code = ?", (poster_file_id, code))
    conn.commit()
    conn.close()


def get_movie_poster(code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT poster_file_id FROM movies WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_movie_title(code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT description FROM movies WHERE code = ?", (code,)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def search_content(query: str) -> list[tuple[str, str, str]]:
    """Nom bo'yicha qidiradi. [(code, title, 'movie'|'series'), ...] qaytaradi."""
    like_query = f"%{query}%"
    conn = sqlite3.connect(DB_PATH)
    movie_rows = conn.execute(
        "SELECT code, description FROM movies WHERE description LIKE ?", (like_query,)
    ).fetchall()
    series_rows = conn.execute(
        "SELECT series_code, description FROM series_info WHERE description LIKE ?", (like_query,)
    ).fetchall()
    conn.close()

    results = [(code, desc, "movie") for code, desc in movie_rows if desc]
    results += [(code, desc, "series") for code, desc in series_rows if desc]
    return results


class UploadMovie(StatesGroup):
    waiting_for_code = State()
    waiting_for_description = State()
    waiting_for_poster = State()


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


class OrderRequest(StatesGroup):
    waiting_for_text = State()


class SettingsUpdate(StatesGroup):
    waiting_for_banner = State()
    waiting_for_welcome_text = State()


def get_setting(key: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🔍 Kino qidirish"))
    builder.add(types.KeyboardButton(text="❓ Yordam"))
    builder.add(types.KeyboardButton(text="🎬 Kino buyurtma berish"))
    builder.add(types.KeyboardButton(text="👤 Profilim"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


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

    # Deep-link orqali kelgan bo'lsa (masalan inline natijadan): /start code_33
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("code_"):
        code = parts[1][len("code_"):]
        delivered = await deliver_series_post(message.chat.id, code)
        if not delivered:
            delivered = await deliver_movie(message.chat.id, code)
        if delivered:
            return

    await send_welcome(message.chat.id)


DEFAULT_WELCOME_TEXT = (
    " Assalomu alaykum! Botimizga xush kelibsiz!\n\n"
    "🍿 Bot orqali siz kino/seriallarni nomi yoki kodi bo'yicha qidirishingiz mumkin.\n\n"
    "👇 Pastdagi tugmalardan foydalaning:"
)


async def send_welcome(chat_id: int):
    banner = get_setting("welcome_photo_file_id")
    text = get_setting("welcome_text") or DEFAULT_WELCOME_TEXT

    if banner:
        await bot.send_photo(
            chat_id,
            photo=banner,
            caption=text,
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
    else:
        await bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )


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


# 0.2.1. Admin uchun — welcome banner rasmini sozlash
@dp.message(Command("setbanner"))
async def setbanner_command(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer("🖼 Yangi banner rasmini yuboring:")
    await state.set_state(SettingsUpdate.waiting_for_banner)


@dp.message(SettingsUpdate.waiting_for_banner, F.photo)
async def process_banner(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    set_setting("welcome_photo_file_id", file_id)
    await message.answer("✅ Banner muvaffaqiyatli saqlandi!")
    await state.clear()


@dp.message(SettingsUpdate.waiting_for_banner)
async def process_banner_wrong(message: types.Message):
    await message.answer("❗ Iltimos, rasm yuboring.")


# 0.2.2. Admin uchun — welcome matnini sozlash
@dp.message(Command("setwelcome"))
async def setwelcome_command(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "📝 Yangi welcome matnini yuboring.\n\n"
        "Qalin matn uchun: <b>matn</b>\n"
        "Qiya matn uchun: <i>matn</i>\n"
        "(HTML teglaridan foydalanishingiz mumkin)"
    )
    await state.set_state(SettingsUpdate.waiting_for_welcome_text)


@dp.message(SettingsUpdate.waiting_for_welcome_text)
async def process_welcome_text(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Iltimos, matn ko'rinishida yuboring!")
        return

    set_setting("welcome_text", message.text)
    await message.answer("✅ Welcome matni muvaffaqiyatli saqlandi!")
    await state.clear()


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
            save_movie(code, message_id, message.text)
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


# 3. Tavsifni qabul qilish, so'ng poster so'rash
@dp.message(UploadMovie.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Iltimos, matn ko'rinishida tavsif yuboring!")
        return

    await state.update_data(description=message.text)

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="movie_skip_poster"))
    await message.answer(
        "🖼 Kino uchun poster (rasm) yuboring (o'zgartirmoqchi bo'lmasangiz, o'tkazib yuboring):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(UploadMovie.waiting_for_poster)


async def finalize_movie_upload(chat_id: int, state: FSMContext, poster_file_id: str | None):
    data = await state.get_data()
    video_id = data.get("video_file_id")
    code = data.get("code")
    tavsif = data.get("description")

    caption = f"🎬 {tavsif}\n\n🔑 Kino kodi: {code}"

    try:
        sent_msg = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=video_id,
            caption=caption
        )
        save_movie(code, sent_msg.message_id, tavsif)
        if poster_file_id:
            set_movie_poster(code, poster_file_id)
        await bot.send_message(chat_id, f"🎉 Muvaffaqiyatli saqlandi! Kodi: {code}")
    except Exception:
        logging.exception("Kanalga video yuborishda xato:")
        await bot.send_message(chat_id, "⚠️ Xatolik yuz berdi, keyinroq urinib ko'ring.")
    finally:
        await state.clear()


# 3.1. Poster rasmi qabul qilinganda — yuklashni yakunlash
@dp.message(UploadMovie.waiting_for_poster, F.photo)
async def process_movie_poster(message: types.Message, state: FSMContext):
    poster_file_id = message.photo[-1].file_id
    await finalize_movie_upload(message.chat.id, state, poster_file_id)


# 3.2. Posterni o'tkazib yuborish
@dp.callback_query(F.data == "movie_skip_poster", UploadMovie.waiting_for_poster)
async def skip_movie_poster(callback: types.CallbackQuery, state: FSMContext):
    await finalize_movie_upload(callback.message.chat.id, state, None)
    await callback.answer()


@dp.message(UploadMovie.waiting_for_poster)
async def process_movie_poster_wrong(message: types.Message):
    await message.answer("❗ Iltimos, rasm yuboring yoki yuqoridagi \"O'tkazib yuborish\" tugmasini bosing.")


# 4. Kino/serial qidirish (foydalanuvchi uchun) — kod orqali
@dp.message(F.text == "🔍 Kino qidirish")
async def search_button(message: types.Message):
    await message.answer(
        "🔎 Kino yoki serial kodini (masalan: 123) yoki nomini (masalan: Yunus Emre) yozing:"
    )


@dp.message(F.text == "❓ Yordam")
async def help_button(message: types.Message):
    await message.answer(
        "ℹ️ Botdan qanday foydalanish mumkin:\n\n"
        "1️⃣ Kino yoki serial kodini bilsangiz — shunchaki raqamni yuboring (masalan: 123)\n"
        "2️⃣ Kodni bilmasangiz — kino yoki serial nomini yozing (masalan: Yunus Emre)\n"
        "3️⃣ Chiqqan natijalardan birini tanlang\n\n"
        "Yordam kerak bo'lsa, botni qayta ishga tushirish uchun /start ni bosing."
    )


@dp.message(F.text == "👤 Profilim")
async def profile_button(message: types.Message):
    user = message.from_user
    username = f"@{user.username}" if user.username else "yo'q"
    joined = get_user_joined(user.id)
    joined_display = joined.split("T")[0] if joined else "noma'lum"

    await message.answer(
        "👤 Profilim\n\n"
        f"📛 Ism: {user.full_name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 ID: {user.id}\n"
        f"📅 Ro'yxatdan o'tgan sana: {joined_display}"
    )


@dp.message(F.text == "🎬 Kino buyurtma berish")
async def order_button(message: types.Message, state: FSMContext):
    await message.answer(
        "📝 Qaysi kino yoki serialni istayotganingizni yozing "
        "(nomi, yili — agar bilsangiz):"
    )
    await state.set_state(OrderRequest.waiting_for_text)


@dp.message(OrderRequest.waiting_for_text)
async def process_order(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Iltimos, matn ko'rinishida yozing.")
        return

    user = message.from_user
    username = f"@{user.username}" if user.username else "yo'q"

    try:
        await bot.send_message(
            ADMIN_ID,
            "🎬 Yangi kino buyurtmasi!\n\n"
            f"👤 Ism: {user.full_name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 ID: {user.id}\n\n"
            f"📝 So'ralgan: {message.text}"
        )
    except Exception:
        logging.exception("Buyurtmani adminga yuborishda xato:")

    await message.answer("✅ So'rovingiz qabul qilindi! Tez orada ko'rib chiqamiz.")
    await state.clear()


@dp.message(F.text.isdigit())
async def get_movie_handler(message: types.Message):
    code = message.text
    delivered = await deliver_series_post(message.chat.id, code)
    if delivered:
        return

    delivered = await deliver_movie(message.chat.id, code)
    if not delivered:
        await message.answer("❌ Bunday kodli kino topilmadi!")


async def deliver_series_post(chat_id: int, code: str) -> bool:
    """Agar bu kodda serial bo'lsa, poster+tavsif+tugmalarni yuboradi va True qaytaradi."""
    episodes = get_episodes(code)
    if not episodes:
        return False

    increment_downloads(code)

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
            await bot.send_photo(chat_id, photo=photo_file_id, caption=caption, reply_markup=builder.as_markup())
        else:
            await bot.send_message(chat_id, caption, reply_markup=builder.as_markup())
    else:
        await bot.send_message(chat_id, "📺 Serial topildi! Qismni tanlang:", reply_markup=builder.as_markup())
    return True


async def deliver_movie(chat_id: int, code: str) -> bool:
    """Agar bu kodda oddiy kino bo'lsa, yuboradi va True qaytaradi."""
    message_id = get_movie(code)
    if not message_id:
        return False

    try:
        await bot.copy_message(chat_id=chat_id, from_chat_id=CHANNEL_ID, message_id=message_id)
        increment_downloads(code)
    except Exception:
        logging.exception("Kino yuborishda xato:")
    return True


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


# 4.2. Nom bo'yicha qidiruv (kod emas, oddiy matn kiritilganda)
@dp.message(F.text)
async def search_by_name(message: types.Message):
    query = message.text.strip()
    if not query or query.startswith("/"):
        return

    results = search_content(query)
    if not results:
        await message.answer("❌ Hech narsa topilmadi. Kod yoki kino/serial nomini kiriting.")
        return

    builder = InlineKeyboardBuilder()
    for code, title, kind in results[:10]:
        icon = "📺" if kind == "series" else "🎬"
        short_title = title.split("\n")[0][:35]
        builder.add(types.InlineKeyboardButton(
            text=f"{icon} {short_title}",
            callback_data=f"pick:{kind}:{code}"
        ))
    builder.adjust(1)
    await message.answer("🔍 Natijalar:", reply_markup=builder.as_markup())


# 4.3. Qidiruv natijasidan birini tanlaganda yuborish
@dp.callback_query(F.data.startswith("pick:"))
async def pick_search_result(callback: types.CallbackQuery):
    _, kind, code = callback.data.split(":")

    if kind == "series":
        await deliver_series_post(callback.message.chat.id, code)
    else:
        await deliver_movie(callback.message.chat.id, code)
    await callback.answer()


# 5. Boshqa hech qaysi handlerga mos kelmagan xabarlar uchun
@dp.message()
async def fallback_handler(message: types.Message):
    await message.answer("❗ Iltimos, kino kodini raqam bilan yuboring yoki /start bosing.")


# 6. Inline qidiruv — foydalanuvchi istalgan chatda "@bot_username so'rov" yozganda
@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    results = search_content(query)[:20]
    items = []

    for code, title, kind in results:
        downloads = get_downloads(code)
        short_title = title.split("\n")[0][:60]
        description_line = f"⬇️ Yuklashlar: {downloads}"

        poster_file_id = get_movie_poster(code) if kind == "movie" else (get_series_info(code) or (None, None))[1]

        builder = InlineKeyboardBuilder()
        label = "🎬 Kinoni olish" if kind == "movie" else "📺 Barcha qismlarni ko'rish"
        builder.add(types.InlineKeyboardButton(
            text=label,
            url=f"https://t.me/{BOT_USERNAME}?start=code_{code}"
        ))

        caption = f"{title}\n\n🔑 Kodi: {code}"

        if poster_file_id:
            items.append(types.InlineQueryResultCachedPhoto(
                id=f"{kind}:{code}",
                photo_file_id=poster_file_id,
                title=short_title,
                description=description_line,
                caption=caption,
                reply_markup=builder.as_markup()
            ))
        else:
            items.append(types.InlineQueryResultArticle(
                id=f"{kind}:{code}",
                title=short_title,
                description=description_line,
                input_message_content=types.InputTextMessageContent(message_text=caption),
                reply_markup=builder.as_markup()
            ))

    await inline_query.answer(items, cache_time=5, is_personal=False)


async def main():
    global BOT_USERNAME
    init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
