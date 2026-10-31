"""Telegram bot: ro'yxatdan o'tish, menyular, balans, to'ldirish, yechish, admin."""
import html
import logging
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, ChatJoinRequest, ContentType, ErrorEvent,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, LabeledPrice, Message,
                           PreCheckoutQuery, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove, WebAppInfo)
from sqlalchemy import select

import config
from db import (JoinRequest, Nft, Request, Session, Transaction, User,
                 UserItem, change_balance, get_or_create_user, get_setting,
                 get_str_setting, get_user, pay_referral, set_setting)
from admins import admin_ids, check_admin, is_admin

log = logging.getLogger(__name__)
bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


@dp.errors()
async def on_error(event: ErrorEvent):
    """Kutilmagan xato bo'lsa bot jim qolmasin: foydalanuvchiga xabar beradi, xatoni logga yozadi."""
    exc, upd = event.exception, event.update
    cq = upd.callback_query
    if "message is not modified" in str(exc).lower():   # bir xil matnni qayta tahrirlash — zararsiz
        if cq:
            try:
                await cq.answer()
            except Exception:
                pass
        return True
    log.exception("Handler xatosi", exc_info=exc)
    try:
        if cq:
            await cq.answer("⚠️ Xatolik yuz berdi, qayta urinib ko'ring", show_alert=True)
        elif upd.message:
            await upd.message.answer("⚠️ Xatolik yuz berdi, qayta urinib ko'ring.")
    except Exception:
        pass
    return True

TERMS = (
    "📜 <b>Foydalanish shartlari</b>\n\n"
    "1. Botdagi barcha to'lovlar admin nazoratida amalga oshiriladi.\n"
    "2. Soxta chek yuborgan foydalanuvchi bloklanadi.\n"
    "3. Yechib olish faqat admin tasdiqlagandan so'ng bajariladi.\n"
    "4. Komissiyalar har bir amalda oldindan ko'rsatiladi."
)


class S(StatesGroup):
    topup_amount = State()
    topup_receipt = State()
    withdraw_amount = State()
    withdraw_details = State()
    convert_amount = State()
    buy_amount = State()


def fmt(n) -> str:
    return f"{Decimal(n):,.0f}".replace(",", " ")


# ---------------------------------------------------------------- obuna
async def is_subscribed(tg_id: int) -> bool:
    ch = await get_str_setting("channel_id", config.CHANNEL_ID)
    if not ch or ch == "-":
        return True
    try:
        m = await bot.get_chat_member(ch, tg_id)
        if m.status in ("member", "administrator", "creator"):
            return True
    except Exception as e:  # kanal topilmasa / bot admin emas
        log.warning("subscription check failed: %s", e)
    # join request tashlagan ham obuna bo'lgan deb hisoblanadi
    async with Session() as s:
        row = (await s.execute(select(JoinRequest).where(JoinRequest.tg_id == tg_id))).first()
    return row is not None


@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    async with Session() as s:
        if not await s.get(JoinRequest, (req.from_user.id, req.chat.id)):
            s.add(JoinRequest(tg_id=req.from_user.id, chat_id=req.chat.id))
            await s.commit()


