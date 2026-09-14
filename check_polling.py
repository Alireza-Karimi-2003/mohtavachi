import asyncio
import httpx
from app.config import settings

async def main():
    url = f"https://api.telegram.org/bot{settings.bot_token}/getUpdates"

    try:
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.get(
                url,
                params={"timeout": 5},
            )
            print("HTTP:", r.status_code)
            print("Response:", r.text[:1000])
    except Exception as e:
        print("FAIL:", type(e).__name__)
        print("Error:", str(e))

if __name__ == "__main__":
    asyncio.run(main())