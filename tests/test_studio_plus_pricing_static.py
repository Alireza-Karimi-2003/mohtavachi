from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_studio_plus_business_defaults():
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    services = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'studio_plus_price: int = 2490000' in config
    assert 'studio_plus_credits: int = 1600' in config
    assert 'studio_plus_ai_model: str = "gpt-5.6-terra"' in config
    assert 'studio_plus_image_model: str = "gpt-image-1-mini"' in config
    assert 'if user.plan == "studio_plus":\n        return 2' in services
    assert '🖼️ روزی ۲ عکس رایگان' in main
    assert 'STUDIO_PLUS_GEN_COST = {' in services
    for line in (
        '"caption": 4',
        '"post": 7',
        '"story": 7',
        '"reel": 8',
        '"product": 7',
        '"ideas": 3',
        '"rewrite": 6',
        '"brief": 4',
        '"campaign": 15',
        '"pack": 15',
        '"image": 5',
    ):
        assert line in services


def test_non_plus_users_are_blocked_but_admins_are_allowed_into_studio_plus():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert main.count('if user.plan != "studio_plus" and not is_admin(user):') >= 2
    assert 'PENDING[user.telegram_id] = {"step": "studio_plus_kind"}' in main
    assert '"image",\n            premium=bool(state.get("premium")),' in main
    assert 'premium=premium' in main


def test_credit_guide_documents_both_normal_and_studio_plus_costs():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    for text in (
        "🔹 هزینه‌های پلن‌های عادی",
        "✨ هزینه‌های Studio+",
        "کپشن ۴",
        "پست ۷",
        "کمپین ۱۵",
        "عکس بعد از سهمیه رایگان ۵ اعتبار",
        "Studio+ روزانه ۲ عکس رایگان",
    ):
        assert text in main
