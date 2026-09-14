import pytest
pytest.importorskip("asyncpg")
pytest.importorskip("aiogram")

import asyncio

import app.main as main


def test_polling_loop_uses_long_polling_and_aliases(monkeypatch):
    calls = []

    class FakeSession:
        async def close(self):
            calls.append(("close",))

    class FakeBot:
        def __init__(self, token):
            assert token == main.settings.bot_token
            self.session = FakeSession()

        async def get_updates(self, **kwargs):
            calls.append(("get_updates", kwargs))
            raise asyncio.CancelledError

    handled = []

    async def fake_handle_update(update):
        handled.append(update)

    monkeypatch.setattr(main, "Bot", FakeBot)
    monkeypatch.setattr(main, "handle_update", fake_handle_update)

    async def scenario():
        with __import__("pytest").raises(asyncio.CancelledError):
            await main.polling_loop()

    asyncio.run(scenario())

    assert calls[0] == (
        "get_updates",
        {
            "offset": None,
            "timeout": 30,
            "allowed_updates": ["message", "callback_query"],
        },
    )
    assert calls[-1] == ("close",)
