import asyncio
import os
import re
import tempfile
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.utils.markdown import hlink
from dotenv import load_dotenv

import yt_dlp

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Put it in .env file: BOT_TOKEN=...")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Telegram upload limits (approx)
TELEGRAM_MAX_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# Recognized URL pattern (simple heuristic)
URL_RE = re.compile(
    r"(https?://[^\s]+)",
    flags=re.IGNORECASE,
)

# Allowed domains (yt-dlp supports far more; we highlight common ones)
ALLOWED_DOMAINS = [
    "youtube.com", "youtu.be",
    "tiktok.com",
    "instagram.com", "cdninstagram.com",
    "facebook.com", "fb.watch",
    "twitter.com", "x.com",
    "reddit.com",
    "vk.com",
    "dailymotion.com",
    "vimeo.com",
]

def is_allowed_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        return any(d in host for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def yt_dlp_options(temp_dir: str):
    # Prefer best MP4 with audio; fallback to best available
    return {
        "outtmpl": os.path.join(temp_dir, "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,  # single item only
        "format": "bestvideo*+bestaudio/best",  # muxed best when possible
        "merge_output_format": "mp4",           # try to produce mp4
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
        "quiet": True,
        "no_warnings": True,
        # In case of age restrictions / login needed, we skip
        "ignoreerrors": True,
        # Better filenames
        "restrictfilenames": False,
        # Timeout safety
        "socket_timeout": 30,
    }

async def download_media(url: str) -> list[str]:
    """
    Download media using yt-dlp into a temp directory.
    Returns list of file paths (usually 1).
    """
    files: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dl_") as tmp:
        ydl_opts = yt_dlp_options(tmp)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return []
            # If it's a playlist (noplaylist=True should prevent), still handle
            if "entries" in info and isinstance(info["entries"], list):
                for entry in info["entries"]:
                    if not entry:
                        continue
                    filename = ydl.prepare_filename(entry)
                    if filename and os.path.exists(filename):
                        files.append(filename)
            else:
                filename = ydl.prepare_filename(info)
                if filename and os.path.exists(filename):
                    files.append(filename)

        # Move files out of temp dir (they would be deleted)
        final_paths: list[str] = []
        for f in files:
            base = os.path.basename(f)
            # create a persistent temp file
            fd, new_path = tempfile.mkstemp(prefix="tg_", suffix=os.path.splitext(base)[1])
            os.close(fd)
            try:
                with open(f, "rb") as src, open(new_path, "wb") as dst:
                    dst.write(src.read())
                final_paths.append(new_path)
            except Exception:
                # Cleanup partial file
                try:
                    os.remove(new_path)
                except Exception:
                    pass
        return final_paths

@asynccontextmanager
async def cleanup_files(paths: list[str]):
    try:
        yield
    finally:
        for p in paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

@dp.message(CommandStart())
async def start(message: Message):
    text = (
        "Salom! Menga YouTube, TikTok, Instagram va boshqa qo‘llab-quvvatlanadigan manbalardan video URL yuboring.\n"
        "Men videoni yuklab, sizga yuboraman.\n\n"
        "Qo‘llab-quvvatlanadigan misollar:\n"
        "- https://youtu.be/...\n"
        "- https://www.tiktok.com/...\n"
        "- https://www.instagram.com/p/... (reels ham)\n"
        "- https://x.com/... (Twitter)\n\n"
        "Cheklov: Telegram orqali maksimal ~2GB fayl yuboriladi."
    )
    await message.answer(text)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = (
        "Foydalanish:\n"
        "- Shunchaki video havolasini yuboring.\n"
        "- Bir xabarda bir nechta URL bo‘lsa, har birini alohida ko‘rib chiqaman.\n"
        "- Agar platforma login talab qilsa yoki video bloklangan bo‘lsa, yuklab bo‘lmasligi mumkin."
    )
    await message.answer(text)

@dp.message(F.text)
async def handle_text(message: Message):
    urls = URL_RE.findall(message.text or "")
    if not urls:
        await message.answer("Iltimos, to‘g‘ri video havolasini yuboring.")
        return

    for url in urls:
        if not is_allowed_url(url):
            await message.answer(f"Bu havola qo‘llab-quvvatlanmaydi yoki noma'lum manba: {url}")
            continue

        status_msg = await message.answer(f"Yuklanmoqda: {hlink('manba', url)} ...")

        try:
            files = await download_media(url)
        except Exception as e:
            await status_msg.edit_text(f"Yuklashda xatolik: {e}")
            continue

        if not files:
            await status_msg.edit_text("Video topilmadi yoki yuklab bo‘lmadi.")
            continue

        async with cleanup_files(files):
            sent_any = False
            for path in files:
                try:
                    size = os.path.getsize(path)
                    if size > TELEGRAM_MAX_SIZE:
                        await message.answer(
                            f"Fayl juda katta ({size/1024/1024:.1f} MB). Telegram limiti ~2048 MB."
                        )
                        continue

                    # Try sending as video if extension looks like video
                    ext = os.path.splitext(path)[1].lower()
                    caption = "Yuklandi ✅"

                    if ext in {".mp4", ".mov", ".mkv", ".webm"}:
                        with open(path, "rb") as f:
                            await message.answer_video(video=f, caption=caption)
                    else:
                        with open(path, "rb") as f:
                            await message.answer_document(document=f, caption=caption)

                    sent_any = True
                except Exception as e:
                    await message.answer(f"Yuborishda xatolik: {e}")

            if sent_any:
                await status_msg.edit_text("Tayyor ✅")
            else:
                await status_msg.edit_text("Hech qanday fayl yuborilmadi.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
