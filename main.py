import asyncio
import os
import logging
import sqlite3
import aiohttp
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

IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "")

# Majburiy obuna kanallari, vergul bilan ajratilgan: @kanal1,@kanal2
MANDATORY_CHANNELS = [c.strip() for c in os.getenv("MANDATORY_CHANNELS", "").split(",") if c.strip()]

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

    # Eski bazalarda "description" / "poster_file_id" / "poster_url" ustunlari bo'lmasligi mumkin — migratsiya
    existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(movies)").fetchall()]
    if "description" not in existing_columns:
        conn.execute("ALTER TABLE movies ADD COLUMN description TEXT")
    if "poster_file_id" not in existing_columns:
        conn.execute("ALTER TABLE movies ADD COLUMN poster_file_id TEXT")
    if "poster_url" not in existing_columns:
        conn.execute("ALTER TABLE movies ADD COLUMN poster_url TEXT")
    if "video_file_id" not in existing_columns:
        conn.execute("ALTER TABLE movies ADD COLUMN video_file_id TEXT")

    series_columns = [row[1] for row in conn.execute("PRAGMA table_info(series_info)").fetchall()]
    if "poster_url" not in series_columns:
        conn.execute("ALTER TABLE series_info ADD COLUMN poster_url TEXT")

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


