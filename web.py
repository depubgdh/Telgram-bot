"""Mini App backend (FastAPI).

Bot bilan BIR XIL bazadan o'qiydi va yozadi — shuning uchun balans, tarix va buyumlar
botda ham, Mini App'da ham doim bir xil. Har bir so'rov Telegram imzosi (initData) bilan tekshiriladi."""
import gzip
import hashlib
import hmac
import html
import io
import json
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl

from aiogram.types import (BufferedInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, LabeledPrice)
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel
from sqlalchemy import func, select

import config
from admins import admin_ids
from bot import (KINDS, WD, bot, create_card_topup, fmt, fmt_ton,
                 is_subscribed, j_cost, nft_card_text, rate_uzs, ton_cost)
from db import (Banner, Nft, Request, Session, Transaction, User, UserItem,
                change_balance, get_setting, get_user, pay_referral)

from nft_fetch import parse_link  # noqa: E402
from uc import uc_kb  # noqa: E402

app = FastAPI()
INDEX = Path(__file__).resolve().parent / "index.html"
STATIC = Path(__file__).resolve().parent / "static"      # logotiplar va rasmlar (J, Stars, Humo/Uzcard, PUBG...)
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
UC_PACKS = [60, 120, 180, 240, 325]
CURS = ("UZS", "J", "STARS", "TON")


# ------------------------------------------------------------------ xavfsizlik
def verify_init_data(init_data: str) -> dict:
    """Telegram Mini App initData imzosini tekshiradi (soxta so'rovlardan himoya)."""
    data = dict(parse_qsl(init_data or "", keep_blank_values=True))
    recv_hash = data.pop("hash", None)
    if not recv_hash:
        raise HTTPException(401, "Mini App'ni Telegram ichidan oching")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, recv_hash):
        raise HTTPException(401, "Imzo noto'g'ri")
    try:
        age = time.time() - int(data.get("auth_date", "0"))
    except ValueError:
        age = 10 ** 9
    if age > 86400:
        raise HTTPException(401, "Sessiya eskirgan. Mini App'ni qayta oching")
    try:
        return json.loads(data["user"])
    except Exception:
        raise HTTPException(401, "Foydalanuvchi ma'lumoti noto'g'ri")


async def auth(x_init_data: str, write: bool = False) -> User:
    """Foydalanuvchini aniqlaydi. write=True (pul bilan bog'liq amal) bo'lsa, qo'shimcha tekshiruvlar."""
    tg = verify_init_data(x_init_data)
    u = await get_user(int(tg["id"]))
    if not u or not u.phone:
        raise HTTPException(403, "Avval botda /start bosib ro'yxatdan o'ting")
    if u.banned:
        raise HTTPException(403, "Hisobingiz bloklangan")
    if u.blocked_until and u.blocked_until > datetime.utcnow():
        raise HTTPException(403, "Xavfsizlik maqsadida vaqtincha bloklangansiz")
    if write:
        if u.frozen:
            raise HTTPException(403, "Hisob muzlatilgan. Botda parol bilan oching")
        if not await is_subscribed(u.tg_id):
            raise HTTPException(403, "Avval botdagi kanalga obuna bo'ling")
    return u


