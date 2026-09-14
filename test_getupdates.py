import asyncio
import traceback

from aiogram import Bot
from app.config import settings


async def main():
    bot = Bot(token=settings.bot_token)

    try:
        print("TEST: getMe")
        me = await bot.get_me()
        print("getMe PASS:", me.username)

        print("TEST: getUpdates")
        updates = await bot.get_updates(timeout=1)
        print("getUpdates PASS")
        print("Updates:", len(updates))

    except Exception as exc:
        print("\ngetUpdates FAILED")
        print(type(exc).__name__, exc)
        traceback.print_exc()

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())