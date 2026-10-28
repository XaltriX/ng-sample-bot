"""
Configuration — fill in your values here or use environment variables.
"""
import os

# ── Telegram API credentials ──────────────────────────────────────────────────
API_ID: int = int(os.getenv("API_ID", "24955235"))
API_HASH: str = os.getenv("API_HASH", "f317b3f7bbe390346d8b46868cff0de8")
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "7205909672:AAF8quuEsZ35aIzf_DOCPqMZ9RCUwJyPZl8")

# ── Admin access ──────────────────────────────────────────────────────────────
_raw = os.getenv("ADMIN_IDS", "5706788169")
ADMIN_IDS: list[int] = (
    [int(x.strip()) for x in _raw.split(",") if x.strip()]
    if _raw
    else []
)

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb+srv://villainravangaming:mikey_kun_781_@cluster0.fbgs1zz.mongodb.net/?retryWrites=true&w=majority")
MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "post_bot")

# ── File-store bot (2nd bot: users get the sample here) ───────────────────────
FILESTORE_BOT_TOKEN: str = os.getenv("FILESTORE_BOT_TOKEN", "PUT_FILESTORE_BOT_TOKEN_HERE")
# Private channel where the file-store bot is ADMIN. Samples are stored here.
DB_CHANNEL_ID: int = int(os.getenv("DB_CHANNEL_ID", "0"))   # e.g. -1001234567890

# ── Sample settings ───────────────────────────────────────────────────────────
SAMPLE_TTL_SECONDS: int = int(os.getenv("SAMPLE_TTL_SECONDS", "300"))  # auto-delete after 5 min
SAMPLE_WORKERS: int = int(os.getenv("SAMPLE_WORKERS", "2"))            # parallel ffmpeg jobs
WATERMARK_TEXT: str = os.getenv("WATERMARK_TEXT", "@linkz_wallah")
