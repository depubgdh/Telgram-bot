"""Telegram bot: ro'yxatdan o'tish, menyular, balans, to'ldirish, yechish, admin."""
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, ChatJoinRequest, ContentType,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, LabeledPrice, Message,
                           PreCheckoutQuery, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove, WebAppInfo)
from sqlalchemy import select

import config
from db import (JoinRequest, Nft, Request, Session, Transaction, User,
                 change_balance, get_or_create_user, get_setting, get_user,
                 set_setting)

log = logging.getLogger(__name__)
bot = Bot(config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

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


def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS


def fmt(n) -> str:
    return f"{Decimal(n):,.0f}".replace(",", " ")


# ---------------------------------------------------------------- obuna
async def is_subscribed(tg_id: int) -> bool:
    if not config.CHANNEL_ID:
        return True
    try:
        m = await bot.get_chat_member(config.CHANNEL_ID, tg_id)
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
def main_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎁 NFT / Sovg'alar", callback_data="nft:list")],
        [InlineKeyboardButton(text="🛒 Sotib olish", callback_data="m:buy"),
         InlineKeyboardButton(text="🔄 Almashtirish", callback_data="m:convert")],
        [InlineKeyboardButton(text="💳 Hisobni to'ldirish", callback_data="m:topup"),
         InlineKeyboardButton(text="💸 Yechib olish", callback_data="m:withdraw")],
        [InlineKeyboardButton(text="📜 Tarix", callback_data="m:history")],
    ]
    if config.WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="📱 Mini App", web_app=WebAppInfo(url=config.WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def header(u: User) -> str:
    return (f"🌟 <b>{fmt(u.stars)}</b> Stars   🪙 <b>{Decimal(u.ton):.4f}</b> TON   "
            f"🇺🇿 <b>{fmt(u.uzs)}</b> so'm")


async def show_main(target: Message | CallbackQuery, tg_id: int):
    u = await get_user(tg_id)
    text = f"{await header(u)}\n\nAsosiy menyu:"
    msg = target.message if isinstance(target, CallbackQuery) else target
    try:
        if isinstance(target, CallbackQuery):
            await msg.edit_text(text, reply_markup=main_kb())
            return
    except Exception:
        pass
    await msg.answer(text, reply_markup=main_kb())


def back_kb(to="m:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=to)]])


# ---------------------------------------------------------------- /start oqimi
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(m.from_user.id, m.from_user.username)
    if u.banned:
        return await m.answer("⛔ Hisobingiz bloklangan.")
    if not await is_subscribed(m.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Kanalga o'tish", url=config.CHANNEL_LINK)],
            [InlineKeyboardButton(text="✅ Tekshirish", callback_data="chk:sub")]])
        return await m.answer("Botdan foydalanish uchun kripto kanalga obuna bo'ling:", reply_markup=kb)
    await after_sub(m, u)


async def after_sub(m: Message, u: User):
    if not u.terms_accepted:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Roziman", callback_data="terms:ok")]])
        return await m.answer(TERMS, reply_markup=kb)
    if not u.phone:
        return await ask_phone(m)
    await show_main(m, m.from_user.id)


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
        items = (await s.execute(select(Nft).where(Nft.active))).scalars().all()
    if not items:
        return await c.message.edit_text("Hozircha NFT yo'q.", reply_markup=back_kb())
    rows = [[InlineKeyboardButton(text=f"{n.title} — {fmt(n.price_uzs)} so'm / {n.price_stars}⭐",
                                  callback_data=f"nft:v:{n.id}")] for n in items]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="m:main")])
    await c.message.edit_text("🎁 <b>NFT / Sovg'alar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("nft:v:"))
async def nft_view(c: CallbackQuery):
    if not await guard(c):
        return
    nid = int(c.data.split(":")[2])
    async with Session() as s:
        n = await s.get(Nft, nid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{fmt(n.price_uzs)} so'm bilan", callback_data=f"nft:b:uzs:{nid}")],
        [InlineKeyboardButton(text=f"{n.price_stars} ⭐ bilan", callback_data=f"nft:b:stars:{nid}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="nft:list")]])
    cap = f"<b>{n.title}</b>\n💰 {fmt(n.price_uzs)} so'm  |  {n.price_stars} ⭐"
    if n.photo_file_id:
        await c.message.delete()
        await c.message.answer_photo(n.photo_file_id, caption=cap, reply_markup=kb)
    else:
        await c.message.edit_text(cap, reply_markup=kb)


@router.callback_query(F.data.startswith("nft:b:"))
async def nft_buy(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    _, _, cur, nid = c.data.split(":")
    async with Session() as s:
        n = await s.get(Nft, int(nid))
        price = n.price_uzs if cur == "uzs" else n.price_stars
        ok = await change_balance(s, u.id, cur.upper(), Decimal(-price), "buy_nft", note=n.title)
        if not ok:
            return await c.answer("Balansda mablag' yetarli emas ❌", show_alert=True)
        await s.commit()
    await c.answer("✅ Sotib olindi!", show_alert=True)
    for a in config.ADMIN_IDS:
        await bot.send_message(a, f"🎁 NFT sotildi: <b>{n.title}</b>\nXaridor: {u.tg_id} @{u.username} {u.phone}\n"
                                  f"To'lov: {fmt(price)} {cur.upper()}\nNFT ni foydalanuvchiga yuboring.")
    await c.message.answer("Admin NFT ni tez orada hisobingizga yuboradi.", reply_markup=back_kb())


# ---------------------------------------------------------------- Sotib olish
@router.callback_query(F.data == "m:buy")
async def m_buy(c: CallbackQuery):
    if not await guard(c):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 Stars sotib olish", callback_data="buy:stars")],
        [InlineKeyboardButton(text="🎁 NFT sotib olish", callback_data="nft:list")],
        [InlineKeyboardButton(text="🎮 PUBG UC", callback_data="buy:uc")],
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


# ---------------------------------------------------------------- Almashtirish
CONV = {  # (from, to)
    "uzs_ton": ("UZS", "TON"), "ton_uzs": ("TON", "UZS"),
    "uzs_stars": ("UZS", "STARS"), "stars_uzs": ("STARS", "UZS"),
}


async def rate_uzs(cur: str) -> Decimal:
    return {"UZS": Decimal(1), "TON": await get_setting("ton_uzs"), "STARS": await get_setting("stars_uzs")}[cur]


@router.callback_query(F.data == "m:convert")
async def m_convert(c: CallbackQuery):
    if not await guard(c):
        return
    rows = [[InlineKeyboardButton(text=f"{a} → {b}", callback_data=f"cv:{k}")] for k, (a, b) in CONV.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="m:main")])
    await c.message.edit_text("🔄 Qaysi yo'nalishda almashtiramiz?\n(Kurs admin tomonidan belgilanadi)",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cv:"))
async def cv_ask(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    key = c.data.split(":")[1]
    a, b = CONV[key]
    ra, rb = await rate_uzs(a), await rate_uzs(b)
    await state.set_state(S.convert_amount)
    await state.update_data(key=key)
    await c.message.edit_text(f"1 {a} = {ra / rb:.6f} {b}\nNecha {a} almashtirmoqchisiz?", reply_markup=back_kb("m:convert"))


@router.message(S.convert_amount)
async def cv_do(m: Message, state: FSMContext):
    try:
        amt = Decimal(m.text.replace(",", ".").replace(" ", ""))
        assert amt > 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri son kiriting.")
    key = (await state.get_data())["key"]
    a, b = CONV[key]
    out = amt * await rate_uzs(a) / await rate_uzs(b)
    if b in ("UZS", "STARS"):
        out = Decimal(int(out))
    if a in ("UZS", "STARS"):
        amt = Decimal(int(amt))
    u = await get_user(m.from_user.id)
    async with Session() as s:
        if not await change_balance(s, u.id, a, -amt, "convert", note=f"{a}->{b}"):
            return await m.answer("Balans yetarli emas ❌")
        await change_balance(s, u.id, b, out, "convert", note=f"{a}->{b}")
        await s.commit()
    await state.clear()
    await m.answer(f"✅ {amt} {a} → {out} {b}")
    await show_main(m, m.from_user.id)


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
            f"Necha so'm o'tkazmoqchisiz? (komissiya {fee}%)", reply_markup=back_kb("m:topup"))
    if method == "stars":
        await state.set_state(S.topup_amount)
        return await c.message.edit_text("Necha ⭐ Stars bilan to'ldirasiz? (kamida 1)", reply_markup=back_kb("m:topup"))
    if method == "ton":
        await state.set_state(S.topup_amount)
        return await c.message.edit_text("Necha TON yuborasiz?", reply_markup=back_kb("m:topup"))
    if method == "nft":
        for a in config.ADMIN_IDS:
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
    for a in config.ADMIN_IDS:
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
        "card": f"Summa (so'm). Komissiya {await get_setting('withdraw_card_fee')}%",
        "phone": f"Summa (so'm). Komissiya {await get_setting('withdraw_phone_fee')}%",
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
    mins = {"uc": "min_withdraw_uc", "stars": "min_withdraw_stars"}
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
    for a in config.ADMIN_IDS:
        await bot.send_message(a, f"💸 <b>Yechish #{r.id}</b>\n👤 {u.tg_id} @{u.username} {u.phone}\n"
                                  f"Tur: {WD[d['method']]}\nMiqdor: {fmt(amt)} {cur} (komissiya {fmt(fee)})\n"
                                  f"Rekvizit: {m.text}", reply_markup=kb)
    await m.answer("✅ So'rov adminga yuborildi.")


# ---------------------------------------------------------------- Tarix
@router.callback_query(F.data == "m:history")
async def m_hist(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    async with Session() as s:
        rows = (await s.execute(select(Transaction).where(Transaction.user_id == u.id)
                                .order_by(Transaction.id.desc()).limit(15))).scalars().all()
    txt = "\n".join(f"{t.created_at:%d.%m %H:%M} | {t.kind} | {t.amount:+.4f} {t.currency}" for t in rows) or "Hali amallar yo'q."
    await c.message.edit_text(f"📜 <b>So'nggi amallar</b>\n<code>{txt}</code>", reply_markup=back_kb())


# ---------------------------------------------------------------- Admin
@router.callback_query(F.data.startswith("ad:"))
async def admin_decide(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    _, act, rid = c.data.split(":")
    async with Session() as s:
        r = await s.get(Request, int(rid))
        if r.status != "pending":
            return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        u = await s.get(User, r.user_id)
        if act == "ok":
            r.status = "approved"
            if r.kind == "topup":  # komissiya ayriladi, qolgani balansga
                await change_balance(s, u.id, r.currency, r.amount - r.fee, "topup", fee=r.fee, note=f"karta #{r.id}")
            msg = "✅ So'rovingiz tasdiqlandi."
        else:
            r.status = "rejected"
            if r.kind == "withdraw":  # ushlab turilgan mablag' qaytariladi
                await change_balance(s, u.id, r.currency, r.amount + r.fee, "refund", note=f"rad #{r.id}")
            msg = "❌ So'rovingiz rad etildi (pul tushmagan yoki ma'lumot noto'g'ri)."
        await s.commit()
    await bot.send_message(u.tg_id, f"#{r.id}: {msg}")
    await c.message.edit_reply_markup(reply_markup=None)
    await c.answer("Bajarildi")


@router.message(Command("set"))
async def cmd_set(m: Message):
    """/set stars_uzs 230  — kurs va komissiyalarni qo'lda o'zgartirish."""
    if not is_admin(m.from_user.id):
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
    if not is_admin(m.from_user.id):
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
    if not is_admin(m.from_user.id):
        return
    try:
        u = await get_user(int(m.text.split()[1]))
    except Exception:
        return await m.answer("Format: /user <tg_id>")
    if not u:
        return await m.answer("Topilmadi")
    async with Session() as s:
        tx = (await s.execute(select(Transaction).where(Transaction.user_id == u.id)
                              .order_by(Transaction.id.desc()).limit(20))).scalars().all()
    h = "\n".join(f"{t.created_at:%d.%m %H:%M} {t.kind} {t.amount:+.4f} {t.currency}" for t in tx)
    await m.answer(f"👤 {u.tg_id} @{u.username} {u.phone}\n{await header(u)}\nBlok: {u.banned}\n<code>{h}</code>")


@router.message(Command("ban"))
async def cmd_ban(m: Message):
    if not is_admin(m.from_user.id):
        return
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == int(m.text.split()[1])))).scalar_one()
        u.banned = not u.banned
        await s.commit()
    await m.answer(f"Blok holati: {u.banned}")


@router.message(Command("credit"))
async def cmd_credit(m: Message):
    """/credit <tg_id> <UZS|STARS|TON|UC> <miqdor> — qo'lda balans qo'shish (NFT/TON uchun)."""
    if not is_admin(m.from_user.id):
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
    if not is_admin(m.from_user.id):
        return
    async with Session() as s:
        users = len((await s.execute(select(User))).scalars().all())
        pend = len((await s.execute(select(Request).where(Request.status == "pending"))).scalars().all())
    await m.answer(f"👥 Foydalanuvchilar: {users}\n⏳ Kutayotgan so'rovlar: {pend}")
