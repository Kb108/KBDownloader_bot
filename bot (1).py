import os
import re
import json
import logging
import tempfile
import asyncio

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ChatAction, ParseMode
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
# CONFIG
# ----------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

# Force-subscribe channel. Set these in Railway Variables if you ever change channels.
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "@loot_dells")
FORCE_SUB_CHANNEL_LINK = os.environ.get("FORCE_SUB_CHANNEL_LINK", "https://t.me/loot_dells")

# Your personal numeric Telegram ID. Needed to use /broadcast. Get it from @userinfobot.
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

MAX_FILESIZE_MB = 50
USERS_FILE = "users.json"

URL_REGEX = re.compile(r"(https?://\S+)", re.IGNORECASE)

# Sites yt-dlp can reliably download from. Add more domains here any time.
SUPPORTED_SITES = {
    "YouTube": ["youtube.com", "youtu.be"],
    "Facebook": ["facebook.com", "fb.watch"],
    "Instagram": ["instagram.com"],
    "TikTok": ["tiktok.com", "vm.tiktok.com"],
    "Twitter / X": ["twitter.com", "x.com"],
    "Reddit": ["reddit.com"],
    "Pinterest": ["pinterest.com", "pin.it"],
    "Vimeo": ["vimeo.com"],
    "Twitch (clips)": ["twitch.tv"],
    "VK": ["vk.com"],
    "OK.ru": ["ok.ru"],
    "Tumblr": ["tumblr.com"],
    "Likee": ["likee.video", "likee.com"],
    "SoundCloud": ["soundcloud.com"],
    "Threads": ["threads.net"],
    "Dailymotion": ["dailymotion.com"],
    "Snapchat": ["snapchat.com"],
}
SUPPORTED_DOMAINS = [d for domains in SUPPORTED_SITES.values() for d in domains]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📋 Supported Sites"), KeyboardButton("🆘 Help")],
        [KeyboardButton("📢 Our Channel")],
    ],
    resize_keyboard=True,
)


# ----------------------------------------------------------------------
# SIMPLE USER STORAGE (for /broadcast)
# ----------------------------------------------------------------------
def load_users() -> set:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_user(user_id: int):
    users = load_users()
    if user_id not in users:
        users.add(user_id)
        with open(USERS_FILE, "w") as f:
            json.dump(list(users), f)


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------
def is_supported_url(url: str) -> bool:
    return any(domain in url.lower() for domain in SUPPORTED_DOMAINS)


def supported_sites_text() -> str:
    lines = ["📋 *Supported sites:*\n"]
    for name in SUPPORTED_SITES:
        lines.append(f"✅ {name}")
    lines.append(
        "\n⚠️ Spotify and Apple Music can't be supported — those platforms encrypt "
        "(DRM-protect) their audio, so no downloader can pull files from them."
    )
    return "\n".join(lines)


def download_video(url: str, download_dir: str) -> str:
    """Downloads the video with yt-dlp and returns the local file path."""
    outtmpl = os.path.join(download_dir, "%(title).80s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": f"best[filesize<{MAX_FILESIZE_MB}M]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        # Some private/age-restricted Instagram or Facebook content needs a
        # cookies.txt file exported from your browser — uncomment and set the path.
        # "cookiefile": "cookies.txt",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        mp4_path = base + ".mp4"
        if os.path.exists(mp4_path):
            return mp4_path
        return filename


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join our channel", url=FORCE_SUB_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've joined, check again", callback_data="check_join")],
        ]
    )


async def is_subscribed(bot, user_id: int) -> bool:
    """Checks whether the user has joined the force-sub channel."""
    try:
        member = await bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError as e:
        logger.error(f"Membership check failed: {e}")
        # If the bot isn't an admin in the channel, this check will always fail.
        return False


async def send_join_prompt(update: Update):
    text = (
        "⚠️ You need to join our channel before using this bot.\n\n"
        "Tap the button below to join, then tap \"I've joined, check again\"."
    )
    await update.message.reply_text(text, reply_markup=join_keyboard())


