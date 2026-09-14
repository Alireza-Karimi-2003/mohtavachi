import pytest
pytest.importorskip("asyncpg")
pytest.importorskip("aiogram")

import asyncio

from app.db import User
import app.main as main
from app.telegram import main_menu, profile_menu, profile_optional_menu, inline_generation_actions
from app.services import profile_complete
from app.ai import build_prompt


def make_user(**kwargs):
    data = dict(
        id=1, telegram_id=123, referral_code="u123",
        niche="فروش لباس", audience="زنان ۲۰ تا ۳۵", tone="صمیمی",
        main_offer="مانتو و شلوار شهری", differentiator="طراحی مینیمال", content_goal="فروش و اعتمادسازی",
        plan="free", credits=15,
    )
    data.update(kwargs)
    return User(**data)


def test_core_profile_requires_all_three_fields():
    assert asyncio.run(profile_complete(make_user())) is True
    assert asyncio.run(profile_complete(make_user(tone=None))) is False
    assert asyncio.run(profile_complete(make_user(niche=None))) is False
    assert asyncio.run(profile_complete(make_user(audience=None))) is False


def test_main_menu_places_content_window_and_daily_suggestion():
    rows = main_menu()["keyboard"]
    assert rows[0] == [{"text": "👤 پروفایل برند"}, {"text": "✍️ تولید محتوا"}]
    assert rows[1] == [{"text": "✨ پیشنهاد امروز"}]
    assert rows[0].count({"text": "✍️ تولید محتوا"}) == 1
    assert all("🧠 ساخت محتوا" not in row[0]["text"] for row in rows)


def test_optional_profile_menu_has_skip():
    assert profile_menu()["keyboard"][1][0]["text"] == "🧩 تکمیل پروفایل"
    assert profile_optional_menu()["keyboard"][0][0]["text"] == "⏭️ رد کردن"


def test_idea_result_has_save_not_my_content():
    rows = inline_generation_actions(12, "ideas")["inline_keyboard"]
    texts = [button["text"] for row in rows for button in row]
    assert "⭐ ذخیره" in texts
    assert "📚 محتوای من" not in texts


def test_extended_profile_is_in_prompt():
    prompt = build_prompt(
        {"niche":"فروش لباس", "audience":"زنان", "tone":"صمیمی", "main_offer":"مانتو", "differentiator":"مینیمال", "content_goal":"فروش", "plan":"free"},
        "caption", "برای محصول جدید یک کپشن بده", "medium", 1,
    )
    for value in ("فروش لباس", "زنان", "صمیمی", "مانتو", "مینیمال", "فروش"):
        assert value in prompt


def test_safe_send_has_no_copyable_feature():
    import inspect
    signature = inspect.signature(main.safe_send)
    assert "copyable" not in signature.parameters
