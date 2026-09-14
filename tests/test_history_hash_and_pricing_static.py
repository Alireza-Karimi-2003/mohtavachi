from pathlib import Path

MAIN = Path(__file__).parents[1] / "app" / "main.py"


def test_history_view_accepts_direct_hash_id():
    text = MAIN.read_text(encoding="utf-8")
    marker = 'if state["step"] == "history_view":'
    start = text.index(marker)
    end = text.index('if state["step"] == "idea_request":', start)
    block = text[start:end]
    assert 'if text.startswith("#") and text[1:].isdigit():' in block
    assert 'target = await get_generation(session, int(text[1:]), user.id)' in block
    assert '"history_mode": state.get("history_mode", "recent")' in block


def test_pro_plan_shows_one_free_image():
    text = MAIN.read_text(encoding="utf-8")
    pro_start = text.index('"🔹 پرو\\n"')
    studio_start = text.index('"🔹 استودیو\\n"', pro_start)
    pro_block = text[pro_start:studio_start]
    assert "🖼️ روزی ۱ عکس رایگان" in pro_block
