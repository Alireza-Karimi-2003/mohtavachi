from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_caddy_receives_domain_environment():
    compose = (ROOT / "docker-compose.production.yml").read_text()
    assert "DOMAIN: ${DOMAIN}" in compose


def test_dockerignore_blocks_secrets_and_local_files():
    text = (ROOT / ".dockerignore").read_text()
    for item in [".env", ".venv/", ".git/", "tests/", "__pycache__/"]:
        assert item in text


def test_daily_suggestion_and_referral_models_match_migrations():
    db = (ROOT / "app/db.py").read_text()
    assert "class DailySuggestion" in db and "class Referral" in db
    daily = db.split("class DailySuggestion", 1)[1].split("class Referral", 1)[0]
    referral = db.split("class Referral", 1)[1].split("class SupportTicket", 1)[0]
    for block in (daily, referral):
        assert "is_banned" not in block
        assert "ban_reason" not in block
        assert "banned_at" not in block
        assert "admin_role" not in block


def test_webhook_header_documentation_matches_code():
    readme = (ROOT / "README.md").read_text()
    main = (ROOT / "app/main.py").read_text()
    assert "X-Telegram-Bot-Api-Secret-Token" in readme
    assert 'alias="X-Telegram-Bot-Api-Secret-Token"' in main
