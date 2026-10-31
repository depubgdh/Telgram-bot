"""Foydalanuvchi bo'limlari: Almashtirish, Mening hisoblarim, Mening buyumlarim, PUBG UC xaridi,
Shartnoma tuzish (Hisobni muzlatish, Pul ishlash, NFT Sotish), Do'stlarim, Bonus olish."""
import asyncio
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from admins import admin_ids
from bot import (back_kb, bot, dp, edit_or_send, fmt, fmt_ton, guard,
                 rate_uzs, show_main)
from db import (Application, Bonus, BonusClaim, Request, Session, Transaction,
                User, UserItem, change_balance, get_setting, get_str_setting,
                get_user, set_setting)

router = Router()
dp.include_router(router)


def B(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def KB(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def esc(v) -> str:
    return html.escape(str(v)) if v not in (None, "") else "-"


async def try_delete(m: Message):
    try:
        await m.delete()
    except Exception:
        pass


async def notify_admins(text: str, markup=None):
    for a in await admin_ids():
        try:
            await bot.send_message(a, text, reply_markup=markup)
        except Exception:
            pass


async def active_user(m: Message, state: FSMContext, allow_frozen: bool = False) -> User | None:
    """Xabar (matn) kutayotgan holatlar uchun tekshiruv."""
    u = await get_user(m.from_user.id)
    if not u or not u.phone or u.banned:
        await state.clear()
        return None
    if u.blocked_until and u.blocked_until > datetime.utcnow():
        await state.clear()
        mins = int((u.blocked_until - datetime.utcnow()).total_seconds() // 60) + 1
        await m.answer(f"⛔ Xavfsizlik maqsadida {mins} daqiqaga bloklangansiz.")
        return None
    if u.frozen and not allow_frozen:
        await state.clear()
        await m.answer("🧊 Hisobingiz muzlatilgan.")
        return None
    return u


# =============================================================== Mening hisoblarim
@router.callback_query(F.data == "m:accounts")
async def m_accounts(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    jr = await get_setting("j_uzs")
    text = ("💼 <b>Mening hisoblarim</b>\n\n"
            f"⭐️ Stars: {fmt(u.stars)}\n"
            f"J🧧: {fmt(u.j)}\n"
            f"🇺🇿 So'm: {fmt(u.uzs)}\n"
            f"🪙 TON: {fmt_ton(u.ton)}\n"
            f"🎮 UC: {fmt(u.uc)}\n\n"
            f"Kurs: 1 J🧧 = {fmt(jr)} so'm")
    await edit_or_send(c, text, back_kb())


# =============================================================== Mening buyumlarim
@router.callback_query(F.data == "m:items")
async def m_items(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    async with Session() as s:
        items = (await s.execute(select(UserItem).where(UserItem.user_id == u.id)
                                 .order_by(UserItem.id.desc()).limit(30))).scalars().all()
    if not items:
        return await edit_or_send(c, "📦 <b>Mening buyumlarim</b>\n\nHozircha bo'sh. "
                                     "NFT sotib olsangiz, shu yerda ko'rinadi.", back_kb())
    rows = [[B(f"🎁 {it.title[:40]}", f"itm:v:{it.id}")] for it in items]
    rows.append([B("⬅️ Orqaga", "m:main")])
    await edit_or_send(c, f"📦 <b>Mening buyumlarim</b> ({len(items)} ta):", KB(rows))


@router.callback_query(F.data.startswith("itm:v:"))
async def item_view(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    iid = int(c.data.split(":")[2])
    async with Session() as s:
        it = await s.get(UserItem, iid)
    if not it or it.user_id != u.id:
        return await c.answer("Topilmadi", show_alert=True)
    cap = f"{it.card_text}\n\n🧾 To'langan: {esc(it.paid)}\n📅 {it.created_at:%d.%m.%Y %H:%M}"
    kb = KB([[B("⬅️ Orqaga", "m:items")]])
    if it.photo_file_id:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer_photo(it.photo_file_id, caption=cap, reply_markup=kb)
    else:
        await edit_or_send(c, cap, kb)


# =============================================================== Almashtirish (konvertatsiya)
CURS = ["UZS", "J", "STARS", "TON"]
CUR_LABEL = {"UZS": "🇺🇿 So'm", "J": "J🧧", "STARS": "⭐️ Stars", "TON": "🪙 TON"}


class Cv(StatesGroup):
    amount = State()


@router.callback_query(F.data == "m:convert")
async def m_convert(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    await state.clear()
    rows = [[B(CUR_LABEL[x], f"cv:f:{x}")] for x in CURS]
    rows.append([B("⬅️ Orqaga", "m:main")])
    await edit_or_send(c, "🔄 <b>Almashtirish</b>\nQaysi valyutadan almashtiramiz?\n(Kurs admin tomonidan belgilanadi)", KB(rows))


@router.callback_query(F.data.startswith("cv:f:"))
async def cv_from(c: CallbackQuery):
    if not await guard(c):
        return
    a = c.data.split(":")[2]
    if a not in CURS:
        return await c.answer()
    rows = [[B(CUR_LABEL[x], f"cv:t:{a}:{x}")] for x in CURS if x != a]
    rows.append([B("⬅️ Orqaga", "m:convert")])
    await edit_or_send(c, f"{CUR_LABEL[a]} → qaysi valyutaga?", KB(rows))


@router.callback_query(F.data.startswith("cv:t:"))
async def cv_to(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    _, _, a, b = c.data.split(":")
    if a not in CURS or b not in CURS or a == b:
        return await c.answer()
    ra, rb = await rate_uzs(a), await rate_uzs(b)
    if ra <= 0 or rb <= 0:
        return await c.answer("Bu yo'nalish uchun kurs belgilanmagan", show_alert=True)
    await state.set_state(Cv.amount)
    await state.update_data(a=a, b=b)
    unit = ra / rb
    await edit_or_send(c, f"1 {CUR_LABEL[a]} = {fmt_ton(unit.quantize(Decimal('0.000001')))} {CUR_LABEL[b]}\n\n"
                          f"Necha {CUR_LABEL[a]} almashtirmoqchisiz? Miqdorni yozing:", back_kb("m:convert"))


@router.message(Cv.amount)
async def cv_do(m: Message, state: FSMContext):
    u = await active_user(m, state)
    if not u:
        return
    d = await state.get_data()
    a, b = d.get("a"), d.get("b")
    if a not in CURS or b not in CURS:
        await state.clear()
        return await m.answer("Sessiya tugagan. Qaytadan boshlang.", reply_markup=back_kb("m:convert"))
    try:
        amt = Decimal((m.text or "").replace(",", ".").replace(" ", ""))
        assert amt > 0
    except (InvalidOperation, AssertionError):
        return await m.answer("To'g'ri musbat son kiriting.")
    amt = amt.quantize(Decimal("0.000000001")) if a == "TON" else Decimal(int(amt))
    if amt <= 0:
        return await m.answer("Miqdor juda kam.")
    ra, rb = await rate_uzs(a), await rate_uzs(b)
    if ra <= 0 or rb <= 0:
        await state.clear()
        return await m.answer("Kurs belgilanmagan.", reply_markup=back_kb("m:convert"))
    out = amt * ra / rb
    out = out.quantize(Decimal("0.000000001")) if b == "TON" else Decimal(int(out))  # butun valyutalar pastga yaxlitlanadi
    if out <= 0:
        return await m.answer("❌ Bu miqdor almashtirish uchun juda kam. Kattaroq miqdor kiriting.")
    async with Session() as s:
        if not await change_balance(s, u.id, a, -amt, "convert", note=f"{a}->{b}"):
            return await m.answer(f"❌ {CUR_LABEL[a]} balansingiz yetarli emas.")
        await change_balance(s, u.id, b, out, "convert", note=f"{a}->{b}")
        await s.commit()
    await state.clear()
    sa = fmt_ton(amt) if a == "TON" else fmt(amt)
    sb = fmt_ton(out) if b == "TON" else fmt(out)
    await m.answer(f"✅ Almashtirildi: {sa} {CUR_LABEL[a]} → {sb} {CUR_LABEL[b]}")
    await show_main(m, u.tg_id)


# =============================================================== PUBG UC xaridi
UC_PACKS = [60, 120, 180, 240, 325]


class Uc(StatesGroup):
    pid = State()


@router.callback_query(F.data == "m:uc")
async def m_uc(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    await state.clear()
    rate = await get_setting("uc_uzs")
    rows = [[B(f"🎮 {q} UC — {fmt(rate * q)} so'm", f"uc:p:{q}")] for q in UC_PACKS]
    rows.append([B("⬅️ Orqaga", "m:main")])
    await edit_or_send(c, "🎮 <b>PUBG Mobile UC</b>\nPaketni tanlang (to'lov so'm balansingizdan):", KB(rows))


@router.callback_query(F.data.startswith("uc:p:"))
async def uc_pack(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    q = int(c.data.split(":")[2])
    if q not in UC_PACKS:
        return await c.answer()
    await state.set_state(Uc.pid)
    await state.update_data(qty=q)
    await edit_or_send(c, f"🎮 {q} UC\n\nPUBG Mobile <b>ID raqamingizni</b> yuboring (faqat raqamlar):", back_kb("m:uc"))


@router.message(Uc.pid)
async def uc_pid(m: Message, state: FSMContext):
    u = await active_user(m, state)
    if not u:
        return
    pid = (m.text or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{6,12}", pid):
        return await m.answer("❌ ID noto'g'ri. PUBG ID 6–12 ta raqamdan iborat. Qayta yuboring:")
    await state.update_data(pid=pid)
    await m.answer(f"🔎 ID tekshiruvi (1/2)\n\nPUBG ID: <code>{pid}</code>\n\nShu ID to'g'rimi? "
                   "Noto'g'ri ID ga yuborilgan UC qaytarilmaydi!",
                   reply_markup=KB([[B("✅ To'g'ri", "uc:idok"), B("✏️ O'zgartirish", "uc:idno")]]))


@router.callback_query(F.data == "uc:idno")
async def uc_idno(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    await state.set_state(Uc.pid)
    await edit_or_send(c, "ID raqamingizni qayta yuboring:", back_kb("m:uc"))


@router.callback_query(F.data == "uc:idok")
async def uc_idok(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    d = await state.get_data()
    q, pid = d.get("qty"), d.get("pid")
    if not q or not pid:
        return await c.answer("Sessiya tugagan, qaytadan boshlang", show_alert=True)
    cost = int(await get_setting("uc_uzs") * q)
    await edit_or_send(c, f"🔎 Yakuniy tasdiq (2/2)\n\n🎮 Paket: <b>{q} UC</b>\n🆔 PUBG ID: <code>{pid}</code>\n"
                          f"💰 Narx: <b>{fmt(cost)} so'm</b>\n🇺🇿 Balansingiz: {fmt(u.uzs)} so'm",
                       KB([[B("✅ Buyurtma berish", "uc:go")], [B("🚫 Bekor qilish", "m:uc")]]))


@router.callback_query(F.data == "uc:go")
async def uc_go(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    d = await state.get_data()
    q, pid = d.get("qty"), d.get("pid")
    if not q or not pid:
        return await c.answer("Sessiya tugagan, qaytadan boshlang", show_alert=True)
    cost = int(await get_setting("uc_uzs") * q)
    async with Session() as s:
        if not await change_balance(s, u.id, "UZS", Decimal(-cost), "uc_order", note=f"{q} UC, ID {pid}"):
            return await c.answer("So'm balansingiz yetarli emas ❌", show_alert=True)
        r = Request(user_id=u.id, kind="uc_order", method="uc", currency="UZS", amount=Decimal(cost),
                    fee=Decimal(0), details=f"PUBG ID: {pid}; UC: {q}")
        s.add(r)
        await s.commit()
    await state.clear()
    await notify_admins(f"🎮 <b>UC buyurtma #{r.id}</b>\n👤 {u.tg_id} @{u.username or '-'} {u.phone}\n"
                        f"UC: <b>{q}</b>\nPUBG ID: <code>{pid}</code>\nTo'langan: {fmt(cost)} so'm",
                        KB([[B("✅ Yetkazildi", f"ad:ok:{r.id}"), B("❌ Rad etish", f"ad:no:{r.id}")]]))
    await edit_or_send(c, f"✅ Buyurtma #{r.id} qabul qilindi!\n{q} UC → ID <code>{pid}</code>\n"
                          "Admin tasdiqlagach, sizga xabar keladi.", back_kb())


# =============================================================== Do'stlarim
_bot_username: str | None = None


async def bot_username() -> str:
    global _bot_username
    if not _bot_username:
        _bot_username = (await bot.get_me()).username
    return _bot_username


async def count_friends(tg_id: int) -> int:
    async with Session() as s:
        return int(await s.scalar(select(func.count(User.id)).where(User.referrer_id == tg_id)) or 0)


@router.callback_query(F.data == "m:friends")
async def m_friends(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    link = f"https://t.me/{await bot_username()}?start=ref_{u.tg_id}"
    n = await count_friends(u.tg_id)
    text = (f"👥 <b>Do'stlarim</b>\n\nTaklif qilgan do'stlaringiz: <b>{n}</b> ta\n\n"
            f"🔗 Shaxsiy havolangiz:\n<code>{link}</code>")
    if u.earner:
        async with Session() as s:
            earned = await s.scalar(select(func.coalesce(func.sum(Transaction.amount), 0))
                                    .where(Transaction.user_id == u.id, Transaction.kind == "ref"))
        text += (f"\n\n💰 Referaldan topilgan: <b>{fmt(earned)}</b> so'm\n"
                 "Do'stlaringiz foyda keltirsa, sizga xabar keladi (ismlari maxfiy saqlanadi).")
    else:
        text += "\n\nℹ️ Referal foyda olish uchun: Shartnoma tuzish → Pul ishlash bo'limida ariza bering."
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('Mana shu botga qo`shiling!')}"
    await edit_or_send(c, text, KB([[InlineKeyboardButton(text="📤 Ulashish", url=share)],
                                    [B("⬅️ Orqaga", "m:main")]]))


# =============================================================== Shartnoma tuzish
@router.callback_query(F.data == "m:contract")
async def m_contract(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    await state.clear()
    rows = [[B("⛑️ Hisobni muzlatish 🔑", "fz:menu")],
            [B("⚖️ Pul ishlash ⛏️", "earn:menu")]]
    if not u.can_sell:
        rows.append([B("🏷️ NFT Sotish", "sell:menu")])
    rows.append([B("⬅️ Orqaga", "m:main")])
    await edit_or_send(c, "✔️ <b>Shartnoma tuzish 📝</b>\nInvestitsiya va xavfsizlik markazi:", KB(rows))


# ---------------------------------------------------------------- Hisobni muzlatish
class Fz(StatesGroup):
    old = State()
    pw1 = State()
    pw2 = State()
    open = State()
    code = State()


def hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()


async def verify_pw(u: User, pw: str) -> bool:
    if not u.freeze_hash or not u.freeze_salt:
        return False
    h = await asyncio.to_thread(hash_pw, pw, u.freeze_salt)
    return hmac.compare_digest(h, u.freeze_hash)


async def block_user(tg_id: int, minutes: int = 5):
    async with Session() as s:
        u = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one()
        u.blocked_until = datetime.utcnow() + timedelta(minutes=minutes)
        await s.commit()


@router.callback_query(F.data == "fz:menu")
async def fz_menu(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    await state.clear()
    info = ("⛑️ <b>Hisobni muzlatish 🔑</b>\n\nMuzlatilganda NFT, Stars, So'm va J🧧 qiymatlari 0 ko'rinadi, "
            "hech qanday amal bajarib bo'lmaydi. Ochish uchun parol kerak.")
    if not u.freeze_hash:
        rows = [[B("✅ Tasdiqlash (Parol o'rnatish)", "fz:set")], [B("🚫 Bekor qilish", "m:contract")]]
    else:
        rows = [[B("🧊 Hisobni muzlatish", "fz:go")], [B("🔄 Parolni almashtirish", "fz:chg")],
                [B("🔄 Parolni tiklash", "fz:reset")], [B("🚫 Bekor qilish", "m:contract")]]
    await edit_or_send(c, info, KB(rows))


@router.callback_query(F.data == "fz:set")
async def fz_set(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    await state.set_state(Fz.pw1)
    await state.update_data(reset=False)
    await edit_or_send(c, "🔑 Yangi parolni yozing (4–32 belgi).\nXabaringiz xavfsizlik uchun avtomatik o'chiriladi.",
                       back_kb("fz:menu"))


@router.callback_query(F.data == "fz:chg")
async def fz_chg(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    await state.set_state(Fz.old)
    await edit_or_send(c, "🔑 Avval <b>hozirgi parolni</b> yozing:", back_kb("fz:menu"))


@router.message(Fz.old)
async def fz_old(m: Message, state: FSMContext):
    u = await active_user(m, state)
    if not u:
        return
    pw = (m.text or "").strip()
    await try_delete(m)
    if not await verify_pw(u, pw):
        return await m.answer("❌ Parol noto'g'ri. Qayta yozing yoki «Parolni tiklash» dan foydalaning.",
                              reply_markup=back_kb("fz:menu"))
    await state.set_state(Fz.pw1)
    await state.update_data(reset=False)
    await m.answer("✅ To'g'ri. Endi <b>yangi parolni</b> yozing (4–32 belgi):")


@router.message(Fz.pw1)
async def fz_pw1(m: Message, state: FSMContext):
    u = await active_user(m, state, allow_frozen=True)
    if not u:
        return
    pw = (m.text or "").strip()
    await try_delete(m)
    if not (4 <= len(pw) <= 32):
        return await m.answer("❌ Parol 4–32 belgidan iborat bo'lsin. Qayta yozing:")
    salt = secrets.token_hex(8)
    h = await asyncio.to_thread(hash_pw, pw, salt)
    await state.update_data(salt=salt, h=h)
    await state.set_state(Fz.pw2)
    await m.answer("🔁 Parolni yana bir bor yozing (tasdiqlash uchun):")


@router.message(Fz.pw2)
async def fz_pw2(m: Message, state: FSMContext):
    u = await active_user(m, state, allow_frozen=True)
    if not u:
        return
    pw = (m.text or "").strip()
    await try_delete(m)
    d = await state.get_data()
    if not d.get("salt"):
        await state.clear()
        return await m.answer("Sessiya tugagan. Qaytadan boshlang.", reply_markup=back_kb("fz:menu"))
    h = await asyncio.to_thread(hash_pw, pw, d["salt"])
    if not hmac.compare_digest(h, d["h"]):
        await state.set_state(Fz.pw1)
        return await m.answer("❌ Parollar mos kelmadi. Yangi parolni qaytadan yozing:")
    reset = bool(d.get("reset"))
    async with Session() as s:
        uu = await s.get(User, u.id)
        uu.freeze_salt, uu.freeze_hash = d["salt"], d["h"]
        if reset:
            uu.frozen = False
            uu.blocked_until = None
        await s.commit()
    await state.clear()
    if reset:
        await m.answer("✅ Yangi parol o'rnatildi, hisobingiz ochildi.")
        return await show_main(m, u.tg_id)
    await m.answer("✅ Parol o'rnatildi. Xohlasangiz, hisobni hozir muzlatishingiz mumkin.",
                   reply_markup=KB([[B("🧊 Hozir muzlatish", "fz:go")], [B("⬅️ Orqaga", "m:contract")]]))


@router.callback_query(F.data == "fz:go")
async def fz_go(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    if not u.freeze_hash:
        return await c.answer("Avval parol o'rnating", show_alert=True)
    async with Session() as s:
        uu = await s.get(User, u.id)
        uu.frozen = True
        await s.commit()
    await state.clear()
    await edit_or_send(c, "🧊 <b>Hisobingiz muzlatildi.</b>\nBarcha qiymatlar 0 ko'rinadi. Ochish uchun asosiy menyudan parolni kiriting.",
                       KB([[B("⬅️ Asosiy menyu", "m:main")]]))


@router.callback_query(F.data == "fz:open")
async def fz_open_ask(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    await state.set_state(Fz.open)
    await state.update_data(tries=0)
    await edit_or_send(c, "🔓 Parolni yozing:", KB([[B("🔄 Parolni tiklash", "fz:reset")], [B("⬅️ Orqaga", "m:main")]]))


@router.message(Fz.open)
async def fz_open(m: Message, state: FSMContext):
    u = await active_user(m, state, allow_frozen=True)
    if not u:
        return
    pw = (m.text or "").strip()
    await try_delete(m)
    if await verify_pw(u, pw):
        async with Session() as s:
            uu = await s.get(User, u.id)
            uu.frozen = False
            await s.commit()
        await state.clear()
        await m.answer("✅ Hisobingiz ochildi.")
        return await show_main(m, u.tg_id)
    tries = int((await state.get_data()).get("tries", 0)) + 1
    if tries >= 5:
        await state.clear()
        await block_user(u.tg_id)
        await m.answer("⛔ 5 marta noto'g'ri kiritdingiz. Xavfsizlik maqsadida 5 daqiqaga bloklandingiz.")
        return await show_main(m, u.tg_id)
    await state.update_data(tries=tries)
    await m.answer(f"❌ Parol noto'g'ri. Qolgan urinishlar: {5 - tries}")


@router.callback_query(F.data == "fz:reset")
async def fz_reset(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    if not u.freeze_hash:
        return await c.answer("Sizda parol o'rnatilmagan", show_alert=True)
    code = f"{secrets.randbelow(900000) + 100000}"
    await set_setting(f"reset_{u.tg_id}", f"{code}|{int(time.time()) + 900}")
    await notify_admins(f"🔄 <b>Parolni tiklash so'rovi</b>\n👤 ID: <code>{u.tg_id}</code> @{u.username or '-'} {u.phone}\n"
                        f"🔑 6 xonali kod: <code>{code}</code>\n\nShaxsiyligini tekshirib, kodni foydalanuvchiga yuboring "
                        "(15 daqiqa amal qiladi).")
    await state.set_state(Fz.code)
    await edit_or_send(c, "🔄 <b>Parolni tiklash</b>\n\nAdmin sizga 6 xonali kod yuboradi. Kodni shu yerga yozing:",
                       back_kb("m:main"))


@router.message(Fz.code)
async def fz_code(m: Message, state: FSMContext):
    u = await active_user(m, state, allow_frozen=True)
    if not u:
        return
    entered = (m.text or "").strip()
    await try_delete(m)
    raw = await get_str_setting(f"reset_{u.tg_id}", "")
    await state.clear()
    try:
        code, exp = raw.split("|")
        valid_time = int(exp) > time.time()
    except ValueError:
        code, valid_time = "", False
    if not raw or not valid_time:
        await m.answer("⌛ Kod muddati tugagan yoki so'ralmagan. Qaytadan «Parolni tiklash» ni bosing.")
        return await show_main(m, u.tg_id)
    if hmac.compare_digest(entered.encode(), code.encode()):
        await set_setting(f"reset_{u.tg_id}", "")  # kod bir marta ishlaydi
        await state.set_state(Fz.pw1)
        await state.update_data(reset=True)
        return await m.answer("✅ Kod to'g'ri. Endi <b>yangi parolni</b> yozing (4–32 belgi):")
    await set_setting(f"reset_{u.tg_id}", "")  # xato bo'lsa kod bekor qilinadi
    await block_user(u.tg_id)
    await m.answer("❌ Kod noto'g'ri. Xavfsizlik maqsadida 5 daqiqaga bloklandingiz.")
    await show_main(m, u.tg_id)


# ---------------------------------------------------------------- Anketa (Pul ishlash / NFT Sotish)
REGIONS = ["Toshkent shahri", "Toshkent viloyati", "Andijon", "Buxoro", "Farg'ona", "Jizzax", "Xorazm",
           "Namangan", "Navoiy", "Qashqadaryo", "Samarqand", "Sirdaryo", "Surxondaryo", "Qoraqalpog'iston"]
RANGES = {
    "earn": ["3–5 ta", "5–10 ta", "10–25 ta", "25–45 ta", "45 tadan yuqori"],
    "sell": ["25 000 – 100 000 so'm", "110 000 – 250 000 so'm", "250 000 so'mdan yuqori"],
}
SEQ = {"earn": ["region", "range"], "sell": ["range", "region"]}


class Ap(StatesGroup):
    first = State()
    last = State()
    birth = State()


def opt_kb(prefix: str, options: list[str]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, o in enumerate(options):
        row.append(B(o, f"ap:{prefix}:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("🚫 Bekor qilish", "m:contract")])
    return KB(rows)


def valid_name(s: str) -> bool:
    core = s.replace("'", "").replace("’", "").replace("ʻ", "").replace("`", "").replace("-", "").replace(" ", "")
    return 2 <= len(s) <= 40 and core.isalpha()


def app_text(a: Application, u: User) -> str:
    """Admin uchun ariza matni."""
    d = json.loads(a.data or "{}")
    title = "⚖️ Pul ishlash" if a.kind == "earn" else "🏷️ NFT Sotish"
    lines = [f"📝 <b>Ariza #{a.id}</b> — {title}",
             f"👤 {u.tg_id} @{u.username or '-'} {u.phone or ''}", "",
             f"Ism: {esc(d.get('first'))}", f"Familiya: {esc(d.get('last'))}",
             f"Tug'ilgan sana: {esc(d.get('birth'))}", f"Viloyat: {esc(d.get('region'))}"]
    lines.append(f"Tanishlar (1 oy ichida): {esc(d.get('range'))}" if a.kind == "earn"
                 else f"NFT narx diapazoni: {esc(d.get('range'))}")
    return "\n".join(lines)


async def last_app(user_id: int, kind: str) -> Application | None:
    async with Session() as s:
        return (await s.execute(select(Application).where(Application.user_id == user_id, Application.kind == kind)
                                .order_by(Application.id.desc()).limit(1))).scalar_one_or_none()


@router.callback_query(F.data.startswith("ap:start:"))
async def ap_start(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    kind = c.data.split(":")[2]
    if kind not in SEQ:
        return await c.answer()
    a = await last_app(u.id, kind)
    if a and a.status == "pending":
        return await c.answer("Arizangiz ko'rib chiqilmoqda ⏳", show_alert=True)
    await state.clear()
    await state.set_state(Ap.first)
    await state.update_data(kind=kind)
    await edit_or_send(c, "📝 <b>Anketa</b>\n\nIsmingizni yozing (haqiqiy ism):", back_kb("m:contract"))


@router.message(Ap.first)
async def ap_first(m: Message, state: FSMContext):
    if not await active_user(m, state):
        return
    v = (m.text or "").strip()
    if not valid_name(v):
        return await m.answer("❌ Ism faqat harflardan iborat bo'lsin (2–40 belgi). Qayta yozing:")
    await state.update_data(first=v)
    await state.set_state(Ap.last)
    await m.answer("Familiyangizni yozing:")


@router.message(Ap.last)
async def ap_last(m: Message, state: FSMContext):
    if not await active_user(m, state):
        return
    v = (m.text or "").strip()
    if not valid_name(v):
        return await m.answer("❌ Familiya faqat harflardan iborat bo'lsin (2–40 belgi). Qayta yozing:")
    await state.update_data(last=v)
    await state.set_state(Ap.birth)
    await m.answer("Tug'ilgan sanangizni yozing (kk.oo.yyyy), masalan: <code>25.03.1998</code>")


@router.message(Ap.birth)
async def ap_birth(m: Message, state: FSMContext):
    if not await active_user(m, state):
        return
    v = (m.text or "").strip()
    try:
        b = datetime.strptime(v, "%d.%m.%Y")
    except ValueError:
        return await m.answer("❌ Format noto'g'ri. Masalan: <code>25.03.1998</code>")
    t = datetime.utcnow().date()
    age = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    if age < 18:
        await state.clear()
        return await m.answer("❌ Bu bo'lim faqat 18 yoshdan oshganlar uchun.", reply_markup=back_kb("m:contract"))
    if age > 100:
        return await m.answer("❌ Sana noto'g'ri. Qayta yozing:")
    await state.update_data(birth=v)
    await ap_ask_next(m, state, m.from_user.id)


async def ap_ask_next(msg: Message, state: FSMContext, tg_id: int):
    d = await state.get_data()
    kind = d["kind"]
    for key in SEQ[kind]:
        if key not in d:
            if key == "region":
                return await msg.answer("📍 Viloyatingizni tanlang:", reply_markup=opt_kb("r", REGIONS))
            q = ("1 oy ichida nechta NFT oladigan tanishlar topa olasiz?" if kind == "earn"
                 else "NFT narx diapazonini tanlang:")
            return await msg.answer(q, reply_markup=opt_kb("q", RANGES[kind]))
    await ap_submit(msg, state, tg_id)


@router.callback_query(F.data.startswith("ap:r:") | F.data.startswith("ap:q:"))
async def ap_choice(c: CallbackQuery, state: FSMContext):
    if not await guard(c):
        return
    d = await state.get_data()
    if "kind" not in d or "birth" not in d:
        return await c.answer("Sessiya tugagan, anketani qaytadan boshlang", show_alert=True)
    _, key, idx = c.data.split(":")
    options = REGIONS if key == "r" else RANGES[d["kind"]]
    try:
        value = options[int(idx)]
    except (ValueError, IndexError):
        return await c.answer()
    await state.update_data(**{("region" if key == "r" else "range"): value})
    try:
        await c.message.edit_text(f"✅ {html.escape(value)}")
    except Exception:
        pass
    await c.answer()
    await ap_ask_next(c.message, state, c.from_user.id)


async def ap_submit(msg: Message, state: FSMContext, tg_id: int):
    d = await state.get_data()
    u = await get_user(tg_id)
    data = {k: d.get(k) for k in ("first", "last", "birth", "region", "range")}
    async with Session() as s:
        a = Application(user_id=u.id, kind=d["kind"], data=json.dumps(data, ensure_ascii=False))
        s.add(a)
        await s.commit()
    await state.clear()
    await notify_admins(app_text(a, u), KB([[B("✅ Tasdiqlash", f"app:ok:{a.id}"), B("❌ Rad etish", f"app:no:{a.id}")]]))
    await msg.answer("✅ Anketangiz adminga yuborildi. Javobni kuting — natija shu yerga xabar sifatida keladi.",
                     reply_markup=back_kb("m:contract"))


# ---------------------------------------------------------------- Pul ishlash
@router.callback_query(F.data == "earn:menu")
async def earn_menu(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    await state.clear()
    a = await last_app(u.id, "earn")
    tp, nf = await get_setting("ref_topup_pct"), await get_setting("ref_nft_pct")
    head = "⚖️ <b>Pul ishlash ⛏️</b>\n\n"
    if u.earner:
        n = await count_friends(u.tg_id)
        text = (head + f"✅ Faol.\nDo'stlaringiz: <b>{n}</b> ta\n\nUlush: hisob to'ldirishdan <b>{fmt_ton(tp)}%</b>, "
                f"NFT xariddan <b>{fmt_ton(nf)}%</b>.\n\nHavolangiz «👥 Do'stlarim» bo'limida.")
        rows = [[B("👥 Do'stlarim", "m:friends")], [B("⬅️ Orqaga", "m:contract")]]
    elif a and a.status == "pending":
        text, rows = head + "⏳ Arizangiz admin tomonidan ko'rib chiqilmoqda.", [[B("⬅️ Orqaga", "m:contract")]]
    elif a and a.status == "approved":
        text = head + "✅ Admin arizangizni tasdiqladi! Boshlash uchun pastdagi tugmani bosing."
        rows = [[B("✔️ Tasdiqlash", "earn:ok")], [B("⬅️ Orqaga", "m:contract")]]
    else:
        text = (head + "Do'stlaringizni botga taklif qiling. Ular hisobni to'ldirganda yoki NFT xarid qilganda "
                f"siz ulush olasiz: hisob to'ldirishdan {fmt_ton(tp)}%, NFT xariddan {fmt_ton(nf)}%.\n\n"
                "Boshlash uchun anketa to'ldiring va admin tasdig'ini kuting.")
        if a and a.status == "rejected":
            text += "\n\n❌ Oldingi arizangiz rad etilgan. Qaytadan ariza berishingiz mumkin."
        rows = [[B("📝 Anketa to'ldirish", "ap:start:earn")], [B("⬅️ Orqaga", "m:contract")]]
    await edit_or_send(c, text, KB(rows))


@router.callback_query(F.data == "earn:ok")
async def earn_ok(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    a = await last_app(u.id, "earn")
    if not a or a.status != "approved":
        return await c.answer("Avval admin tasdig'i kerak", show_alert=True)
    async with Session() as s:
        uu = await s.get(User, u.id)
        uu.earner = True
        await s.commit()
    await c.answer("✅ Faollashtirildi!", show_alert=True)
    await edit_or_send(c, "✅ <b>Pul ishlash faollashtirildi!</b>\nHavolangizni «👥 Do'stlarim» bo'limidan oling.",
                       KB([[B("👥 Do'stlarim", "m:friends")], [B("⬅️ Orqaga", "m:contract")]]))


# ---------------------------------------------------------------- NFT Sotish
class Of(StatesGroup):
    offer = State()


@router.callback_query(F.data == "sell:menu")
async def sell_menu(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    await state.clear()
    a = await last_app(u.id, "sell")
    head = "🏷️ <b>NFT Sotish</b>\n\n"
    if u.can_sell:
        text = head + "✅ Siz tasdiqlangan sotuvchisiz. NFT taklifini adminga yuborishingiz mumkin."
        rows = [[B("📤 NFT taklif qilish", "sell:offer")], [B("⬅️ Orqaga", "m:main")]]
    elif a and a.status == "pending":
        text, rows = head + "⏳ Arizangiz admin tomonidan ko'rib chiqilmoqda.", [[B("⬅️ Orqaga", "m:contract")]]
    else:
        text = (head + "O'z NFT laringizni sotish uchun anketa to'ldiring. Admin tasdiqlagandan so'ng, "
                "asosiy menyuda «🏷️ NFT Sotish» bo'limi paydo bo'ladi.")
        if a and a.status == "rejected":
            text += "\n\n❌ Oldingi arizangiz rad etilgan. Qaytadan ariza berishingiz mumkin."
        rows = [[B("📝 Anketa to'ldirish", "ap:start:sell")], [B("⬅️ Orqaga", "m:contract")]]
    await edit_or_send(c, text, KB(rows))


@router.callback_query(F.data == "sell:offer")
async def sell_offer(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    if not u.can_sell:
        return await c.answer("Sotuvchi huquqi yo'q", show_alert=True)
    await state.set_state(Of.offer)
    await edit_or_send(c, "📤 NFT haqida ma'lumot yuboring (rasm va tagiga nom, narx, tavsif).\n"
                          "Xabaringiz adminga yetkaziladi.", back_kb("sell:menu"))


@router.message(Of.offer)
async def sell_offer_msg(m: Message, state: FSMContext):
    u = await active_user(m, state)
    if not u:
        return
    if not u.can_sell:
        await state.clear()
        return
    await state.clear()
    await notify_admins(f"🏷️ <b>NFT taklifi</b>\n👤 {u.tg_id} @{u.username or '-'} {u.phone}")
    for a in await admin_ids():
        try:
            await bot.copy_message(a, m.chat.id, m.message_id)
        except Exception:
            pass
    await m.answer("✅ Taklifingiz adminga yuborildi.", reply_markup=back_kb("sell:menu"))


# =============================================================== Bonus olish (tugmali konkurs)
@router.callback_query(F.data.startswith("bn:ok:"))
async def bonus_claim(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    bid = int(c.data.split(":")[2])
    async with Session() as s:
        b = await s.get(Bonus, bid)
        if not b:
            return await c.answer("Bonus topilmadi", show_alert=True)
        s.add(BonusClaim(bonus_id=bid, user_id=u.id))
        try:
            await s.flush()
        except IntegrityError:  # PK: har kishi faqat bir marta
            await s.rollback()
            return await c.answer("Siz bu bonusni allaqachon olgansiz ✅", show_alert=True)
        await change_balance(s, u.id, b.currency, Decimal(b.amount), "bonus", note=f"bonus #{bid}")
        await s.commit()
    shown = fmt_ton(b.amount) if b.currency == "TON" else fmt(b.amount)
    await c.answer("🎁 Bonus hisobingizga qo'shildi!", show_alert=True)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.message.answer(f"✅ Bonus qabul qilindi: +{shown} {b.currency}", reply_markup=back_kb())


@router.callback_query(F.data.startswith("bn:no:"))
async def bonus_decline(c: CallbackQuery):
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.answer("Bonusdan voz kechdingiz")
