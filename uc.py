"""PUBG UC vaucher ombori va yarim-avtomatik yetkazish.

Buyurtma kelganda admin ikki yo'ldan birini tanlaydi:
 🔑 «Men kiritaman» — bot ombordan kodni ajratib, PUBG ID bilan birga beradi; admin Midasbuy'da kiritadi va ✅ bosadi → mijozga «muvaffaqiyatli» xabari ketadi.
 📨 «Kodni mijozga yuborish» — bot kodni (nusxa olinadigan) va yo'riqnoma videosini mijozga yuboradi."""
import html
import logging
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from sqlalchemy import func, select

from admins import admin_ids, is_admin
from bot import bot, dp, fmt
from db import (Request, Session, UcCode, User, change_balance, get_str_setting,
                set_setting)

log = logging.getLogger(__name__)
PACKS = [60, 120, 180, 240, 325, 660, 1800]
LOW = 3        # ombordagi kod shundan kam qolsa admin ogohlantiriladi


def B(t, d):
    return InlineKeyboardButton(text=t, callback_data=d)


def KB(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def uc_kb(rid: int) -> InlineKeyboardMarkup:
    """Yangi UC buyurtma kelganda adminga ko'rsatiladigan tugmalar."""
    return KB([[B("🔑 Vaucher bilan men kiritaman", f"ucx:in:{rid}")],
               [B("📨 Kodni mijozga yuborish", f"ucx:send:{rid}")],
               [B("✅ Qo'lda yetkazdim", f"ad:ok:{rid}"), B("❌ Rad etish", f"ucx:no:{rid}")]])


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and await is_admin(event.from_user)


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
dp.include_router(router)


class U(StatesGroup):
    codes = State()
    video = State()


def parse(details: str | None):
    m = re.search(r"PUBG ID:\s*(\d+).*?UC:\s*(\d+)", details or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


async def reserve(s, rid: int, pack: int) -> UcCode | None:
    """Shu buyurtma uchun kodni ajratadi (qayta bosilsa, o'sha kodni qaytaradi)."""
    c = await s.scalar(select(UcCode).where(UcCode.request_id == rid, UcCode.status == "reserved"))
    if c:
        return c
    c = await s.scalar(select(UcCode).where(UcCode.pack == pack, UcCode.status == "free")
                       .order_by(UcCode.id).limit(1).with_for_update(skip_locked=True))
    if c:
        c.status, c.request_id = "reserved", rid
    return c


async def low_stock_alert(pack: int):
    async with Session() as s:
        n = await s.scalar(select(func.count(UcCode.id)).where(UcCode.pack == pack, UcCode.status == "free")) or 0
    if n < LOW:
        for a in await admin_ids():
            try:
                await bot.send_message(a, f"⚠️ {pack} UC vaucher ombori kam qoldi: <b>{n} ta</b>. /admin → 🎟 UC vaucherlar")
            except Exception:
                pass


async def load(rid: int):
    async with Session() as s:
        r = await s.get(Request, rid)
        u = await s.get(User, r.user_id) if r else None
    return r, u


# ------------------------------------------------------------------ buyurtma bilan ishlash
@router.callback_query(F.data.startswith("ucx:in:"))
async def take_in(c: CallbackQuery):
    rid = int(c.data.split(":")[2])
    async with Session() as s:
        r = await s.get(Request, rid, with_for_update=True)
        if not r or r.kind != "uc_order" or r.status != "pending":
            return await c.answer("Bu buyurtma allaqachon ko'rib chiqilgan", show_alert=True)
        pid, pack = parse(r.details)
        if not pid:
            return await c.answer("Buyurtma ma'lumoti o'qilmadi", show_alert=True)
        code = await reserve(s, rid, pack)
        if not code:
            return await c.answer(f"Omborda {pack} UC vaucher yo'q. /admin → 🎟 UC vaucherlar", show_alert=True)
        await s.commit()
    await c.answer()
    try:
        await c.message.edit_reply_markup(reply_markup=KB([[B("⏳ Jarayonda…", "noop")]]))
    except Exception:
        pass
    await c.message.answer(
        f"🔑 <b>Buyurtma #{rid}</b> — <b>{pack} UC</b>\n\nPUBG ID: <code>{pid}</code>\nVaucher: <code>{html.escape(code.code)}</code>\n\n"
        "Midasbuy'da shu ID ga vaucherni kiriting. UC tushgach pastdagi tugmani bosing.",
        reply_markup=KB([[B("✅ UC tushdi", f"ucx:ok:{rid}")], [B("⚠️ Kod ishlamadi — boshqasini ber", f"ucx:bad:{rid}")],
                         [B("❌ Rad etish (pulni qaytarish)", f"ucx:no:{rid}")]]))
    await low_stock_alert(pack)


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data.startswith("ucx:ok:"))
async def done(c: CallbackQuery):
    rid = int(c.data.split(":")[2])
    async with Session() as s:
        r = await s.get(Request, rid, with_for_update=True)
        if not r or r.status != "pending":
            return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        pid, pack = parse(r.details)
        r.status = "approved"
        code = await s.scalar(select(UcCode).where(UcCode.request_id == rid, UcCode.status == "reserved"))
        if code:
            code.status = "used"
        u = await s.get(User, r.user_id)
        await s.commit()
    try:
        await bot.send_message(u.tg_id, f"✅ <b>Muvaffaqiyatli amalga oshirildi!</b>\n\n{pack} UC PUBG ID <code>{pid}</code> hisobiga tushdi. "
                                        "O'yinga kirib tekshirib ko'ring. Rahmat! 🎮")
    except Exception:
        pass
    await c.answer("Mijozga xabar yuborildi")
    await c.message.edit_text((c.message.text or "") + "\n\n✅ Bajarildi, mijozga xabar yuborildi.", reply_markup=None)


@router.callback_query(F.data.startswith("ucx:bad:"))
async def bad(c: CallbackQuery):
    rid = int(c.data.split(":")[2])
    async with Session() as s:
        r = await s.get(Request, rid, with_for_update=True)
        if not r or r.status != "pending":
            return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        pid, pack = parse(r.details)
        old = await s.scalar(select(UcCode).where(UcCode.request_id == rid, UcCode.status == "reserved"))
        if old:
            old.status = "bad"
        code = await reserve(s, rid, pack)
        await s.commit()
    if not code:
        await c.answer("Omborda boshqa kod qolmadi", show_alert=True)
        return await c.message.edit_text((c.message.text or "") + "\n\n⚠️ Kod ishlamadi, omborda boshqa kod yo'q.",
                                         reply_markup=KB([[B("❌ Rad etish (pulni qaytarish)", f"ucx:no:{rid}")]]))
    await c.answer("Yangi kod berildi")
    await c.message.edit_text(
        f"🔑 <b>Buyurtma #{rid}</b> — <b>{pack} UC</b>\n\nPUBG ID: <code>{pid}</code>\nYangi vaucher: <code>{html.escape(code.code)}</code>",
        reply_markup=KB([[B("✅ UC tushdi", f"ucx:ok:{rid}")], [B("⚠️ Kod ishlamadi — boshqasini ber", f"ucx:bad:{rid}")],
                         [B("❌ Rad etish (pulni qaytarish)", f"ucx:no:{rid}")]]))
    await low_stock_alert(pack)


@router.callback_query(F.data.startswith("ucx:send:"))
async def send_code(c: CallbackQuery):
    rid = int(c.data.split(":")[2])
    async with Session() as s:
        r = await s.get(Request, rid, with_for_update=True)
        if not r or r.kind != "uc_order" or r.status != "pending":
            return await c.answer("Bu buyurtma allaqachon ko'rib chiqilgan", show_alert=True)
        pid, pack = parse(r.details)
        code = await reserve(s, rid, pack)
        if not code:
            return await c.answer(f"Omborda {pack} UC vaucher yo'q", show_alert=True)
        code.status = "used"
        r.status = "approved"
        u = await s.get(User, r.user_id)
        await s.commit()
    video = await get_str_setting("uc_video", "")
    text = (f"🎟 <b>{pack} UC vaucheringiz tayyor!</b>\n\nKod (ustiga bosing — nusxa olinadi):\n<code>{html.escape(code.code)}</code>\n\n"
            "Kodni Midasbuy'da «Redeem» bo'limida PUBG ID ingiz bilan kiriting. Qanday qilishni videoda ko'ring 👇")
    try:
        await bot.send_message(u.tg_id, text)
        if video:
            await bot.send_video(u.tg_id, video)
    except Exception:
        log.exception("vaucher mijozga yuborilmadi")
        async with Session() as s:     # yuborilmadi — kod va buyurtma qaytariladi
            code2 = await s.get(UcCode, code.id)
            r2 = await s.get(Request, rid)
            code2.status, r2.status = "reserved", "pending"
            await s.commit()
        return await c.answer("Mijozga yuborib bo'lmadi (botni bloklagan bo'lishi mumkin)", show_alert=True)
    await c.answer("Yuborildi")
    await c.message.edit_text((c.message.text or "") + f"\n\n📨 Kod mijozga yuborildi{'' if video else ' (video yuklanmagan)'}.", reply_markup=None)
    await low_stock_alert(pack)


@router.callback_query(F.data.startswith("ucx:no:"))
async def reject(c: CallbackQuery):
    rid = int(c.data.split(":")[2])
    async with Session() as s:
        r = await s.get(Request, rid, with_for_update=True)
        if not r or r.status != "pending":
            return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        r.status = "rejected"
        code = await s.scalar(select(UcCode).where(UcCode.request_id == rid, UcCode.status == "reserved"))
        if code:                        # ajratilgan kod omborga qaytadi
            code.status, code.request_id = "free", None
        await change_balance(s, r.user_id, "UZS", Decimal(r.amount), "refund", note=f"UC rad #{r.id}")
        u = await s.get(User, r.user_id)
        await s.commit()
    try:
        await bot.send_message(u.tg_id, f"#{rid}: ❌ UC buyurtma bekor qilindi, mablag' balansingizga qaytarildi.")
    except Exception:
        pass
    await c.answer("Pul qaytarildi")
    try:
        await c.message.edit_text((c.message.text or "") + "\n\n❌ Rad etildi, pul qaytarildi.", reply_markup=None)
    except Exception:
        pass


# ------------------------------------------------------------------ Ombor (admin panel)
async def stock_text() -> str:
    async with Session() as s:
        rows = (await s.execute(select(UcCode.pack, UcCode.status, func.count(UcCode.id)).group_by(UcCode.pack, UcCode.status))).all()
    d: dict[int, dict] = {}
    for pack, st, n in rows:
        d.setdefault(pack, {})[st] = n
    lines = [f"• <b>{p} UC</b>: bor {v.get('free', 0)} · jarayonda {v.get('reserved', 0)} · ishlatilgan {v.get('used', 0)}"
             + (f" · yaroqsiz {v['bad']}" if v.get("bad") else "") for p, v in sorted(d.items())]
    video = "✅ yuklangan" if await get_str_setting("uc_video", "") else "❌ yuklanmagan"
    return "🎟 <b>UC vaucherlar ombori</b>\n\n" + ("\n".join(lines) or "Hozircha bo'sh.") + f"\n\n🎬 Yo'riqnoma video: {video}"


def stock_kb():
    rows, row = [], []
    for p in PACKS:
        row.append(B(f"➕ {p} UC", f"ucs:add:{p}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("🎬 Video yuklash", "ucs:video")])
    rows.append([B("⬅️ Panel", "adm:main")])
    return KB(rows)


@router.callback_query(F.data == "adm:uc")
async def stock(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    try:
        await c.message.edit_text(await stock_text(), reply_markup=stock_kb())
    except Exception:
        await c.message.answer(await stock_text(), reply_markup=stock_kb())


@router.callback_query(F.data.startswith("ucs:add:"))
async def add_start(c: CallbackQuery, state: FSMContext):
    pack = int(c.data.split(":")[2])
    await c.answer()
    await state.set_state(U.codes)
    await state.update_data(pack=pack)
    await c.message.answer(f"<b>{pack} UC</b> vaucher kodlarini yuboring. Bir nechta bo'lsa, har birini yangi qatordan yozing.\nTugatish: /admin")


@router.message(U.codes, F.text, ~F.text.startswith("/"))
async def add_codes(m: Message, state: FSMContext):
    pack = (await state.get_data())["pack"]
    codes = list(dict.fromkeys(x.strip() for x in re.split(r"[\s,;]+", m.text) if 4 <= len(x.strip()) <= 80))
    if not codes:
        return await m.answer("Kod topilmadi. Har bir kodni yangi qatorga yozing.")
    async with Session() as s:
        have = set((await s.execute(select(UcCode.code).where(UcCode.code.in_(codes)))).scalars().all())
        new = [x for x in codes if x not in have]
        for x in new:
            s.add(UcCode(pack=pack, code=x))
        await s.commit()
    await m.answer(f"✅ {pack} UC: <b>{len(new)}</b> ta qo'shildi" + (f", {len(have)} ta takror o'tkazib yuborildi" if have else "") +
                   ".\nYana kod yuborishingiz mumkin yoki /admin.", reply_markup=KB([[B("🎟 Ombor", "adm:uc")]]))


@router.callback_query(F.data == "ucs:video")
async def video_start(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(U.video)
    await c.message.answer("🎬 Vaucherni qanday kiritishni ko'rsatuvchi <b>videoni</b> yuboring (mijozga kod bilan birga boradi).")


@router.message(U.video, F.video)
async def video_save(m: Message, state: FSMContext):
    await set_setting("uc_video", m.video.file_id)
    await state.clear()
    await m.answer("✅ Video saqlandi.", reply_markup=KB([[B("🎟 Ombor", "adm:uc")]]))


@router.message(U.video)
async def video_bad(m: Message):
    await m.answer("Iltimos, video yuboring (fayl emas, oddiy video).")
