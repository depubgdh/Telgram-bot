"""Bot (polling) va Mini App serverini bitta jarayonda ishga tushiradi."""
import asyncio
import logging

import uvicorn

import config
from bot import bot, dp
from db import init_db
from web import app


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=config.PORT, log_level="info"))
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_join_request",
                                               "pre_checkout_query"]),
    )


if __name__ == "__main__":
    asyncio.run(main())
