import asyncio
import httpx
from app.config import settings


async def main() -> None:
    if not settings.bot_token or not settings.public_base_url:
        raise SystemExit("BOT_TOKEN و PUBLIC_BASE_URL را در .env تنظیم کنید")
    url = f"https://api.telegram.org/bot{settings.bot_token}/setWebhook"
    payload = {
        "url": f"{settings.public_base_url.rstrip('/')}/telegram/webhook",
        "secret_token": settings.webhook_secret,
        "drop_pending_updates": True,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        print(r.json())


if __name__ == "__main__":
    asyncio.run(main())
