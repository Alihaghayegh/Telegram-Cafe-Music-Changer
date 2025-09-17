#!/usr/bin/env python3
import io
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)


load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
print(BOT_TOKEN)

# فایل ذخیره تنظیمات
SETTINGS_FILE = Path("user_settings.json")

# حافظه موقت: user_id -> settings
user_settings = {}


# --- مدیریت تنظیمات (load/save) ---
def load_settings():
    global user_settings
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            user_settings = json.load(f)
    else:
        user_settings = {}


def save_settings():
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_settings, f, ensure_ascii=False, indent=2)


# --- گرفتن تنظیمات پیش‌فرض برای کاربر ---
def get_user_settings(user_id: int):
    uid = str(user_id)
    if uid not in user_settings:
        user_settings[uid] = {
            "channel_id": None,
            "cafe_name": "☕ کافه‌ی من",
            "caption": "🎶 آهنگ جدید از کافه",
            "logo": None,  # لوگو در فایل جدا ذخیره نمیشه، فقط موقع اجرا
        }
        save_settings()
    return user_settings[uid]


# --- Helper برای ریسایز عکس ---
def resize_logo(img_bytes, size=(300, 300)):
    img = Image.open(io.BytesIO(img_bytes))
    img = img.convert("RGB")
    img.thumbnail(size)   # تناسب حفظ میشه
    out = io.BytesIO()
    img.save(out, format="JPEG")
    out.seek(0)
    out.name = "logo.jpg"
    return out


# --- دستورات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 سلام! به ربات موزیک‌چنجر خوش اومدی.\n\n"
        "با این ربات می‌تونی آهنگ‌هاتو با اسم و لوگوی کافه‌ی خودت توی کانالت منتشر کنی 🎶☕\n"
        "برای راهنما دستور /help رو بزن."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 راهنمای دستورات:\n\n"
        "⚙️ تنظیمات:\n"
        " /setchannel <@channel یا -100...> → ست کردن کانال\n"
        " /setname <اسم کافه> → تغییر نام کافه\n"
        " /setcaption <متن کپشن> → تغییر کپشن\n"
        " ارسال یک عکس → تنظیم لوگو\n\n"
        "🎵 استفاده:\n"
        " فقط یک آهنگ بفرست، ربات اون رو با تنظیمات دلخواهت به کانالت می‌فرسته ✅"
    )


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_user_settings(update.effective_user.id)
    if not context.args:
        return await update.message.reply_text("📌 لطفاً آیدی کانال رو بده. مثال:\n`/setchannel @mychannel`", parse_mode="Markdown")
    settings["channel_id"] = context.args[0]
    save_settings()
    await update.message.reply_text(f"✅ کانال تنظیم شد: {settings['channel_id']}")


async def set_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_user_settings(update.effective_user.id)
    name = " ".join(context.args)
    if not name:
        return await update.message.reply_text("📌 لطفاً اسم کافه رو بده.")
    settings["cafe_name"] = name
    save_settings()
    await update.message.reply_text(f"✅ اسم کافه تنظیم شد: {name}")


async def set_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_user_settings(update.effective_user.id)
    cap = " ".join(context.args)
    if not cap:
        return await update.message.reply_text("📌 لطفاً متن کپشن رو بده.")
    settings["caption"] = cap
    save_settings()
    await update.message.reply_text("✅ کپشن تنظیم شد.")


async def set_logo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_user_settings(update.effective_user.id)
    if not update.message.photo:
        return await update.message.reply_text("📷 لطفاً یک عکس بفرست.")
    file = await update.message.photo[-1].get_file()
    img_bytes = await file.download_as_bytearray()
    settings["logo"] = resize_logo(img_bytes)
    await update.message.reply_text("✅ لوگو با موفقیت تنظیم شد.")


# --- هندل آهنگ ---
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    settings = get_user_settings(user_id)

    if not settings["channel_id"]:
        return await update.message.reply_text("⚠️ لطفاً اول کانال رو با دستور /setchannel تنظیم کن.")

    audio = update.message.audio or update.message.document or update.message.voice
    if not audio:
        return await update.message.reply_text("📌 یک فایل صوتی بفرست.")

    if not settings["logo"]:
        return await update.message.reply_text("⚠️ لطفاً اول یک لوگو بفرست.")

    # دانلود آهنگ به صورت موقت در حافظه
    file = await audio.get_file()
    data = await file.download_as_bytearray()
    buf = io.BytesIO(data)
    buf.name = getattr(audio, "file_name", "track.mp3")

    # ارسال به کانال
    await context.bot.send_audio(
        chat_id=settings["channel_id"],
        audio=buf,
        performer=settings["cafe_name"],
        title=getattr(audio, "title", "Track"),
        caption=settings["caption"],
        thumbnail=settings["logo"],
    )
    await update.message.reply_text("✅ آهنگ با موفقیت در کانال منتشر شد.")


# --- main ---
def main():
    load_settings()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("setchannel", set_channel))
    app.add_handler(CommandHandler("setname", set_name))
    app.add_handler(CommandHandler("setcaption", set_caption))
    app.add_handler(MessageHandler(filters.PHOTO, set_logo))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.AUDIO, handle_audio))

    print("🚀 Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()