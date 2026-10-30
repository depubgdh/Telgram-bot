"""Admin panel: hamma narsa Telegram ichida, tugmalar orqali.
Faqat admin (ADMIN_USERNAMES / ADMIN_IDS) foydalana oladi. /admin yoki asosiy menyudagi "👑 Admin panel"."""
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from sqlalchemy import func, or_, select

import config
from admins import is_admin
from bot import bot, dp, fmt, fmt_ton, header
from db import (Nft, Request, Session, Transaction, User, change_balance,
                get_setting, get_str_setting, set_setting)


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


PANEL = KB([
    [B("⏳ Kutayotgan so'rovlar", "adm:pending")],
    [B("👤 Foydalanuvchi / hisobni o'zgartirish", "adm:find")],
    [B("💱 Kurs, Komissiya va Rekvizitlar", "adm:settings")],
    [B("🎁 NFT boshqaruvi", "adm:nfts")],
    [B("📢 Majburiy kanal", "adm:channel")],
    [B("📊 Statistika", "adm:stats")],
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
    kind = "💳 To'ldirish" if r.kind == "topup" else "💸 Yechish"
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
    blk = "ha" if u.banned else "yo'q"
    return (f"👤 <b>{u.tg_id}</b> @{u.username or '-'}\n📞 {u.phone or '-'}\n\n{await header(u)}\n"
            f"🎮 UC: {fmt(u.uc)}\n🚫 Bloklangan: {blk}")


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
    cur_rows = [[B(cur, f"adm:cur:{uid}:{sign}:{cur}") for cur in ("UZS", "STARS", "TON", "UC", "J_COIN")],
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
    if cur not in ("TON", "J_COIN"):
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
    shown = fmt_ton(amt) if cur in ("TON", "J_COIN") else fmt(amt)
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


# ------------------------------------------------------------ Kurs, Komissiya va Rekvizitlar
LABELS = {
    "stars_uzs": "1 Star = so'm",
    "ton_uzs": "1 TON = so'm",
    "uc_uzs": "1 UC = so'm",
    "j_rate": "1 J🧧 = so'm (Hozir: 1200)",
    "admin_ton_wallet": "Admin TON hamyon manzili",
    "admin_card_number": "Admin plastik karta raqami",
    "topup_card_fee": "Karta to'ldirish komissiyasi %",
    "withdraw_card_fee": "Kartaga yechish %",
    "withdraw_phone_fee": "Telefonga yechish %",
    "withdraw_nft_fee": "NFT yechish %",
    "min_withdraw_uc": "Min. UC yechish",
    "min_withdraw_stars": "Min. Stars yechish",
}


async def settings_view():
    rows = []
    for k, label in LABELS.items():
        val = await get_str_setting(k, "Kiritilmagan")
        rows.append([B(f"{label}: {val if k in ('admin_ton_wallet', 'admin_card_number') else fmt_ton(val)}", f"adm:set:{k}")])
    rows.append([B("⬅️ Panel", "adm:main")])
    return "💱 <b>Kurs, Komissiya va Rekvizitlar</b>\nO'zgartirish uchun tanlang:", KB(rows)


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
    current_val = await get_str_setting(key, "-")
    await show(c, f"<b>{LABELS[key]}</b>\nHozirgi qiymat: {current_val}\n\nYangi qiymatni yuboring:",
               back("adm:settings"))


@router.message(A.setting)
async def adm_set_apply(m: Message, state: FSMContext):
    d = await state.get_data()
    key = d["key"]
    val_text = (m.text or "").strip()
    
    if key in ("admin_ton_wallet", "admin_card_number"):
        # Matnli rekvizitlar uchun
        await set_setting(key, val_text)
    else:
        # Raqamli qiymatlar uchun
        try:
            v = Decimal(val_text.replace(",", ".").replace(" ", ""))
            assert v >= 0
            await set_setting(key, str(v))
        except (InvalidOperation, AssertionError):
            return await m.answer("To'g'ri son kiriting.")
            
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
async def adm_nft(c: CallbackQuery):
    await c.answer()
    nid = int(c.data.split(":")[2])
    async with Session() as s:
        n = await s.get(Nft, nid)
    await show(c, f"<b>{n.title}</b>\n💰 {fmt(n.price_uzs)} so'm | {n.price_stars} ⭐", KB([
        [B("✏️ So'm narxi", f"adm:nftp:{nid}:uzs"), B("✏️ Stars narxi", f"adm:nftp:{nid}:stars")],
        [B("🗑 O'chirish", f"adm:nftdel:{nid}")],
        [B("⬅️ Orqaga", "adm:nfts")]]))


@router.callback_query(F.data.startswith("adm:nftp:"))
async def adm_nft_price(c: CallbackQuery, state: FSMContext):
    await c.answer()
    _, _, nid, cur = c.data.split(":")
    await state.set_state(A.nft_price)
    await state.update_data(nid=int(nid), cur=cur)
    await show(c, f"Yangi narxni yuboring ({'so`m' if cur == 'uzs' else 'Stars'}):", back("adm:nfts"))


@router.message(A.nft_price)
async def adm_nft_price_apply(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        v = int((m.text or "").replace(" ", ""))
        assert v >= 0
    except (ValueError, AssertionError):
        return await m.answer("Butun son kiriting.")
    async with Session() as s:
        n = await s.get(Nft, d["nid"])
        if d["cur"] == "uzs":
            n.price_uzs = v
        else:
            n.price_stars = v
        await s.commit()
    await state.clear()
    await m.answer("✅ Narx yangilandi.", reply_markup=back("adm:nfts"))


@router.callback_query(F.data.startswith("adm:nftdel:"))
async def adm_nft_del(c: CallbackQuery):
    nid = int(c.data.split(":")[2])
    async with Session() as s:
        n = await s.get(Nft, nid)
        n.active = False
        await s.commit()
    await c.answer("O'chirildi")
    await show(c, "🗑 NFT o'chirildi.", back("adm:nfts"))


@router.callback_query(F.data == "adm:nftadd")
async def adm_nft_add(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(A.nft_add)
    await show(c, "NFT rasmini yuboring va rasm tagiga (caption) shunday yozing:\n\n"
                  "<code>Nomi | narx so'mda | narx Stars da</code>\n\nMasalan: <code>Plush Pepe | 500000 | 2000</code>",
               back("adm:nfts"))


@router.message(A.nft_add)
async def adm_nft_add_apply(m: Message, state: FSMContext):
    text = (m.caption or m.text or "").strip()
    try:
        title, p_uzs, p_st = [x.strip() for x in text.split("|")]
        n = Nft(title=title, price_uzs=int(p_uzs.replace(" ", "")), price_stars=int(p_st.replace(" ", "")),
                photo_file_id=m.photo[-1].file_id if m.photo else None)
    except Exception:
        return await m.answer("Format xato. Misol: Plush Pepe | 500000 | 2000")
    async with Session() as s:
        s.add(n)
        await s.commit()
    await state.clear()
    await m.answer("✅ NFT qo'shildi.", reply_markup=back("adm:nfts"))


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
        j_coin = await s.scalar(select(func.coalesce(func.sum(User.j_coin), 0)))
    await show(c, f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {users} (bloklangan: {banned})\n"
                  f"⏳ Kutayotgan so'rovlar: {pend}\n\n<b>Foydalanuvchilar balansi yig'indisi:</b>\n"
                  f"🇺🇿 {fmt(uzs)} so'm\n⭐️ {fmt(stars)} Stars\n🪙 {fmt_ton(ton)} TON\n🧧 {fmt_ton(j_coin)} J🧧\n🎮 {fmt(uc)} UC", back())