# ---------------------------------------------------------------- menyu
def main_kb(adm: bool = False, can_sell: bool = False) -> InlineKeyboardMarkup:
    def b(t: str, d: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=t, callback_data=d)

    rows = [
        [b("🎁 NFT / Sovg'alar", "nft:list")],
        [b("🛒 Sotib olish", "m:buy"), b("📦 Mening buyumlarim", "m:items")],
        [b("🔄 Almashtirish", "m:convert"), b("💳 Hisobni to'ldirish", "m:topup")],
        [b("💸 Yechib olish", "m:withdraw"), b("🎮 PUBG UC xaridi", "m:uc")],
        [b("✔️ Shartnoma tuzish 📝", "m:contract")],
        [b("👥 Do'stlarim", "m:friends"), b("📜 Tarix", "m:history")],
        [b("💼 Mening hisoblarim", "m:accounts")],
    ]
    if can_sell:
        rows.append([b("🏷️ NFT Sotish", "sell:menu")])
    if adm:
        rows.append([b("👑 Admin panel", "adm:main")])
    if config.WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="📱 Mini App", web_app=WebAppInfo(url=config.WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fmt_ton(n) -> str:
    s = f"{Decimal(n):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def j_cost(price_uzs: int, rate: Decimal) -> int:
    """NFT narxi J🧧 da (so'm narxi / kurs, yuqoriga yaxlitlanadi)."""
    if rate <= 0 or price_uzs <= 0:
        return 0
    return int((Decimal(price_uzs) / rate).to_integral_value(rounding=ROUND_CEILING))


def nft_card_text(n: Nft, jc: int | None = None, price: bool = True) -> str:
    """Fragment uslubidagi NFT kartochkasi: Model / Orqa fon / Belgi va ularning foizlari."""
    lines = [f"🎁 <b>{html.escape(n.title)}</b>", ""]
    for label, name, pct in (("Model", n.model, n.model_pct),
                             ("Orqa fon", n.backdrop, n.backdrop_pct),
                             ("Belgi", n.symbol, n.symbol_pct)):
        if name:
            line = f"▫️ {label}: <b>{html.escape(name)}</b>"
            if pct:
                line += f"  <i>{html.escape(pct)}</i>"
            lines.append(line)
    if price:
        lines.append("")
        parts = []
        if n.price_uzs > 0:
            parts.append(f"{fmt(n.price_uzs)} so'm")
        if jc:
            parts.append(f"{fmt(jc)} J🧧")
        if n.price_stars > 0:
            parts.append(f"{n.price_stars} ⭐")
        lines.append("💰 " + "  |  ".join(parts))
        if n.stock is not None:
            lines.append(f"📦 Qoldi: {n.stock} dona")
    return "\n".join(lines)


async def header(u: User) -> str:
    return f"⭐️ Stars: {fmt(u.stars)}\nJ🧧: {fmt(u.j)}\n🇺🇿 So'm: {fmt(u.uzs)}"


async def edit_or_send(c: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    """Xabarni tahrirlaydi; rasmli xabar bo'lsa (tahrirlab bo'lmaydi) o'chirib, yangisini yuboradi."""
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except Exception:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer(text, reply_markup=markup)


async def show_main(target: Message | CallbackQuery, tg_id: int):
    u = await get_user(tg_id)
    adm = await check_admin(u.tg_id, u.username)
    if u.frozen:
        text = ("🧊 <b>Hisobingiz muzlatilgan</b>\n\n⭐️ Stars: 0\nJ🧧: 0\n🇺🇿 So'm: 0\n\n"
                "Ochish uchun parolni kiriting.")
        rows = [[InlineKeyboardButton(text="🔓 Hisobni ochish", callback_data="fz:open")],
                [InlineKeyboardButton(text="🔄 Parolni tiklash", callback_data="fz:reset")]]
        if adm:
            rows.append([InlineKeyboardButton(text="👑 Admin panel", callback_data="adm:main")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
    else:
        text = f"{await header(u)}\n\nAsosiy menyu:"
        kb = main_kb(adm, u.can_sell)
    msg = target.message if isinstance(target, CallbackQuery) else target
    if isinstance(target, CallbackQuery):
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await msg.answer(text, reply_markup=kb)


def back_kb(to="m:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=to)]])


# ---------------------------------------------------------------- /start oqimi
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    ref = None
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_") and parts[1][4:].isdigit():
        ref = int(parts[1][4:])
    u = await get_or_create_user(m.from_user.id, m.from_user.username, ref)
    await is_admin(m.from_user)  # admin bo'lsa, ID si eslab qolinadi
    if u.banned:
        return await m.answer("⛔ Hisobingiz bloklangan.")
    if u.blocked_until and u.blocked_until > datetime.utcnow():
        mins = int((u.blocked_until - datetime.utcnow()).total_seconds() // 60) + 1
        return await m.answer(f"⛔ Xavfsizlik maqsadida {mins} daqiqaga bloklangansiz.")
    if not await is_subscribed(m.from_user.id):
        link = await get_str_setting("channel_link", config.CHANNEL_LINK)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Kanalga o'tish", url=link)],
            [InlineKeyboardButton(text="✅ Tekshirish", callback_data="chk:sub")]])
        return await m.answer("Botdan foydalanish uchun kripto kanalga obuna bo'ling:", reply_markup=kb)
    await after_sub(m, u)


async def after_sub(m: Message, u: User):
    if not u.terms_accepted:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Roziman", callback_data="terms:ok")]])
        return await m.answer(TERMS, reply_markup=kb)
    if not u.phone:
        return await ask_phone(m)
    await show_main(m, u.tg_id)


async def ask_phone(m: Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📞 Raqamni yuborish", request_contact=True)]],
                             resize_keyboard=True, one_time_keyboard=True)
    await m.answer("Telefon raqamingizni yuboring (pastdagi tugma orqali):", reply_markup=kb)


@router.callback_query(F.data == "chk:sub")
async def chk_sub(c: CallbackQuery):
    if not await is_subscribed(c.from_user.id):
        return await c.answer("Hali obuna bo'lmagansiz ❌", show_alert=True)
    await c.message.delete()
    u = await get_user(c.from_user.id)
    await after_sub(c.message, u)


@router.callback_query(F.data == "terms:ok")
async def terms_ok(c: CallbackQuery):
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == c.from_user.id))).scalar_one()
        u.terms_accepted = True
        await s.commit()
    await c.message.delete()
    await ask_phone(c.message)


@router.message(F.contact)
async def got_contact(m: Message):
    if m.contact.user_id != m.from_user.id:
        return await m.answer("Iltimos, o'zingizning raqamingizni yuboring.")
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == m.from_user.id))).scalar_one()
        u.phone = m.contact.phone_number
        await s.commit()
    await m.answer("✅ Ro'yxatdan o'tdingiz!", reply_markup=ReplyKeyboardRemove())
    await show_main(m, m.from_user.id)