# ----------------------------------------------------------------------
# DOWNLOAD ANIMATION
# ----------------------------------------------------------------------
async def _animate_progress(status_msg, bot, chat_id):
    """Cycles a small text animation on the status message while downloading,
    and keeps the 'uploading video...' indicator alive in the chat header."""
    frames = [
        "⏳ Downloading",
        "⏳ Downloading.",
        "⏳ Downloading..",
        "⏳ Downloading...",
    ]
    i = 0
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                await status_msg.edit_text(frames[i % len(frames)])
            except TelegramError:
                pass  # e.g. "message is not modified" — safe to ignore
            i += 1
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        pass


# ----------------------------------------------------------------------
# COMMAND HANDLERS
# ----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    await update.message.reply_text(
        "👋 Welcome!\n\n"
        "Send me any video link from YouTube, Facebook, Instagram, TikTok, and more — "
        "I'll download it automatically and send it right back to you.\n\n"
        f"⚠️ Note: Telegram bots can only send files up to {MAX_FILESIZE_MB}MB.",
        reply_markup=MAIN_MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just send me a video link, for example:\n"
        "https://www.youtube.com/watch?v=xxxx\n"
        "https://www.instagram.com/reel/xxxx\n"
        "https://vm.tiktok.com/xxxx\n"
        "https://www.facebook.com/watch/?v=xxxx",
        reply_markup=MAIN_MENU,
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏠 Main menu:", reply_markup=MAIN_MENU)


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if await is_subscribed(context.bot, user_id):
        await query.edit_message_text("✅ Thanks for joining! You can now send links to download videos.")
    else:
        await query.answer("❌ You haven't joined the channel yet!", show_alert=True)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: sends a message to every user who has ever started the bot.

    Usage:
      • Reply to any message with /broadcast  -> forwards that message to everyone
      • /broadcast Your text here              -> sends that text to everyone
    """
    if update.effective_user.id != ADMIN_ID:
        return  # silently ignore non-admins

    source_message = update.message.reply_to_message
    text = " ".join(context.args) if context.args else None

    if not source_message and not text:
        await update.message.reply_text(
            "Usage:\n"
            "• Reply to any message with /broadcast to forward it to all users, or\n"
            "• /broadcast Your message here"
        )
        return

    users = load_users()
    sent, failed = 0, 0
    status = await update.message.reply_text(f"📢 Sending to {len(users)} users...")

    for uid in users:
        try:
            if source_message:
                await source_message.copy(chat_id=uid)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)  # gentle pacing to avoid Telegram rate limits

    await status.edit_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}")


# ----------------------------------------------------------------------
# MAIN MESSAGE HANDLER
# ----------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    # Bottom menu button taps
    if text == "📋 Supported Sites":
        await update.message.reply_text(supported_sites_text(), parse_mode=ParseMode.MARKDOWN)
        return
    if text == "🆘 Help":
        await help_command(update, context)
        return
    if text == "📢 Our Channel":
        await update.message.reply_text(f"📢 Join our channel: {FORCE_SUB_CHANNEL_LINK}")
        return

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    match = URL_REGEX.search(text)
    if not match:
        await update.message.reply_text("Please send a valid video link.")
        return

    url = match.group(1)

    if not is_supported_url(url):
        await update.message.reply_text(
            "Sorry, this site isn't supported yet. Tap 📋 Supported Sites below to see the full list."
        )
        return

    status_msg = await update.message.reply_text("⏳ Starting download...")
    progress_task = asyncio.create_task(
        _animate_progress(status_msg, context.bot, update.effective_chat.id)
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            loop = asyncio.get_running_loop()
            file_path = await loop.run_in_executor(None, download_video, url, tmp_dir)
            progress_task.cancel()

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > MAX_FILESIZE_MB:
                await status_msg.edit_text(
                    f"❌ This video is {file_size_mb:.1f}MB, which is over the {MAX_FILESIZE_MB}MB limit."
                )
                return

            await status_msg.edit_text("📤 Uploading...")
            with open(file_path, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption="✅ Here's your video!",
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                )
            await status_msg.delete()

        except yt_dlp.utils.DownloadError as e:
            progress_task.cancel()
            logger.error(f"Download error: {e}")
            await status_msg.edit_text(
                "❌ Couldn't download this video. It might be private, the link may be wrong, "
                "or the platform is blocking downloads right now."
            )
        except Exception as e:
            progress_task.cancel()
            logger.error(f"Unexpected error: {e}")
            await status_msg.edit_text(f"❌ Something went wrong: {e}")


def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN is not set. Set the BOT_TOKEN environment variable, "
            "or paste it directly in the code for local testing only."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
