"""Cloud baza: foydalanuvchilar, balans, NFT, tranzaksiyalar, so'rovlar."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer,
                        Numeric, String, Text, func, select)
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
    j_coin: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal(0))  # J🧧 valyutasi uchun balans
    uc: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Nft(Base):
    __tablename__ = "nfts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    price_uzs: Mapped[int] = mapped_column(BigInteger, default=0)
    price_stars: Mapped[int] = mapped_column(BigInteger, default=0)
    photo_file_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


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


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as s:
        for k, v in DEFAULT_SETTINGS.items():
            if not await s.get(Setting, k):
                s.add(Setting(key=k, value=v))
        await s.commit()


async def get_setting(key: str) -> Decimal:
    async with Session() as s:
        row = await s.get(Setting, key)
        return Decimal(row.value if row else DEFAULT_SETTINGS.get(key, "0"))


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


async def get_or_create_user(tg_id: int, username: str | None) -> User:
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if not u:
            u = User(tg_id=tg_id, username=username)
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
    if field == "j_coin" or field == "j_rate":
        field = "j_coin"
        
    cur = Decimal(getattr(u, field, Decimal(0)))
    if cur + delta < 0:
        return False
        
    if field in ("ton", "j_coin"):
        setattr(u, field, (cur + delta))
    else:
        setattr(u, field, int(cur + delta))
        
    s.add(Transaction(user_id=user_id, kind=kind, currency=currency.upper(),
                      amount=delta, fee=fee, note=note))
    return True
