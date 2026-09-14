from __future__ import annotations

import asyncio

from aiogram import Bot

from app.config import settings


BOT_DESCRIPTION = (
    "✨ محتواچی؛ کارخونه محتوای برندت در تلگرام.\n\n"
    "✍️ کپشن و پست\n"
    "📱 استوری و Reel\n"
    "💡 ایده و مسیر محتوا\n"
    "📦 بسته هفتگی و کمپین\n"
    "🖼️ تولید تصویر\n"
    "🔄 بازنویسی و تبدیل محتوا"
)

BOT_SHORT_DESCRIPTION = "✨ تولید محتوای حرفه‌ای برای برندت؛ مستقیم داخل تلگرام."


async def main() -> None:
    bot = Bot(token=settings.bot_token)
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION, language_code="fa")
        await bot.set_my_description(description=BOT_DESCRIPTION, language_code="fa")
        print("Bot profile description: PASS")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
