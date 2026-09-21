import os
import re
import json
import logging
import tempfile
import asyncio

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

# Force-subscribe channel. Change these in Railway Variables if you ever
# switch channels — no code edit needed.
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "@loot_dells")
FORCE_SUB_CHANNEL_LINK = os.environ.get("FORCE_SUB_CHANNEL_LINK", "https://t.me/loot_dells")

# Your personal numeric Telegram ID. Needed for /broadcast and /stats.
# Get it from @userinfobot.
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

MAX_FILESIZE_MB = 50

# DATA_DIR should point to a persistent Railway Volume (e.g. /data) so the
# users list and cookies survive redeploys and restarts. Falls back to the
# local folder if no volume is configured (fine for local testing only).
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
USERS_FILE = os.path.join(DATA_DIR, "users.json")

# SaverAPI.NET is a specialized third-party downloader service — the same
# kind of service popular "all-in-one downloader" bots rely on internally.
# It handles the tricky per-platform parsing (Facebook photos, Instagram
# albums, Twitter GIFs, etc.) far more reliably than raw yt-dlp scraping.
# Get a free key at https://saverapi.net and set SAVERAPI_KEY in Railway.
# If it's not set, the bot falls back to yt-dlp + page-scraping only —
# which is noticeably weaker for pure PHOTO posts (yt-dlp is primarily a
# *video* extractor, and page-scraping breaks on any login-walled page).
SAVERAPI_KEY = os.environ.get("SAVERAPI_KEY", "")

# Private/age-restricted content on YouTube, Facebook, and Instagram needs a
# logged-in browser session's cookies to download. Export cookies.txt from a
# browser where you're logged into these sites (all three at once works
# great) and paste its content into the SITE_COOKIES environment variable.
# Never commit a cookies.txt file to GitHub.
SITE_COOKIES = os.environ.get("SITE_COOKIES") or os.environ.get("YOUTUBE_COOKIES", "")
COOKIES_FILE = os.path.join(DATA_DIR, "cookies.txt")
if SITE_COOKIES:
    with open(COOKIES_FILE, "w") as f:
        f.write(SITE_COOKIES)

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

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac"}
SKIP_EXTENSIONS = (".part", ".ytdl", ".json", ".description", ".info.json")

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not SAVERAPI_KEY:
    logger.warning(
        "SAVERAPI_KEY is not set. Photo-only posts (especially Facebook and "
        "Instagram) are the weakest case without it — yt-dlp is built for "
        "video, and the raw page-scrape fallback breaks on login-walled "
        "pages. Get a free key at https://saverapi.net and set SAVERAPI_KEY "
        "in your Railway Variables to fix most photo-download failures."
    )

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🚀 Start"), KeyboardButton("📋 Supported Sites")],
        [KeyboardButton("🆘 Help"), KeyboardButton("📢 Our Channel")],
    ],
    resize_keyboard=True,
)


# ----------------------------------------------------------------------
# SIMPLE USER STORAGE (for /broadcast and /stats)
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
# DOWNLOAD LOGIC
# ----------------------------------------------------------------------
SAVERAPI_ENDPOINT = "https://saverapi.net/api/all-in-one-downloader-api"
# The real SaverAPI.NET contract (confirmed from the official saverapi-client
# SDK source) is: GET request, the url passed as a query PARAMETER (not a
# JSON body), and the key sent in an "x-api-key" header (not "Authorization:
# Bearer ..."). Using the wrong shape here is exactly what produces a
# 401 Unauthorized even with a perfectly valid key.


def _resolve_redirect(url: str) -> str:
    """Follows short/share links (facebook.com/share/p/..., fb.watch, pin.it,
    vm.tiktok.com, etc.) to their final real URL before handing off to any
    downloader tier. SaverAPI and yt-dlp both work far more reliably against
    the resolved post URL than against a redirect wrapper — a share-link
    that never gets resolved is a common reason a photo post fails to
    download even though a direct link to the same kind of post works."""
    try:
        resp = requests.head(
            url, headers=HTTP_HEADERS, allow_redirects=True, timeout=10
        )
        final_url = resp.url
    except requests.RequestException:
        try:
            resp = requests.get(
                url, headers=HTTP_HEADERS, allow_redirects=True, timeout=15, stream=True
            )
            final_url = resp.url
            resp.close()
        except requests.RequestException as e:
            logger.warning(f"Could not resolve redirect for {url}: {e}")
            return url

    if final_url != url:
        logger.info(f"Resolved redirect: {url} -> {final_url}")
    return final_url


