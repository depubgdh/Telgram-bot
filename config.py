import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Admin: Telegram username (@siz) YOKI raqamli ID. Username bilan admin /start bosganda avtomatik taniladi.
ADMIN_USERNAMES = [x.strip().lstrip("@").lower()
                   for x in os.getenv("ADMIN_USERNAMES", "Jalolkhon_Abdullayev").split(",") if x.strip()]
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
             if x.strip().lstrip("-").isdigit()]

# Majburiy obuna kanali (admin panel orqali ham o'zgartiriladi)
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")


def _normalize_db(url: str) -> str:
    """Neon / Supabase / Render havolalarini asyncpg uchun avtomatik moslaydi."""
    url = (url or "").strip()
    if not url:
        return "sqlite+aiosqlite:///bot.db"   # DIQQAT: Render'da qayta deploydan keyin o'chib ketadi!
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    url = url.replace("sslmode=require", "ssl=require")
    # asyncpg bu parametrni tushunmaydi (Neon qo'shib beradi) — olib tashlaymiz
    base, _, query = url.partition("?")
    params = [p for p in query.split("&") if p and not p.startswith("channel_binding")]
    return base + ("?" + "&".join(params) if params else "")


DATABASE_URL = _normalize_db(os.getenv("DATABASE_URL", ""))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_OWNER = os.getenv("CARD_OWNER", "")
TON_WALLET = os.getenv("TON_WALLET", "")
PORT = int(os.getenv("PORT", "8000"))

# Admin qo'lda o'zgartira oladigan standart qiymatlar (admin panel → Kurs va komissiyalar)
DEFAULT_SETTINGS = {
    "stars_uzs": "230",        # 1 Star = necha so'm (qo'lda)
    "ton_uzs": "35000",        # 1 TON = necha so'm (qo'lda)
    "uc_uzs": "220",           # 1 UC = necha so'm
    "topup_card_fee": "0.6",   # % karta orqali to'ldirish
    "withdraw_card_fee": "1.5",
    "withdraw_phone_fee": "0.3",
    "withdraw_nft_fee": "2.1",
    "min_withdraw_uc": "60",
    "min_withdraw_stars": "50",
    "min_withdraw_phone": "10000",   # telefon raqamga yechish minimumi (so'm)
    "min_withdraw_card": "25000",    # kartaga yechish minimumi (so'm) — admin panelda o'zgaradi
    "j_uzs": "1200",           # 1 J🧧 = necha so'm (admin kunlik o'zgartiradi)
    "topup_min": "5000",       # karta orqali eng kam to'ldirish (so'm)
    "ref_topup_pct": "0.5",    # referal: do'st hisob to'ldirganda % (so'm dan)
    "ref_nft_pct": "1.6",      # referal: do'st NFT xarid qilganda %
}
