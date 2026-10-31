"""Cloud baza: foydalanuvchilar, balans, NFT, tranzaksiyalar, so'rovlar."""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer,
                        Numeric, String, Text, func, select, text)
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import DATABASE_URL, DEFAULT_SETTINGS

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terms_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    uzs: Mapped[int] = mapped_column(BigInteger, default=0)
    stars: Mapped[int] = mapped_column(BigInteger, default=0)
    ton: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal(0))
    uc: Mapped[int] = mapped_column(BigInteger, default=0)
    j: Mapped[int] = mapped_column(BigInteger, default=0)                      # J🧧 valyutasi
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # taklif qilgan odamning tg_id si
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)               # hisob muzlatilgan
    freeze_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    freeze_salt: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    can_sell: Mapped[bool] = mapped_column(Boolean, default=False)             # "NFT Sotish" bo'limi ochilgan
    earner: Mapped[bool] = mapped_column(Boolean, default=False)               # "Pul ishlash" faol
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Nft(Base):
    __tablename__ = "nfts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    price_uzs: Mapped[int] = mapped_column(BigInteger, default=0)
    price_stars: Mapped[int] = mapped_column(BigInteger, default=0)
    photo_file_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_pct: Mapped[str | None] = mapped_column(String(16), nullable=True)
    backdrop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    backdrop_pct: Mapped[str | None] = mapped_column(String(16), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(100), nullable=True)
    symbol_pct: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stock: Mapped[int] = mapped_column(BigInteger, default=1)                  # nechta nusxa qoldi


class Transaction(Base):
    """Foydalanuvchining barcha amallari tarixi."""
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # topup, withdraw, buy_nft, buy_stars, buy_uc, convert, refund
    currency: Mapped[str] = mapped_column(String(8))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 9))  # +kirim / -chiqim
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal(0))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Request(Base):
    """Admin tasdiqlashi kerak bo'lgan so'rovlar (to'ldirish / yechish)."""
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))      # topup | withdraw
    method: Mapped[str] = mapped_column(String(16))    # card | phone | stars | uc | nft | ton
    currency: Mapped[str] = mapped_column(String(8))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal(0))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # karta raqami, telefon, UC ID...
    receipt_file_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class JoinRequest(Base):
    """Guruhga so'rov tashlaganlar — obuna bo'lgan deb hisoblanadi."""
    __tablename__ = "join_requests"
    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class UserItem(Base):
    """Foydalanuvchi xarid qilgan buyumlar (Mening buyumlarim)."""
    __tablename__ = "user_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    nft_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    card_text: Mapped[str] = mapped_column(Text, default="")
    photo_file_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    paid: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Application(Base):
    """Anketa-arizalar: 'earn' (Pul ishlash) va 'sell' (NFT Sotish). Admin tasdiqlaydi."""
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    data: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Bonus(Base):
    """Tugmali (konkurs) bonus: har foydalanuvchi faqat bir marta ola oladi."""
    __tablename__ = "bonuses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BonusClaim(Base):
    __tablename__ = "bonus_claims"
    bonus_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