async def guard(c: CallbackQuery) -> User | None:
    """Har bir amaldan oldin: ro'yxatdan o'tgan, bloklanmagan, obuna bo'lgan."""
    u = await get_user(c.from_user.id)
    if not u or not u.phone or u.banned or not await is_subscribed(c.from_user.id):
        await c.answer("Avval /start bosing", show_alert=True)
        return None
    now = datetime.utcnow()
    if u.blocked_until and u.blocked_until > now:
        mins = int((u.blocked_until - now).total_seconds() // 60) + 1
        await c.answer(f"⛔ Xavfsizlik maqsadida {mins} daqiqaga bloklangansiz.", show_alert=True)
        return None
    if u.frozen and not (c.data or "").startswith(("fz:", "m:main")):
        await c.answer("🧊 Hisobingiz muzlatilgan. Asosiy menyudan parol bilan oching.", show_alert=True)
        return None
    return u


@router.callback_query(F.data == "m:main")
async def m_main(c: CallbackQuery, state: FSMContext):
    await state.clear()
    if await guard(c):
        await show_main(c, c.from_user.id)


# ---------------------------------------------------------------- NFT
@router.callback_query(F.data == "nft:list")
async def nft_list(c: CallbackQuery):
    if not await guard(c):
        return
    async with Session() as s:
        items = (await s.execute(select(Nft).where(Nft.active, Nft.stock > 0).order_by(Nft.id))).scalars().all()
    if not items:
        return await edit_or_send(c, "Hozircha NFT yo'q.", back_kb())
    rows = [[InlineKeyboardButton(text=f"{n.title} — {fmt(n.price_uzs)} so'm",
                                  callback_data=f"nft:v:{n.id}")] for n in items]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="m:main")])
    await edit_or_send(c, "🎁 <b>NFT / Sovg'alar</b>\nKartochkani ko'rish uchun tanlang:",
                       InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("nft:v:"))
