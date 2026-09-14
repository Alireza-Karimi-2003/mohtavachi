from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_callback_route_reads_authority_and_status_and_verifies_order():
    main = (ROOT / "app" / "main.py").read_text()
    assert '@app.get("/payment/callback")' in main
    assert 'request.query_params.get("Authority")' in main
    assert 'request.query_params.get("Status")' in main
    assert 'verify_and_apply_payment(' in main


def test_payment_activation_is_only_after_gateway_verification():
    services = (ROOT / "app" / "services.py").read_text()
    block = services.split("async def verify_and_apply_payment", 1)[1].split("async def get_open_support_ticket", 1)[0]
    assert 'result.code not in {100, 101}' in block
    assert 'locked_order.status = "paid"' in block
    assert 'user.plan = locked_order.plan' in block
    assert 'user.credits = new_credits' in block


def test_payment_is_idempotent_and_locks_order_before_granting_entitlement():
    services = (ROOT / "app" / "services.py").read_text()
    block = services.split("async def verify_and_apply_payment", 1)[1].split("async def get_open_support_ticket", 1)[0]
    assert 'if order.status == "paid"' in block
    assert 'with_for_update=True' in block
    assert 'if locked_order.status == "paid"' in block


def test_upgrade_menu_has_buy_buttons():
    telegram = (ROOT / "app" / "telegram.py").read_text()
    for plan in ("starter", "pro", "studio"):
        assert f'callback_data": "buy:{plan}"' in telegram
