import os
import re
import logging
import tempfile
import asyncio
from pathlib import Path

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------------------------------------------------------------
# কনফিগারেশন
# ----------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

# ফোর্স-সাবস্ক্রাইব চ্যানেল
# এটা @username আকারে দিতে হবে (পাবলিক চ্যানেল হলে)। বটকে এই চ্যানেলের Admin বানাতে হবে।
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "@loot_dells")
FORCE_SUB_CHANNEL_LINK = os.environ.get("FORCE_SUB_CHANNEL_LINK", "https://t.me/loot_dells")

# টেলিগ্রামের ফাইল সাইজ লিমিট (বট API দিয়ে সাধারণ আপলোডে ৫০MB পর্যন্ত নিরাপদ)
MAX_FILESIZE_MB = 50

URL_REGEX = re.compile(
    r"(https?://\S+)", re.IGNORECASE
)

SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be",
    "facebook.com", "fb.watch",
    "instagram.com",
    "tiktok.com", "vm.tiktok.com",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def is_supported_url(url: str) -> bool:
    return any(domain in url.lower() for domain in SUPPORTED_DOMAINS)


def download_video(url: str, download_dir: str) -> str:
    """yt-dlp দিয়ে ভিডিও ডাউনলোড করে এবং ফাইলের পাথ রিটার্ন করে।"""
    outtmpl = os.path.join(download_dir, "%(title).80s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": f"best[filesize<{MAX_FILESIZE_MB}M]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        # ইনস্টাগ্রাম/ফেসবুকের কিছু প্রাইভেট বা এজ-রেস্ট্রিক্টেড কন্টেন্টের জন্য
        # cookies.txt ফাইল দরকার হতে পারে (নিচে README দ্রষ্টব্য)
        # "cookiefile": "cookies.txt",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # merge_output_format mp4 হলে এক্সটেনশন পরিবর্তন হতে পারে
        base, _ = os.path.splitext(filename)
        mp4_path = base + ".mp4"
        if os.path.exists(mp4_path):
            return mp4_path
        return filename


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 চ্যানেলে জয়েন করুন", url=FORCE_SUB_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ জয়েন করেছি, আবার চেক করুন", callback_data="check_join")],
        ]
    )


async def is_subscribed(bot, user_id: int) -> bool:
    """ইউজার চ্যানেলে জয়েন করেছে কিনা চেক করে।"""
    try:
        member = await bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError as e:
        logger.error(f"Membership check failed: {e}")
        # বট চ্যানেলে Admin না থাকলে বা চ্যানেল ভুল হলে এই এরর আসবে।
        # এমন হলে নিরাপত্তার জন্য ধরে নিচ্ছি জয়েন করেনি।
        return False


async def send_join_prompt(update_or_query, is_callback: bool = False):
    text = (
        "⚠️ বট ব্যবহার করতে হলে আগে আমাদের চ্যানেলে জয়েন করতে হবে।\n\n"
        "নিচের বাটনে ক্লিক করে জয়েন করুন, তারপর \"জয়েন করেছি\" বাটনে চাপুন।"
    )
    if is_callback:
        await update_or_query.edit_message_text(text, reply_markup=join_keyboard())
    else:
        await update_or_query.message.reply_text(text, reply_markup=join_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    await update.message.reply_text(
        "স্বাগতম! 👋\n\n"
        "আমাকে Facebook, YouTube, Instagram বা TikTok এর যেকোনো ভিডিও লিংক পাঠান, "
        "আমি অটোমেটিক ডাউনলোড করে আপনাকে ভিডিওটি পাঠিয়ে দেব।\n\n"
        f"⚠️ নোট: টেলিগ্রাম বট API-তে ফাইল আপলোডের লিমিট আছে, তাই {MAX_FILESIZE_MB}MB এর "
        "বড় ভিডিও ডাউনলোড নাও হতে পারে।"
    )


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if await is_subscribed(context.bot, user_id):
        await query.edit_message_text(
            "✅ ধন্যবাদ! এখন আপনি লিংক পাঠিয়ে ভিডিও ডাউনলোড করতে পারবেন।"
        )
    else:
        await query.answer("❌ আপনি এখনো চ্যানেলে জয়েন করেননি!", show_alert=True)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "শুধু ভিডিওর লিংক পাঠান — যেমন:\n"
        "https://www.youtube.com/watch?v=xxxx\n"
        "https://www.instagram.com/reel/xxxx\n"
        "https://vm.tiktok.com/xxxx\n"
        "https://www.facebook.com/watch/?v=xxxx"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    text = update.message.text or ""
    match = URL_REGEX.search(text)

    if not match:
        await update.message.reply_text("দয়া করে একটি সঠিক ভিডিও লিংক পাঠান।")
        return

    url = match.group(1)

    if not is_supported_url(url):
        await update.message.reply_text(
            "দুঃখিত, এই সাইটটি এখনো সাপোর্ট করে না। "
            "Facebook, YouTube, Instagram, TikTok লিংক পাঠান।"
        )
        return

    status_msg = await update.message.reply_text("⏳ ডাউনলোড হচ্ছে, একটু অপেক্ষা করুন...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            loop = asyncio.get_running_loop()
            file_path = await loop.run_in_executor(None, download_video, url, tmp_dir)

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > MAX_FILESIZE_MB:
                await status_msg.edit_text(
                    f"❌ ভিডিওটির সাইজ {file_size_mb:.1f}MB, যা {MAX_FILESIZE_MB}MB লিমিটের চেয়ে বড়। "
                    "পাঠানো সম্ভব হচ্ছে না।"
                )
                return

            await status_msg.edit_text("📤 আপলোড হচ্ছে...")
            with open(file_path, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption="✅ এই যে আপনার ভিডিও!",
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                )
            await status_msg.delete()

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Download error: {e}")
            await status_msg.edit_text(
                "❌ ভিডিও ডাউনলোড করা যায়নি। লিংকটি প্রাইভেট, ভুল বা প্ল্যাটফর্মের রেস্ট্রিকশনের "
                "কারণে হতে পারে।"
            )
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await status_msg.edit_text(f"❌ একটি সমস্যা হয়েছে: {e}")


def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN সেট করা হয়নি। এনভায়রনমেন্ট ভ্যারিয়েবল BOT_TOKEN সেট করুন অথবা "
            "কোডে সরাসরি বসান (শুধু টেস্টের জন্য)।"
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("বট চালু হয়েছে...")
    app.run_polling()


if __name__ == "__main__":
    main()
