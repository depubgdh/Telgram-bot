"""Sotib olingan NFT sovg'ani «Chiqarib olish»: o'ziga yoki boshqa username'ga.

Oqim: Buyum → Chiqarib olish → (o'zimga | username) → profil ko'rsatiladi «Shu profilmi?» → Ha →
adminga xabar: qaysi username'ga va qaysi sovg'a silkasini yuborish kerakligi → admin «Yuborildi» bosadi.
Mini App ham, bot ham shu yerdagi funksiyalardan foydalanadi."""
import html
import io
import logging
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from sqlalchemy import select

from admins import admin_ids, is_admin
from bot import bot, dp, edit_or_send, guard
from db import Nft, Session, User, UserItem, get_user

log = logging.getLogger(__name__)
router = Router()
dp.include_router(router)

UNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def norm_username(text: str | None) -> str | None:
    """'@ali', 't.me/ali', 'https://t.me/ali' -> 'ali'. Noto'g'ri bo'lsa None."""
    t = (text or "").strip()
    t = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", t, flags=re.I).lstrip("@").split("?")[0].strip("/")
    return t if UNAME_RE.fullmatch(t) else None


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ------------------------------------------------------------------ profil qidirish
async def lookup_profile(username: str) -> dict:
    """Username bo'yicha profil. Telegram bot'ga faqat ba'zi profillarni ko'rsatadi:
    botga kirgan foydalanuvchilar to'liq ko'rinadi, boshqalar ko'rinmasligi mumkin (found=False)."""
    info = {"username": username, "found": False, "name": "", "bio": "", "has_photo": False, "tg_id": None}
    chat = None
    async with Session() as s:
        u = (await s.execute(select(User).where(User.username.ilike(username)))).scalars().first()
    if u:
        try:
            chat = await bot.get_chat(u.tg_id)
        except Exception:
            chat = None
    if chat is None:
        try:
            chat = await bot.get_chat("@" + username)
        except Exception:
            chat = None
    if chat is None or getattr(chat, "type", "") != "private":
        return info
    info.update(found=True, tg_id=chat.id,
                name=" ".join(x for x in (chat.first_name, chat.last_name) if x),
                bio=(getattr(chat, "bio", None) or "")[:200],
                has_photo=bool(getattr(chat, "photo", None)))
    info["_photo_id"] = chat.photo.big_file_id if getattr(chat, "photo", None) else None
    return info


async def profile_photo(username: str) -> bytes | None:
    info = await lookup_profile(username)
    fid = info.get("_photo_id")
    if not fid:
        return None
    try:
        buf = io.BytesIO()
        await bot.download(fid, destination=buf)
        return buf.getvalue()
    except Exception:
        return None


def public_info(info: dict) -> dict:
    return {k: v for k, v in info.items() if not k.startswith("_")}


# ------------------------------------------------------------------ so'rov yuborish (bot va Mini App uchun umumiy)
class GiftError(Exception):
    pass


async def request_gift(user: User, item_id: int, username: str) -> UserItem:
    uname = norm_username(username)
    if not uname:
        raise GiftError("Username noto'g'ri. Masalan: @ali_123 (kamida 5 belgi)")
    async with Session() as s:
        it = await s.get(UserItem, item_id, with_for_update=True)
        if not it or it.user_id != user.id:
            raise GiftError("Buyum topilmadi")
        if it.gift_status != "owned":
            raise GiftError("Bu buyum uchun so'rov allaqachon yuborilgan")
        link = it.link
        if not link and it.nft_id:
            n = await s.get(Nft, it.nft_id)
            link = n.link if n else None
        it.link = link
        it.gift_status, it.gift_to, it.gift_at = "requested", uname, datetime.utcnow()
        await s.commit()
    text = (f"📤 <b>Sovg'ani yuborish so'rovi #{it.id}</b>\n\n"
            f"🎁 {html.escape(it.title)}\n"
            f"🔗 Sovg'a silkasi: {html.escape(link) if link else '⚠️ silka kiritilmagan (NFT boshqaruvida «🔗 Havola» qo`shing)'}\n\n"
            f"➡️ Yuboriladigan akkaunt: <b>@{uname}</b>\n"
            f"👤 Xaridor: {user.tg_id} @{user.username or '-'} {user.phone or ''}")
    rows = [[InlineKeyboardButton(text=f"👤 @{uname} profili", url=f"https://t.me/{uname}")]]
    if link and link.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🎁 Sovg'ani ochish", url=link)])
    rows.append([_b("✅ Yuborildi", f"gf:done:{it.id}")])
    for a in await admin_ids():
        try:
            await bot.send_message(a, text, reply_markup=_kb(rows))
        except Exception:
            log.warning("Adminga sovg'a so'rovi yuborilmadi: %s", a)
    return it


# ------------------------------------------------------------------ admin: «Yuborildi»
@router.callback_query(F.data.startswith("gf:done:"))
async def gift_done(c: CallbackQuery):
    if not await is_admin(c.from_user):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    iid = int(c.data.split(":")[2])
    async with Session() as s:
        it = await s.get(UserItem, iid, with_for_update=True)
        if not it:
            return await c.answer("Topilmadi", show_alert=True)
        if it.gift_status == "sent":
            return await c.answer("Allaqachon yuborilgan deb belgilangan", show_alert=True)
        it.gift_status = "sent"
        owner = await s.get(User, it.user_id)
        title, to = it.title, it.gift_to
        await s.commit()
    try:
        await bot.send_message(owner.tg_id, f"✅ <b>{html.escape(title)}</b> sovg'asi <b>@{to}</b> akkauntiga yuborildi!")
    except Exception:
        pass
    try:
        await c.message.edit_reply_markup(reply_markup=_kb([[_b("✅ Yuborilgan", "gf:noop")]]))
    except Exception:
        pass
    await c.answer("Belgilandi")