def save_series_info(series_code: str, description: str, photo_file_id: str | None, poster_url: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO series_info (series_code, description, photo_file_id, poster_url) VALUES (?, ?, ?, ?)",
        (series_code, description, photo_file_id, poster_url)
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


def get_series_poster_url(series_code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT poster_url FROM series_info WHERE series_code = ?", (series_code,)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def increment_downloads(code: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO stats (code, downloads) VALUES (?, 1) "
        "ON CONFLICT(code) DO UPDATE SET downloads = downloads + 1",
        (code,)
    )
    conn.commit()
    conn.close()


def get_download_baseline() -> int:
    value = get_setting("download_baseline")
    return int(value) if value and value.isdigit() else 0


def set_download_baseline(value: int):
    set_setting("download_baseline", str(value))


def get_downloads(code: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT downloads FROM stats WHERE code = ?", (code,)).fetchone()
    conn.close()
    real_count = row[0] if row else 0
    return real_count + get_download_baseline()


def save_movie(code: str, message_id: int, description: str | None = None, video_file_id: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute(
        "SELECT description, poster_file_id, poster_url, video_file_id FROM movies WHERE code = ?", (code,)
    ).fetchone()
    old_description, old_poster_file_id, old_poster_url, old_video_file_id = existing if existing else (None, None, None, None)

    final_description = description if description is not None else old_description
    final_video_file_id = video_file_id if video_file_id is not None else old_video_file_id

    conn.execute(
        "INSERT OR REPLACE INTO movies (code, message_id, description, poster_file_id, poster_url, video_file_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (code, message_id, final_description, old_poster_file_id, old_poster_url, final_video_file_id)
    )
    conn.commit()
    conn.close()


def get_movie_video_file_id(code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT video_file_id FROM movies WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_movie(code: str) -> int | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT message_id FROM movies WHERE code = ?", (code,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def set_movie_poster(code: str, poster_file_id: str, poster_url: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE movies SET poster_file_id = ?, poster_url = ? WHERE code = ?",
        (poster_file_id, poster_url, code)
    )
    conn.commit()
    conn.close()


def get_movie_poster(code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT poster_file_id FROM movies WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_movie_poster_url(code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT poster_url FROM movies WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_movie_title(code: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT description FROM movies WHERE code = ?", (code,)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_all_content() -> list[tuple[str, str, str]]:
    """Barcha kino va seriallarni [(code, title, kind), ...] ko'rinishida qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    movie_rows = conn.execute("SELECT code, description FROM movies WHERE description IS NOT NULL").fetchall()
    series_rows = conn.execute("SELECT series_code, description FROM series_info WHERE description IS NOT NULL").fetchall()
    conn.close()

    results = [(code, desc, "movie") for code, desc in movie_rows if desc]
    results += [(code, desc, "series") for code, desc in series_rows if desc]
    return results


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
    waiting_for_movie_poster = State()
    waiting_for_movie_video = State()
    waiting_for_episode_video = State()


class OrderRequest(StatesGroup):
    waiting_for_text = State()


class SettingsUpdate(StatesGroup):
    waiting_for_banner = State()
    waiting_for_welcome_text = State()


async def upload_poster_to_telegraph(photo_file_id: str) -> str | None:
    """Posterni ImgBB'ga yuklab, ochiq URL qaytaradi (xato bo'lsa None)."""
    if not IMGBB_API_KEY:
        logging.warning("IMGBB_API_KEY sozlanmagan — poster yuklanmaydi.")
        return None

    try:
        file = await bot.get_file(photo_file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        data = file_bytes_io.read()

        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("key", IMGBB_API_KEY)
            form.add_field("image", data, filename="poster.jpg", content_type="image/jpeg")
            async with session.post(
                "https://api.imgbb.com/1/upload",
                data=form,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                result = await resp.json()
                if resp.status == 200 and result.get("success"):
                    url = result["data"]["url"]
                    logging.info(f"ImgBB'ga muvaffaqiyatli yuklandi: {url}")
                    return url
                else:
                    logging.error(f"ImgBB javob berdi: {result}")
    except Exception:
        logging.exception("Rasmni yuklashda xato:")
    return None


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


async def check_subscription(user_id: int) -> bool:
    """Foydalanuvchi barcha majburiy kanallarga a'zo ekanini tekshiradi."""
    if not MANDATORY_CHANNELS:
        return True

    for channel in MANDATORY_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            logging.exception(f"Obuna tekshirishda xato ({channel}):")
            continue
    return True


def get_subscribe_keyboard():
    builder = InlineKeyboardBuilder()
    for channel in MANDATORY_CHANNELS:
        builder.add(types.InlineKeyboardButton(
            text=f"➡️ {channel}",
            url=f"https://t.me/{channel.replace('@', '')}"
        ))
    builder.add(types.InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription"))
    builder.adjust(1)
    return builder.as_markup()


def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🔍 Kino qidirish", switch_inline_query_current_chat=""))
    builder.add(types.InlineKeyboardButton(text="❓ Yordam", callback_data="menu:help"))
    builder.add(types.InlineKeyboardButton(text="🎬 Kino buyurtma berish", callback_data="menu:order"))
    builder.add(types.InlineKeyboardButton(text="👤 Profilim", callback_data="menu:profile"))
    builder.adjust(2)
    return builder.as_markup()


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

    # Majburiy obuna tekshiruvi (adminga tegishli emas)
    if message.from_user.id != ADMIN_ID and not await check_subscription(message.from_user.id):
        await message.answer(
            "⚠️ Botdan foydalanish uchun quyidagi kanal(lar)ga a'zo bo'ling, "
            "so'ng \"✅ Tekshirish\" tugmasini bosing:",
            reply_markup=get_subscribe_keyboard()
        )
        return

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


@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    if await check_subscription(callback.from_user.id):
        await callback.message.delete()
        await send_welcome(callback.message.chat.id)
    else:
        await callback.answer("❌ Siz hali hamma kanalga a'zo bo'lmadingiz!", show_alert=True)


DEFAULT_WELCOME_TEXT = (
    "👋 Assalomu alaykum! Botimizga xush kelibsiz!\n\n"
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


# 0.2.3. Admin uchun — yuklashlar sonining boshlang'ich bazasini sozlash
@dp.message(Command("setbaseline"))
async def setbaseline_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        current = get_download_baseline()
        await message.answer(
            f"ℹ️ Hozirgi boshlang'ich raqam: {current}\n\n"
            f"O'zgartirish uchun: /setbaseline 100"
        )
        return

    new_value = int(parts[1].strip())
    set_download_baseline(new_value)
    await message.answer(f"✅ Boshlang'ich raqam {new_value} ga o'rnatildi. Endi har bir kino/serial kamida shuncha yuklashlar bilan ko'rinadi.")


# 0.2.4. Admin uchun — mavjud posterni Telegraph'ga qayta yuklashga urinish
@dp.message(Command("fixposter"))
async def fixposter_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Foydalanish: /fixposter <kod>\nMasalan: /fixposter 5")
        return

    code = parts[1].strip()
    is_series = bool(get_episodes(code))

    if is_series:
        info = get_series_info(code)
        photo_file_id = info[1] if info else None
    else:
        photo_file_id = get_movie_poster(code)

    if not photo_file_id:
        await message.answer("❌ Bu kod uchun saqlangan poster topilmadi (avval rasm yuklashingiz kerak).")
        return

    await message.answer("⏳ Qayta yuklanmoqda...")
    poster_url = await upload_poster_to_telegraph(photo_file_id)

    if not poster_url:
        await message.answer("❌ Yuklab bo'lmadi. Railway loglarini tekshiring (aniq xato u yerda ko'rinadi).")
        return

    if is_series:
        old_description = info[0] if info else ""
        save_series_info(code, old_description, photo_file_id, poster_url)
    else:
        set_movie_poster(code, photo_file_id, poster_url)

    await message.answer(f"✅ Muvaffaqiyatli! Yangi URL: {poster_url}")


# 0.2.5. Admin uchun — eski kinolarda video_file_id'ni tiklash (qayta yuklamasdan)
@dp.message(Command("fixvideo"))
async def fixvideo_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Foydalanish: /fixvideo <kod>\nMasalan: /fixvideo 100")
        return

    code = parts[1].strip()
    channel_message_id = get_movie(code)

    if not channel_message_id:
        await message.answer("❌ Bu kodli kino topilmadi.")
        return

    await message.answer("⏳ Video ma'lumoti tiklanmoqda...")

    try:
        fwd = await bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=CHANNEL_ID,
            message_id=channel_message_id
        )
        if not fwd.video:
            await message.answer("❌ Bu xabarda video topilmadi.")
            return

        save_movie(code, channel_message_id, None, fwd.video.file_id)
        await bot.delete_message(ADMIN_ID, fwd.message_id)
        await message.answer(f"✅ Muvaffaqiyatli! Endi bu kino inline qidiruvda to'g'ridan-to'g'ri yuboriladi.")
    except Exception:
        logging.exception("Video ma'lumotini tiklashda xato:")
        await message.answer("⚠️ Xatolik yuz berdi, Railway loglarini tekshiring.")


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

    poster_url = await upload_poster_to_telegraph(photo_file_id)
    save_series_info(data["series_code"], data["description"], photo_file_id, poster_url)
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
        builder.add(types.InlineKeyboardButton(text="🔁 Qism videosini almashtirish", callback_data=f"edit_action:replaceep:{code}"))
        builder.adjust(1)
        await message.answer("Nimani tahrirlaysiz?", reply_markup=builder.as_markup())
        await state.set_state(EditContent.waiting_for_action)
    elif is_movie:
        await state.update_data(code=code)
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text="📝 Tavsif", callback_data=f"edit_action:moviedesc:{code}"))
        builder.add(types.InlineKeyboardButton(text="🖼 Poster", callback_data=f"edit_action:moviepic:{code}"))
        builder.add(types.InlineKeyboardButton(text="🎥 Videoni almashtirish", callback_data=f"edit_action:movievideo:{code}"))
        builder.adjust(1)
        await message.answer("Nimani tahrirlaysiz?", reply_markup=builder.as_markup())
        await state.set_state(EditContent.waiting_for_action)
    else:
        await message.answer("❌ Bunday kodli kino yoki serial topilmadi!")
        await state.clear()


# 0.9.0.1. Kino tavsifini tahrirlash
@dp.callback_query(F.data.startswith("edit_action:moviedesc:"))
async def edit_action_moviedesc(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    await state.update_data(code=code, content_type="movie")
    await callback.message.answer("📝 Yangi tavsif matnini yuboring:")
    await state.set_state(EditContent.waiting_for_description)
    await callback.answer()


# 0.9.0.2. Kino posterini tahrirlash
@dp.callback_query(F.data.startswith("edit_action:moviepic:"))
async def edit_action_moviepic(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    await state.update_data(code=code)
    await callback.message.answer("🖼 Yangi poster (rasm) yuboring:")
    await state.set_state(EditContent.waiting_for_movie_poster)
    await callback.answer()


@dp.message(EditContent.waiting_for_movie_poster, F.photo)
async def process_edit_movie_poster(message: types.Message, state: FSMContext):
    data = await state.get_data()
    code = data["code"]
    new_photo_file_id = message.photo[-1].file_id

    poster_url = await upload_poster_to_telegraph(new_photo_file_id)
    set_movie_poster(code, new_photo_file_id, poster_url)
    await message.answer("✅ Poster muvaffaqiyatli yangilandi!")
    await state.clear()


@dp.message(EditContent.waiting_for_movie_poster)
async def process_edit_movie_poster_wrong(message: types.Message):
    await message.answer("❗ Iltimos, rasm yuboring.")


# 0.9.0.3. Kino videosini almashtirish
@dp.callback_query(F.data.startswith("edit_action:movievideo:"))
async def edit_action_movievideo(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    await state.update_data(code=code)
    await callback.message.answer("🎥 Yangi video faylni yuboring:")
    await state.set_state(EditContent.waiting_for_movie_video)
    await callback.answer()


@dp.message(EditContent.waiting_for_movie_video, F.video)
async def process_edit_movie_video(message: types.Message, state: FSMContext):
    data = await state.get_data()
    code = data["code"]
    message_id = get_movie(code)
    description = get_movie_title(code) or ""
    caption = f"🎬 {description}\n\n🔑 Kino kodi: {code}"

    try:
        await bot.edit_message_media(
            chat_id=CHANNEL_ID,
            message_id=message_id,
            media=types.InputMediaVideo(media=message.video.file_id, caption=caption)
        )
        save_movie(code, message_id, description, message.video.file_id)
        await message.answer("✅ Video muvaffaqiyatli almashtirildi!")
    except Exception:
        logging.exception("Videoni almashtirishda xato:")
        await message.answer("⚠️ Xatolik yuz berdi, keyinroq urinib ko'ring.")
    await state.clear()


@dp.message(EditContent.waiting_for_movie_video)
async def process_edit_movie_video_wrong(message: types.Message):
    await message.answer("❗ Iltimos, video yuboring.")


# 0.9.0.4. Serial qismi videosini almashtirish — avval qism raqamini tanlash
@dp.callback_query(F.data.startswith("edit_action:replaceep:"))
async def edit_action_replaceep(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    episodes = get_episodes(code)

    builder = InlineKeyboardBuilder()
    for episode_number, _ in episodes:
        builder.add(types.InlineKeyboardButton(
            text=f"{episode_number}-qism",
            callback_data=f"edit_pick_ep:{code}:{episode_number}"
        ))
    builder.adjust(3)
    await callback.message.answer("Qaysi qismni almashtirasiz?", reply_markup=builder.as_markup())
    await callback.answer()


@dp.callback_query(F.data.startswith("edit_pick_ep:"))
async def edit_pick_episode(callback: types.CallbackQuery, state: FSMContext):
    _, code, episode_number = callback.data.split(":")
    await state.update_data(code=code, episode_number=int(episode_number))
    await callback.message.answer(f"🎥 {episode_number}-qism uchun yangi videoni yuboring:")
    await state.set_state(EditContent.waiting_for_episode_video)
    await callback.answer()


@dp.message(EditContent.waiting_for_episode_video, F.video)
async def process_edit_episode_video(message: types.Message, state: FSMContext):
    data = await state.get_data()
    code = data["code"]
    episode_number = data["episode_number"]
    message_id = get_episode_message_id(code, episode_number)

    caption = f"🎬 Serial kodi: {code}\n📺 {episode_number}-qism"

    try:
        await bot.edit_message_media(
            chat_id=CHANNEL_ID,
            message_id=message_id,
            media=types.InputMediaVideo(media=message.video.file_id, caption=caption)
        )
        await message.answer(f"✅ {episode_number}-qism videosi muvaffaqiyatli almashtirildi!")
    except Exception:
        logging.exception("Qism videosini almashtirishda xato:")
        await message.answer("⚠️ Xatolik yuz berdi, keyinroq urinib ko'ring.")
    await state.clear()


@dp.message(EditContent.waiting_for_episode_video)
async def process_edit_episode_video_wrong(message: types.Message):
    await message.answer("❗ Iltimos, video yuboring.")


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

    poster_url = await upload_poster_to_telegraph(new_photo_file_id)
    save_series_info(code, data["new_description"], new_photo_file_id, poster_url)
    await message.answer("✅ Tavsif va poster muvaffaqiyatli yangilandi!")
    await state.clear()


# 0.11.1. Posterni o'tkazib yuborish (eski poster saqlanadi)
@dp.callback_query(F.data.startswith("edit_skip:poster:"))
async def edit_skip_poster(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    code = data["code"]

    old_info = get_series_info(code)
    old_photo_file_id = old_info[1] if old_info else None
    old_poster_url = get_series_poster_url(code)

    save_series_info(code, data["new_description"], old_photo_file_id, old_poster_url)
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
        save_movie(code, sent_msg.message_id, tavsif, video_id)
        if poster_file_id:
            poster_url = await upload_poster_to_telegraph(poster_file_id)
            set_movie_poster(code, poster_file_id, poster_url)
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


# 4. Asosiy menyu tugmalari (inline)


def get_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="menu:back"))
    return builder.as_markup()


@dp.callback_query(F.data == "menu:help")
async def menu_help(callback: types.CallbackQuery):
    await callback.message.answer(
        "ℹ️ Botdan qanday foydalanish mumkin:\n\n"
        "1️⃣ Kino yoki serial kodini bilsangiz — shunchaki raqamni yuboring (masalan: 123)\n"
        "2️⃣ Kodni bilmasangiz — kino yoki serial nomini yozing (masalan: Yunus Emre)\n"
        "3️⃣ Chiqqan natijalardan birini tanlang\n\n"
        "Yordam kerak bo'lsa, botni qayta ishga tushirish uchun /start ni bosing.",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:profile")
async def menu_profile(callback: types.CallbackQuery):
    user = callback.from_user
    username = f"@{user.username}" if user.username else "yo'q"
    joined = get_user_joined(user.id)
    joined_display = joined.split("T")[0] if joined else "noma'lum"

    await callback.message.answer(
        "👤 Profilim\n\n"
        f"📛 Ism: {user.full_name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 ID: {user.id}\n"
        f"📅 Ro'yxatdan o'tgan sana: {joined_display}",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:back")
async def menu_back(callback: types.CallbackQuery):
    await callback.message.delete()
    await send_welcome(callback.message.chat.id)
    await callback.answer()


@dp.callback_query(F.data == "menu:order")
async def menu_order(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📝 Qaysi kino yoki serialni istayotganingizni yozing "
        "(nomi, yili — agar bilsangiz):",
        reply_markup=types.ForceReply(input_field_placeholder="🎬 Kino nomini yozing...")
    )
    await state.set_state(OrderRequest.waiting_for_text)
    await callback.answer()


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
            chat_id=callback.from_user.id,
            from_chat_id=CHANNEL_ID,
            message_id=message_id
        )
        await callback.answer()
    except Exception:
        logging.exception("Serial qismini yuborishda xato:")
        await callback.answer(
            "❌ Yuborib bo'lmadi. Avval botga /start yozib, so'ng qayta urinib ko'ring.",
            show_alert=True
        )


# 4.2. Nom bo'yicha qidiruv (kod emas, oddiy matn kiritilganda)
@dp.message(F.text)
async def search_by_name(message: types.Message):
    # Inline natijadan tanlangan xabar (via_bot) qidiruv so'rovi emas — e'tiborsiz qoldiramiz
    if message.via_bot is not None:
        return

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
        await deliver_series_post(callback.from_user.id, code)
    else:
        await deliver_movie(callback.from_user.id, code)
    await callback.answer()


# 5. Boshqa hech qaysi handlerga mos kelmagan xabarlar uchun
@dp.message()
async def fallback_handler(message: types.Message):
    if message.via_bot is not None:
        return
    await message.answer("❗ Iltimos, kino kodini raqam bilan yuboring yoki /start bosing.")


# 6. Inline qidiruv — foydalanuvchi istalgan chatda "@bot_username so'rov" yozganda
@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()

    if not query:
        # Hech narsa yozilmagan bo'lsa ham — eng ko'p yuklangan kinolarni ko'rsatamiz
        all_content = get_all_content()
        results = sorted(all_content, key=lambda item: get_downloads(item[0]), reverse=True)[:15]
    else:
        results = search_content(query)[:20]
    items = []

    for code, title, kind in results:
        downloads = get_downloads(code)
        short_title = title.split("\n")[0][:60]
        description_line = f"⬇️ Yuklashlar: {downloads}"
        caption = f"{title}\n\n🔑 Kodi: {code}"

        if kind == "movie":
            video_file_id = get_movie_video_file_id(code)
            if video_file_id:
                # Video faylining o'zi natija sifatida — bitta bosishda darhol yuboriladi
                items.append(types.InlineQueryResultCachedVideo(
                    id=f"movie:{code}",
                    video_file_id=video_file_id,
                    title=short_title,
                    description=description_line,
                    caption=caption
                ))
                continue

            # Eski kinolarda video_file_id saqlanmagan bo'lishi mumkin — tugma bilan zaxira variant
            poster_url = get_movie_poster_url(code)
            builder = InlineKeyboardBuilder()
            builder.add(types.InlineKeyboardButton(text="🎬 Kinoni olish", callback_data=f"pick:movie:{code}"))
            items.append(types.InlineQueryResultArticle(
                id=f"movie:{code}",
                title=short_title,
                description=description_line,
                thumbnail_url=poster_url if poster_url else None,
                input_message_content=types.InputTextMessageContent(message_text=caption),
                reply_markup=builder.as_markup()
            ))
        else:
            poster_url = get_series_poster_url(code)
            builder = InlineKeyboardBuilder()
            episodes = get_episodes(code)
            for episode_number, _ in episodes:
                builder.add(types.InlineKeyboardButton(
                    text=f"{episode_number}-qism",
                    callback_data=f"ep:{code}:{episode_number}"
                ))
            builder.adjust(3)

            items.append(types.InlineQueryResultArticle(
                id=f"series:{code}",
                title=short_title,
                description=description_line,
                thumbnail_url=poster_url if poster_url else None,
                input_message_content=types.InputTextMessageContent(message_text=caption),
                reply_markup=builder.as_markup()
            ))

    await inline_query.answer(items, cache_time=5, is_personal=False)


@dp.chosen_inline_result()
async def track_chosen_result(chosen: types.ChosenInlineResult):
    # result_id "movie:<code>" ko'rinishida — video to'g'ridan-to'g'ri yuborilgani uchun
    # bu yerda hisoblaymiz (Article natijalar esa tugma bosilganda o'zi hisoblanadi)
    try:
        kind, code = chosen.result_id.split(":", 1)
        if kind == "movie":
            increment_downloads(code)
    except Exception:
        pass


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