async def nft_view(c: CallbackQuery):
    if not await guard(c):
        return
    nid = int(c.data.split(":")[2])
    async with Session() as s:
        n = await s.get(Nft, nid)
    if not n or not n.active or n.stock <= 0:
        return await c.answer("Bu NFT sotilgan yoki mavjud emas", show_alert=True)
    jc = j_cost(n.price_uzs, await get_setting("j_uzs"))
    rows = []
    if n.price_uzs > 0:
        rows.append([InlineKeyboardButton(text=f"{fmt(n.price_uzs)} so'm bilan", callback_data=f"nft:b:uzs:{nid}")])
    if jc > 0:
        rows.append([InlineKeyboardButton(text=f"{fmt(jc)} J🧧 bilan", callback_data=f"nft:b:j:{nid}")])
    if n.price_stars > 0:
        rows.append([InlineKeyboardButton(text=f"{n.price_stars} ⭐ bilan", callback_data=f"nft:b:stars:{nid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="nft:list")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    cap = nft_card_text(n, jc)
    if n.photo_file_id:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer_photo(n.photo_file_id, caption=cap, reply_markup=kb)
    else:
        await edit_or_send(c, cap, kb)


@router.callback_query(F.data.startswith("nft:b:"))
async def nft_buy(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    _, _, cur, nid = c.data.split(":")
    if cur not in ("uzs", "stars", "j"):
        return await c.answer()
    stars_rate = await get_setting("stars_uzs")
    j_rate = await get_setting("j_uzs")
    ref_pct = await get_setting("ref_nft_pct")
    ref_info = None
    async with Session() as s:
        n = await s.get(Nft, int(nid), with_for_update=True)  # bir vaqtda ikki kishi olmasligi uchun qulflanadi
        if not n or not n.active or n.stock <= 0:
            return await c.answer("Afsuski, bu NFT allaqachon sotilgan ❌", show_alert=True)
        if cur == "uzs":
            price = n.price_uzs
            base = Decimal(price)
        elif cur == "stars":
            price = n.price_stars
            base = Decimal(price) * stars_rate
        else:
            price = j_cost(n.price_uzs, j_rate)
            base = Decimal(price) * j_rate
        if price <= 0:
            return await c.answer("Bu usulda sotib olib bo'lmaydi", show_alert=True)
        if not await change_balance(s, u.id, cur.upper(), Decimal(-price), "buy_nft", note=n.title):
            return await c.answer("Balansda mablag' yetarli emas ❌", show_alert=True)
        n.stock -= 1
        if n.stock <= 0:
            n.active = False
        s.add(UserItem(user_id=u.id, nft_id=n.id, title=n.title, card_text=nft_card_text(n, price=False),
                       photo_file_id=n.photo_file_id, paid=f"{fmt(price)} {cur.upper()}"))
        ref_info = await pay_referral(s, u, base, ref_pct, "nft")
        await s.commit()
    await c.answer("✅ Sotib olindi!", show_alert=True)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    for a in await admin_ids():
        try:
            await bot.send_message(a, f"🎁 NFT sotildi: <b>{html.escape(n.title)}</b>\n"
                                      f"Xaridor: {u.tg_id} @{u.username or '-'} {u.phone}\n"
                                      f"To'lov: {fmt(price)} {cur.upper()}\nNFT ni foydalanuvchiga yuboring.")
        except Exception:
            pass
    if ref_info:
        try:
            await bot.send_message(ref_info[0], f"💰 Do'stlaringizdan biri NFT xarid qildi: sizga +{fmt(ref_info[1])} so'm referal foyda.")
        except Exception:
            pass
    await c.message.answer("✅ Xarid qilindi! Buyum «📦 Mening buyumlarim» bo'limida. "
                           "Admin NFT ni tez orada yuboradi.", reply_markup=back_kb())


# ---------------------------------------------------------------- Sotib olish
@router.callback_query(F.data == "m:buy")
async def m_buy(c: CallbackQuery):
    if not await guard(c):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 Stars sotib olish", callback_data="buy:stars")],
        [InlineKeyboardButton(text="🎁 NFT sotib olish", callback_data="nft:list")],
        [InlineKeyboardButton(text="🎮 PUBG UC", callback_data="m:uc")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="m:main")]])
    await c.message.edit_text("🛒 Nimani sotib olmoqchisiz?", reply_markup=kb)


@router.callback_query(F.data.in_({"buy:stars", "buy:uc"}))
async def buy_ask(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    kind = c.data.split(":")[1]
    rate = await get_setting("stars_uzs" if kind == "stars" else "uc_uzs")
    await state.set_state(S.buy_amount)
    await state.update_data(kind=kind)
    await c.message.edit_text(f"Nechta {'Stars' if kind == 'stars' else 'UC'} kerak? (1 ta = {fmt(rate)} so'm, "
                              f"so'm balansingizdan yechiladi)", reply_markup=back_kb("m:buy"))


@router.message(S.buy_amount)
async def buy_do(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        qty = int(m.text)
        assert qty > 0
    except Exception:
        return await m.answer("Butun musbat son kiriting.")
    kind = d["kind"]
    rate = await get_setting("stars_uzs" if kind == "stars" else "uc_uzs")
    cost = int(rate * qty)
    u = await get_user(m.from_user.id)
    async with Session() as s:
        if not await change_balance(s, u.id, "UZS", Decimal(-cost), f"buy_{kind}", note=f"{qty} {kind}"):
            return await m.answer("So'm balansingiz yetarli emas ❌")
        await change_balance(s, u.id, kind.upper(), Decimal(qty), f"buy_{kind}")
        await s.commit()
    await state.clear()
    await m.answer(f"✅ {qty} {kind.upper()} hisobingizga qo'shildi ({fmt(cost)} so'm).")
    await show_main(m, m.from_user.id)


# ---------------------------------------------------------------- Kurslar (almashtirish bo'limi features.py da)
async def rate_uzs(cur: str) -> Decimal:
    """1 birlik valyuta necha so'm (admin qo'lda belgilaydi)."""
    return {"UZS": Decimal(1), "J": await get_setting("j_uzs"),
            "TON": await get_setting("ton_uzs"), "STARS": await get_setting("stars_uzs")}[cur]


# ---------------------------------------------------------------- To'ldirish
@router.callback_query(F.data == "m:topup")
async def m_topup(c: CallbackQuery):
    if not await guard(c):
        return
    fee = await get_setting("topup_card_fee")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Karta (komissiya {fee}%)", callback_data="tp:card")],
        [InlineKeyboardButton(text="⭐ Telegram Stars (0%)", callback_data="tp:stars")],
        [InlineKeyboardButton(text="🪙 TON (Gram)", callback_data="tp:ton")],
        [InlineKeyboardButton(text="🎁 NFT orqali (0%)", callback_data="tp:nft")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="m:main")]])
    await c.message.edit_text("💳 To'ldirish usulini tanlang:", reply_markup=kb)


