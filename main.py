"""Bot (polling) va Mini App serverini bitta jarayonda ishga tushiradi."""
import asyncio
import logging

import uvicorn

import config
from bot import bot, dp
from db import init_db
from web import app
import admin  # noqa: F401  (admin panel handlerlarini ro'yxatdan o'tkazadi)
import features  # noqa: F401  (foydalanuvchi bo'limlari)


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    if config.DATABASE_URL.startswith("sqlite"):
        logging.warning("DIQQAT: DATABASE_URL berilmagan! Ma'lumotlar vaqtinchalik faylda saqlanmoqda va qayta deploydan keyin YO'QOLADI.")
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=config.PORT, log_level="info"))
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_join_request",
                                               "pre_checkout_query"]),
    )


if __name__ == "__main__":
    asyncio.run(main())
