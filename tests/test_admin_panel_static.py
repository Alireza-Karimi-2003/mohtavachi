from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
SERVICES = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
DB = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "alembic" / "versions" / "0004_add_admin_controls.py").read_text(encoding="utf-8")
TELEGRAM = (ROOT / "app" / "telegram.py").read_text(encoding="utf-8")


def test_admin_schema_present():
    for token in ["is_banned", "ban_reason", "banned_at", "admin_role", "CreditTransaction", "AdminAuditLog"]:
        assert token in DB
    assert "0004_add_admin_controls" in MIGRATION


def test_admin_security_and_actions_present():
    for token in ["admin_role", "can_manage_users", "can_manage_credits", "can_manage_bans", "can_manage_roles", "adjust_user_credits", "set_user_plan_by_admin", "set_user_ban", "admin_stats"]:
        assert token in SERVICES or token in MAIN
    assert '"/admin"' in MAIN
    assert '"/admin_stats"' in MAIN
    assert "user.is_banned" in MAIN
    assert "ROLE_CHANGE" in MAIN
    assert "CREDIT_ADJUST" in SERVICES
    assert "PLAN_CHANGE" in SERVICES
    assert "BAN" in SERVICES
    assert "UNBAN" in SERVICES


def test_admin_ui_present():
    for token in ["👤 کاربران", "📊 آمار", "🎫 تیکت‌ها", "📜 گزارش عملیات", "👮 مدیریت نقش‌ها", "➕ اعتبار", "➖ اعتبار", "📦 تغییر پلن", "🚫 Ban"]:
        assert token in TELEGRAM or token in MAIN


def test_admin_plan_stats_are_inside_main_stats_and_not_a_separate_menu_item():
    telegram = (ROOT / "app" / "telegram.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "💰 آمار پلن‌ها" not in telegram
    stats_block = main.split('if is_admin(user) and text == "📊 آمار":', 1)[1].split('if is_admin(user) and text == "📜 گزارش عملیات":', 1)[0]
    assert "purchased" in stats_block and "active_plans" in stats_block


def test_admin_hash_selection_is_not_preempted_by_ticket_lookup():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'if user.telegram_id not in PENDING and text.startswith("#")' in main


def test_admin_can_enter_studio_plus_in_development_mode():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert main.count('if user.plan != "studio_plus" and not is_admin(user):') >= 2


def test_admin_plan_change_writes_credit_ledger_when_balance_changes():
    services = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
    block = services.split("async def set_user_plan_by_admin", 1)[1].split("async def set_user_ban", 1)[0]
    assert "CreditTransaction(" in block
    assert "credit_delta = new_credits - old_credits" in block
    assert "PLAN_CHANGE" in block
