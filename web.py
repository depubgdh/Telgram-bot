"""Mini App backend (FastAPI): balans, tarix, chek yuklash."""
import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from aiogram.types import BufferedInputFile
from sqlalchemy import select

import config
from bot import bot, create_card_topup
from db import Session, Transaction, get_user

app = FastAPI()
INDEX = Path(__file__).resolve().parent / "index.html"


def verify_init_data(init_data: str) -> dict:
    """Telegram Mini App initData imzosini tekshiradi (soxta so'rovlardan himoya)."""
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    recv_hash = data.pop("hash", None)
    if not recv_hash:
        raise HTTPException(401, "no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, recv_hash):
        raise HTTPException(401, "bad signature")
    return json.loads(data["user"])


@app.get("/")
async def index():
    return FileResponse(INDEX)


@app.get("/api/me")
async def me(x_init_data: str = Header(...)):
    tg = verify_init_data(x_init_data)
    u = await get_user(tg["id"])
    if not u or not u.phone:
        raise HTTPException(403, "Avval botda ro'yxatdan o'ting")
    async with Session() as s:
        tx = (await s.execute(select(Transaction).where(Transaction.user_id == u.id)
                              .order_by(Transaction.id.desc()).limit(30))).scalars().all()
    return {
        "stars": u.stars, "ton": str(u.ton), "uzs": u.uzs, "uc": u.uc,
        "card": config.CARD_NUMBER, "card_owner": config.CARD_OWNER,
        "history": [{"t": f"{t.created_at:%d.%m %H:%M}", "kind": t.kind,
                     "amount": str(t.amount), "cur": t.currency} for t in tx],
    }


@app.post("/api/receipt")
async def receipt(x_init_data: str = Header(...), amount: int = Form(...), file: UploadFile = File(...)):
    tg = verify_init_data(x_init_data)
    u = await get_user(tg["id"])
    if not u or not u.phone or u.banned:
        raise HTTPException(403, "ruxsat yo'q")
    if amount <= 0 or amount > 1_000_000_000:
        raise HTTPException(400, "summa noto'g'ri")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "fayl 10MB dan katta")
    # faylni bot orqali foydalanuvchining o'ziga yuborib file_id olamiz
    msg = await bot.send_document(tg["id"], BufferedInputFile(content, filename=file.filename or "check.jpg"),
                                  caption="📎 Chek qabul qilindi, admin tekshiradi.")
    await create_card_topup(tg["id"], Decimal(amount), msg.document.file_id)
    return {"ok": True}
