import pytest
pytest.importorskip("asyncpg")
pytest.importorskip("aiogram")

import asyncio
from datetime import timedelta

from app.db import User
from app.services import (
    daily_free_image_limit,
    daily_free_images_remaining,
    record_generation,
    STUDIO_PLUS_GEN_COST,
)
import app.services as services
import app.main as main


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    async def scalar(self, query):
        return None

    async def get(self, model, ident):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def make_user(plan, credits=100):
    return User(
        id=1,
        telegram_id=123456789,
        referral_code="u123456789",
        plan=plan,
        credits=credits,
        daily_free_used=0,
        daily_image_free_used=0,
        streak_days=0,
    )


def test_paid_daily_free_image_limits_reset_by_date():
    starter = make_user("starter")
    pro = make_user("pro")
    studio = make_user("studio")
    free = make_user("free")

    assert daily_free_image_limit(starter) == 0
    assert daily_free_image_limit(pro) == 1
    assert daily_free_image_limit(studio) == 2
    assert daily_free_image_limit(free) == 0

    assert daily_free_images_remaining(starter) == 0
    assert daily_free_images_remaining(studio) == 2

    from app.services import today
    starter.daily_image_free_date = today()
    starter.daily_image_free_used = 1
    assert daily_free_images_remaining(starter) == 0

    starter.daily_image_free_date = (services.date.today() - timedelta(days=1)).isoformat()
    assert daily_free_images_remaining(starter) == 0


def test_starter_pays_credits_for_every_image():
    async def scenario():
        user = make_user("starter", credits=50)
        session = FakeSession()

        first = await record_generation(session, user, "image", "first", "[image generated]")
        assert user.credits == 40
        assert user.daily_image_free_used == 0
        assert first.cost_credits == 10

    asyncio.run(scenario())


def test_studio_gets_two_free_images_then_credit_cost():
    async def scenario():
        user = make_user("studio", credits=50)
        session = FakeSession()

        first = await record_generation(session, user, "image", "1", "[image generated]")
        second = await record_generation(session, user, "image", "2", "[image generated]")
        third = await record_generation(session, user, "image", "3", "[image generated]")

        assert user.credits == 40
        assert user.daily_image_free_used == 2
        assert first.cost_credits == 0
        assert second.cost_credits == 0
        assert third.cost_credits == 10

    asyncio.run(scenario())


def test_studio_plus_uses_separate_image_credit_cost_after_free_quota():
    async def scenario():
        user = make_user("studio_plus", credits=20)
        session = FakeSession()

        first = await record_generation(session, user, "image", "1", "[image generated]", premium=True)
        second = await record_generation(session, user, "image", "2", "[image generated]", premium=True)
        third = await record_generation(session, user, "image", "3", "[image generated]", premium=True)

        assert first.cost_credits == 0
        assert second.cost_credits == 0
        assert third.cost_credits == STUDIO_PLUS_GEN_COST["image"]
        assert user.credits == 15

    asyncio.run(scenario())


def test_free_user_never_gets_paid_plan_free_image():
    async def scenario():
        user = make_user("free", credits=15)
        session = FakeSession()

        generation = await record_generation(session, user, "image", "1", "[image generated]")

        assert user.credits == 5
        assert user.daily_image_free_used == 0
        assert generation.cost_credits == 10

    asyncio.run(scenario())


def test_safe_send_returns_last_telegram_message(monkeypatch):
    async def fake_send_message(*args, **kwargs):
        return {"message_id": 77}

    monkeypatch.setattr(main, "send_message", fake_send_message)

    async def scenario():
        result = await main.safe_send(123, "hello")
        assert result == {"message_id": 77}

    asyncio.run(scenario())


def test_processing_message_can_be_cleared(monkeypatch):
    deleted = []

    async def fake_delete_message(chat_id, message_id):
        deleted.append((chat_id, message_id))

    monkeypatch.setattr(main, "delete_message", fake_delete_message)
    main.PROCESSING_MESSAGES[123] = (456, 789)

    async def scenario():
        await main.clear_processing_message(123)
        assert deleted == [(456, 789)]
        assert 123 not in main.PROCESSING_MESSAGES

    asyncio.run(scenario())