def to_dec(v) -> Decimal:
    try:
        d = Decimal(str(v).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise HTTPException(400, "To'g'ri son kiriting")
    if not d.is_finite() or d <= 0:
        raise HTTPException(400, "Miqdor 0 dan katta bo'lsin")
    if d > Decimal("1000000000000"):
        raise HTTPException(400, "Miqdor juda katta")
    return d


def decide_kb(rid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"ad:ok:{rid}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"ad:no:{rid}")]])


async def tell_admins(text: str, markup=None):
    for a in await admin_ids():
        try:
            await bot.send_message(a, text, reply_markup=markup)
        except Exception:
            pass


async def tell(tg_id: int, text: str):
    try:
        await bot.send_message(tg_id, text)
    except Exception:
        pass


_uname: str | None = None


async def bot_username() -> str:
    global _uname
    if not _uname:
        _uname = (await bot.get_me()).username
    return _uname


async def balances(u: User) -> dict:
    """Foydalanuvchi balansi (muzlatilgan bo'lsa hammasi 0 ko'rinadi — botdagi kabi)."""
    if u.frozen:
        return {"uzs": 0, "j": 0, "stars": 0, "ton": "0", "uc": 0}
    return {"uzs": int(u.uzs), "j": int(u.j), "stars": int(u.stars), "ton": fmt_ton(u.ton), "uc": int(u.uc)}


async def wd_methods() -> dict:
    """Yechish usullari — botdagi bilan bir xil minimum va komissiyalar."""
    return {
        "uc": {"label": "PUBG UC", "cur": "UC", "min": await get_setting("min_withdraw_uc"),
               "fee": Decimal(0), "ask": "PUBG ID raqamingiz"},
        "stars": {"label": "Stars", "cur": "STARS", "min": await get_setting("min_withdraw_stars"),
                  "fee": Decimal(0), "ask": "Stars qabul qiladigan @username"},
        "nft": {"label": "NFT", "cur": "UZS", "min": Decimal(0),
                "fee": await get_setting("withdraw_nft_fee"), "ask": "NFT yuboriladigan @username"},
        "card": {"label": "Kartaga", "cur": "UZS", "min": await get_setting("min_withdraw_card"),
                 "fee": await get_setting("withdraw_card_fee"), "ask": "Karta raqamingiz"},
        "phone": {"label": "Telefonga", "cur": "UZS", "min": await get_setting("min_withdraw_phone"),
                  "fee": await get_setting("withdraw_phone_fee"), "ask": "Telefon raqamingiz"},
    }


# ------------------------------------------------------------------ sahifa
@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ profil va balans
@app.get("/api/me")
async def me(x_init_data: str = Header(...)):
    u = await auth(x_init_data)
    rates = {}
    for k in ("j_uzs", "stars_uzs", "ton_uzs", "uc_uzs"):
        rates[k] = float(await get_setting(k))
    bal = await balances(u)
    total = Decimal(u.uzs) + Decimal(u.j) * Decimal(str(rates["j_uzs"])) \
        + Decimal(u.stars) * Decimal(str(rates["stars_uzs"])) + Decimal(u.ton) * Decimal(str(rates["ton_uzs"]))
    async with Session() as s:
        friends = int(await s.scalar(select(func.count(User.id)).where(User.referrer_id == u.tg_id)) or 0)
    try:
        link = f"https://t.me/{await bot_username()}?start=ref_{u.tg_id}"
    except Exception:
        link = ""
    methods = await wd_methods()
    return {
        "user": {"tg_id": u.tg_id, "username": u.username},
        "frozen": bool(u.frozen),
        "bal": bal,
        "total_uzs": 0 if u.frozen else int(total),
        "rates": rates,
        "topup": {"card": config.CARD_NUMBER, "owner": config.CARD_OWNER,
                  "min": int(await get_setting("topup_min")), "fee": float(await get_setting("topup_card_fee")),
                  "ton_wallet": config.TON_WALLET, "ton_memo": f"U{u.tg_id}", "ton_min": 0.1},
        "support": config.SUPPORT_USERNAME,
        "withdraw": {k: {"label": m["label"], "cur": m["cur"], "min": int(m["min"]),
                         "fee": float(m["fee"]), "ask": m["ask"]} for k, m in methods.items()},
        "uc_packs": [{"qty": q, "price": int(rates["uc_uzs"] * q)} for q in UC_PACKS],
        "friends": {"count": friends, "link": link, "earner": bool(u.earner)},
    }


@app.get("/api/history")
async def history(x_init_data: str = Header(...)):
    u = await auth(x_init_data)
    if u.frozen:
        return []
    async with Session() as s:
        rows = (await s.execute(select(Transaction).where(Transaction.user_id == u.id)
                                .order_by(Transaction.id.desc()).limit(50))).scalars().all()
    out = []
    for t in rows:
        amt = Decimal(t.amount)
        out.append({"t": f"{t.created_at:%d.%m %H:%M}", "label": KINDS.get(t.kind, t.kind), "cur": t.currency,
                    "amount": fmt_ton(abs(amt)) if t.currency == "TON" else fmt(abs(amt)),
                    "pos": amt > 0 and t.kind != "topup_rejected", "cancel": t.kind == "topup_rejected"})
    return out


# ------------------------------------------------------------------ NFT
_photo_cache: dict[int, tuple[bytes, str]] = {}


def media_kind(media: str | None) -> str:
    media = media or "photo"
    return "lottie" if media in ("tgs", "tgsdoc") else "video" if media in ("webm", "webmdoc") else "image"


@app.get("/media/{nft_id}")
@app.get("/photo/{nft_id}")
async def nft_media(nft_id: int):
    """NFT rasmi / animatsiyasi: Telegram'dan olinadi. .tgs bo'lsa Lottie JSON'ga ochib beriladi."""
    hit = _photo_cache.get(nft_id)
    if hit is None:
        async with Session() as s:
            n = await s.get(Nft, nft_id)
        if not n or not n.photo_file_id:
            raise HTTPException(404, "Rasm yo'q")
        try:
            f = await bot.get_file(n.photo_file_id)
            buf = io.BytesIO()
            await bot.download_file(f.file_path, destination=buf)
            data = buf.getvalue()
        except Exception:
            raise HTTPException(404, "Rasm topilmadi")
        kind = media_kind(n.media)
        if kind == "lottie":
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
            ctype = "application/json"
        elif kind == "video":
            ctype = "video/webm"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ctype = "image/png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ctype = "image/webp"
        else:
            ctype = "image/jpeg"
        if len(_photo_cache) > 100:
            _photo_cache.clear()
        hit = _photo_cache[nft_id] = (data, ctype)
    return Response(hit[0], media_type=hit[1], headers={"Cache-Control": "public, max-age=3600"})


_thumb_cache: dict[int, bytes] = {}
_thumb_miss: set[int] = set()


@app.get("/thumb/{nft_id}")
async def nft_thumb(nft_id: int):
    """NFT ro'yxati uchun YENGIL statik rasm (animatsiyasiz) — telefon qotmasligi uchun.
    Animatsiya faqat sotib olish oynasi ochilganda yuklanadi."""
    data = _thumb_cache.get(nft_id)
    if data is None and nft_id not in _thumb_miss:
        async with Session() as s:
            n = await s.get(Nft, nft_id)
        if not n:
            raise HTTPException(404, "NFT yo'q")
        g = parse_link(n.link or "")
        if g:
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as cl:
                    r = await cl.get(f"https://nft.fragment.com/gift/{g[0].lower()}-{g[1]}.webp")
                if r.status_code == 200 and len(r.content) > 100:
                    data = r.content
            except Exception:
                pass
        if data is None and media_kind(n.media) == "image":
            return await nft_media(nft_id)       # oddiy rasm bo'lsa o'zi yengil
        if data is None:
            _thumb_miss.add(nft_id)
        else:
            if len(_thumb_cache) > 150:
                _thumb_cache.clear()
            _thumb_cache[nft_id] = data
    if data is None:
        raise HTTPException(404, "Rasm yo'q")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/nfts")
async def nfts(x_init_data: str = Header(...)):
    await auth(x_init_data)
    jr, tr = await get_setting("j_uzs"), await get_setting("ton_uzs")
    async with Session() as s:
        items = (await s.execute(select(Nft).where(Nft.active, Nft.stock > 0).order_by(Nft.id))).scalars().all()
    return [{"id": n.id, "title": n.title, "photo": bool(n.photo_file_id), "kind": media_kind(n.media), "link": n.link or "",
             "model": n.model, "model_pct": n.model_pct, "backdrop": n.backdrop, "backdrop_pct": n.backdrop_pct,
             "symbol": n.symbol, "symbol_pct": n.symbol_pct, "price_uzs": int(n.price_uzs),
             "price_j": j_cost(n.price_uzs, jr), "price_ton": float(ton_cost(n.price_uzs, tr)), "price_stars": int(n.price_stars), "stock": int(n.stock)}
            for n in items]


class BuyIn(BaseModel):
    nft_id: int
    cur: str


@app.post("/api/nft/buy")
async def nft_buy(body: BuyIn, x_init_data: str = Header(...)):
    u = await auth(x_init_data, write=True)
    cur = body.cur.lower()
    if cur not in ("uzs", "stars", "j", "ton"):
        raise HTTPException(400, "Noto'g'ri valyuta")
    stars_rate, j_rate, ref_pct = await get_setting("stars_uzs"), await get_setting("j_uzs"), await get_setting("ref_nft_pct")
    ton_rate = await get_setting("ton_uzs")
    async with Session() as s:
        n = await s.get(Nft, body.nft_id, with_for_update=True)   # ikki kishi bir vaqtda olmasligi uchun qulflanadi
        if not n or not n.active or n.stock <= 0:
            raise HTTPException(409, "Afsuski, bu NFT allaqachon sotilgan")
        if cur == "uzs":
            price = int(n.price_uzs)
            base = Decimal(price)
        elif cur == "stars":
            price = int(n.price_stars)
            base = Decimal(price) * stars_rate
        elif cur == "ton":
            price = ton_cost(n.price_uzs, ton_rate)
            base = price * ton_rate
        else:
            price = j_cost(n.price_uzs, j_rate)
            base = Decimal(price) * j_rate
        if price <= 0:
            raise HTTPException(400, "Bu usulda sotib olib bo'lmaydi")
        price_txt = fmt_ton(price) if cur == "ton" else fmt(price)
        if not await change_balance(s, u.id, cur.upper(), Decimal(-price), "buy_nft", note=n.title):
            raise HTTPException(400, "Balansda mablag' yetarli emas")
        n.stock -= 1
        if n.stock <= 0:
            n.active = False
        s.add(UserItem(user_id=u.id, nft_id=n.id, title=n.title, card_text=nft_card_text(n, price=False),
                       photo_file_id=n.photo_file_id, media=n.media or "photo", paid=f"{price_txt} {cur.upper()}"))
        ref_info = await pay_referral(s, u, base, ref_pct, "nft")
        await s.commit()
    await tell_admins(f"🎁 NFT sotildi (Mini App): <b>{html.escape(n.title)}</b>\n"
                      f"Xaridor: {u.tg_id} @{u.username or '-'} {u.phone}\n"
                      f"To'lov: {price_txt} {cur.upper()}\nNFT ni foydalanuvchiga yuboring.")
    if ref_info:
        await tell(ref_info[0], f"💰 Do'stlaringizdan biri NFT xarid qildi: sizga +{fmt(ref_info[1])} so'm referal foyda.")
    return {"ok": True, "msg": "Xarid qilindi! Admin NFT ni tez orada yuboradi."}


def strip_tags(t: str) -> list[str]:
    plain = html.unescape(re.sub(r"<[^>]+>", "", t or ""))
    return [ln.strip() for ln in plain.splitlines() if ln.strip()]


@app.get("/api/items")
async def items(x_init_data: str = Header(...)):
    u = await auth(x_init_data)
    if u.frozen:
        return []
    async with Session() as s:
        rows = (await s.execute(select(UserItem).where(UserItem.user_id == u.id)
                                .order_by(UserItem.id.desc()).limit(50))).scalars().all()
    return [{"id": it.id, "title": it.title, "nft_id": it.nft_id, "photo": bool(it.photo_file_id),
             "kind": media_kind(it.media), "lines": strip_tags(it.card_text)[1:], "paid": it.paid, "date": f"{it.created_at:%d.%m.%Y}"}
            for it in rows]


# ------------------------------------------------------------------ almashtirish
class ConvIn(BaseModel):
    a: str
    b: str
    amount: str


@app.post("/api/convert")
async def convert(body: ConvIn, x_init_data: str = Header(...)):
    u = await auth(x_init_data, write=True)
    a, b = body.a.upper(), body.b.upper()
    if a not in CURS or b not in CURS or a == b:
        raise HTTPException(400, "Noto'g'ri yo'nalish")
    amt = to_dec(body.amount)
    amt = amt.quantize(Decimal("0.000000001")) if a == "TON" else Decimal(int(amt))
    if amt <= 0:
        raise HTTPException(400, "Miqdor juda kam")
    ra, rb = await rate_uzs(a), await rate_uzs(b)
    if ra <= 0 or rb <= 0:
        raise HTTPException(400, "Bu yo'nalish uchun kurs belgilanmagan")
    out = amt * ra / rb
    out = out.quantize(Decimal("0.000000001")) if b == "TON" else Decimal(int(out))
    if out <= 0:
        raise HTTPException(400, "Bu miqdor almashtirish uchun juda kam. Kattaroq miqdor kiriting")
    async with Session() as s:
        if not await change_balance(s, u.id, a, -amt, "convert", note=f"{a}->{b}"):
            raise HTTPException(400, "Balansda mablag' yetarli emas")
        await change_balance(s, u.id, b, out, "convert", note=f"{a}->{b}")
        await s.commit()
    sa = fmt_ton(amt) if a == "TON" else fmt(amt)
    sb = fmt_ton(out) if b == "TON" else fmt(out)
    return {"ok": True, "msg": f"Almashtirildi: {sa} {a} → {sb} {b}"}


# ------------------------------------------------------------------ yechib olish
class WdIn(BaseModel):
    method: str
    amount: str
    details: str


@app.post("/api/withdraw")
async def withdraw(body: WdIn, x_init_data: str = Header(...)):
    u = await auth(x_init_data, write=True)
    m = (await wd_methods()).get(body.method)
    if not m:
        raise HTTPException(400, "Noto'g'ri usul")
    amt = to_dec(body.amount)
    if amt != amt.to_integral_value():
        raise HTTPException(400, "Butun son kiriting")
    if amt < m["min"]:
        raise HTTPException(400, f"Minimal miqdor: {fmt(m['min'])} {m['cur']}")
    details = (body.details or "").strip()
    if not details or len(details) > 200:
        raise HTTPException(400, "Rekvizitni to'g'ri kiriting")
    cur = m["cur"]
    fee = (amt * m["fee"] / 100).quantize(Decimal(1))
    async with Session() as s:
        # mablag' so'rov paytida ushlab qo'yiladi; rad etilsa qaytariladi (botdagi kabi)
        if not await change_balance(s, u.id, cur, -(amt + fee), "withdraw", fee=fee, note=f"hold:{body.method}"):
            raise HTTPException(400, f"Balans yetarli emas. Kerak: {fmt(amt + fee)} {cur}")
        r = Request(user_id=u.id, kind="withdraw", method=body.method, currency=cur,
                    amount=amt, fee=fee, details=details)
        s.add(r)
        await s.commit()
    await tell_admins(f"💸 <b>Yechish #{r.id}</b> (Mini App)\n👤 {u.tg_id} @{u.username or '-'} {u.phone}\n"
                      f"Tur: {WD[body.method]}\nMiqdor: {fmt(amt)} {cur} (komissiya {fmt(fee)})\n"
                      f"Rekvizit: {html.escape(details)}", decide_kb(r.id))
    return {"ok": True, "msg": f"So'rov #{r.id} adminga yuborildi. Tasdiqlangach xabar keladi."}


# ------------------------------------------------------------------ PUBG UC
class UcIn(BaseModel):
    qty: int
    pid: str


@app.post("/api/uc")
async def uc_order(body: UcIn, x_init_data: str = Header(...)):
    u = await auth(x_init_data, write=True)
    if body.qty not in UC_PACKS:
        raise HTTPException(400, "Noto'g'ri paket")
    pid = (body.pid or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{6,12}", pid):
        raise HTTPException(400, "PUBG ID 6–12 ta raqamdan iborat bo'lsin")
    cost = int(await get_setting("uc_uzs") * body.qty)
    async with Session() as s:
        if not await change_balance(s, u.id, "UZS", Decimal(-cost), "uc_order", note=f"{body.qty} UC, ID {pid}"):
            raise HTTPException(400, "So'm balansingiz yetarli emas")
        r = Request(user_id=u.id, kind="uc_order", method="uc", currency="UZS", amount=Decimal(cost),
                    fee=Decimal(0), details=f"PUBG ID: {pid}; UC: {body.qty}")
        s.add(r)
        await s.commit()
    await tell_admins(f"🎮 <b>UC buyurtma #{r.id}</b> (Mini App)\n👤 {u.tg_id} @{u.username or '-'} {u.phone}\n"
                      f"UC: <b>{body.qty}</b>\nPUBG ID: <code>{pid}</code>\nTo'langan: {fmt(cost)} so'm",
                      uc_kb(r.id))
    return {"ok": True, "msg": f"Buyurtma #{r.id} qabul qilindi. Admin tasdiqlagach xabar keladi."}


# ------------------------------------------------------------------ hisobni to'ldirish (chek)
@app.post("/api/receipt")
async def receipt(x_init_data: str = Header(...), amount: int = Form(...), file: UploadFile = File(...)):
    u = await auth(x_init_data, write=True)
    if amount <= 0 or amount > 1_000_000_000:
        raise HTTPException(400, "Summa noto'g'ri")
    min_amt = int(await get_setting("topup_min"))
    if amount < min_amt:
        raise HTTPException(400, f"Minimal summa {fmt(min_amt)} so'm")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Fayl bo'sh")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Fayl 10 MB dan katta")
    # faylni bot orqali foydalanuvchining o'ziga yuborib file_id olamiz
    msg = await bot.send_document(u.tg_id, BufferedInputFile(content, filename=file.filename or "check.jpg"),
                                  caption="📎 Chek qabul qilindi, admin tekshiradi.")
    await create_card_topup(u.tg_id, Decimal(amount), msg.document.file_id)
    return {"ok": True, "msg": "Chek adminga yuborildi. Tasdiqlangach balansingiz to'ladi."}


# ------------------------------------------------------------------ TON / Stars bilan to'ldirish
class TonIn(BaseModel):
    amount: str


@app.post("/api/topup/ton")
async def topup_ton(body: TonIn, x_init_data: str = Header(...)):
    """TON yuborilganini bildiradi: admin tasdiqlasa hisobga tushadi, rad etsa tarixda 'bekor qilindi' bo'ladi."""
    u = await auth(x_init_data, write=True)
    if not config.TON_WALLET:
        raise HTTPException(400, "TON manzil hali sozlanmagan")
    amt = to_dec(body.amount).quantize(Decimal("0.0001"))
    if amt < Decimal("0.1"):
        raise HTTPException(400, "Minimal miqdor: 0.1 TON")
    async with Session() as s:
        pend = await s.scalar(select(func.count(Request.id)).where(
            Request.user_id == u.id, Request.kind == "topup", Request.method == "ton", Request.status == "pending"))
        if (pend or 0) >= 3:
            raise HTTPException(429, "Sizda kutilayotgan so'rovlar ko'p. Admin tasdiqlashini kuting")
        r = Request(user_id=u.id, kind="topup", method="ton", currency="TON", amount=amt, fee=Decimal(0),
                    details=f"U{u.tg_id}")
        s.add(r)
        await s.commit()
    await tell_admins(f"🪙 <b>TON to'ldirish #{r.id}</b> (Mini App)\n👤 {u.tg_id} @{u.username or '-'} {u.phone}\n"
                      f"Miqdor: <b>{fmt_ton(amt)} TON</b>\nIzoh (MEMO): <code>U{u.tg_id}</code>\n"
                      f"Hamyoningizni tekshiring: <code>{config.TON_WALLET}</code>", decide_kb(r.id))
    return {"ok": True, "id": r.id, "msg": "So'rov adminga yuborildi. Tasdiqlanishini kuting."}


@app.get("/api/req/{rid}")
async def req_status(rid: int, x_init_data: str = Header(...)):
    u = await auth(x_init_data)
    async with Session() as s:
        r = await s.get(Request, rid)
    if not r or r.user_id != u.id:
        raise HTTPException(404, "So'rov topilmadi")
    return {"status": r.status}


class StarsIn(BaseModel):
    amount: int


@app.post("/api/topup/stars")
async def topup_stars(body: StarsIn, x_init_data: str = Header(...)):
    """Telegram Stars hisob-fakturasi havolasini yaratadi; to'lov botdagi 'successful_payment' orqali hisobga tushadi."""
    u = await auth(x_init_data, write=True)
    if body.amount < 1 or body.amount > 100000:
        raise HTTPException(400, "Miqdor 1 dan 100000 gacha bo'lsin")
    url = await bot.create_invoice_link(title="Hisobni to'ldirish", description=f"{body.amount} Stars",
                                        payload=f"topup_stars:{body.amount}", provider_token="", currency="XTR",
                                        prices=[LabeledPrice(label="Stars", amount=body.amount)])
    return {"url": url}


# ------------------------------------------------------------------ reklama bannerlari
@app.get("/api/banners")
async def banners(x_init_data: str = Header(...)):
    await auth(x_init_data)
    async with Session() as s:
        rows = (await s.execute(select(Banner).order_by(Banner.id))).scalars().all()
    return [{"id": b.id, "url": b.url} for b in rows]


_banner_cache: dict[int, bytes] = {}


@app.get("/banner/{bid}")
async def banner_img(bid: int):
    data = _banner_cache.get(bid)
    if data is None:
        async with Session() as s:
            b = await s.get(Banner, bid)
        if not b:
            raise HTTPException(404, "Reklama yo'q")
        try:
            f = await bot.get_file(b.file_id)
            buf = io.BytesIO()
            await bot.download_file(f.file_path, destination=buf)
            data = buf.getvalue()
        except Exception:
            raise HTTPException(404, "Rasm topilmadi")
        if len(_banner_cache) > 50:
            _banner_cache.clear()
        _banner_cache[bid] = data
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
