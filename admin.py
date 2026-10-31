"""Admin panel: hamma narsa Telegram ichida, tugmalar orqali.
Faqat admin (ADMIN_USERNAMES / ADMIN_IDS) foydalana oladi. /admin yoki asosiy menyudagi "👑 Admin panel"."""
import asyncio
import html
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from sqlalchemy import func, or_, select

import config
from admins import admin_ids, is_admin
from bot import bot, dp, fmt, fmt_ton, fmt_tx, header, j_cost, nft_card_text
from db import (Application, Bonus, Nft, Request, Session, Transaction, User,
                UserItem, change_balance, get_setting, get_str_setting,
                set_setting)
from features import app_text

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and await is_admin(event.from_user)


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
dp.include_router(router)


def B(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def KB(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back(to: str = "adm:main") -> InlineKeyboardMarkup:
    return KB([[B("⬅️ Orqaga", to)]])


async def tsum(s, col, *conds) -> Decimal:
    """Ustun yig'indisi (shartlar bilan); bo'sh bo'lsa 0."""
    v = await s.scalar(select(func.coalesce(func.sum(col), 0)).where(*conds))
    return Decimal(v or 0)


async def show(c: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    """Xabarni tahrirlaydi; bo'lmasa (masalan rasm bo'lsa) yangisini yuboradi."""
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except Exception:
        await c.message.answer(text, reply_markup=markup)


class A(StatesGroup):
    find_user = State()
    balance = State()
    setting = State()
    nft_add = State()
    nft_price = State()
    channel_id = State()
    channel_link = State()
    nft_title = State()
    nft_photo = State()
    nft_model = State()
    nft_backdrop = State()
    nft_symbol = State()
    nft_prices = State()
    bn_amount = State()
    bn_user = State()
    bn_text = State()
    ad_text = State()
    ad_btn = State()


PANEL = KB([
    [B("⏳ Kutayotgan so'rovlar", "adm:pending"), B("📝 Arizalar", "adm:apps")],
    [B("👤 Foydalanuvchi kartochkasi", "adm:find")],
    [B("💱 Kurs va komissiyalar", "adm:settings")],
    [B("📊 Moliyaviy hisobot", "adm:fin"), B("🏆 Reyting", "adm:top")],
    [B("🎁 NFT boshqaruvi", "adm:nfts")],
    [B("🎉 Bonus yuborish", "adm:bonus"), B("📣 Reklama tarqatish", "adm:ad")],
    [B("📢 Majburiy kanal", "adm:channel"), B("📈 Statistika", "adm:stats")],
    [B("⬅️ Asosiy menyu", "m:main")],
])


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("👑 <b>Admin panel</b>", reply_markup=PANEL)


@router.callback_query(F.data == "adm:main")
async def adm_main(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await show(c, "👑 <b>Admin panel</b>", PANEL)


# ------------------------------------------------------------ Kutayotgan so'rovlar
async def send_request_card(chat_id: int, r: Request, u: User):
    kind = {"topup": "💳 To'ldirish", "withdraw": "💸 Yechish", "uc_order": "🎮 UC buyurtma"}.get(r.kind, r.kind)
    cap = (f"{kind} #{r.id}\n👤 {u.tg_id} @{u.username or '-'} {u.phone or ''}\n"
           f"Usul: {r.method}\nMiqdor: {fmt(r.amount)} {r.currency} (komissiya {fmt(r.fee)})")
    if r.details:
        cap += f"\nRekvizit: {r.details}"
    markup = KB([[B("✅ Tasdiqlash", f"ad:ok:{r.id}"), B("❌ Rad etish", f"ad:no:{r.id}")]])
    if r.receipt_file_id:
        try:
            return await bot.send_photo(chat_id, r.receipt_file_id, caption=cap, reply_markup=markup)
        except Exception:
            return await bot.send_document(chat_id, r.receipt_file_id, caption=cap, reply_markup=markup)
    await bot.send_message(chat_id, cap, reply_markup=markup)


@router.callback_query(F.data == "adm:pending")
async def adm_pending(c: CallbackQuery):
    await c.answer()
    async with Session() as s:
        rows = (await s.execute(
            select(Request, User).join(User, User.id == Request.user_id)
            .where(Request.status == "pending").order_by(Request.id).limit(20))).all()
    if not rows:
        return await show(c, "✅ Kutayotgan so'rov yo'q.", back())
    await show(c, f"⏳ Kutayotgan so'rovlar: {len(rows)} ta (pastda)", back())
    for r, u in rows:
        await send_request_card(c.from_user.id, r, u)


# ------------------------------------------------------------ Foydalanuvchi
async def user_card(u: User) -> str:
    async with Session() as s:
        friends = await s.scalar(select(func.count(User.id)).where(User.referrer_id == u.tg_id)) or 0
        nfts = await s.scalar(select(func.count(UserItem.id)).where(UserItem.user_id == u.id)) or 0
        topups = await tsum(s, Request.amount, Request.user_id == u.id, Request.kind == "topup",
                            Request.status == "approved")
        wd_n = await s.scalar(select(func.count(Request.id)).where(
            Request.user_id == u.id, Request.kind == "withdraw", Request.status == "approved")) or 0
        wd_sum = await tsum(s, Request.amount, Request.user_id == u.id, Request.kind == "withdraw",
                            Request.currency == "UZS", Request.status == "approved")
        apps = (await s.execute(select(Application).where(Application.user_id == u.id)
                                .order_by(Application.id))).scalars().all()
    contracts = ", ".join(f"{'Pul ishlash' if a.kind == 'earn' else 'NFT Sotish'} ({a.status})" for a in apps) or "yo'q"
    flags = []
    if u.earner:
        flags.append("⚖️ pul ishlash faol")
    if u.can_sell:
        flags.append("🏷️ sotuvchi")
    if u.frozen:
        flags.append("🧊 muzlatilgan")
    if u.banned:
        flags.append("🚫 bloklangan")
    return (f"👤 <b>{u.tg_id}</b> @{u.username or '-'}\n📞 {u.phone or '-'}\n"
            f"📅 Ro'yxatdan o'tgan: {u.created_at:%d.%m.%Y %H:%M}\n\n{await header(u)}\n"
            f"🪙 TON: {fmt_ton(u.ton)}\n🎮 UC: {fmt(u.uc)}\n\n"
            f"📝 Shartnomalar/arizalar: {contracts}\n🎁 NFT xaridlari: {nfts} ta\n"
            f"💳 To'ldirgan jami: {fmt(topups)} so'm\n💸 Pul yechgan: {wd_n} marta ({fmt(wd_sum)} so'm)\n"
            f"👥 Taklif qilgan do'stlari: {friends} ta\n"
            f"🔖 Holat: {', '.join(flags) if flags else 'oddiy'}")


def user_kb(u: User) -> InlineKeyboardMarkup:
    return KB([
        [B("➕ Qo'shish", f"adm:bal:{u.id}:+"), B("➖ Ayirish", f"adm:bal:{u.id}:-")],
        [B("📜 Tarixi", f"adm:hist:{u.id}")],
        [B("✅ Blokdan chiqarish" if u.banned else "🚫 Bloklash", f"adm:ban:{u.id}")],
        [B("⬅️ Panel", "adm:main")],
    ])


async def get_u(uid: int) -> User | None:
    async with Session() as s:
        return await s.get(User, uid)


@router.callback_query(F.data == "adm:find")
async def adm_find(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(A.find_user)
    await show(c, "Foydalanuvchining Telegram ID si, @username yoki telefon raqamini yuboring:", back())


@router.message(A.find_user)
async def adm_find_do(m: Message, state: FSMContext):
    q = (m.text or "").strip()
    conds = [func.lower(User.username) == q.lstrip("@").lower()]
    digits = "".join(ch for ch in q if ch.isdigit())
    if digits and len(digits) <= 15:
        conds += [User.tg_id == int(digits), User.phone.like(f"%{digits}")]
    async with Session() as s:
        u = (await s.execute(select(User).where(or_(*conds)).limit(1))).scalar_one_or_none()
    if not u:
        return await m.answer("❌ Topilmadi. Qayta urinib ko'ring yoki ⬅️ bosing.", reply_markup=back())
    await state.clear()
    await m.answer(await user_card(u), reply_markup=user_kb(u))


@router.callback_query(F.data.startswith("adm:bal:"))
async def adm_bal(c: CallbackQuery):
    await c.answer()
    _, _, uid, sign = c.data.split(":")
    cur_rows = [[B(cur, f"adm:cur:{uid}:{sign}:{cur}") for cur in ("UZS", "J", "STARS", "TON", "UC")],
                [B("⬅️ Orqaga", "adm:main")]]
    await show(c, f"Qaysi balansni {'oshiramiz' if sign == '+' else 'kamaytiramiz'}?", KB(cur_rows))


@router.callback_query(F.data.startswith("adm:cur:"))
async def adm_cur(c: CallbackQuery, state: FSMContext):
    await c.answer()
    _, _, uid, sign, cur = c.data.split(":")
    await state.set_state(A.balance)
    await state.update_data(uid=int(uid), sign=sign, cur=cur)
    await show(c, f"Qancha {cur} {'qo`shamiz' if sign == '+' else 'ayiramiz'}? Miqdorni yuboring (masalan 50000):", back())


@router.message(A.balance)
async def adm_bal_apply(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        amt = Decimal((m.text or "").replace(",", ".").replace(" ", ""))
        assert amt > 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri musbat son kiriting.")
    cur = d["cur"]
    if cur != "TON":
        amt = Decimal(int(amt))
    if amt <= 0:
        return await m.answer("Miqdor 0 dan katta bo'lishi kerak.")
    delta = amt if d["sign"] == "+" else -amt
    async with Session() as s:
        ok = await change_balance(s, d["uid"], cur, delta, "admin", note="admin qo'lda")
        if not ok:
            return await m.answer("❌ Foydalanuvchida buncha mablag' yo'q.")
        await s.commit()
    u = await get_u(d["uid"])
    await state.clear()
    shown = fmt_ton(amt) if cur == "TON" else fmt(amt)
    try:
        await bot.send_message(u.tg_id, f"ℹ️ Hisobingiz admin tomonidan o'zgartirildi: "
                                        f"{'+' if delta > 0 else '-'}{shown} {cur}")
    except Exception:
        pass
    await m.answer("✅ Bajarildi.\n\n" + await user_card(u), reply_markup=user_kb(u))


@router.callback_query(F.data.startswith("adm:hist:"))
async def adm_hist(c: CallbackQuery):
    await c.answer()
    uid = int(c.data.split(":")[2])
    async with Session() as s:
        tx = (await s.execute(select(Transaction).where(Transaction.user_id == uid)
                              .order_by(Transaction.id.desc()).limit(20))).scalars().all()
    from bot import fmt_tx
    txt = "\n".join(fmt_tx(t) for t in tx) or "Bo'sh"
    await show(c, f"📜 So'nggi amallar:\n\n{txt}", back())


@router.callback_query(F.data.startswith("adm:ban:"))
async def adm_ban(c: CallbackQuery):
    uid = int(c.data.split(":")[2])
    async with Session() as s:
        u = await s.get(User, uid)
        u.banned = not u.banned
        await s.commit()
    await c.answer("Bloklandi" if u.banned else "Blokdan chiqarildi")
    await show(c, await user_card(u), user_kb(u))


# ------------------------------------------------------------ Kurs va komissiyalar
LABELS = {
    "stars_uzs": "1 Star = so'm",
    "ton_uzs": "1 TON = so'm",
    "uc_uzs": "1 UC = so'm",
    "topup_card_fee": "Karta to'ldirish komissiyasi %",
    "withdraw_card_fee": "Kartaga yechish %",
    "withdraw_phone_fee": "Telefonga yechish %",
    "withdraw_nft_fee": "NFT yechish %",
    "min_withdraw_uc": "Min. UC yechish",
    "min_withdraw_stars": "Min. Stars yechish",
    "j_uzs": "1 J🧧 = so'm (kunlik kurs)",
    "topup_min": "Min. karta to'ldirish (so'm)",
    "min_withdraw_phone": "Min. telefonga yechish (so'm)",
    "min_withdraw_card": "Min. kartaga yechish (so'm)",
    "ref_topup_pct": "Referal: to'ldirishdan %",
    "ref_nft_pct": "Referal: NFT xariddan %",
}


async def settings_view():
    rows = []
    for k, label in LABELS.items():
        rows.append([B(f"{label}: {fmt_ton(await get_setting(k))}", f"adm:set:{k}")])
    rows.append([B("⬅️ Panel", "adm:main")])
    return "💱 <b>Kurs va komissiyalar</b>\nO'zgartirish uchun tanlang:", KB(rows)


@router.callback_query(F.data == "adm:settings")
async def adm_settings(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    text, markup = await settings_view()
    await show(c, text, markup)


@router.callback_query(F.data.startswith("adm:set:"))
async def adm_set(c: CallbackQuery, state: FSMContext):
    await c.answer()
    key = c.data.split(":", 2)[2]
    if key not in LABELS:
        return
    await state.set_state(A.setting)
    await state.update_data(key=key)
    await show(c, f"<b>{LABELS[key]}</b>\nHozirgi qiymat: {fmt_ton(await get_setting(key))}\n\nYangi qiymatni yuboring:",
               back("adm:settings"))


@router.message(A.setting)
async def adm_set_apply(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        v = Decimal((m.text or "").replace(",", ".").replace(" ", ""))
        assert v >= 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri son kiriting.")
    await set_setting(d["key"], fmt_ton(v))
    await state.clear()
    text, markup = await settings_view()
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=markup)


# ------------------------------------------------------------ NFT boshqaruvi
@router.callback_query(F.data == "adm:nfts")
async def adm_nfts(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    async with Session() as s:
        items = (await s.execute(select(Nft).where(Nft.active).order_by(Nft.id))).scalars().all()
    rows = [[B(f"{n.title} — {fmt(n.price_uzs)} so'm / {n.price_stars}⭐", f"adm:nft:{n.id}")] for n in items]
    rows.append([B("➕ NFT qo'shish", "adm:nftadd")])
    rows.append([B("⬅️ Panel", "adm:main")])
    await show(c, "🎁 <b>NFT boshqaruvi</b>" + ("" if items else "\nHozircha NFT yo'q."), KB(rows))


@router.callback_query(F.data.startswith("adm:nft:"))
async def adm_nft(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    nid = int(c.data.split(":")[2])
    async with Session() as s:
        n = await s.get(Nft, nid)
    if not n:
        return await show(c, "NFT topilmadi.", back("adm:nfts"))
    jc = j_cost(n.price_uzs, await get_setting("j_uzs"))
    await show(c, nft_card_text(n, jc), KB([
        [B("✏️ So'm narxi", f"adm:nftp:{nid}:uzs"), B("✏️ Stars narxi", f"adm:nftp:{nid}:stars")],
        [B("📦 Nusxalar soni", f"adm:nftp:{nid}:stock")],
        [B("🗑 O'chirish", f"adm:nftdel:{nid}")],
        [B("⬅️ Orqaga", "adm:nfts")]]))


@router.callback_query(F.data.startswith("adm:nftp:"))
async def adm_nft_price(c: CallbackQuery, state: FSMContext):
    await c.answer()
    _, _, nid, cur = c.data.split(":")
    await state.set_state(A.nft_price)
    await state.update_data(nid=int(nid), cur=cur)
    label = {"uzs": "narxni (so'm)", "stars": "narxni (Stars)", "stock": "nusxalar sonini"}.get(cur, "qiymatni")
    await show(c, f"Yangi {label} yuboring:", back("adm:nfts"))


@router.message(A.nft_price)
async def adm_nft_price_apply(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        v = int((m.text or "").replace(" ", ""))
        assert v >= 0
    except (ValueError, AssertionError):
        return await m.answer("Butun musbat son kiriting.")
    async with Session() as s:
        n = await s.get(Nft, d["nid"])
        if d["cur"] == "uzs":
            n.price_uzs = v
        elif d["cur"] == "stars":
            n.price_stars = v
        else:
            n.stock = v
            n.active = v > 0
        await s.commit()
    await state.clear()
    await m.answer("✅ Yangilandi.", reply_markup=back("adm:nfts"))


@router.callback_query(F.data.startswith("adm:nftdel:"))
async def adm_nft_del(c: CallbackQuery):
    nid = int(c.data.split(":")[2])
    async with Session() as s:
        n = await s.get(Nft, nid)
        n.active = False
        await s.commit()
    await c.answer("O'chirildi")
    await show(c, "🗑 NFT o'chirildi.", back("adm:nfts"))


def parse_trait(t: str):
    """'Gold Star 1.5' -> ('Gold Star', '1.5%'); '-' -> (None, None)."""
    t = (t or "").strip()
    if t in ("", "-"):
        return None, None
    parts = t.rsplit(" ", 1)
    if len(parts) == 2 and parts[0].strip():
        last = parts[1].replace("%", "").replace(",", ".")
        try:
            float(last)
            return parts[0].strip()[:100], f"{last}%"[:16]
        except ValueError:
            pass
    return t[:100], None


@router.callback_query(F.data == "adm:nftadd")
async def adm_nft_add(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    await state.set_state(A.nft_title)
    await show(c, "🎁 <b>Yangi NFT</b>\n\n1/6 — NFT nomini yozing:", back("adm:nfts"))


@router.message(A.nft_title)
async def adm_nft_title(m: Message, state: FSMContext):
    t = (m.text or "").strip()
    if not t or len(t) > 200:
        return await m.answer("Nom 1–200 belgi bo'lsin.")
    await state.update_data(title=t)
    await state.set_state(A.nft_photo)
    await m.answer("2/6 — NFT rasmini yuboring (rasm yo'q bo'lsa <code>-</code> yozing):")


@router.message(A.nft_photo)
async def adm_nft_photo(m: Message, state: FSMContext):
    if m.photo:
        await state.update_data(photo=m.photo[-1].file_id)
    elif (m.text or "").strip() == "-":
        await state.update_data(photo=None)
    else:
        return await m.answer("Rasm yuboring yoki <code>-</code> yozing.")
    await state.set_state(A.nft_model)
    await m.answer("3/6 — <b>Model</b> nomi va foizi. Masalan: <code>Gold Star 1.5</code>\n(foiz ixtiyoriy; yo'q bo'lsa <code>-</code>)")


@router.message(A.nft_model)
async def adm_nft_model(m: Message, state: FSMContext):
    name, pct = parse_trait(m.text)
    await state.update_data(model=name, model_pct=pct)
    await state.set_state(A.nft_backdrop)
    await m.answer("4/6 — <b>Orqa fon</b> nomi va foizi. Masalan: <code>Emerald 2</code> (yo'q bo'lsa <code>-</code>)")


@router.message(A.nft_backdrop)
async def adm_nft_backdrop(m: Message, state: FSMContext):
    name, pct = parse_trait(m.text)
    await state.update_data(backdrop=name, backdrop_pct=pct)
    await state.set_state(A.nft_symbol)
    await m.answer("5/6 — <b>Belgi</b> nomi va foizi. Masalan: <code>Crown 0.5</code> (yo'q bo'lsa <code>-</code>)")


@router.message(A.nft_symbol)
async def adm_nft_symbol(m: Message, state: FSMContext):
    name, pct = parse_trait(m.text)
    await state.update_data(symbol=name, symbol_pct=pct)
    await state.set_state(A.nft_prices)
    await m.answer("6/6 — Narxlar: <code>so'm | stars | nusxa soni</code>\n"
                   "Masalan: <code>500000 | 2000 | 1</code>\n(Stars narxi yo'q bo'lsa 0; soni yozilmasa 1)")


@router.message(A.nft_prices)
async def adm_nft_prices(m: Message, state: FSMContext):
    try:
        parts = [int(x.strip().replace(" ", "")) for x in (m.text or "").split("|")]
        p_uzs, p_st = parts[0], parts[1]
        stock = parts[2] if len(parts) > 2 else 1
        assert p_uzs >= 0 and p_st >= 0 and stock >= 1 and (p_uzs > 0 or p_st > 0)
    except Exception:
        return await m.answer("Format xato. Misol: <code>500000 | 2000 | 1</code>")
    d = await state.get_data()
    n = Nft(title=d["title"], photo_file_id=d.get("photo"), price_uzs=p_uzs, price_stars=p_st, stock=stock,
            model=d.get("model"), model_pct=d.get("model_pct"), backdrop=d.get("backdrop"),
            backdrop_pct=d.get("backdrop_pct"), symbol=d.get("symbol"), symbol_pct=d.get("symbol_pct"))
    async with Session() as s:
        s.add(n)
        await s.commit()
    await state.clear()
    jc = j_cost(p_uzs, await get_setting("j_uzs"))
    await m.answer("✅ NFT qo'shildi. Foydalanuvchilarga shunday ko'rinadi:\n\n" + nft_card_text(n, jc),
                   reply_markup=back("adm:nfts"))


# ------------------------------------------------------------ Majburiy kanal
@router.callback_query(F.data == "adm:channel")
async def adm_channel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    ch = await get_str_setting("channel_id", config.CHANNEL_ID)
    link = await get_str_setting("channel_link", config.CHANNEL_LINK)
    on = bool(ch) and ch != "-"
    await show(c, f"📢 <b>Majburiy obuna kanali</b>\nHolat: {'yoqilgan ✅' if on else 'o`chiq ❌'}\n"
                  f"Kanal: {ch if on else '-'}\nHavola: {link or '-'}",
               KB([[B("✏️ Kanalni o'zgartirish", "adm:chset")],
                   [B("🚫 Obunani o'chirish", "adm:choff")],
                   [B("⬅️ Panel", "adm:main")]]))


@router.callback_query(F.data == "adm:choff")
async def adm_choff(c: CallbackQuery):
    await set_setting("channel_id", "-")
    await c.answer("O'chirildi")
    await show(c, "🚫 Majburiy obuna o'chirildi.", back())


@router.callback_query(F.data == "adm:chset")
async def adm_chset(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(A.channel_id)
    await show(c, "Kanal @username yoki ID sini yuboring (masalan <code>@kripto_kanal</code>).\n\n"
                  "⚠️ Avval botni shu kanalga <b>ADMIN</b> qilib qo'shing.", back("adm:channel"))


@router.message(A.channel_id)
async def adm_ch_id(m: Message, state: FSMContext):
    q = (m.text or "").strip()
    if q.startswith("https://t.me/"):
        q = "@" + q.rstrip("/").split("/")[-1]
    elif q and not q.startswith(("@", "-")) and not q.lstrip("-").isdigit():
        q = "@" + q
    try:
        chat = await bot.get_chat(q)
        me = await bot.get_me()
        mem = await bot.get_chat_member(chat.id, me.id)
    except Exception:
        return await m.answer("❌ Kanal topilmadi yoki bot kanalda admin emas.\n"
                              "Botni kanalga ADMIN qiling va qayta yuboring.", reply_markup=back("adm:channel"))
    await set_setting("channel_id", str(chat.id))
    warn = "" if mem.status in ("administrator", "creator") else "\n⚠️ Bot kanalda admin emas — obunani tekshira olmaydi!"
    if chat.username:
        await set_setting("channel_link", f"https://t.me/{chat.username}")
        await state.clear()
        return await m.answer(f"✅ Kanal saqlandi: {chat.title}{warn}", reply_markup=back("adm:channel"))
    await state.set_state(A.channel_link)
    await m.answer(f"✅ Kanal topildi: {chat.title}{warn}\n\nBu yopiq kanal. Taklif havolasini yuboring (https://t.me/+...):")


@router.message(A.channel_link)
async def adm_ch_link(m: Message, state: FSMContext):
    link = (m.text or "").strip()
    if not link.startswith("http"):
        return await m.answer("Havola https:// bilan boshlanishi kerak.")
    await set_setting("channel_link", link)
    await state.clear()
    await m.answer("✅ Saqlandi.", reply_markup=back("adm:channel"))


# ------------------------------------------------------------ Statistika
@router.callback_query(F.data == "adm:stats")
async def adm_stats(c: CallbackQuery):
    await c.answer()
    async with Session() as s:
        users = await s.scalar(select(func.count(User.id)))
        banned = await s.scalar(select(func.count(User.id)).where(User.banned))
        pend = await s.scalar(select(func.count(Request.id)).where(Request.status == "pending"))
        uzs = await s.scalar(select(func.coalesce(func.sum(User.uzs), 0)))
        stars = await s.scalar(select(func.coalesce(func.sum(User.stars), 0)))
        ton = await s.scalar(select(func.coalesce(func.sum(User.ton), 0)))
        uc = await s.scalar(select(func.coalesce(func.sum(User.uc), 0)))
        j = await s.scalar(select(func.coalesce(func.sum(User.j), 0)))
    await show(c, f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {users} (bloklangan: {banned})\n"
                  f"⏳ Kutayotgan so'rovlar: {pend}\n\n<b>Foydalanuvchilar balansi yig'indisi:</b>\n"
                  f"🇺🇿 {fmt(uzs)} so'm\n⭐️ {fmt(stars)} Stars\nJ🧧 {fmt(j)}\n🪙 {fmt_ton(ton)} TON\n🎮 {fmt(uc)} UC", back())



# ------------------------------------------------------------ Arizalar (Pul ishlash / NFT Sotish)
@router.callback_query(F.data == "adm:apps")
async def adm_apps(c: CallbackQuery):
    await c.answer()
    async with Session() as s:
        rows = (await s.execute(select(Application, User).join(User, User.id == Application.user_id)
                                .where(Application.status == "pending").order_by(Application.id).limit(20))).all()
    if not rows:
        return await show(c, "✅ Kutayotgan ariza yo'q.", back())
    await show(c, f"📝 Kutayotgan arizalar: {len(rows)} ta (pastda)", back())
    for ap, u in rows:
        await bot.send_message(c.from_user.id, app_text(ap, u),
                               reply_markup=KB([[B("✅ Tasdiqlash", f"app:ok:{ap.id}"), B("❌ Rad etish", f"app:no:{ap.id}")]]))


@router.callback_query(F.data.startswith("app:"))
async def app_decide(c: CallbackQuery):
    _, act, aid = c.data.split(":")
    async with Session() as s:
        ap = await s.get(Application, int(aid), with_for_update=True)
        if not ap:
            return await c.answer("Ariza topilmadi", show_alert=True)
        if ap.status != "pending":
            return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        u = await s.get(User, ap.user_id)
        if act == "ok":
            ap.status = "approved"
            if ap.kind == "sell":
                u.can_sell = True
        else:
            ap.status = "rejected"
        await s.commit()
    if act == "ok":
        if ap.kind == "earn":
            msg = ("✅ «Pul ishlash» arizangiz tasdiqlandi!\nShartnoma tuzish → Pul ishlash bo'limiga kirib, "
                   "<b>✔️ Tasdiqlash</b> tugmasini bosing.")
        else:
            msg = "✅ «NFT Sotish» arizangiz tasdiqlandi!\nAsosiy menyuda 🏷️ NFT Sotish bo'limi paydo bo'ldi."
    else:
        msg = "❌ Arizangiz rad etildi. Xohlasangiz, qaytadan ariza berishingiz mumkin."
    try:
        await bot.send_message(u.tg_id, msg, reply_markup=KB([[B("⬅️ Asosiy menyu", "m:main")]]))
    except Exception:
        pass
    try:
        await c.message.edit_text(c.message.html_text + ("\n\n✅ <b>Tasdiqlandi</b>" if act == "ok" else "\n\n❌ <b>Rad etildi</b>"),
                                  reply_markup=None)
    except Exception:
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await c.answer("Bajarildi")


# ------------------------------------------------------------ Moliyaviy hisobot
def day_start_utc() -> datetime:
    """Toshkent (UTC+5) bo'yicha bugungi kun boshi, UTC da."""
    local = datetime.utcnow() + timedelta(hours=5)
    return local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5)


async def report_block(since: datetime | None) -> str:
    R, T = Request, Transaction
    rb = [R.status == "approved"] + ([R.created_at >= since] if since else [])
    tb = [T.id > 0] + ([T.created_at >= since] if since else [])
    async with Session() as s:
        card_in = await tsum(s, R.amount, R.kind == "topup", R.method == "card", *rb)
        card_fee = await tsum(s, R.fee, R.kind == "topup", R.method == "card", *rb)
        stars_in = await tsum(s, T.amount, T.kind == "topup", T.currency == "STARS", T.amount > 0,
                              T.note.like("Telegram Stars%"), *tb)
        j_in = -(await tsum(s, T.amount, T.kind == "buy_nft", T.currency == "J", *tb))
        nft_uzs = -(await tsum(s, T.amount, T.kind == "buy_nft", T.currency == "UZS", *tb))
        nft_st = -(await tsum(s, T.amount, T.kind == "buy_nft", T.currency == "STARS", *tb))
        wd = {}
        for cur in ("UZS", "STARS", "UC"):
            wd[cur] = (await tsum(s, R.amount, R.kind == "withdraw", R.currency == cur, *rb),
                       await tsum(s, R.fee, R.kind == "withdraw", R.currency == cur, *rb))
        uc_ord = await tsum(s, R.amount, R.kind == "uc_order", *rb)
        ref_paid = await tsum(s, T.amount, T.kind == "ref", *tb)
    net = card_fee + wd["UZS"][1] - ref_paid
    return (f"<b>Kirim:</b>\n💳 Karta: {fmt(card_in)} so'm\n⭐️ Stars (to'ldirish): {fmt(stars_in)}\n"
            f"J🧧 (NFT xaridi): {fmt(j_in)}\n🎁 NFT xaridi: {fmt(nft_uzs)} so'm / {fmt(nft_st)} ⭐\n\n"
            f"<b>Chiqim:</b>\n💸 Yechildi: {fmt(wd['UZS'][0])} so'm, {fmt(wd['STARS'][0])} ⭐, {fmt(wd['UC'][0])} UC\n"
            f"🎮 UC buyurtmalar: {fmt(uc_ord)} so'm\n🤝 Referal ulushlar: {fmt(ref_paid)} so'm\n\n"
            f"<b>Komissiyadan foyda:</b>\nTo'ldirish: {fmt(card_fee)} so'm\nYechish: {fmt(wd['UZS'][1])} so'm, "
            f"{fmt(wd['STARS'][1])} ⭐\n💰 <b>Sof foyda (taxminan): {fmt(net)} so'm</b>")


@router.callback_query(F.data == "adm:fin")
async def adm_fin(c: CallbackQuery):
    await c.answer()
    today = await report_block(day_start_utc())
    total = await report_block(None)
    await show(c, f"📊 <b>Moliyaviy hisobot</b>\n\n📅 <b>BUGUN</b>\n{today}\n\n━━━━━━━━━━\n🗓 <b>JAMI</b>\n{total}", back())


# ------------------------------------------------------------ Reyting va tahlil
def uname(u: User) -> str:
    return f"@{u.username}" if u.username else str(u.tg_id)


@router.callback_query(F.data == "adm:top")
async def adm_top(c: CallbackQuery):
    await c.answer()
    sr, jr, tr = await get_setting("stars_uzs"), await get_setting("j_uzs"), await get_setting("ton_uzs")
    async with Session() as s:
        users = (await s.execute(select(User))).scalars().all()
        ref_rows = (await s.execute(select(User.referrer_id, func.count(User.id)).where(User.referrer_id.isnot(None))
                                    .group_by(User.referrer_id).order_by(func.count(User.id).desc()).limit(10))).all()
        profit_rows = (await s.execute(select(Transaction.user_id, func.sum(Transaction.amount))
                                       .where(Transaction.kind == "ref").group_by(Transaction.user_id)
                                       .order_by(func.sum(Transaction.amount).desc()).limit(10))).all()
    by_tg = {u.tg_id: u for u in users}
    by_id = {u.id: u for u in users}

    def eq(u: User) -> Decimal:
        return Decimal(u.uzs) + Decimal(u.stars) * sr + Decimal(u.j) * jr + Decimal(u.ton) * tr

    top_bal = sorted(users, key=eq, reverse=True)[:10]
    lines = ["🏆 <b>Reyting va tahlil</b>", "", "<b>💰 Eng boy hisoblar (so'm ekvivalenti):</b>"]
    lines += [f"{i}. {html.escape(uname(u))} — {fmt(eq(u))}" for i, u in enumerate(top_bal, 1)] or ["—"]
    lines += ["", "<b>👥 Eng ko'p do'st chaqirganlar:</b>"]
    lines += [f"{i}. {html.escape(uname(by_tg[r])) if r in by_tg else r} — {n} ta" for i, (r, n) in enumerate(ref_rows, 1)] or ["—"]
    lines += ["", "<b>📈 Referal foyda ko'rayotganlar:</b>"]
    lines += [f"{i}. {html.escape(uname(by_id[uid])) if uid in by_id else uid} — {fmt(v)} so'm" for i, (uid, v) in enumerate(profit_rows, 1)] or ["—"]
    await show(c, "\n".join(lines), back())


# ------------------------------------------------------------ Xabar yuborish yordamchisi
_bg: set = set()


def spawn(coro):
    """Fon vazifasi (uzoq yuborishlar uchun). Xatolar logga yoziladi."""
    async def runner():
        try:
            await coro
        except Exception:
            log.exception("background task failed")
    t = asyncio.create_task(runner())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


async def safe_send(chat_id: int, text: str, markup=None, photo: str | None = None) -> bool:
    """Xabar yuboradi. Flood-limitda kutadi; HTML xato bo'lsa oddiy matn sifatida qayta yuboradi."""
    plain = False
    for _ in range(4):
        try:
            kw = {"parse_mode": None} if plain else {}
            if photo:
                await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup, **kw)
            else:
                await bot.send_message(chat_id, text, reply_markup=markup, **kw)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if not plain and "parse" in str(e).lower():
                plain = True
                continue
            return False
        except TelegramForbiddenError:
            return False
        except Exception:
            return False
    return False


async def audience(target) -> list[tuple[int, int]]:
    """[(user.id, tg_id)] — hammaga (ro'yxatdan o'tgan, bloklanmagan) yoki bitta foydalanuvchiga."""
    async with Session() as s:
        if target == "all":
            q = select(User.id, User.tg_id).where(User.phone.isnot(None), User.banned.is_(False))
        else:
            q = select(User.id, User.tg_id).where(User.tg_id == int(target))
        return [(r[0], r[1]) for r in (await s.execute(q)).all()]


async def find_user_q(q: str) -> User | None:
    q = (q or "").strip()
    conds = [func.lower(User.username) == q.lstrip("@").lower()]
    digits = "".join(ch for ch in q if ch.isdigit())
    if digits and len(digits) <= 15:
        conds += [User.tg_id == int(digits), User.phone.like(f"%{digits}")]
    async with Session() as s:
        return (await s.execute(select(User).where(or_(*conds)).limit(1))).scalar_one_or_none()


# ------------------------------------------------------------ Bonus yuborish
@router.callback_query(F.data == "adm:bonus")
async def adm_bonus(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await show(c, "🎉 <b>Bonus yuborish</b> (minimal cheklov yo'q)\n\n"
                  "1️⃣ <b>Konkurs</b>: xabar ostida ✅ Olish / 🚫 Kerak emas tugmalari bo'ladi\n"
                  "2️⃣ <b>To'g'ridan-to'g'ri</b>: tugmasiz, balansga avtomatik tushadi",
               KB([[B("1️⃣ Konkurs (tugmali)", "adm:bn:1")], [B("2️⃣ To'g'ridan-to'g'ri", "adm:bn:2")],
                   [B("⬅️ Panel", "adm:main")]]))


@router.callback_query(F.data.startswith("adm:bn:"))
async def adm_bn_method(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    await state.update_data(method=c.data.split(":")[2])
    await show(c, "Qaysi valyutada?", KB([[B(cur, f"adm:bnc:{cur}") for cur in ("UZS", "J", "STARS", "TON", "UC")],
                                          [B("⬅️ Orqaga", "adm:bonus")]]))


@router.callback_query(F.data.startswith("adm:bnc:"))
async def adm_bn_cur(c: CallbackQuery, state: FSMContext):
    await c.answer()
    cur = c.data.split(":")[2]
    if cur not in ("UZS", "J", "STARS", "TON", "UC"):
        return
    await state.update_data(cur=cur)
    await state.set_state(A.bn_amount)
    await show(c, f"Har bir foydalanuvchiga necha {cur} bonus? Miqdorni yozing:", back("adm:bonus"))


@router.message(A.bn_amount)
async def adm_bn_amount(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        amt = Decimal((m.text or "").replace(",", ".").replace(" ", ""))
        assert amt > 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri musbat son kiriting.")
    amt = amt.quantize(Decimal("0.000000001")) if d["cur"] == "TON" else Decimal(int(amt))
    if amt <= 0:
        return await m.answer("Miqdor 0 dan katta bo'lsin.")
    await state.update_data(amount=str(amt))
    await m.answer("Kimga yuboramiz?", reply_markup=KB([[B("👥 Hammaga", "adm:bnt:all"), B("👤 Bittasiga", "adm:bnt:one")],
                                                       [B("⬅️ Bekor", "adm:bonus")]]))


@router.callback_query(F.data.startswith("adm:bnt:"))
async def adm_bn_target(c: CallbackQuery, state: FSMContext):
    await c.answer()
    if c.data.endswith(":all"):
        await state.update_data(target="all")
        await state.set_state(A.bn_text)
        return await show(c, "Xabar matnini yozing (standart matn uchun <code>-</code>):", back("adm:bonus"))
    await state.set_state(A.bn_user)
    await show(c, "Foydalanuvchining ID, @username yoki telefon raqamini yuboring:", back("adm:bonus"))


@router.message(A.bn_user)
async def adm_bn_user(m: Message, state: FSMContext):
    u = await find_user_q(m.text)
    if not u:
        return await m.answer("❌ Topilmadi. Qayta yuboring.")
    await state.update_data(target=u.tg_id)
    await state.set_state(A.bn_text)
    await m.answer(f"👤 {u.tg_id} @{u.username or '-'}\n\nXabar matnini yozing (standart matn uchun <code>-</code>):")


@router.message(A.bn_text)
async def adm_bn_text(m: Message, state: FSMContext):
    d = await state.get_data()
    txt = (m.text or "").strip()
    amt = Decimal(d["amount"])
    shown = fmt_ton(amt) if d["cur"] == "TON" else fmt(amt)
    if txt in ("", "-"):
        txt = (f"🎁 Sizga bonus! <b>{shown} {d['cur']}</b>" if d["method"] == "1"
               else f"🎁 Hisobingizga bonus tushdi: <b>+{shown} {d['cur']}</b>")
    else:
        txt = html.escape(txt)
    await state.update_data(text=txt)
    n = len(await audience(d["target"]))
    await m.answer(f"📋 <b>Tasdiqlang</b>\n\nUsul: {'1 (tugmali)' if d['method'] == '1' else '2 (to`g`ridan-to`g`ri)'}\n"
                   f"Miqdor: {shown} {d['cur']}\nKimga: {'hammaga' if d['target'] == 'all' else d['target']} ({n} ta)\n\n"
                   f"Xabar:\n{txt}",
                   reply_markup=KB([[B("✅ Yuborish", "adm:bnok")], [B("🚫 Bekor", "adm:bonus")]]))


async def run_bonus(admin_chat: int, d: dict):
    cur, amt, method, text = d["cur"], Decimal(d["amount"]), d["method"], d["text"]
    rows = await audience(d["target"])
    bonus_id = None
    if method == "1":
        async with Session() as s:
            b = Bonus(currency=cur, amount=amt, text=text)
            s.add(b)
            await s.commit()
            bonus_id = b.id
    ok = fail = 0
    for uid, tg in rows:
        if method == "1":
            sent = await safe_send(tg, text, KB([[B("✅ Olish", f"bn:ok:{bonus_id}"), B("🚫 Kerak emas", f"bn:no:{bonus_id}")]]))
        else:
            async with Session() as s:
                await change_balance(s, uid, cur, amt, "bonus", note="admin bonus")
                await s.commit()
            sent = await safe_send(tg, text)
        ok, fail = (ok + 1, fail) if sent else (ok, fail + 1)
        await asyncio.sleep(0.05)
    extra = "" if method == "1" else "\n(Bonus balanslarga tushirildi, xabar yetmaganlarga ham.)"
    await safe_send(admin_chat, f"✅ Bonus yuborish tugadi.\nXabar yetdi: {ok}\nYetmadi: {fail}{extra}")


@router.callback_query(F.data == "adm:bnok")
async def adm_bn_ok(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("text") or not d.get("cur"):
        return await c.answer("Sessiya tugagan, qaytadan boshlang", show_alert=True)
    await state.clear()
    await c.answer("Yuborilmoqda…")
    await show(c, "⏳ Yuborilmoqda… Tugagach xabar beraman.", back())
    spawn(run_bonus(c.from_user.id, d))


# ------------------------------------------------------------ Reklama tarqatish
BOT_LINKS = {"nft": "nft:list", "buy": "m:buy", "topup": "m:topup", "withdraw": "m:withdraw",
             "convert": "m:convert", "uc": "m:uc", "friends": "m:friends", "contract": "m:contract",
             "main": "m:main"}


def parse_buttons(text: str):
    """Har qator: 'Tugma matni | https://havola' yoki 'Tugma matni | bot:topup'."""
    rows = []
    for line in [x.strip() for x in text.splitlines() if x.strip()][:6]:
        if "|" not in line:
            raise ValueError(f"«{line}» — '|' belgisi yo'q")
        label, target = [x.strip() for x in line.split("|", 1)]
        if not label or len(label) > 60:
            raise ValueError(f"«{line}» — tugma matni 1–60 belgi bo'lsin")
        if target.startswith(("http://", "https://")):
            rows.append([InlineKeyboardButton(text=label, url=target)])
        elif target.startswith("bot:") and target[4:] in BOT_LINKS:
            rows.append([InlineKeyboardButton(text=label, callback_data=BOT_LINKS[target[4:]])])
        else:
            raise ValueError(f"«{line}» — havola https:// bilan yoki bot:{'/'.join(BOT_LINKS)} bo'lsin")
    return KB(rows) if rows else None


@router.callback_query(F.data == "adm:ad")
async def adm_ad(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await state.set_state(A.ad_text)
    await show(c, "📣 <b>Reklama</b>\n\nReklama xabarini yuboring (matn, yoki rasm + tagiga matn). "
                  "Qalin/kursiv formatlash saqlanadi.", back())


@router.message(A.ad_text)
async def adm_ad_text(m: Message, state: FSMContext):
    try:
        txt = m.html_text
    except Exception:
        txt = html.escape(m.text or m.caption or "")
    photo = m.photo[-1].file_id if m.photo else None
    if not txt and not photo:
        return await m.answer("Matn yoki rasm yuboring.")
    await state.update_data(ad_text=txt, ad_photo=photo)
    await state.set_state(A.ad_btn)
    await m.answer("Tugmalar qo'shasizmi? Har qatorga bittadan yozing:\n\n"
                   "<code>Kanalga o'tish | https://t.me/kanal</code>\n"
                   "<code>Hisobni to'ldirish | bot:topup</code>\n\n"
                   f"Bot bo'limlari: <code>{' '.join('bot:' + k for k in BOT_LINKS)}</code>\n"
                   "Tugma kerak bo'lmasa <code>-</code> yozing.")


@router.message(A.ad_btn)
async def adm_ad_btn(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    markup = None
    if raw != "-":
        try:
            markup = parse_buttons(raw)
        except ValueError as e:
            return await m.answer(f"❌ {html.escape(str(e))}\nQayta yozing yoki <code>-</code>.")
    await state.update_data(ad_btn=None if raw == "-" else raw)
    d = await state.get_data()
    n = len(await audience("all"))
    await m.answer(f"👀 <b>Ko'rinishi (oldindan)</b> — {n} ta foydalanuvchiga ketadi:")
    await safe_send(m.from_user.id, d["ad_text"] or "\u200b", markup, d.get("ad_photo"))
    await m.answer("Yuboraymi?", reply_markup=KB([[B("✅ Yuborish", "adm:adok")], [B("🚫 Bekor", "adm:main")]]))


async def run_ad(admin_chat: int, d: dict):
    markup = parse_buttons(d["ad_btn"]) if d.get("ad_btn") else None
    rows = await audience("all")
    ok = fail = 0
    for _uid, tg in rows:
        sent = await safe_send(tg, d["ad_text"] or "\u200b", markup, d.get("ad_photo"))
        ok, fail = (ok + 1, fail) if sent else (ok, fail + 1)
        await asyncio.sleep(0.05)
    await safe_send(admin_chat, f"✅ Reklama tarqatildi.\nYetib bordi: {ok}\nYetmadi (bot bloklangan va h.k.): {fail}")


@router.callback_query(F.data == "adm:adok")
async def adm_ad_ok(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "ad_text" not in d:
        return await c.answer("Sessiya tugagan, qaytadan boshlang", show_alert=True)
    await state.clear()
    await c.answer("Yuborilmoqda…")
    await show(c, "⏳ Reklama yuborilmoqda… Tugagach xabar beraman.", back())
    spawn(run_ad(c.from_user.id, d))
