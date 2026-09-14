import pytest
pytest.importorskip("asyncpg")
pytest.importorskip("aiogram")

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import (  # noqa: E402
    ACTIVE_GENERATION_TASKS,
    ACTIVE_GENERATION_USERS,
    cancel_active_generation,
    start_background,
)


def test_generation_can_be_cancelled_and_releases_gate():
    async def scenario():
        user_id = 987654321
        started = asyncio.Event()

        async def fake_generation():
            started.set()
            await asyncio.Event().wait()

        task_started = start_background(fake_generation(), user_id)
        assert task_started is True
        await started.wait()
        assert user_id in ACTIVE_GENERATION_USERS
        assert user_id in ACTIVE_GENERATION_TASKS

        assert await cancel_active_generation(user_id) is True
        task = ACTIVE_GENERATION_TASKS[user_id]
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(0)
        assert user_id not in ACTIVE_GENERATION_USERS
        assert user_id not in ACTIVE_GENERATION_TASKS
        assert await cancel_active_generation(user_id) is False

    asyncio.run(scenario())
