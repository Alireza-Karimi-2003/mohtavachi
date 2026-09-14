import asyncio

from aiogram import Bot
from app.config import settings


async def main():
    bot = Bot(token=settings.bot_token)

    try:
        me = await bot.get_me()
        print("Telegram Bot API: PASS")
        print("Bot username:", me.username)
        print("Bot ID:", me.id)
    except Exception as e:
        print("Telegram Bot API: FAIL")
        print(type(e).__name__)
        print(str(e))
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())