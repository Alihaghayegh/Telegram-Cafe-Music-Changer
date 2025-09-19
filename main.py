#!/usr/bin/env python3

import os
import io
import sqlite3
import asyncio
from typing import Optional, Dict, Any, Tuple
from dotenv import load_dotenv
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.environ.get("BOT_DB") or "bot_data.sqlite3"
LOGO_SIZE = (300, 300)  # desired thumbnail size (Telegram prefers small jpgs)

# --- DB helpers using run_in_executor to avoid blocking loop ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        channel_id TEXT NOT NULL,
        cafe_name TEXT,
        caption TEXT,
        logo BLOB,
        is_default INTEGER DEFAULT 0,
        UNIQUE(user_id, channel_id)
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel_db_id INTEGER,
        title TEXT,
        file_name TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );""")
    conn.commit()
    conn.close()

async def db_execute(query: str, args: tuple = ()):
    loop = asyncio.get_running_loop()
    def _do():
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(query, args)
        conn.commit()
        rowid = cur.lastrowid
        conn.close()
        return rowid
    return await loop.run_in_executor(None, _do)

async def db_query_one(query: str, args: tuple = ()):
    loop = asyncio.get_running_loop()
    def _do():
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(query, args)
        r = cur.fetchone()
        conn.close()
        return r
    return await loop.run_in_executor(None, _do)

async def db_query_all(query: str, args: tuple = ()):
    loop = asyncio.get_running_loop()
    def _do():
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(query, args)
        r = cur.fetchall()
        conn.close()
        return r
    return await loop.run_in_executor(None, _do)

# --- image helper (in-memory, returns BytesIO jpg) ---
def resize_image_bytes(img_bytes: bytes, size: Tuple[int,int]=LOGO_SIZE) -> io.BytesIO:
    img = Image.open(io.BytesIO(img_bytes))
    img = img.convert("RGB")
    img.thumbnail(size)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    out.seek(0)
    out.name = "logo.jpg"
    return out

# --- CRUD helpers for channels ---
async def add_or_update_channel(user_id: int, channel_id: str, cafe_name: Optional[str]=None, caption: Optional[str]=None, logo_bytes: Optional[bytes]=None, make_default: bool=False):
    # insert or update; store logo as blob if provided
    existing = await db_query_one("SELECT id FROM channels WHERE user_id=? AND channel_id=?", (user_id, channel_id))
    if existing:
        q = "UPDATE channels SET cafe_name = COALESCE(?, cafe_name), caption = COALESCE(?, caption) WHERE id=?"
        await db_execute(q, (cafe_name, caption, existing[0]))
        if logo_bytes:
            await db_execute("UPDATE channels SET logo=? WHERE id=?", (logo_bytes, existing[0]))
        if make_default:
            await db_execute("UPDATE channels SET is_default=0 WHERE user_id=?", (user_id,))
            await db_execute("UPDATE channels SET is_default=1 WHERE id=?", (existing[0],))
        return existing[0]
    else:
        if make_default:
            await db_execute("UPDATE channels SET is_default=0 WHERE user_id=?", (user_id,))
            is_def = 1
        else:
            is_def = 0
        q = "INSERT INTO channels(user_id, channel_id, cafe_name, caption, logo, is_default) VALUES(?,?,?,?,?,?)"
        rowid = await db_execute(q, (user_id, channel_id, cafe_name, caption, logo_bytes, is_def))
        return rowid

async def list_channels_of_user(user_id: int):
    rows = await db_query_all("SELECT id, channel_id, cafe_name, caption, is_default FROM channels WHERE user_id=?", (user_id,))
    return rows

async def get_channel_by_dbid(channel_db_id: int):
    return await db_query_one("SELECT id, user_id, channel_id, cafe_name, caption, logo, is_default FROM channels WHERE id=?", (channel_db_id,))

async def get_default_channel(user_id: int):
    return await db_query_one("SELECT id, channel_id, cafe_name, caption, logo FROM channels WHERE user_id=? AND is_default=1", (user_id,))

async def set_default_channel(user_id: int, channel_db_id: int):
    await db_execute("UPDATE channels SET is_default=0 WHERE user_id=?", (user_id,))
    await db_execute("UPDATE channels SET is_default=1 WHERE id=?", (channel_db_id,))

# --- save song history ---
async def record_song(user_id: int, channel_db_id: int, title: str, file_name: str):
    await db_execute("INSERT INTO songs(user_id, channel_db_id, title, file_name) VALUES(?,?,?,?)", (user_id, channel_db_id, title, file_name))

# --- simple in-memory map for awaiting logo (user_id -> channel_db_id) ---
awaiting_logo: Dict[int, int] = {}
# --- temporary pending audio storage keyed by user (in memory, BytesIO) ---
# this is per-process; if process restarts, pending lost (but DB keeps channels)
pending_audio: Dict[int, Dict[str, Any]] = {}

# --- bot commands / flows ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام ☕️\n"
        "ربات موزیک کافه — تنظیمات کانال‌ها را اضافه کن و بعد آهنگ بفرست تا منتشر شود.\n\n"
        "دستورها: /help"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "راهنما:\n"
        "/addchannel <@channel یا -100...> [اسم_کافه] — اضافه کردن کانال\n"
        "/listchannels — لیست کانال‌های شما\n"
        "/setdefault <channel_db_id> — تنظیم کانال پیش‌فرض\n"
        "/setname <channel_db_id> <نام> — تنظیم اسم کافه برای کانال\n"
        "/setcaption <channel_db_id> <متن> — تنظیم کپشن\n"
        "/setlogo <channel_db_id> — سپس یک عکس بفرست تا لوگو ذخیره شود\n\n"
        "پس از تنظیم حداقل یک کانال: فقط آهنگ بفرست؛ اگر بیش از یک کانال داشته باشی، ربات ازت می‌پرسد کدام کانال."
    )

async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        return await update.message.reply_text("Usage: /addchannel <@channel یا -100...> [اسم_کافه]")
    channel_id = args[0]
    cafe_name = " ".join(args[1:]) if len(args) > 1 else None
    rowid = await add_or_update_channel(user_id, channel_id, cafe_name=cafe_name)
    await update.message.reply_text(f"✅ کانال اضافه/به‌روز شد (db_id={rowid}). برای آپلود لوگو: /setlogo {rowid}")

async def cmd_listchannels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = await list_channels_of_user(user_id)
    if not rows:
        return await update.message.reply_text("شما هنوز کانالی اضافه نکردید. با /addchannel شروع کنید.")
    text_lines = []
    for r in rows:
        dbid, chanid, name, cap, is_def = r[0], r[1], r[2] or "", r[3] or "", r[4]
        text_lines.append(f"#{dbid} — {chanid} | {name} | default={is_def}")
    await update.message.reply_text("\n".join(text_lines))

async def cmd_setdefault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("Usage: /setdefault <channel_db_id>")
    try:
        dbid = int(context.args[0])
    except:
        return await update.message.reply_text("آیدی صحیح نیست.")
    await set_default_channel(user_id, dbid)
    await update.message.reply_text("✅ کانال پیش‌فرض تنظیم شد.")

async def cmd_setname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /setname <channel_db_id> <name>")
    try:
        dbid = int(context.args[0])
    except:
        return await update.message.reply_text("آیدی صحیح نیست.")
    name = " ".join(context.args[1:])
    await db_execute("UPDATE channels SET cafe_name=? WHERE id=?", (name, dbid))
    await update.message.reply_text("✅ نام کافه تنظیم شد.")

async def cmd_setcaption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /setcaption <channel_db_id> <caption>")
    try:
        dbid = int(context.args[0])
    except:
        return await update.message.reply_text("آیدی صحیح نیست.")
    cap = " ".join(context.args[1:])
    await db_execute("UPDATE channels SET caption=? WHERE id=?", (cap, dbid))
    await update.message.reply_text("✅ کپشن تنظیم شد.")

async def cmd_setlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("Usage: /setlogo <channel_db_id> — سپس یک عکس بفرست.")
    try:
        dbid = int(context.args[0])
    except:
        return await update.message.reply_text("آیدی صحیح نیست.")
    # check ownership
    rec = await get_channel_by_dbid(dbid)
    if not rec or rec[1] != user_id:
        return await update.message.reply_text("کانال پیدا نشد یا ادمین شما نیستید.")
    awaiting_logo[user_id] = dbid
    await update.message.reply_text("لطفاً اکنون یک عکس (photo) ارسال کنید تا به‌عنوان لوگو ذخیره شود.")

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in awaiting_logo:
        dbid = awaiting_logo.pop(user_id)
        # get largest photo
        file = await update.message.photo[-1].get_file()
        img_bytes = await file.download_as_bytearray()
        resized = resize_image_bytes(bytes(img_bytes))
        # store blob in DB
        await db_execute("UPDATE channels SET logo=? WHERE id=?", (resized.read(), dbid))
        await update.message.reply_text("✅ لوگو ذخیره شد و برای آن کانال اعمال می‌شود.")
    else:
        await update.message.reply_text("اگر می‌خواهی لوگو ست کنی، ابتدا از /setlogo <channel_db_id> استفاده کن.")

# --- audio flow ---
async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    audio_msg = update.message.audio or update.message.voice or (update.message.document if update.message.document and (update.message.document.mime_type or "").startswith("audio") else None)
    if not audio_msg:
        return await update.message.reply_text("فایل صوتی شناسایی نشد.")
    # download audio into memory
    f = await audio_msg.get_file()
    data = await f.download_as_bytearray()
    buf = io.BytesIO(data)
    # set some metadata
    file_name = getattr(audio_msg, "file_name", None) or getattr(audio_msg, "title", None) or "track"
    buf.name = file_name
    # store pending audio in memory for user
    pending_audio[user_id] = {
        "buf": buf,
        "title": getattr(audio_msg, "title", file_name),
        "file_name": buf.name
    }
    # fetch user's channels
    rows = await list_channels_of_user(user_id)
    if not rows:
        return await update.message.reply_text("ابتدا حداقل یک کانال اضافه کن: /addchannel")
    if len(rows) == 1:
        # directly post to single channel
        chan_db_id = rows[0][0]
        await do_post_audio_for_user_channel(update, context, user_id, chan_db_id)
    else:
        # present inline keyboard to choose channel
        buttons = []
        for r in rows:
            dbid, chanid, name, caption, is_def = r[0], r[1], r[2] or "", r[3] or "", r[4]
            label = f"{chanid} ({name})" if name else chanid
            buttons.append([InlineKeyboardButton(label, callback_data=f"post:{dbid}")])
        # also a default option if exists
        await update.message.reply_text("کدام کانال را انتخاب می‌کنید؟", reply_markup=InlineKeyboardMarkup(buttons))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("post:"):
        try:
            dbid = int(data.split(":",1)[1])
        except:
            return await query.edit_message_text("اطلاعات نامعتبر.")
        user_id = query.from_user.id
        # ensure pending audio exists
        if user_id not in pending_audio:
            return await query.edit_message_text("فایل صوتی پیدا نشد؛ لطفاً دوباره فایل را ارسال کنید.")
        await do_post_audio_for_user_channel(update, context, user_id, dbid)
        # edit original inline message to confirm
        await query.edit_message_text("در حال ارسال به کانال...")

async def do_post_audio_for_user_channel(update_or_msg, context: ContextTypes.DEFAULT_TYPE, user_id: int, channel_db_id: int):
    pend = pending_audio.pop(user_id, None)
    if not pend:
        # called without a pending audio
        # send message to user
        try:
            await context.bot.send_message(chat_id=user_id, text="خطا: فایل صوتی مفقود شد.")
        except:
            pass
        return
    buf: io.BytesIO = pend["buf"]
    title = pend["title"]
    file_name = pend["file_name"]
    # fetch channel settings
    rec = await get_channel_by_dbid(channel_db_id)
    if not rec:
        await context.bot.send_message(chat_id=user_id, text="کانال انتخابی یافت نشد.")
        return
    # rec: id, user_id, channel_id, cafe_name, caption, logo, is_default
    chan_db_id, owner_id, chan_id, cafe_name, caption, logo_blob, is_def = rec
    # ensure bot is member etc. We'll attempt and catch exceptions
    # prepare buffers for upload: rewind buf
    buf.seek(0)
    # prepare thumb
    thumb_buf = None
    if logo_blob:
        thumb_buf = io.BytesIO(logo_blob)
        thumb_buf.name = "logo.jpg"
        thumb_buf.seek(0)
    # performer will be channel_id as requested
    performer = chan_id
    try:
        await context.bot.send_audio(
            chat_id=chan_id,
            audio=buf,
            title=title,
            performer=performer,
            thumbnail=thumb_buf,
            caption=caption or ""
        )
        # record history
        await record_song(user_id, channel_db_id, title, file_name)
        await context.bot.send_message(chat_id=user_id, text=f"✅ آهنگ در {chan_id} منتشر شد.")
    except Exception as e:
        # relay useful error to user
        await context.bot.send_message(chat_id=user_id, text=f"ارسال به کانال با خطا مواجه شد:\n{e}")

# --- startup ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("addchannel", cmd_addchannel))
    app.add_handler(CommandHandler("listchannels", cmd_listchannels))
    app.add_handler(CommandHandler("setdefault", cmd_setdefault))
    app.add_handler(CommandHandler("setname", cmd_setname))
    app.add_handler(CommandHandler("setcaption", cmd_setcaption))
    app.add_handler(CommandHandler("setlogo", cmd_setlogo))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.AUDIO, audio_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()