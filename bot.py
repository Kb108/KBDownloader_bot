import os
import re
import json
import logging
import tempfile
import asyncio
import time
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

SHOPPING_OFFER_LABEL = os.environ.get("SHOPPING_OFFER_LABEL", "🛒 Amazon Flipkart Offer")
SHOPPING_OFFER_URL = os.environ.get("SHOPPING_OFFER_URL", FORCE_SUB_CHANNEL_LINK)

BOT_SERVICE_LABEL = os.environ.get("BOT_SERVICE_LABEL", "🤖 Our Bot Service")
BOT_SERVICE_LINK = os.environ.get("BOT_SERVICE_LINK", "https://t.me/KbBotService")

PREMIUM_PRICE_LABEL = os.environ.get("PREMIUM_PRICE_LABEL", "₹20 / month")
PREMIUM_CONTACT = "@share_kb"
PREMIUM_DAYS_DEFAULT = int(os.environ.get("PREMIUM_DAYS_DEFAULT", "30"))

DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
USERS_FILE = os.path.join(DATA_DIR, "users.json")

STARTER_POINTS = int(os.environ.get("STARTER_POINTS", "3"))
REFERRAL_POINTS = int(os.environ.get("REFERRAL_POINTS", "10"))
POINTS_FILE = os.path.join(DATA_DIR, "points.json")

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
    print("⚠️ SITE_COOKIES is not set. Private content may fail.")

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
        [KeyboardButton("🎁 Refer & Earn"), KeyboardButton("💎 Premium")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Status"), KeyboardButton("👥 Total Users")],
        [KeyboardButton("📢 Broadcast"), KeyboardButton("💎 Give Premium")],
        [KeyboardButton("ℹ️ How to Give Premium"), KeyboardButton("🔙 User Menu")],
    ],
    resize_keyboard=True,
)

# ----------------------------------------------------------------------
# STORAGE FUNCTIONS
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

def load_points() -> dict:
    if os.path.exists(POINTS_FILE):
        try:
            with open(POINTS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_points(data: dict):
    with open(POINTS_FILE, "w") as f:
        json.dump(data, f)

def _get_or_init_record(user_id: int, data: dict) -> dict:
    key = str(user_id)
    if key not in data:
        data[key] = {"points": STARTER_POINTS, "referred_by": None, "referral_count": 0}
    return data[key]

def get_points(user_id: int) -> int:
    data = load_points()
    record = _get_or_init_record(user_id, data)
    save_points(data)
    return record["points"]

def get_referral_count(user_id: int) -> int:
    data = load_points()
    record = _get_or_init_record(user_id, data)
    save_points(data)
    return record.get("referral_count", 0)

def has_points(user_id: int) -> bool:
    if user_id == ADMIN_ID or is_premium(user_id):
        return True
    return get_points(user_id) > 0

def deduct_point(user_id: int) -> bool:
    if user_id == ADMIN_ID or is_premium(user_id):
        return True
    data = load_points()
    record = _get_or_init_record(user_id, data)
    if record["points"] <= 0:
        save_points(data)
        return False
    record["points"] -= 1
    save_points(data)
    return True

def register_referral(referrer_id: int, new_user_id: int) -> bool:
    if referrer_id == new_user_id:
        return False
    data = load_points()
    new_record = _get_or_init_record(new_user_id, data)
    if new_record.get("referred_by") is not None:
        save_points(data)
        return False
    new_record["referred_by"] = referrer_id
    referrer_record = _get_or_init_record(referrer_id, data)
    referrer_record["points"] += REFERRAL_POINTS
    referrer_record["referral_count"] = referrer_record.get("referral_count", 0) + 1
    save_points(data)
    return True

def grant_premium(user_id: int, days: int):
    data = load_points()
    record = _get_or_init_record(user_id, data)
    now = time.time()
    base = max(record.get("premium_until") or 0, now)
    record["premium_until"] = base + days * 86400
    save_points(data)

def is_premium(user_id: int) -> bool:
    data = load_points()
    record = _get_or_init_record(user_id, data)
    save_points(data)
    until = record.get("premium_until")
    return bool(until and until > time.time())

def premium_days_left(user_id: int) -> int:
    data = load_points()
    record = _get_or_init_record(user_id, data)
    save_points(data)
    until = record.get("premium_until")
    if not until or until <= time.time():
        return 0
    return int((until - time.time()) // 86400) + 1

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

def referral_link_for(user_id: int, bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

async def send_referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username
    link = referral_link_for(user_id, bot_username)
    points = get_points(user_id)
    referrals = get_referral_count(user_id)
    text = (
        "*🎁 Refer & Earn Points*\n\n"
        f"🔗 Your referral link:\n`{link}`\n\n"
        f"⭐ Your current points: *{points}*\n"
        f"👥 Total referrals: *{referrals}*\n\n"
        "*How it works:*\n"
        f"• 1 referral = *{REFERRAL_POINTS} points*\n"
        "• 1 download = *1 point*\n\n"
        f"When a friend joins using your link, you get *{REFERRAL_POINTS} points* instantly."
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Share this link",
                url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote('🎬 Download any video or photo for free!', safe='')}")
        ]])
    )

