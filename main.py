import asyncio
import os
import re
import tempfile
from contextlib import asynccontextmanager
from typing import List

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.utils.markdown import hlink

import yt_dlp
import requests

# ---------------------------
# Config (env orqali)
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ACR_HOST = os.getenv("ACR_HOST")       # masalan: "identify-eu-west-1.acrcloud.com"
ACR_KEY = os.getenv("ACR_KEY")
ACR_SECRET = os.getenv("ACR_SECRET")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable topilmadi!")

TELEGRAM_MAX_SIZE = 2 * 1024 * 1024 * 1024  # ~2GB

URL_RE = re.compile(r"(https?://[^\s]+)", flags=re.IGNORECASE)

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

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# ---------------------------
# Helpers
# ---------------------------
def is_allowed_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    return any(d in host for d in ALLOWED_DOMAINS)

def yt_dlp_options(temp_dir: str):
    return {
        "outtmpl": os.path.join(temp_dir, "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "restrictfilenames": False,
        "socket_timeout": 30,
    }

async def download_media(url: str) -> List[str]:
    files: List[str] = []
    with tempfile.TemporaryDirectory(prefix="dl_") as tmp:
        ydl_opts = yt_dlp_options(tmp)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return []
            if isinstance(info, dict) and "entries" in info:
                for entry in info["entries"]:
                    filename = ydl.prepare_filename(entry)
                    if filename and os.path.exists(filename):
                        files.append(filename)
            else:
                filename = ydl.prepare_filename(info)
                if filename and os.path.exists(filename):
                    files.append(filename)

        final_paths: List[str] = []
        for f in files:
            base = os.path.basename(f)
            suffix = os.path.splitext(base)[1] or ".mp4"
            fd, new_path = tempfile.mkstemp(prefix="tg_", suffix=suffix)
            os.close(fd)
            with open(f, "rb") as src, open(new_path, "wb") as dst:
                dst.write(src.read())
            final_paths.append(new_path)
        return final_paths

@asynccontextmanager
async def cleanup_files(paths: List[str]):
    try:
        yield
    finally:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)

# ---------------------------
# ACRCloud helper
# ---------------------------
def recognize_music(file_path: str) -> str:
    """ACRCloud API orqali qo‘shiqni aniqlash"""
    try:
        with open(file_path, "rb") as f:
            sample_bytes = f.read()

        import time, hmac, hashlib, base64
        http_method = "POST"
        http_uri = "/v1/identify"
        data_type = "audio"
        signature_version = "1"
        timestamp = str(int(time.time()))

        string_to_sign = http_method + "\n" + http_uri + "\n" + ACR_KEY + "\n" + data_type + "\n" + signature_version + "\n" + timestamp
        sign = base64.b64encode(hmac.new(ACR_SECRET.encode('utf8'), string_to_sign.encode('utf8'), digestmod=hashlib.sha1).digest()).decode('utf-8')

        files = {'sample': sample_bytes}
        data = {
            'access_key': ACR_KEY,
            'data_type': data_type,
            'signature_version': signature_version,
            'signature': sign,
            'timestamp': timestamp
        }
        res = requests.post(f"http://{ACR_HOST}/v1/identify", files=files, data=data)
        result = res.json()

        if "metadata" in result and "music" in result["metadata"]:
            music = result["metadata"]["music"][0]
            title = music.get("title", "Noma'lum")
            artist = music.get("artists", [{}])[0].get("name", "Noma'lum")
            return f"🎶 Topildi: {title} — {artist}"
        return "Qo‘shiq aniqlanmadi."
    except Exception as e:
        return f"Xatolik: {e}"

# ---------------------------
# Handlers
# ---------------------------
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("Salom! Menga video link, audio fayl yoki ovozli xabar yuboring.\n"
                         "Men qo‘shiqni aniqlashga yoki videoni yuklab berishga harakat qilaman.")

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("Qo‘llab-quvvatlanadigan funksiyalar:\n"
                         "- YouTube/TikTok/Instagram link → video yuklab beriladi\n"
                         "- Ovozli xabar yoki audio fayl → qo‘shiq nomi aniqlanadi\n"
                         "- Matn (qo‘shiq nomi) → YouTube link qaytariladi")

@dp.message(F.text)
async def handle_text(message: Message):
    urls = URL_RE.findall(message.text or "")
    if urls:
        for url in urls:
            if not is_allowed_url(url):
                await message.answer(f"Bu havola qo‘llab-quvvatlanmaydi: {url}")
                continue
            status_msg = await message.answer(f"Yuklanmoqda: {hlink('manba', url)} ...")
            try:
                files = await download_media(url)
            except Exception as e:
                await status_msg.edit_text(f"Yuklashda xatolik: {e}")
                continue
            if not files:
                await status_msg.edit_text("Video topilmadi.")
                continue
            async with cleanup_files(files):
                for path in files:
                    size = os.path.getsize(path)
                    if size > TELEGRAM_MAX_SIZE:
                        await message.answer("Fayl juda katta.")
                        continue
                    with open(path, "rb") as f:
                        await message.answer_video(video=f, caption="Yuklandi ✅")
                    break
            await status_msg.edit_text("Tayyor ✅")
    else:
        # Matn orqali qo‘shiq qidirish
        query = message.text.strip()
        await message.answer(f"🔍 '{query}' uchun YouTube’da qidiryapman...")
        # oddiy link qaytarish (search_web orqali ham qilsa bo‘ladi)
        await message.answer(f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}")

@dp.message(F.voice)
async def handle_voice(message: Message):
    file = await bot.get_file(message.voice.file_id)
    file_path = await bot.download_file(file.file_path)
    result = recognize_music(file_path.name)
    await message.answer(result)

@dp.message(F.audio)
async def handle_audio(message: Message):
    file = await bot.get_file(message.audio.file_id)
    file_path = await bot.download_file(file.file_path)
    result = recognize_music(file_path.name)
    await message.answer(result)

# ---------------------------
# Entrypoint
# ---------------------------
async def main():
    # Bot pollingni ishga tushiramiz
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
