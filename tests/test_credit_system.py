import asyncio
import pytest

pytest.importorskip("asyncpg")

from app.db import User, Generation
from app.services import GEN_COST, STUDIO_PLUS_GEN_COST, record_generation


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def scalar(self, *args, **kwargs):
        return None

    async def get(self, *args, **kwargs):
        return None


def make_user(credits=15, plan="starter"):
    return User(
        id=1, telegram_id=123, referral_code="u123", credits=credits, plan=plan,
        tone="صمیمی و حرفه‌ای", daily_image_free_used=0, streak_days=0,
    )


def test_successful_generation_deducts_current_cost_once():
    async def scenario():
        session = FakeSession()
        user = make_user(credits=20)
        generation = await record_generation(session, user, "caption", "test", "output")
        assert user.credits == 18
        assert generation.cost_credits == GEN_COST["caption"]
        assert generation in session.added

    asyncio.run(scenario())


def test_studio_plus_uses_separate_costs():
    async def scenario():
        session = FakeSession()
        user = make_user(credits=20, plan="studio_plus")
        generation = await record_generation(session, user, "post", "test", "output", premium=True)
        assert user.credits == 20 - STUDIO_PLUS_GEN_COST["post"]
        assert generation.cost_credits == STUDIO_PLUS_GEN_COST["post"]

    asyncio.run(scenario())