@router.callback_query(F.data.startswith("tp:"))
async def tp_method(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    method = c.data.split(":")[1]
    await state.update_data(method=method)
    if method == "card":
        await state.set_state(S.topup_amount)
        fee = await get_setting("topup_card_fee")
        return await c.message.edit_text(
            f"Karta: <code>{config.CARD_NUMBER}</code>\nEgasi: {config.CARD_OWNER}\n\n"
            f"Necha so'm o'tkazmoqchisiz? (minimal {fmt(await get_setting('topup_min'))} so'm, komissiya {fee}%)", reply_markup=back_kb("m:topup"))
    if method == "stars":
        await state.set_state(S.topup_amount)
        return await c.message.edit_text("Necha ⭐ Stars bilan to'ldirasiz? (kamida 1)", reply_markup=back_kb("m:topup"))
    if method == "ton":
        await state.set_state(S.topup_amount)
        return await c.message.edit_text("Necha TON yuborasiz?", reply_markup=back_kb("m:topup"))
    if method == "nft":
        for a in await admin_ids():
            await bot.send_message(a, f"🎁 NFT orqali to'ldirish so'rovi: {c.from_user.id} @{c.from_user.username}")
        return await c.message.edit_text("Admin siz bilan bog'lanib, NFT ni qabul qiladi va balansni to'ldiradi (foizsiz).",
                                         reply_markup=back_kb("m:topup"))


@router.message(S.topup_amount)
async def tp_amount(m: Message, state: FSMContext):
    d = await state.get_data()
    method = d["method"]
    try:
        amt = Decimal(m.text.replace(",", ".").replace(" ", ""))
        assert amt > 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri son kiriting.")
    if method == "card":
        min_amt = await get_setting("topup_min")
        if amt < min_amt:
            return await m.answer(f"❌ Minimal miqdor: {fmt(min_amt)} so'm. Kattaroq summa kiriting.")
        await state.update_data(amount=str(int(amt)))
        await state.set_state(S.topup_receipt)
        return await m.answer("O'tkazma qiling va chek (skrinshot/fayl) ni shu yerga yuboring "
                              "yoki Mini App orqali yuklang.")
    if method == "stars":
        n = int(amt)
        await state.clear()
        return await m.answer_invoice("Hisobni to'ldirish", f"{n} Stars", f"topup_stars:{n}", "XTR",
                                      [LabeledPrice(label="Stars", amount=n)], provider_token="")
    if method == "ton":
        await state.clear()
        memo = f"U{m.from_user.id}"
        return await m.answer(
            f"Manzil: <code>{config.TON_WALLET}</code>\nIzoh (MEMO): <code>{memo}</code>\nMiqdor: {amt} TON\n\n"
            "Izohni albatta yozing! To'lov tushgach balans avtomatik oshadi "
            "(TON tekshiruvi sozlangan bo'lsa, aks holda admin tasdiqlaydi).")


@router.message(S.topup_receipt, F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT}))
async def tp_receipt(m: Message, state: FSMContext):
    d = await state.get_data()
    fid = m.photo[-1].file_id if m.photo else m.document.file_id
    await create_card_topup(m.from_user.id, Decimal(d["amount"]), fid)
    await state.clear()
    await m.answer("✅ Chek adminga yuborildi. Tasdiqlanishini kuting.")