# Mavjud (eski) jadvallarga yangi ustunlarni avtomatik qo'shish: (jadval, ustun, postgres turi, sqlite turi)
_MIGRATIONS = [
    ("users", "j", "BIGINT NOT NULL DEFAULT 0", "BIGINT NOT NULL DEFAULT 0"),
    ("users", "referrer_id", "BIGINT", "BIGINT"),
    ("users", "frozen", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
    ("users", "freeze_hash", "VARCHAR(128)", "VARCHAR(128)"),
    ("users", "freeze_salt", "VARCHAR(32)", "VARCHAR(32)"),
    ("users", "blocked_until", "TIMESTAMP", "DATETIME"),
    ("users", "can_sell", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
    ("users", "earner", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
    ("nfts", "model", "VARCHAR(100)", "VARCHAR(100)"),
    ("nfts", "model_pct", "VARCHAR(16)", "VARCHAR(16)"),
    ("nfts", "backdrop", "VARCHAR(100)", "VARCHAR(100)"),
    ("nfts", "backdrop_pct", "VARCHAR(16)", "VARCHAR(16)"),
    ("nfts", "symbol", "VARCHAR(100)", "VARCHAR(100)"),
    ("nfts", "symbol_pct", "VARCHAR(16)", "VARCHAR(16)"),
    ("nfts", "stock", "BIGINT NOT NULL DEFAULT 1", "BIGINT NOT NULL DEFAULT 1"),
]


async def migrate():
    pg = engine.dialect.name == "postgresql"
    for table, col, pg_t, lite_t in _MIGRATIONS:
        ddl = f"ALTER TABLE {table} ADD COLUMN {'IF NOT EXISTS ' if pg else ''}{col} {pg_t if pg else lite_t}"
        try:
            async with engine.begin() as conn:
                await conn.execute(text(ddl))
        except Exception as e:  # ustun allaqachon bor (sqlite) — normal holat
            logging.getLogger(__name__).debug("migrate skip %s.%s: %s", table, col, e)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_referrer ON users (referrer_id)"))
    except Exception as e:
        logging.getLogger(__name__).warning("index create failed: %s", e)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate()
    async with Session() as s:
        for k, v in DEFAULT_SETTINGS.items():
            if not await s.get(Setting, k):
                s.add(Setting(key=k, value=v))
        await s.commit()


async def get_setting(key: str) -> Decimal:
    async with Session() as s:
        row = await s.get(Setting, key)
        return Decimal(row.value if row else DEFAULT_SETTINGS[key])


async def set_setting(key: str, value: str):
    async with Session() as s:
        row = await s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        await s.commit()


async def get_str_setting(key: str, default: str = "") -> str:
    async with Session() as s:
        row = await s.get(Setting, key)
        return row.value if row and row.value else default


async def get_user(tg_id: int) -> User | None:
    async with Session() as s:
        return (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def get_or_create_user(tg_id: int, username: str | None, referrer: int | None = None) -> User:
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if not u:
            if referrer == tg_id:
                referrer = None
            if referrer is not None:  # taklif qilgan odam haqiqatan bor bo'lsa
                exists = (await s.execute(select(User.id).where(User.tg_id == referrer))).first()
                referrer = referrer if exists else None
            u = User(tg_id=tg_id, username=username, referrer_id=referrer)
            s.add(u)
            await s.commit()
        elif username and u.username != username:
            u.username = username
            await s.commit()
        return u


async def change_balance(s: AsyncSession, user_id: int, currency: str, delta: Decimal,
                         kind: str, fee: Decimal = Decimal(0), note: str | None = None) -> bool:
    """Balansni o'zgartiradi va tarixga yozadi. Yetarli mablag' bo'lmasa False."""
    u = await s.get(User, user_id, with_for_update=True)
    field = currency.lower()
    cur = Decimal(getattr(u, field))
    if cur + delta < 0:
        return False
    setattr(u, field, (cur + delta) if field == "ton" else int(cur + delta))
    s.add(Transaction(user_id=user_id, kind=kind, currency=currency,
                      amount=delta, fee=fee, note=note))
    return True


async def pay_referral(s: AsyncSession, user: User, base_uzs: Decimal, pct: Decimal, kind: str):
    """Do'sti foyda keltirsa, uni taklif qilgan (Pul ishlash faol) foydalanuvchiga ulush beradi.
    Qaytaradi: (referrer tg_id, so'm) yoki None."""
    if not user.referrer_id or pct <= 0:
        return None
    ref = (await s.execute(select(User).where(User.tg_id == user.referrer_id))).scalar_one_or_none()
    if not ref or not ref.earner or ref.banned:
        return None
    amt = int(Decimal(base_uzs) * Decimal(pct) / 100)
    if amt <= 0:
        return None
    await change_balance(s, ref.id, "UZS", Decimal(amt), "ref", note=kind)
    return ref.tg_id, amt
