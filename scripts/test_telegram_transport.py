import asyncio
from unittest.mock import AsyncMock

from app import telegram


async def main() -> None:
    original_get_bot = telegram.get_bot

    class FakeMessage:
        def model_dump(self, **kwargs):
            return {"message_id": 123, "chat": {"id": 456}, "text": "ok"}

    class FakeBot:
        def __init__(self):
            self.send_message = AsyncMock(return_value=FakeMessage())
            self.delete_message = AsyncMock(return_value=True)
            self.send_photo = AsyncMock(return_value=True)
            self.answer_callback_query = AsyncMock(return_value=True)
            self.session = type("Session", (), {"close": AsyncMock()})()

    fake = FakeBot()
    telegram.get_bot = lambda: fake
    try:
        result = await telegram.send_message(456, "hello", {"keyboard": []})
        assert result["message_id"] == 123
        fake.send_message.assert_awaited_once()

        await telegram.delete_message(456, 123)
        fake.delete_message.assert_awaited_once()

        await telegram.send_photo_bytes(456, b"png-data", "caption")
        fake.send_photo.assert_awaited_once()

        await telegram.answer_callback("callback-1", "ok")
        fake.answer_callback_query.assert_awaited_once()

        print("Telegram transport wrapper tests: PASS")
    finally:
        telegram.get_bot = original_get_bot


if __name__ == "__main__":
    asyncio.run(main())