def _clean_caption(text) -> str:
    """Normalizes a caption pulled from a source post: strips whitespace,
    collapses blank lines, and trims to Telegram's 1024-char caption limit
    (leaving room for the small credit line appended when sending)."""
    if not text:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    limit = 1000
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _saverapi_download(url: str, download_dir: str) -> tuple:
    """Tries SaverAPI.NET first — a specialized downloader service that
    handles Facebook photos, Instagram albums, and similar tricky posts far
    more reliably than raw yt-dlp scraping. Returns ([], "") if no key is
    set, the platform isn't supported by it, or the request fails.
    Returns (files, original_caption)."""
    if not SAVERAPI_KEY:
        return [], ""

    try:
        resp = requests.get(
            SAVERAPI_ENDPOINT,
            params={"url": url},
            headers={"x-api-key": SAVERAPI_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"SaverAPI request failed: {e}")
        return [], ""

    if data.get("error"):
        logger.warning(f"SaverAPI returned an error for {url}: {data.get('error')}")
        return [], ""

    caption = _clean_caption(data.get("caption") or data.get("title"))

    # The API sometimes returns one item ("download_url") and sometimes a
    # list of items for multi-photo/video posts — handle both shapes.
    items = data.get("medias") or data.get("items") or data.get("photos")
    if not items:
        single_url = data.get("download_url")
        items = [{"url": single_url, "type": data.get("type", "video")}] if single_url else []

    if not items:
        logger.warning(f"SaverAPI returned no downloadable items for {url}: {data}")
        return [], ""

    files = []
    for i, item in enumerate(items[:10]):
        media_url = item.get("url") or item.get("download_url")
        media_type = (item.get("type") or "video").lower()
        if not media_url:
            continue
        try:
            r = requests.get(media_url, headers=HTTP_HEADERS, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"SaverAPI media download failed for {media_url}: {e}")
            continue

        ext = ".mp4" if "video" in media_type else ".jpg" if "photo" in media_type or "image" in media_type else ".mp3"
        path = os.path.join(download_dir, f"{i:03d}_saverapi{ext}")
        with open(path, "wb") as f:
            f.write(r.content)
        files.append(path)

    return files, caption


def _ytdlp_download(url: str, download_dir: str) -> tuple:
    """Tries downloading with yt-dlp. Returns (files, original_caption) —
    files is a list of downloaded file paths (possibly several, for
    multi-item posts like Instagram/Facebook carousels), and the caption
    comes from the post's title/description as yt-dlp extracted them.

    Note: yt-dlp is primarily a *video* extractor. It does download stand-
    alone photo posts on some sites (e.g. Twitter, some Instagram posts),
    but it's far less reliable at this than SaverAPI — this is the #1
    reason photo links can fail even when video links from the same site
    work fine.
    """
    outtmpl = os.path.join(download_dir, "%(autonumber)03d_%(title).60s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": f"best[filesize<{MAX_FILESIZE_MB}M]/best",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        # Cap items pulled from one link — guards against someone pasting a
        # whole profile/channel URL, and Telegram albums max out at 10 anyway.
        "playlistend": 10,
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}

    # yt-dlp uses "title" as a generic label even for photo posts (often the
    # post's own caption text on Instagram/Facebook/Twitter); fall back to
    # "description" when title is just a generic placeholder.
    raw_caption = info.get("title") or info.get("description") or ""
    if raw_caption.lower() in ("", "no title"):
        raw_caption = info.get("description") or ""
    caption = _clean_caption(raw_caption)

    files = sorted(
        os.path.join(download_dir, name)
        for name in os.listdir(download_dir)
        if not name.endswith(SKIP_EXTENSIONS)
    )
    return files, caption


def _fallback_scrape(url: str, download_dir: str) -> tuple:
    """Last-resort fallback for posts yt-dlp's extractors don't handle well —
    most commonly plain Facebook/Instagram photo posts. Fetches the page HTML
    directly and pulls whatever og/twitter image or video tags it can find,
    plus the page's og:title/og:description as the original caption.

    Note: this only works if requests.get() actually receives the real page
    (not a login wall). Facebook and Instagram frequently serve a stripped
    login page to logged-out / non-browser requests, in which case there is
    no real og:image to find and this tier will legitimately return [] —
    that's the scenario SaverAPI (see SAVERAPI_KEY above) is meant to cover.
    """
    cookies = None
    if os.path.exists(COOKIES_FILE):
        try:
            jar = requests.cookies.RequestsCookieJar()
            with open(COOKIES_FILE) as f:
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) == 7:
                        domain, _, path, _, _, name, value = parts
                        jar.set(name, value, domain=domain, path=path)
            cookies = jar
        except Exception as e:
            logger.warning(f"Could not parse cookies for fallback scrape: {e}")

    try:
        resp = requests.get(url, headers=HTTP_HEADERS, cookies=cookies, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        logger.error(f"Fallback scrape request failed: {e}")
        return [], ""

    media_urls = []
    for pattern in (
        r'<meta[^>]+property="og:video(?::url)?"[^>]+content="([^"]+)"',
        r'<meta[^>]+property="og:image(?::secure_url)?"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"',
        # Some pages emit content before property/name — cover that order too.
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:image(?::secure_url)?"',
    ):
        media_urls += re.findall(pattern, html)

    if not media_urls:
        logger.warning(f"Fallback scrape found no og/twitter media tags for {url} — likely a login wall.")

    # og:description usually holds the post's own caption text; og:title is
    # a weaker fallback (often just "Facebook" or the page name).
    caption_match = re.search(
        r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html
    ) or re.search(
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:description"', html
    )
    raw_caption = caption_match.group(1) if caption_match else ""
    caption = _clean_caption(raw_caption.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"'))

    seen, ordered = set(), []
    for u in media_urls:
        u = u.replace("&amp;", "&")
        if u not in seen:
            seen.add(u)
            ordered.append(u)

    files = []
    for i, media_url in enumerate(ordered[:10]):
        try:
            r = requests.get(media_url, headers=HTTP_HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException:
            continue

        content_type = r.headers.get("Content-Type", "")
        if "video" in content_type:
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

    return files, caption


def download_media(url: str, download_dir: str) -> tuple:
    """Downloads a post and returns (files, original_caption).

    Tries three approaches in order, each catching where the previous
    leaves off:
      1. SaverAPI.NET — the specialized service (if a key is configured),
         which reliably handles Facebook photos, Instagram albums, etc.
      2. yt-dlp — handles the vast majority of videos and some photo posts.
      3. Direct page scraping — a last resort for whatever's left.

    Every tier logs why it failed, so Railway logs will show exactly which
    step a failing link is dying at.
    """
    url = _resolve_redirect(url)

    files, caption = _saverapi_download(url, download_dir)
    if files:
        logger.info(f"[{url}] downloaded via SaverAPI: {len(files)} file(s)")
        return files, caption

    try:
        files, caption = _ytdlp_download(url, download_dir)
        if files:
            logger.info(f"[{url}] downloaded via yt-dlp: {len(files)} file(s)")
    except Exception as e:
        # Broadened from yt_dlp.utils.DownloadError -> Exception: yt-dlp can
        # raise other error types (e.g. ExtractorError) for photo-only posts
        # it doesn't fully support, and those must not skip the fallback tier.
        logger.warning(f"[{url}] yt-dlp failed, falling back to page scrape: {e}")
        files, caption = [], ""

    if not files:
        files, caption = _fallback_scrape(url, download_dir)
        if files:
            logger.info(f"[{url}] downloaded via fallback scrape: {len(files)} file(s)")
        else:
            logger.error(f"[{url}] all three download tiers failed.")

    return files, caption


# ----------------------------------------------------------------------
# SENDING DOWNLOADED FILES BACK
# ----------------------------------------------------------------------
async def send_downloaded_file(update: Update, file_path: str, caption: str = ""):
    """Sends a single downloaded file back as a photo, audio, or video,
    using the original post's caption when one was found."""
    ext = os.path.splitext(file_path)[1].lower()

    with open(file_path, "rb") as f:
        if ext in IMAGE_EXTENSIONS:
            await update.message.reply_photo(photo=f, caption=caption or "✅ Here's your photo!")
        elif ext in AUDIO_EXTENSIONS:
            await update.message.reply_audio(
                audio=f, caption=caption or "✅ Here's your audio!", read_timeout=120, write_timeout=120
            )
        else:
            await update.message.reply_video(
                video=f,
                caption=caption or "✅ Here's your video!",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
            )


async def send_downloaded_files(update: Update, file_paths: list, caption: str = ""):
    """Sends one or many downloaded files. Multiple photos/videos from the
    same post (e.g. a carousel) go out together as an album, and carry the
    original post's caption on the first item when one was found."""
    if len(file_paths) == 1:
        await send_downloaded_file(update, file_paths[0], caption)
        return

    # Telegram albums can only hold photos+videos together (not audio mixed
    # in), and at most 10 items per album — send any audio separately.
    album_paths = [p for p in file_paths if os.path.splitext(p)[1].lower() not in AUDIO_EXTENSIONS]
    audio_paths = [p for p in file_paths if os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS]

    for batch_start in range(0, len(album_paths), 10):
        batch = album_paths[batch_start : batch_start + 10]
        opened_files = []
        media = []
        for idx, path in enumerate(batch):
            f = open(path, "rb")
            opened_files.append(f)
            ext = os.path.splitext(path)[1].lower()
            # Caption must be passed at construction time — InputMediaPhoto /
            # InputMediaVideo are immutable after creation in modern
            # python-telegram-bot versions, so setting `.caption =` later
            # raises "Attribute `caption` of class ... can't be set!".
            item_caption = (caption or "✅ Here's everything from that post!") if idx == 0 else None
            if ext in IMAGE_EXTENSIONS:
                media.append(InputMediaPhoto(media=f, caption=item_caption))
            else:
                media.append(InputMediaVideo(media=f, caption=item_caption))
        if media:
            await update.message.reply_media_group(media=media, read_timeout=120, write_timeout=120)
        for f in opened_files:
            f.close()

    for path in audio_paths:
        await send_downloaded_file(update, path, caption)


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
        "Send me any video, photo, or audio link from YouTube, Facebook, Instagram, "
        "TikTok, and more — I'll download it automatically and send it right back to you.\n\n"
        f"⚠️ Note: Telegram bots can only send files up to {MAX_FILESIZE_MB}MB.",
        reply_markup=MAIN_MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 Need Any Help?\n\n"
        "Need help or facing any issue?\n"
        "👉 Join our Help Bot:\n"
        "https://t.me/KbBotService\n\n"
        "💬 Send your issue and get help.",
        reply_markup=MAIN_MENU,
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏠 Main menu:", reply_markup=MAIN_MENU)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: shows how many users have started the bot."""
    if update.effective_user.id != ADMIN_ID:
        return  # silently ignore non-admins

    users = load_users()
    await update.message.reply_text(f"📊 Total users: {len(users)}")


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if await is_subscribed(context.bot, user_id):
        await query.edit_message_text("✅ Thanks for joining! You can now send links to download videos.")
    else:
        await query.answer("❌ You haven't joined the channel yet!", show_alert=True)


BROADCAST_CAPTION_RE = re.compile(r"^/broadcast(@\w+)?\s*", re.IGNORECASE)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: sends a message to every user who has ever started the bot.

    Usage:
      • Reply to any message with /broadcast           -> forwards that message to everyone
      • /broadcast Your text here                        -> sends that text to everyone
      • Send a photo/video with caption "/broadcast ..."  -> sends that photo/video to everyone
    """
    if update.effective_user.id != ADMIN_ID:
        return  # silently ignore non-admins

    message = update.message
    source_message = message.reply_to_message
    text = None
    override_caption = None

    if not source_message:
        if message.caption and BROADCAST_CAPTION_RE.match(message.caption):
            # A photo/video/document sent directly with "/broadcast ..." as its caption.
            source_message = message
            override_caption = BROADCAST_CAPTION_RE.sub("", message.caption).strip()
        elif context.args:
            text = " ".join(context.args)

    if not source_message and not text:
        await update.message.reply_text(
            "Usage:\n"
            "• Reply to any message with /broadcast to forward it to all users, or\n"
            "• /broadcast Your message here, or\n"
            "• Send a photo/video with \"/broadcast your caption\" as the caption"
        )
        return

    users = load_users()
    sent, failed = 0, 0
    status = await update.message.reply_text(f"📢 Sending to {len(users)} users...")

    for uid in users:
        try:
            if source_message is message:
                # Broadcasting the media message itself — strip the /broadcast
                # command out of the caption before it goes to everyone.
                await source_message.copy(chat_id=uid, caption=override_caption or None)
            elif source_message:
                await source_message.copy(chat_id=uid)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)  # gentle pacing to avoid Telegram rate limits

    await status.edit_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}")


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
# MAIN MESSAGE HANDLER
# ----------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    # Bottom menu button taps
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
            file_paths, caption = await loop.run_in_executor(None, download_media, url, tmp_dir)
            progress_task.cancel()

            if not file_paths:
                await status_msg.edit_text(
                    "❌ Couldn't download this. It might be private, the link may be wrong, "
                    "or the platform is blocking downloads right now."
                )
                return

            # Drop anything over the size limit, but still send what fits.
            fitting_paths = [
                p for p in file_paths if os.path.getsize(p) / (1024 * 1024) <= MAX_FILESIZE_MB
            ]
            skipped = len(file_paths) - len(fitting_paths)

            if not fitting_paths:
                await status_msg.edit_text(
                    f"❌ This file is over the {MAX_FILESIZE_MB}MB limit, so it can't be sent."
                )
                return

            await status_msg.edit_text("📤 Uploading...")
            await send_downloaded_files(update, fitting_paths, caption)
            if skipped:
                await update.message.reply_text(
                    f"⚠️ {skipped} item(s) from this post were skipped — over the {MAX_FILESIZE_MB}MB limit."
                )
            await status_msg.delete()

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
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(
        MessageHandler(filters.CaptionRegex(BROADCAST_CAPTION_RE), broadcast_command)
    )
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