@router.callback_query(F.data == "gf:noop")
async def gift_noop(c: CallbackQuery):
    await c.answer()


# ------------------------------------------------------------------ bot oqimi (foydalanuvchi)
class Gf(StatesGroup):
    username = State()


def own_item_kb(item_id: int) -> InlineKeyboardMarkup:
    return _kb([[_b("⬅️ Orqaga", f"itm:v:{item_id}")]])


@router.callback_query(F.data.startswith("gf:start:"))
async def gift_start(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    await state.clear()
    iid = int(c.data.split(":")[2])
    async with Session() as s:
        it = await s.get(UserItem, iid)
    if not it or it.user_id != u.id or it.gift_status != "owned":
        return await c.answer("Bu buyum uchun amal mavjud emas", show_alert=True)
    rows = []
    if u.username:
        rows.append([_b(f"👤 O'zimga (@{u.username})", f"gf:pick:{iid}:{u.username}")])
    rows.append([_b("✏️ Boshqa username", f"gf:other:{iid}")])
    rows.append([_b("⬅️ Orqaga", f"itm:v:{iid}")])
    note = "" if u.username else "\n\n⚠️ Telegram'da username'ingiz yo'q, shuning uchun «O'zimga» tugmasi ko'rinmaydi."
    await edit_or_send(c, f"📤 <b>Chiqarib olish</b>\n🎁 {html.escape(it.title)}\n\nSovg'ani qaysi akkauntga yuboramiz?{note}", _kb(rows))


@router.callback_query(F.data.startswith("gf:other:"))
async def gift_other(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    iid = int(c.data.split(":")[2])
    await state.set_state(Gf.username)
    await state.update_data(iid=iid)
    await edit_or_send(c, "✏️ Sovg'a yuboriladigan odamning <b>username</b>'ini yozing (masalan: <code>@ali_123</code>):", own_item_kb(iid))


async def show_preview(msg: Message, iid: int, uname: str):
    info = await lookup_profile(uname)
    if info["found"]:
        cap = (f"👤 <b>{html.escape(info['name'] or '—')}</b>\n@{uname}\n"
               + (f"📝 {html.escape(info['bio'])}\n" if info["bio"] else "")
               + "\n<b>Shu profilmi?</b>")
    else:
        cap = (f"@{uname}\n\n⚠️ Bot bu profilni ko'ra olmadi (Telegram cheklovi). "
               f"«👤 Profilni ko'rish» tugmasi orqali o'zingiz tekshiring.\n\n<b>Shu profilmi?</b>")
    rows = [[_b("✅ Ha, shu", f"gf:ok:{iid}:{uname}")]]
    if not info["found"]:
        rows.insert(0, [InlineKeyboardButton(text="👤 Profilni ko'rish", url=f"https://t.me/{uname}")])
    rows.append([_b("✏️ Boshqa username", f"gf:other:{iid}"), _b("❌ Bekor", f"itm:v:{iid}")])
    photo = await profile_photo(uname) if info["found"] and info["has_photo"] else None
    if photo:
        try:
            return await msg.answer_photo(BufferedInputFile(photo, "p.jpg"), caption=cap, reply_markup=_kb(rows))
        except Exception:
            pass
    await msg.answer(cap, reply_markup=_kb(rows))


@router.message(Gf.username)
async def gift_username(m: Message, state: FSMContext):
    u = await get_user(m.from_user.id)
    if not u or not u.phone or u.banned or u.frozen:
        await state.clear()
        return await m.answer("Avval /start bosing.")
    uname = norm_username(m.text)
    if not uname:
        return await m.answer("Username noto'g'ri. Masalan: <code>@ali_123</code> (kamida 5 belgi). Qayta yozing:")
    iid = (await state.get_data()).get("iid")
    await state.clear()
    await show_preview(m, iid, uname)


@router.callback_query(F.data.startswith("gf:pick:"))
async def gift_pick(c: CallbackQuery, state: FSMContext):
    u = await guard(c)
    if not u:
        return
    _, _, iid, uname = c.data.split(":", 3)
    await state.clear()
    await c.answer()
    await show_preview(c.message, int(iid), uname)


@router.callback_query(F.data.startswith("gf:ok:"))
async def gift_ok(c: CallbackQuery):
    u = await guard(c)
    if not u:
        return
    _, _, iid, uname = c.data.split(":", 3)
    try:
        it = await request_gift(u, int(iid), uname)
    except GiftError as e:
        return await c.answer(str(e), show_alert=True)
    await c.answer("✅ Yuborildi")
    text = (f"✅ So'rov adminga yuborildi.\n🎁 {html.escape(it.title)} → <b>@{it.gift_to}</b>\n"
            "Admin sovg'ani yuborgach, sizga xabar keladi.")
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.message.answer(text, reply_markup=_kb([[_b("📦 Mening buyumlarim", "m:items")]]))
