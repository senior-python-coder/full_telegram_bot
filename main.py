import asyncio
import os
import re
import tempfile
from contextlib import asynccontextmanager
from typing import List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.utils.markdown import hlink

import yt_dlp

# ---------------------------
# Config (envsiz)
# ---------------------------
BOT_TOKEN = "8389267896:AAFKVW35hSTYKh90HZiQ3uV1VaGWNjWdQ5k"  # bu yerga bot tokeningizni yozing
TELEGRAM_MAX_SIZE = 2 * 1024 * 1024 * 1024  # ~2GB

# URL aniqlash
URL_RE = re.compile(r"(https?://[^\s]+)", flags=re.IGNORECASE)

# Ruxsat etilgan domenlar (asosiylari)
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

# ---------------------------
# Bot init (aiogram 3.7+)
# ---------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# ---------------------------
# Helpers
# ---------------------------
def is_allowed_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        return any(d in host for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def yt_dlp_options(temp_dir: str):
    # mp4 ga majburlashga urinamiz, bo'lmasa best mavjud format
    return {
        "outtmpl": os.path.join(temp_dir, "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
        ],
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "restrictfilenames": False,
        "socket_timeout": 30,
    }

async def download_media(url: str) -> List[str]:
    """
    URL dan media yuklab, vaqtinchalik papkada saqlaydi.
    Yakunda temporar papkadan tashqariga ko'chirilgan final pathlarni qaytaradi.
    """
    files: List[str] = []
    with tempfile.TemporaryDirectory(prefix="dl_") as tmp:
        ydl_opts = yt_dlp_options(tmp)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return []

            # playlist bo'lsa ham, ehtiyot choralari
            if isinstance(info, dict) and "entries" in info and isinstance(info["entries"], list):
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

        # temporar papkadan tashqariga ko'chirish (TemporaryDirectory yopilganda o'chib ketmasin)
        final_paths: List[str] = []
        for f in files:
            base = os.path.basename(f)
            suffix = os.path.splitext(base)[1] or ".mp4"
            fd, new_path = tempfile.mkstemp(prefix="tg_", suffix=suffix)
            os.close(fd)
            try:
                with open(f, "rb") as src, open(new_path, "wb") as dst:
                    dst.write(src.read())
                final_paths.append(new_path)
            except Exception:
                try:
                    os.remove(new_path)
                except Exception:
                    pass
        return final_paths

@asynccontextmanager
async def cleanup_files(paths: List[str]):
    try:
        yield
    finally:
        for p in paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

# ---------------------------
# Handlers
# ---------------------------
@dp.message(CommandStart())
async def start(message: Message):
    text = (
        "Salom! Menga YouTube, TikTok, Instagram va boshqa qo‘llab-quvvatlanadigan manbalardan video URL yuboring.\n"
        "Men videoni yuklab, sizga yuboraman.\n\n"
        "Misollar:\n"
        "- https://youtu.be/...\n"
        "- https://www.tiktok.com/...\n"
        "- https://www.instagram.com/p/... (reels ham)\n"
        "- https://x.com/... (Twitter/X)\n\n"
        "Cheklov: Telegram orqali maksimal ~2GB fayl yuboriladi."
    )
    await message.answer(text)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = (
        "Foydalanish:\n"
        "- Video havolasini yuboring.\n"
        "- Bir xabarda bir nechta URL bo‘lsa, har birini alohida ko‘rib chiqaman.\n"
        "- Agar platforma login talab qilsa yoki video bloklangan bo‘lsa, yuklab bo‘lmasligi mumkin.\n"
        "- Fayl hajmi ~2GB dan katta bo‘lsa, Telegram orqali yuborilmaydi."
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
            # faqat bitta eng mos faylni yuboramiz (kerak bo'lsa hammasini aylanib chiqadi)
            for path in files:
                try:
                    size = os.path.getsize(path)
                    if size > TELEGRAM_MAX_SIZE:
                        await message.answer(
                            f"Fayl juda katta ({size/1024/1024:.1f} MB). Telegram limiti ~2048 MB."
                        )
                        continue

                    ext = os.path.splitext(path)[1].lower()
                    caption = "Yuklandi ✅"

                    # video sifatida yuborishga urinamiz
                    if ext in {".mp4", ".mov", ".mkv", ".webm"}:
                        with open(path, "rb") as f:
                            await message.answer_video(video=f, caption=caption)
                    else:
                        with open(path, "rb") as f:
                            await message.answer_document(document=f, caption=caption)

                    sent_any = True
                    break  # bitta nusxa prinsipiga amal: birinchi muvaffaqiyatli faylni yuboramiz
                except Exception as e:
                    await message.answer(f"Yuborishda xatolik: {e}")

            if sent_any:
                await status_msg.edit_text("Tayyor ✅")
            else:
                await status_msg.edit_text("Hech qanday fayl yuborilmadi.")

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
