import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_OWNER = os.getenv("CARD_OWNER", "")
TON_WALLET = os.getenv("TON_WALLET", "")
PORT = int(os.getenv("PORT", "8000"))

# Admin qo'lda o'zgartira oladigan standart qiymatlar (/set buyrug'i bilan)
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
}
