from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai import build_prompt  # noqa: E402


MAIN_SOURCE = (ROOT / "app" / "main.py").read_text(encoding="utf-8")


def test_studio_plus_uses_the_shared_prompt_builder():
    profile = {
        "niche": "فروش لباس",
        "audience": "زنان ۲۰ تا ۳۵ سال",
        "tone": "صمیمی و حرفه‌ای",
        "main_offer": "مانتو",
        "differentiator": "طراحی ایرانی",
        "content_goal": "فروش",
        "plan": "studio_plus",
    }
    prompt = build_prompt(profile, "caption", "نمونه درخواست", "medium", 1)
    assert "حوزه: فروش لباس" in prompt
    assert "مخاطب: زنان ۲۰ تا ۳۵ سال" in prompt
    assert "لحن: صمیمی و حرفه‌ای" in prompt
    assert "محصول/خدمت اصلی: مانتو" in prompt
    assert "مزیت/تفاوت برند: طراحی ایرانی" in prompt
    assert "هدف اصلی محتوا: فروش" in prompt
    assert 'process_generation(' in MAIN_SOURCE
    assert 'model_override=(settings.studio_plus_ai_model if premium else None)' in MAIN_SOURCE


def test_studio_plus_input_messages_reuse_normal_tool_messages():
    for symbol in (
        "GENERIC_TOOL_REQUEST_MESSAGE",
        "REWRITE_REQUEST_MESSAGE",
        "IDEAS_REQUEST_MESSAGE",
        "CAMPAIGN_REQUEST_MESSAGE",
        "IMAGE_REQUEST_MESSAGE",
    ):
        assert f"{symbol} =" in MAIN_SOURCE
    assert 'await safe_send(chat_id, tool_request_message(kind) + " ⭐", back_home_menu())' in MAIN_SOURCE


def test_studio_plus_rechecks_profile_and_access_before_generation():
    start = MAIN_SOURCE.index('if state["step"] == "studio_plus_request":')
    end = MAIN_SOURCE.index('    if state["step"] == "kind":', start)
    block = MAIN_SOURCE[start:end]
    assert "if not await profile_complete(user):" in block
    assert 'await can_generate(session, user, kind, premium=True)' in block
    assert 'start_background(process_generation' in block


def test_studio_plus_final_text_is_normal_output_plus_star():
    assert 'await safe_send(chat_id, output + ("\\n\\n⭐" if premium else ""), markup)' in MAIN_SOURCE


def test_studio_plus_image_uses_the_same_image_prompt_path_and_adds_star_to_caption():
    assert 'await generate_image({' in MAIN_SOURCE
    assert 'model_override=(settings.studio_plus_image_model if state.get("premium") else None)' in MAIN_SOURCE
    assert '"تصویرت آماده شد. ✨" + (" ⭐" if state.get("premium") else "")' in MAIN_SOURCE
