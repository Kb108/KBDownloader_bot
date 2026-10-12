import os
import re
import json
import logging
import tempfile
import asyncio
from urllib.parse import quote
import requests
import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
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

FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "@loot_dells")
FORCE_SUB_CHANNEL_LINK = os.environ.get("FORCE_SUB_CHANNEL_LINK", "https://t.me/loot_dells")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "100"))

# Optional: self-hosted Telegram Bot API server (raises upload limit from 50MB to 2000MB).
# Example: LOCAL_BOT_API_URL=http://localhost:8081
LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL", "").rstrip("/")

SHOPPING_OFFER_LABEL = os.environ.get("SHOPPING_OFFER_LABEL", "🛒 Amazon Flipkart Offer")
SHOPPING_OFFER_URL = os.environ.get("SHOPPING_OFFER_URL", FORCE_SUB_CHANNEL_LINK)

BOT_SERVICE_LABEL = os.environ.get("BOT_SERVICE_LABEL", "🤖 Our Bot Service")
BOT_SERVICE_LINK = os.environ.get("BOT_SERVICE_LINK", "https://t.me/KbBotService")

SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@share_kb")

DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
USERS_FILE = os.path.join(DATA_DIR, "users.json")

SAVERAPI_KEY = os.environ.get("SAVERAPI_KEY", "")

SITE_COOKIES = os.environ.get("SITE_COOKIES") or os.environ.get("YOUTUBE_COOKIES", "")
COOKIES_FILE = os.path.join(DATA_DIR, "cookies.txt")

if SITE_COOKIES and SITE_COOKIES.strip():
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(SITE_COOKIES.strip())
    print("✅ Cookies file written successfully.")
else:
    if os.path.exists(COOKIES_FILE):
        try:
            os.remove(COOKIES_FILE)
        except Exception:
            pass
    print("⚠️ SITE_COOKIES is not set.")

URL_REGEX = re.compile(r"(https?://\S+)", re.IGNORECASE)

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

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac"}
SKIP_EXTENSIONS = (".part", ".ytdl", ".json", ".description", ".info.json")

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🚀 Start"), KeyboardButton("📋 Supported Sites")],
        [KeyboardButton("🆘 Help"), KeyboardButton("📢 Our Channel")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Status"), KeyboardButton("👥 Total Users")],
        [KeyboardButton("📢 Broadcast"), KeyboardButton("🔙 User Menu")],
    ],
    resize_keyboard=True,
)

# ----------------------------------------------------------------------
# STORAGE (only user list, used for broadcast & stats)
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
    return (
        "*📋 SUPPORTED SITES*\n\n"
        "Our downloader currently supports the following platforms:\n\n"
        "▶️ YouTube\n📘 Facebook\n📸 Instagram\n🎵 TikTok\n𝕏 Twitter / X\n"
        "👽 Reddit\n📌 Pinterest\n🎬 Vimeo\n🎮 Twitch (Clips)\n🌐 VK\n"
        "🟢 OK.ru\n📝 Tumblr\n💜 Likee\n🎧 SoundCloud\n🧵 Threads\n"
        "📺 Dailymotion\n👻 Snapchat\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "*⚠️ NOT SUPPORTED*\n\n"
        "🔒 Spotify\n🍎 Apple Music\n\n"
        "These platforms are currently not supported due to DRM protection.\n\n"
        "✨ More platforms may be added in the future!"
    )

def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join our channel", url=FORCE_SUB_CHANNEL_LINK)],
        [InlineKeyboardButton("✅ I've joined, check again", callback_data="check_join")],
    ])

def video_action_keyboard(source_url: str) -> InlineKeyboardMarkup:
    share_url = f"https://t.me/share/url?url={quote(source_url, safe='')}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Share Video", url=share_url),
        InlineKeyboardButton(SHOPPING_OFFER_LABEL, url=SHOPPING_OFFER_URL),
    ]])