async def create_card_topup(tg_id: int, amount: Decimal, file_id: str):
    """Bot ham, Mini App ham shu funksiyani ishlatadi."""
    u = await get_user(tg_id)
    fee_pct = await get_setting("topup_card_fee")
    fee = (amount * fee_pct / 100).quantize(Decimal(1))
    async with Session() as s:
        r = Request(user_id=u.id, kind="topup", method="card", currency="UZS",
                    amount=amount, fee=fee, receipt_file_id=file_id)
        s.add(r)
        await s.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, tushgan", callback_data=f"ad:ok:{r.id}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data=f"ad:no:{r.id}")]])
    cap = (f"💳 <b>To'ldirish #{r.id}</b>\n👤 {u.tg_id} @{u.username} {u.phone}\n"
           f"Summa: {fmt(amount)} so'm | komissiya: {fmt(fee)}\nKartangiz tarixini tekshiring.")
    for a in await admin_ids():
        try:
            await bot.send_photo(a, file_id, caption=cap, reply_markup=kb)
        except Exception:
            await bot.send_document(a, file_id, caption=cap, reply_markup=kb)


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message):
    n = int(m.successful_payment.invoice_payload.split(":")[1])
    u = await get_user(m.from_user.id)
    async with Session() as s:
        await change_balance(s, u.id, "STARS", Decimal(n), "topup", note="Telegram Stars (0%)")
        await s.commit()
    await m.answer(f"✅ {n} ⭐ hisobingizga qo'shildi.")


# ---------------------------------------------------------------- Yechib olish
WD = {"uc": "🎮 PUBG UC", "stars": "🌟 Stars", "nft": "🎁 NFT", "card": "💳 Kartaga", "phone": "📱 Telefon raqamga"}


@router.callback_query(F.data == "m:withdraw")
async def m_wd(c: CallbackQuery):
    if not await guard(c):
        return
    rows = [[InlineKeyboardButton(text=t, callback_data=f"wd:{k}")] for k, t in WD.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="m:main")])
    await c.message.edit_text("💸 Yechib olish turi:\n(Har bir so'rov admin tasdig'idan keyin bajariladi)",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("wd:"))
