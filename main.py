import asyncio
import os
import re
import tempfile
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.markdown import hlink

import yt_dlp

# ---------------------------
# Config
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable topilmadi!")

TELEGRAM_MAX_SIZE = 2 * 1024 * 1024 * 1024  # ~2GB
URL_RE = re.compile(r"(https?://[^\s]+)", flags=re.IGNORECASE)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# ---------------------------
# In-memory cache
# ---------------------------
# user_id -> list of entries (each: dict with keys: id, title, duration, uploader, url)
SEARCH_CACHE: Dict[int, List[Dict]] = {}
# user_id -> selected index (0..9)
PICK_CACHE: Dict[int, int] = {}

# ---------------------------
# Helpers
# ---------------------------
def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "—:—"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def keypad_keyboard(count: int) -> InlineKeyboardMarkup:
    # 1..10 tugmalar, 5x2 layout yoki 3 satr
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for i in range(1, min(count, 10) + 1):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"pick_{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Qo‘shimcha boshqaruv tugmalari
    buttons.append([
        InlineKeyboardButton(text="🔁 Yangilash", callback_data="refresh"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def actions_keyboard(entry: Dict) -> InlineKeyboardMarkup:
    url = entry.get("url")
    vid = entry.get("id")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Audio (MP3)", callback_data=f"aud_{vid}")],
        [InlineKeyboardButton(text="🎥 Video (MP4)", callback_data=f"vid_{vid}")],
        [InlineKeyboardButton(text="🔗 YouTube’da ochish", url=url)],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back")]
    ])

def yt_search(query: str, limit: int = 10) -> List[Dict]:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = []
        if info and "entries" in info and info["entries"]:
            for e in info["entries"]:
                if not e:
                    continue
                entries.append({
                    "id": e.get("id"),
                    "title": e.get("title") or "Untitled",
                    "duration": e.get("duration"),
                    "uploader": e.get("uploader") or e.get("channel") or "",
                    "url": e.get("webpage_url") or (f"https://www.youtube.com/watch?v={e.get('id')}" if e.get("id") else None),
                })
        return entries

def yt_dlp_options(temp_dir: str, audio_only=False):
    opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": 30,
    }
    if audio_only:
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        })
    else:
        opts.update({
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
        })
    return opts

async def download_by_url(url: str, audio_only=False) -> Optional[str]:
    with tempfile.TemporaryDirectory(prefix="dl_") as tmp:
        ydl_opts = yt_dlp_options(tmp, audio_only=audio_only)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
    return None

def build_results_text(query: str, entries: List[Dict]) -> str:
    lines = [f"🔎 Qidiruv: <b>{query}</b>", ""]
    for i, e in enumerate(entries[:10], start=1):
        title = e["title"]
        artist = e["uploader"]
        dur = format_duration(e["duration"])
        # “1. Title — Artist  4:10”
        lines.append(f"{i}. {title} — {artist}  {dur}")
    lines.append("")
    lines.append("Quyidagi raqam tugmalaridan birini bosing ⬇️")
    return "\n".join(lines)

# ---------------------------
# Handlers
# ---------------------------
@dp.message(CommandStart())
async def start(message: Message):
    text = (
        "👋 Salom!\n\n"
        "Matnli so‘rov yuboring (qo‘shiq nomi yoki ijrochi ismi), men YouTube’dan 10 ta natija beraman "
        "va pastida raqamli tugmalar chiqadi. Tanlaganingizni audio yoki video qilib yuboraman.\n\n"
        "Misollar:\n"
        "- Maher Zain Medina\n"
        "- Madina Aknazarova Safa Safa\n"
        "- Billie Eilish Ocean Eyes"
    )
    await message.answer(text)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = (
        "Foydalanish:\n"
        "- Matn yuboring → 10 ta natija chiqadi + raqamli tugmalar.\n"
        "- Raqamni bosing → tanlangan trek uchun Audio/Video/YouTube tugmalari.\n"
        "- Audio/Video tugmalarini bossangiz → fayl tayyorlanib yuboriladi.\n\n"
        "Eslatma: Fayl hajmi cheklovi ~2GB."
    )
    await message.answer(text)

@dp.message(F.text)
async def search_handler(message: Message):
    query = (message.text or "").strip()
    # Agar URL bo‘lsa, foydalanuvchiga bu rejim faqat matn ekanini aytamiz
    if URL_RE.search(query):
        await message.answer("Bu rejim faqat matnli qidiruv uchun. Iltimos, faqat qo‘shiq nomi yoki ijrochi ismini yozing.")
        return

    await message.answer(f"🔍 Qidirilmoqda…")
    entries = yt_search(query, limit=10)
    if not entries:
        await message.answer("❌ Hech narsa topilmadi. Boshqa so‘rov sinab ko‘ring.")
        return

    # Cache saqlash
    SEARCH_CACHE[message.from_user.id] = entries
    PICK_CACHE.pop(message.from_user.id, None)

    text = build_results_text(query, entries)
    await message.answer(text, reply_markup=keypad_keyboard(len(entries)))