async def is_subscribed(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError as e:
        logger.error(f"Membership check failed: {e}")
        return False

async def send_join_prompt(update: Update):
    text = (
        "*⚠️ You need to join our channel before using this bot.*\n\n"
        "Tap the button below to join, then tap \"I've joined, check again\"."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=join_keyboard())

# ----------------------------------------------------------------------
# DOWNLOAD LOGIC
# ----------------------------------------------------------------------
SAVERAPI_ENDPOINT = "https://saverapi.net/api/all-in-one-downloader-api"

def _resolve_redirect(url: str) -> str:
    try:
        resp = requests.head(url, headers=HTTP_HEADERS, allow_redirects=True, timeout=10)
        return resp.url
    except Exception:
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, allow_redirects=True, timeout=15, stream=True)
            final = resp.url
            resp.close()
            return final
        except Exception:
            return url

def _clean_caption(text) -> str:
    if not text:
        return ""
    text = str(text).strip()
    if len(text) > 1000:
        text = text[:1000].rstrip() + "…"
    return text

def _saverapi_download(url: str, download_dir: str) -> tuple:
    if not SAVERAPI_KEY:
        return [], ""
    try:
        resp = requests.get(SAVERAPI_ENDPOINT, params={"url": url}, headers={"x-api-key": SAVERAPI_KEY}, timeout=40)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"SaverAPI failed: {e}")
        return [], ""

    if data.get("error"):
        logger.warning(f"SaverAPI error: {data.get('error')}")
        return [], ""

    caption = _clean_caption(data.get("caption") or data.get("title") or "")

    items = data.get("medias") or data.get("items") or data.get("photos") or []
    if not items and data.get("download_url"):
        items = [{"url": data["download_url"], "type": data.get("type", "video")}]
    if not items and data.get("url"):
        items = [{"url": data["url"], "type": data.get("type", "image")}]

    if not items:
        return [], ""

    files = []
    for i, item in enumerate(items[:12]):
        media_url = item.get("url") or item.get("download_url") or item.get("src")
        if not media_url:
            continue
        media_type = (item.get("type") or "").lower()
        try:
            r = requests.get(media_url, headers=HTTP_HEADERS, timeout=60)
            r.raise_for_status()
            if len(r.content) < 10000:
                continue
        except Exception:
            continue

        content_type = r.headers.get("Content-Type", "").lower()
        if "video" in media_type or "video" in content_type or media_url.endswith((".mp4", ".mov")):
            ext = ".mp4"
        elif "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"
        else:
            ext = ".jpg"

        path = os.path.join(download_dir, f"{i:03d}_media{ext}")
        with open(path, "wb") as f:
            f.write(r.content)
        files.append(path)

    return files, caption

def _ytdlp_download(url: str, download_dir: str) -> tuple:
    outtmpl = os.path.join(download_dir, "%(autonumber)03d_%(title).60s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": f"best[filesize<{MAX_FILESIZE_MB}M]/best",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "playlistend": 12,
        "retries": 5,
        "fragment_retries": 5,
        "http_headers": HTTP_HEADERS,
        "extractor_args": {"youtube": {"player_client": ["android", "web", "tv"]}},
    }
    if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 50:
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except Exception as e:
        error_msg = str(e).lower()
        if any(x in error_msg for x in ["private", "login", "age-restricted", "challenge"]):
            raise Exception("This content requires login / is private. Update SITE_COOKIES.") from e
        raise

    caption = _clean_caption(info.get("title") or info.get("description") or "")
    files = sorted([
        os.path.join(download_dir, name)
        for name in os.listdir(download_dir)
        if not name.endswith(SKIP_EXTENSIONS)
    ])
    return files, caption