async def wd_method(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    method = c.data.split(":")[1]
    await state.update_data(method=method)
    await state.set_state(S.withdraw_amount)
    hints = {
        "uc": f"Miqdor (UC). Minimal: {await get_setting('min_withdraw_uc')}",
        "stars": f"Miqdor (Stars). Minimal: {await get_setting('min_withdraw_stars')}",
        "nft": "Qaysi NFT narxida (so'm) yechmoqchisiz? Foiz: " + str(await get_setting("withdraw_nft_fee")) + "%",
        "card": f"Summa (so'm). Minimal: {fmt(await get_setting('min_withdraw_card'))}. Komissiya {await get_setting('withdraw_card_fee')}%",
        "phone": f"Summa (so'm). Minimal: {fmt(await get_setting('min_withdraw_phone'))}. Komissiya {await get_setting('withdraw_phone_fee')}%",
    }
    await c.message.edit_text(hints[method], reply_markup=back_kb("m:withdraw"))


@router.message(S.withdraw_amount)
async def wd_amount(m: Message, state: FSMContext):
    try:
        amt = Decimal(m.text.replace(",", ".").replace(" ", ""))
        assert amt > 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri son kiriting.")
    method = (await state.get_data())["method"]
    mins = {"uc": "min_withdraw_uc", "stars": "min_withdraw_stars",
            "phone": "min_withdraw_phone", "card": "min_withdraw_card"}
    if method in mins and amt < await get_setting(mins[method]):
        return await m.answer(f"Minimal miqdor: {await get_setting(mins[method])}")
    cur = {"uc": "UC", "stars": "STARS"}.get(method, "UZS")
    feekey = {"nft": "withdraw_nft_fee", "card": "withdraw_card_fee", "phone": "withdraw_phone_fee"}.get(method)
    fee = (amt * await get_setting(feekey) / 100).quantize(Decimal(1)) if feekey else Decimal(0)
    u = await get_user(m.from_user.id)
    if Decimal(getattr(u, cur.lower())) < amt + fee:   # balans tekshiruvi
        return await m.answer(f"❌ Balans yetarli emas. Kerak: {fmt(amt + fee)} {cur}")
    await state.update_data(amount=str(amt), fee=str(fee), cur=cur)
    await state.set_state(S.withdraw_details)
    ask = {"card": "Karta raqamingiz:", "phone": "Telefon raqamingiz:", "uc": "PUBG o'yinchi ID raqamingiz:",
           "stars": "Stars qabul qiladigan @username:", "nft": "NFT yuboriladigan @username:"}[method]
    await m.answer(f"Komissiya: {fmt(fee)} {cur}\n{ask}")


@router.message(S.withdraw_details)
async def wd_details(m: Message, state: FSMContext):
    d = await state.get_data()
    amt, fee, cur = Decimal(d["amount"]), Decimal(d["fee"]), d["cur"]
    u = await get_user(m.from_user.id)
    async with Session() as s:
        # mablag' so'rov paytida ushlab qo'yiladi; rad etilsa qaytariladi
        if not await change_balance(s, u.id, cur, -(amt + fee), "withdraw", fee=fee, note=f"hold:{d['method']}"):
            return await m.answer("❌ Balans yetarli emas.")
        r = Request(user_id=u.id, kind="withdraw", method=d["method"], currency=cur,
                    amount=amt, fee=fee, details=m.text)
        s.add(r)
        await s.commit()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"ad:ok:{r.id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"ad:no:{r.id}")]])
    for a in await admin_ids():
        await bot.send_message(a, f"💸 <b>Yechish #{r.id}</b>\n👤 {u.tg_id} @{u.username} {u.phone}\n"
                                  f"Tur: {WD[d['method']]}\nMiqdor: {fmt(amt)} {cur} (komissiya {fmt(fee)})\n"
                                  f"Rekvizit: {m.text}", reply_markup=kb)
    await m.answer("✅ So'rov adminga yuborildi.")


# ---------------------------------------------------------------- Tarix
KINDS = {"topup": "To'ldirish", "withdraw": "Yechish", "buy_nft": "NFT xarid", "buy_stars": "Stars xarid",
         "buy_uc": "UC xarid", "convert": "Almashtirish", "refund": "Qaytarildi", "admin": "Admin o'zgartirdi",
         "uc_order": "UC buyurtma", "ref": "Referal foyda", "bonus": "Bonus"}


def fmt_tx(t) -> str:
    amt = fmt_ton(t.amount) if t.currency == "TON" else fmt(t.amount)
    sign = "+" if Decimal(t.amount) > 0 else ""
    return f"{t.created_at:%d.%m %H:%M} · {KINDS.get(t.kind, t.kind)}: {sign}{amt} {t.currency}"


@router.callback_query(F.data == "m:history")
async def m_hist(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    async with Session() as s:
        rows = (await s.execute(select(Transaction).where(Transaction.user_id == u.id)
                                .order_by(Transaction.id.desc()).limit(15))).scalars().all()
    if not rows:
        txt = "📜 So'nggi amallar:\n\nBo'sh"
    else:
        txt = "📜 So'nggi amallar:\n\n" + "\n".join(fmt_tx(t) for t in rows)
    await c.message.edit_text(txt, reply_markup=back_kb())


# ---------------------------------------------------------------- Admin
@router.callback_query(F.data.startswith("ad:"))
async def admin_decide(c: CallbackQuery):
    if not await is_admin(c.from_user):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    _, act, rid = c.data.split(":")
    ref_pct = await get_setting("ref_topup_pct")
    ref_info = None
    async with Session() as s:
        r = await s.get(Request, int(rid), with_for_update=True)
        if not r:
            return await c.answer("So'rov topilmadi", show_alert=True)
        if r.status != "pending":
            return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        u = await s.get(User, r.user_id)
        if act == "ok":
            r.status = "approved"
            if r.kind == "topup":  # komissiya ayriladi, qolgani balansga
                await change_balance(s, u.id, r.currency, r.amount - r.fee, "topup", fee=r.fee, note=f"karta #{r.id}")
                if r.method == "card":
                    ref_info = await pay_referral(s, u, Decimal(r.amount), ref_pct, "topup")
            msg = "✅ So'rovingiz tasdiqlandi."
            if r.kind == "uc_order":
                msg = "✅ UC buyurtmangiz bajarildi! PUBG hisobingizni tekshiring."
        else:
            r.status = "rejected"
            if r.kind == "withdraw":  # ushlab turilgan mablag' qaytariladi
                await change_balance(s, u.id, r.currency, r.amount + r.fee, "refund", note=f"rad #{r.id}")
            if r.kind == "uc_order":
                await change_balance(s, u.id, "UZS", Decimal(r.amount), "refund", note=f"UC rad #{r.id}")
            msg = "❌ So'rovingiz rad etildi (pul tushmagan yoki ma'lumot noto'g'ri)."
            if r.kind == "uc_order":
                msg = "❌ UC buyurtma bekor qilindi, mablag' balansingizga qaytarildi."
        await s.commit()
    try:
        await bot.send_message(u.tg_id, f"#{r.id}: {msg}")
    except Exception:
        pass
    if ref_info:
        try:
            await bot.send_message(ref_info[0], f"💰 Do'stlaringizdan biri hisobini to'ldirdi: sizga +{fmt(ref_info[1])} so'm referal foyda.")
        except Exception:
            pass
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.answer("Bajarildi")


@router.message(Command("set"))
async def cmd_set(m: Message):
    """/set stars_uzs 230  — kurs va komissiyalarni qo'lda o'zgartirish."""
    if not await is_admin(m.from_user):
        return
    p = m.text.split()
    if len(p) != 3:
        return await m.answer("Format: /set kalit qiymat\nKalitlar: stars_uzs ton_uzs uc_uzs topup_card_fee "
                              "withdraw_card_fee withdraw_phone_fee withdraw_nft_fee min_withdraw_uc min_withdraw_stars")
    await set_setting(p[1], p[2])
    await m.answer("✅ Saqlandi.")


@router.message(Command("addnft"))
async def cmd_addnft(m: Message):
    """Rasmga caption yozing: /addnft Nomi | narx_som | narx_stars"""
    if not await is_admin(m.from_user):
        return
    text = (m.caption or m.text or "").replace("/addnft", "").strip()
    try:
        title, p_uzs, p_st = [x.strip() for x in text.split("|")]
        async with Session() as s:
            s.add(Nft(title=title, price_uzs=int(p_uzs), price_stars=int(p_st),
                      photo_file_id=m.photo[-1].file_id if m.photo else None))
            await s.commit()
        await m.answer("✅ NFT qo'shildi.")
    except Exception:
        await m.answer("Format: rasm yuboring, caption: /addnft Nomi | narx_so'm | narx_stars")


@router.message(F.photo, F.caption.startswith("/addnft"))
async def cmd_addnft_photo(m: Message):
    await cmd_addnft(m)


@router.message(Command("user"))
async def cmd_user(m: Message):
    """/user <tg_id> — foydalanuvchi va uning tarixi."""
    if not await is_admin(m.from_user):
        return
    try:
        u = await get_user(int(m.text.split()[1]))
    except Exception:
        return await m.answer("Format: /user tg_id")
    if not u:
        return await m.answer("Topilmadi")
    async with Session() as s:
        tx = (await s.execute(select(Transaction).where(Transaction.user_id == u.id)
                              .order_by(Transaction.id.desc()).limit(20))).scalars().all()
    h = "\n".join(f"{t.created_at:%d.%m %H:%M} {t.kind} {t.amount:+.4f} {t.currency}" for t in tx)
    await m.answer(f"👤 {u.tg_id} @{u.username} {u.phone}\n{await header(u)}\nBlok: {u.banned}\n<code>{h}</code>")


@router.message(Command("ban"))
async def cmd_ban(m: Message):
    if not await is_admin(m.from_user):
        return
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == int(m.text.split()[1])))).scalar_one()
        u.banned = not u.banned
        await s.commit()
    await m.answer(f"Blok holati: {u.banned}")


@router.message(Command("credit"))
async def cmd_credit(m: Message):
    """/credit <tg_id> <UZS|STARS|TON|UC> <miqdor> — qo'lda balans qo'shish (NFT/TON uchun)."""
    if not await is_admin(m.from_user):
        return
    try:
        _, tid, cur, amt = m.text.split()
        u = await get_user(int(tid))
        async with Session() as s:
            await change_balance(s, u.id, cur.upper(), Decimal(amt), "topup", note="admin qo'lda")
            await s.commit()
        await bot.send_message(u.tg_id, f"✅ Hisobingiz {amt} {cur.upper()} ga to'ldirildi.")
        await m.answer("✅")
    except Exception as e:
        await m.answer(f"Format: /credit tg_id UZS|STARS|TON|UC miqdor\n{e}")


@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not await is_admin(m.from_user):
        return
    async with Session() as s:
        users = len((await s.execute(select(User))).scalars().all())
        pend = len((await s.execute(select(Request).where(Request.status == "pending"))).scalars().all())
    await m.answer(f"👥 Foydalanuvchilar: {users}\n⏳ Kutayotgan so'rovlar: {pend}")