async def referral_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await send_referral_info(update, context)

async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_referral_info(update, context)

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_premium(user_id):
        days_left = premium_days_left(user_id)
        text = (
            f"*💎 You already have Premium!*\n\n"
            f"*Remaining Days:* {days_left} day(s)\n\n"
            f"*Unlimited downloads* — no points needed.\n\n"
            f"For any problem contact: *{PREMIUM_CONTACT}*"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    text = (
        "*💎 Get Premium — Unlimited Downloads*\n\n"
        "*About this Bot:*\n"
        "Download videos & photos from YouTube, Facebook, Instagram, TikTok and many more sites easily.\n\n"
        f"*Premium Price:* Only *{PREMIUM_PRICE_LABEL}*\n\n"
        "*Benefits:*\n"
        "✅ Unlimited downloads\n✅ No points required\n✅ Priority support\n\n"
        f"*To buy Premium contact:*\n*{PREMIUM_CONTACT}*\n\n"
        "Send payment screenshot + your Telegram username or User ID.\n\n"
        f"For any problem contact: *{PREMIUM_CONTACT}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def addpremium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text(
            f"*Usage:*\n`/addpremium <user_id> [days={PREMIUM_DAYS_DEFAULT}]`\n\n"
            f"*Example:*\n`/addpremium 123456789 30`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        target_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else PREMIUM_DAYS_DEFAULT
    except ValueError:
        await update.message.reply_text(
            f"*Usage:*\n`/addpremium <user_id> [days={PREMIUM_DAYS_DEFAULT}]`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    grant_premium(target_id, days)
    await update.message.reply_text(f"✅ Premium granted to `{target_id}` for *{days}* day(s).", parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"*🎉 Your Premium is now active for {days} day(s)!*\nUnlimited downloads unlocked.\n\nFor any problem contact: *{PREMIUM_CONTACT}*",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        pass

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
    except requests.RequestException:
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, allow_redirects=True, timeout=15, stream=True)
            final = resp.url
            resp.close()
            return final
        except requests.RequestException:
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
        resp = requests.get(
            SAVERAPI_ENDPOINT,
            params={"url": url},
            headers={"x-api-key": SAVERAPI_KEY},
            timeout=40,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"SaverAPI failed: {e}")
        return [], ""

    if data.get("error"):
        logger.warning(f"SaverAPI error: {data.get('error')}")
        return [], ""

    caption = _clean_caption(data.get("caption") or data.get("title") or "")

    items = []
    if data.get("medias"):
        items = data["medias"]
    elif data.get("items"):
        items = data["items"]
    elif data.get("photos"):
        items = data["photos"]
    elif data.get("download_url"):
        items = [{"url": data["download_url"], "type": data.get("type", "video")}]
    elif data.get("url"):
        items = [{"url": data["url"], "type": data.get("type", "image")}]

    if not items:
        return [], ""

    files = []
    for i, item in enumerate(items[:12]):
        media_url = item.get("url") or item.get("download_url") or item.get("src")
        if not media_url:
            continue
        media_type = (item.get("type") or item.get("media_type") or "").lower()
        try:
            r = requests.get(media_url, headers=HTTP_HEADERS, timeout=60)
            r.raise_for_status()
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
        "extractor_args": {
            "youtube": {"player_client": ["android", "web", "tv"]},
        },
    }
    if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 50:
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except Exception as e:
        error_msg = str(e).lower()
        if any(x in error_msg for x in ["private", "login", "age-restricted", "challenge", "rate-limit"]):
            raise Exception("This content requires login / is private. Update SITE_COOKIES.") from e
        raise

    caption = _clean_caption(info.get("title") or info.get("description") or "")
    files = sorted(
        os.path.join(download_dir, name)
        for name in os.listdir(download_dir)
        if not name.endswith(SKIP_EXTENSIONS)
    )
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
        logger.error(f"Fallback request failed: {e}")
        return [], ""

    media_urls = []
    patterns = [
        r'"display_url":"([^"]+)"',
        r'"video_url":"([^"]+)"',
        r'property="og:image" content="([^"]+)"',
        r'property="og:video" content="([^"]+)"',
        r'"url":"(https://[^"]+\.(?:jpg|jpeg|png|webp|mp4)[^"]*)"',
    ]
    for pattern in patterns:
        found = re.findall(pattern, html)
        for u in found:
            u = u.replace("\\u0026", "&").replace("\\/", "/")
            if u.startswith("http") and u not in media_urls:
                media_urls.append(u)

    caption = ""
    cap_match = re.search(r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"([^"]+)"', html)
    if cap_match:
        caption = _clean_caption(cap_match.group(1).encode().decode("unicode_escape"))
    else:
        og_cap = re.search(r'property="og:description" content="([^"]+)"', html)
        if og_cap:
            caption = _clean_caption(og_cap.group(1))

    files = []
    seen = set()
    for i, media_url in enumerate(media_urls[:12]):
        if media_url in seen:
            continue
        seen.add(media_url)
        try:
            r = requests.get(media_url, headers=headers, timeout=40)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "").lower()
            if "video" in content_type or media_url.endswith((".mp4", ".mov")):
                ext = ".mp4"
            elif "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            else:
                ext = ".jpg"
            path = os.path.join(download_dir, f"{i:03d}_fallback{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            files.append(path)
        except Exception:
            continue
    return files, caption

def download_media(url: str, download_dir: str) -> tuple:
    url = _resolve_redirect(url)

    files, caption = _saverapi_download(url, download_dir)
    if files:
        logger.info(f"[{url}] SaverAPI success: {len(files)} file(s)")
        return files, caption

    try:
        files, caption = _ytdlp_download(url, download_dir)
        if files:
            logger.info(f"[{url}] yt-dlp success: {len(files)} file(s)")
            return files, caption
    except Exception as e:
        logger.warning(f"[{url}] yt-dlp failed: {e}")
        if "private" in str(e).lower() or "login" in str(e).lower():
            raise

    files, caption = _fallback_scrape(url, download_dir)
    if files:
        logger.info(f"[{url}] Fallback success: {len(files)} file(s)")
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

    for batch_start in range(0, len(album_paths), 10):
        batch = album_paths[batch_start:batch_start + 10]
        opened = []
        media = []
        for idx, path in enumerate(batch):
            f = open(path, "rb")
            opened.append(f)
            ext = os.path.splitext(path)[1].lower()
            item_caption = (caption or "✅ Full album from the post!") if idx == 0 else None
            if ext in IMAGE_EXTENSIONS:
                media.append(InputMediaPhoto(media=f, caption=item_caption))
            else:
                media.append(InputMediaVideo(media=f, caption=item_caption))
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
    is_new_user = user_id not in load_users()
    save_user(user_id)

    if user_id == ADMIN_ID:
        await update.message.reply_text("*👑 Admin Panel*\n\nChoose an option from the menu below.", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
        return

    if not await is_subscribed(context.bot, user_id):
        await send_join_prompt(update)
        return

    if is_new_user and context.args:
        payload = context.args[0]
        if payload.startswith("ref_"):
            try:
                referrer_id = int(payload[4:])
                if referrer_id and register_referral(referrer_id, user_id):
                    try:
                        await context.bot.send_message(chat_id=referrer_id, text=f"*🎉 New user joined with your link!*\n+{REFERRAL_POINTS} points added.", parse_mode=ParseMode.MARKDOWN)
                    except TelegramError:
                        pass
            except ValueError:
                pass

    points = get_points(user_id)
    await update.message.reply_text(
        "*👋 Welcome to KB Downloader!*\n\n"
        "📥 Send any video, photo or audio link from:\n"
        "▶️ YouTube • Facebook • Instagram • TikTok and more\n\n"
        f"⭐ Your points: *{points}* (1 point = 1 download)\n"
        f"🎁 Refer a friend to earn +{REFERRAL_POINTS} points!\n\n"
        f"⚠️ Max file size: {MAX_FILESIZE_MB}MB\n\n"
        "🚀 Just send a link to start!",
        parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU,
    )
    await update.message.reply_text("👇 Check these out:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(SHOPPING_OFFER_LABEL, url=SHOPPING_OFFER_URL),
         InlineKeyboardButton(BOT_SERVICE_LABEL, url=BOT_SERVICE_LINK)],
        [InlineKeyboardButton("🎁 Refer & Earn Points", callback_data="show_referral")],
    ]))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"*🆘 Need Help?*\n\nContact: *{PREMIUM_CONTACT}*\n\nDescribe your problem.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU,
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("*👑 Admin Menu:*", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
    else:
        await update.message.reply_text("*🏠 Main Menu:*", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    points_data = load_points()
    premium_count = sum(1 for rec in points_data.values() if rec.get("premium_until", 0) > time.time())
    await update.message.reply_text(
        f"*📊 Bot Status*\n\n👥 Total Users: *{len(users)}*\n💎 Active Premium: *{premium_count}*\n"
        f"⭐ Starter Points: {STARTER_POINTS}\n🎁 Referral Points: {REFERRAL_POINTS}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU,
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await is_subscribed(context.bot, query.from_user.id):
        await query.edit_message_text("✅ Thanks for joining! You can now send links.")
    else:
        await query.answer("❌ You haven't joined yet!", show_alert=True)

BROADCAST_CAPTION_RE = re.compile(r"^/broadcast(@\w+)?\s*", re.IGNORECASE)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    message = update.message
    source_message = message.reply_to_message
    text = None
    override_caption = None

    if not source_message:
        if message.caption and BROADCAST_CAPTION_RE.match(message.caption):
            source_message = message
            override_caption = BROADCAST_CAPTION_RE.sub("", message.caption).strip()
        elif context.args:
            text = " ".join(context.args)

    if not source_message and not text:
        await update.message.reply_text(
            "*📢 How to Broadcast:*\n\n1. Reply to any message with `/broadcast`\n"
            "2. Or `/broadcast Your message`\n3. Or send media with caption `/broadcast caption`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU,
        )
        return

    users = load_users()
    sent = failed = 0
    status = await update.message.reply_text(f"📢 Sending to {len(users)} users...")
    for uid in users:
        try:
            if source_message is message:
                await source_message.copy(chat_id=uid, caption=override_caption or None)
            elif source_message:
                await source_message.copy(chat_id=uid)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except TelegramError:
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
            except TelegramError:
                pass
            i += 1
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

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
        if text == "💎 Give Premium":
            await update.message.reply_text("*💎 Give Premium:*\n\n`/addpremium <user_id> [days]`\n\nExample: `/addpremium 123456789 30`", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
            return
        if text == "ℹ️ How to Give Premium":
            await update.message.reply_text("*ℹ️ How to Give Premium:*\n\n1. Get User ID from @userinfobot\n2. Run `/addpremium USER_ID 30`", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU)
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
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False
        )
        return
    if text == "🎁 Refer & Earn":
        await send_referral_info(update, context)
        return
    if text == "💎 Premium":
        await premium_command(update, context)
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

    if not has_points(user_id):
        link = referral_link_for(user_id, context.bot.username)
        await update.message.reply_text(
            f"*❌ You are out of points.*\n\n*🎁 Share your link to earn +{REFERRAL_POINTS} points!*\n\n"
            f"🔗 `{link}`\n\n*💎 Or buy Premium:* {PREMIUM_CONTACT}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Share & Earn Points", url=f"https://t.me/share/url?url={quote(link, safe='')}")]])
        )
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
                    "*❌ Could not download this media.*\n\n"
                    "Possible reasons:\n• Private / Age-restricted\n• Invalid link\n• Platform blocking\n\n"
                    "Make sure SITE_COOKIES and SAVERAPI_KEY are set."
                )
                return

            fitting = [p for p in file_paths if os.path.getsize(p) / (1024*1024) <= MAX_FILESIZE_MB]
            skipped = len(file_paths) - len(fitting)
            if not fitting:
                await status_msg.edit_text(f"*❌ File larger than {MAX_FILESIZE_MB}MB limit.*")
                return

            await status_msg.edit_text("📤 Uploading...")
            try:
                await send_downloaded_files(update, fitting, caption, url)
            except TelegramError as e:
                if "too big" in str(e).lower() or "entity too large" in str(e).lower():
                    await status_msg.edit_text("*❌ File exceeds Telegram's 50MB limit.*")
                    return
                raise

            deduct_point(user_id)
            if skipped:
                await update.message.reply_text(f"⚠️ {skipped} item(s) skipped (over size limit).")
            await status_msg.delete()

        except Exception as e:
            progress_task.cancel()
            logger.error(f"Error: {e}")
            await status_msg.edit_text(f"❌ {e}")

def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN is not set.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("admin", menu_command))
    app.add_handler(CommandHandler("refer", refer_command))
    app.add_handler(CommandHandler("premium", premium_command))
    app.add_handler(CommandHandler("addpremium", addpremium_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.CaptionRegex(BROADCAST_CAPTION_RE), broadcast_command))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(referral_callback, pattern="^show_referral$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