@dp.callback_query(F.data == "refresh")
async def refresh_callback(cb: CallbackQuery):
    user_id = cb.from_user.id
    entries = SEARCH_CACHE.get(user_id)
    if not entries:
        await cb.answer("Avval qidiruv yuboring.", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=keypad_keyboard(len(entries)))
    await cb.answer("Yangilandi.")

@dp.callback_query(F.data == "cancel")
async def cancel_callback(cb: CallbackQuery):
    user_id = cb.from_user.id
    SEARCH_CACHE.pop(user_id, None)
    PICK_CACHE.pop(user_id, None)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Bekor qilindi.")

@dp.callback_query(F.data == "back")
async def back_callback(cb: CallbackQuery):
    user_id = cb.from_user.id
    entries = SEARCH_CACHE.get(user_id)
    if not entries:
        await cb.answer("Avval qidiruv yuboring.", show_alert=True)
        return
    PICK_CACHE.pop(user_id, None)
    await cb.message.edit_reply_markup(reply_markup=keypad_keyboard(len(entries)))
    await cb.answer()

@dp.callback_query(F.data.startswith("pick_"))
async def pick_callback(cb: CallbackQuery):
    user_id = cb.from_user.id
    entries = SEARCH_CACHE.get(user_id)
    if not entries:
        await cb.answer("Avval qidiruv yuboring.", show_alert=True)
        return

    try:
        idx = int(cb.data.split("_")[1]) - 1
    except Exception:
        await cb.answer("Noto‘g‘ri tanlov.", show_alert=True)
        return

    if idx < 0 or idx >= len(entries):
        await cb.answer("Tanlov chegaradan tashqarida.", show_alert=True)
        return

    PICK_CACHE[user_id] = idx
    entry = entries[idx]
    title = entry["title"]
    artist = entry["uploader"]
    dur = format_duration(entry["duration"])
    url = entry["url"]

    desc = f"🎵 <b>{title}</b>\n👤 {artist}\n⏱ {dur}\n\n{hlink('YouTube', url)}"
    await cb.message.edit_text(desc)
    await cb.message.edit_reply_markup(reply_markup=actions_keyboard(entry))
    await cb.answer(f"Tanlandi: {idx+1}")

@dp.callback_query(F.data.startswith("aud_"))
async def audio_callback(cb: CallbackQuery):
    vid = cb.data.split("_", 1)[1]
    user_id = cb.from_user.id
    entries = SEARCH_CACHE.get(user_id) or []
    entry = next((e for e in entries if e.get("id") == vid), None)
    if not entry:
        await cb.answer("Topilmadi. Orqaga qayting va qaytadan tanlang.", show_alert=True)
        return

    url = entry["url"]
    await cb.message.edit_caption if hasattr(cb.message, "caption") else None
    await cb.message.answer("🎧 Audio tayyorlanmoqda…")

    file = await download_by_url(url, audio_only=True)
    if not file:
        await cb.message.answer("❌ Audio tayyorlab bo‘lmadi.")
        await cb.answer()
        return

    size = os.path.getsize(file)
    if size > TELEGRAM_MAX_SIZE:
        await cb.message.answer("❌ Fayl juda katta (Telegram limiti ~2GB).")
        await cb.answer()
        return

    try:
        with open(file, "rb") as f:
            await cb.message.answer_audio(audio=f, caption=f"🎧 {entry['title']}")
    finally:
        try:
            os.remove(file)
        except Exception:
            pass
    await cb.answer("Audio yuborildi ✅")

@dp.callback_query(F.data.startswith("vid_"))
async def video_callback(cb: CallbackQuery):
    vid = cb.data.split("_", 1)[1]
    user_id = cb.from_user.id
    entries = SEARCH_CACHE.get(user_id) or []
    entry = next((e for e in entries if e.get("id") == vid), None)
    if not entry:
        await cb.answer("Topilmadi. Orqaga qayting va qaytadan tanlang.", show_alert=True)
        return

    url = entry["url"]
    await cb.message.answer("🎥 Video tayyorlanmoqda…")

    file = await download_by_url(url, audio_only=False)
    if not file:
        await cb.message.answer("❌ Video tayyorlab bo‘lmadi.")
        await cb.answer()
        return

    size = os.path.getsize(file)
    if size > TELEGRAM_MAX_SIZE:
        await cb.message.answer("❌ Fayl juda katta (Telegram limiti ~2GB).")
        await cb.answer()
        return

    try:
        with open(file, "rb") as f:
            await cb.message.answer_video(video=f, caption=f"🎥 {entry['title']}")
    finally:
        try:
            os.remove(file)
        except Exception:
            pass
    await cb.answer("Video yuborildi ✅")

# ---------------------------
# Entrypoint
# ---------------------------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
