from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


def test_admin_growth_menus_and_states_are_unique():
    tg = text('app/telegram.py')
    main = text('app/main.py')
    for marker in ['📣 بازاریابی', '📣 ارسال پیام', '🎟️ کدهای تخفیف', '🎟️ کد تخفیف']:
        assert main.count(marker) + tg.count(marker) >= 1
    for step in ['admin_ticket_select', 'admin_ticket_view', 'admin_broadcast_segment', 'admin_broadcast_message', 'admin_broadcast_confirm', 'admin_coupon_menu', 'admin_coupon_code', 'admin_coupon_type', 'admin_coupon_value', 'admin_coupon_plan', 'admin_coupon_expiry', 'admin_coupon_deactivate', 'coupon_code', 'coupon_select_plan']:
        assert main.count(f'"{step}"') >= 1


def test_admin_dashboard_contains_business_and_usage_metrics():
    main = text('app/main.py')
    for marker in ['درآمد کل', 'درآمد ۳۰ روز اخیر', 'میانگین خرید موفق', 'تبدیل', 'اعتبار مصرف‌شده', 'بیشترین مصرف اعتبار']:
        assert marker in main


def test_models_and_migration_match():
    db = text('app/db.py')
    mig = text('alembic/versions/0007_admin_growth_tools.py')
    for marker in ['priority', 'assigned_admin_id', 'first_response_at', 'original_amount_toman', 'coupon_code', 'discount_toman', 'class Coupon', 'class CouponRedemption']:
        assert marker in db
    for marker in ['support_tickets', 'payment_orders', 'coupons', 'coupon_redemptions', '0006_studio_plus_controls']:
        assert marker in mig


def test_revision_lengths_and_chain():
    versions = sorted((ROOT / 'alembic/versions').glob('*.py'))
    revisions = {}
    for path in versions:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        rev = down = None
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and getattr(node.target, 'id', None) in {'revision','down_revision'}:
                if isinstance(node.value, ast.Constant):
                    if node.target.id == 'revision': rev = node.value.value
                    else: down = node.value.value
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if getattr(target, 'id', None) in {'revision','down_revision'} and isinstance(node.value, ast.Constant):
                        if target.id == 'revision': rev = node.value.value
                        else: down = node.value.value
        if rev:
            assert len(rev) <= 32
            revisions[rev] = down
    assert len(revisions) == 7
    assert revisions['0007_admin_growth_tools'] == '0006_studio_plus_controls'


def test_payment_coupon_flow_is_server_side():
    services = text('app/services.py')
    main = text('app/main.py')
    assert 'original_amount_toman' in services
    assert 'discount_toman' in services
    assert 'create_payment_order(session, user, plan, coupon_code=coupon_code)' in services
    assert 'order.original_amount_toman' in main
    assert 'order.discount_toman' in main


def test_admin_ticket_and_marketing_auth_is_centralized():
    services = text('app/services.py')
    assert 'if not is_admin(admin):' in services
    assert 'if not can_manage_users(admin):' in services
    assert 'async def assign_ticket_to_admin' in services
    assert 'async def set_ticket_priority' in services
    assert 'async def get_admin_segment_users' in services


def test_admin_menu_is_role_scoped_and_broadcast_is_background_safe():
    tg = text('app/telegram.py')
    main = text('app/main.py')
    assert 'if role == "support":' in tg
    assert '[{"text": "🎫 تیکت‌ها"}]' in tg
    assert 'async def run_broadcast' in main
    assert 'start_background(run_broadcast(admin.id' in main
    assert 'can_manage_marketing(admin)' in main


def test_admin_sensitive_entry_points_are_restricted():
    main = text('app/main.py')
    assert 'if not can_manage_users(user):' in main
    assert 'if not can_manage_marketing(user):' in main
    assert 'if state.get("step") in marketing_steps and not can_manage_marketing(admin):' in main
    assert 'close_support_ticket_by_admin(session, user' in main
    assert 'close_support_ticket_by_admin(session, admin' in main