def _fallback_scrape(url: str, download_dir: str) -> tuple:
    cookies = None
    if os.path.exists(COOKIES_FILE):
        try:
            jar = requests.cookies.RequestsCookieJar()
            with open(COOKIES_FILE, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) >= 7:
                        domain, _, path, _, _, name, value = parts[:7]
                        jar.set(name, value, domain=domain, path=path)
            cookies = jar
        except Exception as e:
            logger.warning(f"Cookie parse error: {e}")

    headers = HTTP_HEADERS.copy()
    headers["Referer"] = "https://www.instagram.com/"

    try:
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=25)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.error(f"Fallback failed: {e}")
        return [], ""

    media_urls = []
    patterns = [
        r'"display_url":"(https://[^"]+)"',
        r'"image_versions2":\{"candidates":\[\{"url":"(https://[^"]+)"',
        r'"url":"(https://scontent[^"]+?_[ns]\d+x\d+[^"]*\.jpg[^"]*)"',
        r'"url":"(https://scontent[^"]+\.jpg[^"]*)"',
        r'property="og:image" content="(https://[^"]+)"',
        r'"video_url":"(https://[^"]+)"',
    ]
    for pattern in patterns:
        for u in re.findall(pattern, html):
            u = u.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            if u.startswith("http") and u not in media_urls:
                if any(x in u.lower() for x in ["150x150", "320x320", "s150x150", "s320x320", "profile", "t51.2885-19"]):
                    continue
                media_urls.append(u)

    caption = ""
    cap_match = re.search(r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"([^"]+)"', html)
    if cap_match:
        try:
            caption = _clean_caption(cap_match.group(1).encode().decode("unicode_escape"))
        except Exception:
            caption = _clean_caption(cap_match.group(1))
    else:
        og = re.search(r'property="og:description" content="([^"]+)"', html)
        if og:
            caption = _clean_caption(og.group(1))

    files = []
    seen = set()
    for i, media_url in enumerate(media_urls[:12]):
        if media_url in seen:
            continue
        seen.add(media_url)
        try:
            r = requests.get(media_url, headers=headers, timeout=40)
            r.raise_for_status()
            if len(r.content) < 15000:
                continue
            content_type = r.headers.get("Content-Type", "").lower()
            if "video" in content_type or media_url.endswith((".mp4", ".mov")):
                ext = ".mp4"
            elif "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            else:
                ext = ".jpg"
            path = os.path.join(download_dir, f"{i:03d}_photo{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            files.append(path)
        except Exception:
            continue
    return files, caption

def download_media(url: str, download_dir: str) -> tuple:
    url = _resolve_redirect(url)

    # 1. SaverAPI first (best for Instagram)
    files, caption = _saverapi_download(url, download_dir)
    if files:
        logger.info(f"[{url}] SaverAPI: {len(files)} file(s)")
        return files, caption

    # 2. yt-dlp
    try:
        files, caption = _ytdlp_download(url, download_dir)
        if files:
            logger.info(f"[{url}] yt-dlp: {len(files)} file(s)")
            return files, caption
    except Exception as e:
        logger.warning(f"[{url}] yt-dlp failed: {e}")
        if "private" in str(e).lower() or "login" in str(e).lower():
            raise

    # 3. Fallback
    files, caption = _fallback_scrape(url, download_dir)
    if files:
        logger.info(f"[{url}] Fallback: {len(files)} file(s)")
    else:
        logger.error(f"[{url}] All methods failed")
    return files, caption

# ----------------------------------------------------------------------
# SEND FILES
# ----------------------------------------------------------------------
async def send_downloaded_file(update: Update, file_path: str, caption: str = "", source_url: str = ""):
    ext = os.path.splitext(file_path)[1].lower()
    with open(file_path, "rb") as f:
        if ext in IMAGE_EXTENSIONS:
            await update.message.reply_photo(photo=f, caption=caption or "✅ Here's your photo!")
        elif ext in AUDIO_EXTENSIONS:
            await update.message.reply_audio(audio=f, caption=caption or "✅ Here's your audio!", read_timeout=120, write_timeout=120)
        else:
            await update.message.reply_video(
                video=f, caption=caption or "✅ Here's your video!",
                supports_streaming=True, read_timeout=120, write_timeout=120,
                reply_markup=video_action_keyboard(source_url) if source_url else None,
            )

async def send_downloaded_files(update: Update, file_paths: list, caption: str = "", source_url: str = ""):
    if len(file_paths) == 1:
        await send_downloaded_file(update, file_paths[0], caption, source_url)
        return

    album_paths = [p for p in file_paths if os.path.splitext(p)[1].lower() not in AUDIO_EXTENSIONS]
    audio_paths = [p for p in file_paths if os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS]
    has_video = any(os.path.splitext(p)[1].lower() not in IMAGE_EXTENSIONS | AUDIO_EXTENSIONS for p in album_paths)

    for i in range(0, len(album_paths), 10):
        batch = album_paths[i:i+10]
        opened = []
        media = []
        for idx, path in enumerate(batch):
            f = open(path, "rb")
            opened.append(f)
            ext = os.path.splitext(path)[1].lower()
            cap = (caption or "✅ Full album!") if idx == 0 else None
            if ext in IMAGE_EXTENSIONS:
                media.append(InputMediaPhoto(media=f, caption=cap))
            else:
                media.append(InputMediaVideo(media=f, caption=cap))
        if media:
            await update.message.reply_media_group(media=media, read_timeout=120, write_timeout=120)
        for f in opened:
            f.close()

    if has_video and source_url:
        await update.message.reply_text("🎬", reply_markup=video_action_keyboard(source_url))

    for path in audio_paths:
        await send_downloaded_file(update, path, caption)

# ----------------------------------------------------------------------
# HANDLERS
# ----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)

    if user_id == ADMIN_ID:
        await update.message.reply_text("*👑 Admin Panel*\n\nChoose an option from the menu below.", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
        return

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    await update.message.reply_text(
        "*👋 Welcome to KB Downloader!*\n\n"
        "📥 Send any video, photo or audio link from:\n"
        "▶️ YouTube • Facebook • Instagram • TikTok and more\n\n"
        "🆓 *100% Free — Unlimited downloads!*\n\n"
        f"⚠️ Max file size: {MAX_FILESIZE_MB}MB\n\n"
        "🚀 Just send a link to start!",
        parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU,
    )
    await update.message.reply_text("👇 Check these out:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(SHOPPING_OFFER_LABEL, url=SHOPPING_OFFER_URL), InlineKeyboardButton(BOT_SERVICE_LABEL, url=BOT_SERVICE_LINK)],
    ]))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"*🆘 Need Help?*\n\nContact: *{SUPPORT_CONTACT}*", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("*👑 Admin Menu:*", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
    else:
        await update.message.reply_text("*🏠 Main Menu:*", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    await update.message.reply_text(
        f"*📊 Bot Status*\n\n👥 Total Users: *{len(users)}*\n🆓 Mode: Free (unlimited)",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU,
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await is_subscribed(context.bot, query.from_user.id):
        await query.answer()
        await query.edit_message_text("✅ Thanks for joining! You can now send links.")
    else:
        await query.answer("❌ You haven't joined yet!", show_alert=True)

BROADCAST_CAPTION_RE = re.compile(r"^/broadcast(@\w+)?\s*", re.IGNORECASE)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    message = update.message
    source = message.reply_to_message
    text = None
    override_caption = None

    if not source:
        if message.caption and BROADCAST_CAPTION_RE.match(message.caption):
            source = message
            override_caption = BROADCAST_CAPTION_RE.sub("", message.caption).strip()
        elif context.args:
            text = " ".join(context.args)

    if not source and not text:
        await update.message.reply_text("*📢 How to Broadcast:*\n\n1. Reply with `/broadcast`\n2. Or `/broadcast Your message`", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
        return

    users = load_users()
    sent = failed = 0
    status = await update.message.reply_text(f"📢 Sending to {len(users)} users...")
    for uid in users:
        try:
            if source is message:
                await source.copy(chat_id=uid, caption=override_caption or None)
            elif source:
                await source.copy(chat_id=uid)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}")

async def _animate_progress(status_msg, bot, chat_id):
    frames = ["⏳ Downloading", "⏳ Downloading.", "⏳ Downloading..", "⏳ Downloading..."]
    i = 0
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                await status_msg.edit_text(frames[i % 4])
            except Exception:
                pass
            i += 1
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    save_user(user_id)

    if user_id == ADMIN_ID:
        if text == "📊 Status":
            await stats_command(update, context)
            return
        if text == "👥 Total Users":
            await update.message.reply_text(f"*👥 Total Users:* {len(load_users())}", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
            return
        if text == "📢 Broadcast":
            await update.message.reply_text("*📢 How to Broadcast:*\n\n1. Reply with `/broadcast`\n2. Or `/broadcast message`", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
            return
        if text == "🔙 User Menu":
            await update.message.reply_text("*🏠 Switched to User Menu:*", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)
            return

    if text == "🚀 Start":
        await start(update, context)
        return
    if text == "📋 Supported Sites":
        await update.message.reply_text(supported_sites_text(), parse_mode=ParseMode.MARKDOWN)
        return
    if text == "🆘 Help":
        await help_command(update, context)
        return
    if text == "📢 Our Channel":
        await update.message.reply_text(
            "*📢 Our Official Channel*\n\n"
            "Join our channel for updates, offers and support:\n\n"
            "👉 https://t.me/KbBotService\n\n"
            "Thank you for using our bot!",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    match = URL_REGEX.search(text)
    if not match:
        await update.message.reply_text("*Please send a valid video or photo link.*", parse_mode=ParseMode.MARKDOWN)
        return

    url = match.group(1)
    if not is_supported_url(url):
        await update.message.reply_text("*Sorry, this site is not supported yet.*\nTap 📋 Supported Sites.", parse_mode=ParseMode.MARKDOWN)
        return

    status_msg = await update.message.reply_text("⏳ Starting download...")
    progress_task = asyncio.create_task(_animate_progress(status_msg, context.bot, update.effective_chat.id))

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            loop = asyncio.get_running_loop()
            file_paths, caption = await loop.run_in_executor(None, download_media, url, tmp_dir)
            progress_task.cancel()

            if not file_paths:
                await status_msg.edit_text(
                    "❌ Could not download this media.\n\n"
                    "Possible reasons:\n• Private / Restricted content\n• Invalid link\n• Platform blocking"
                )
                return

            fitting = [p for p in file_paths if os.path.getsize(p) / (1024 * 1024) <= MAX_FILESIZE_MB]
            skipped = len(file_paths) - len(fitting)
            if not fitting:
                await status_msg.edit_text(f"❌ File larger than {MAX_FILESIZE_MB}MB limit.")
                return

            await status_msg.edit_text("📤 Uploading...")
            try:
                await send_downloaded_files(update, fitting, caption, url)
            except TelegramError as e:
                if "too big" in str(e).lower() or "entity too large" in str(e).lower():
                    await status_msg.edit_text("❌ File is too big for Telegram to send.")
                    return
                raise

            if skipped:
                await update.message.reply_text(f"⚠️ {skipped} item(s) skipped (size limit).")
            await status_msg.delete()

        except Exception as e:
            progress_task.cancel()
            logger.error(f"Error: {e}")
            await status_msg.edit_text(f"❌ {e}")

def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN is not set.")
    builder = ApplicationBuilder().token(BOT_TOKEN)
    if LOCAL_BOT_API_URL:
        builder = (
            builder.base_url(f"{LOCAL_BOT_API_URL}/bot")
            .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
            .local_mode(True)
        )
        logger.info(f"Using local Bot API server: {LOCAL_BOT_API_URL}")
    else:
        logger.info("Using official Bot API (50MB upload limit).")
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("admin", menu_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.CaptionRegex(BROADCAST_CAPTION_RE), broadcast_command))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
