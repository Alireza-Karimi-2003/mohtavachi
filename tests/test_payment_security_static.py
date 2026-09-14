from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _verification_block() -> str:
    services = (ROOT / "app" / "services.py").read_text()
    return services.split("async def verify_and_apply_payment", 1)[1].split(
        "async def get_open_support_ticket", 1
    )[0]


def test_callback_does_not_trust_client_supplied_amount():
    main = (ROOT / "app" / "main.py").read_text()
    callback = main.split('@app.get("/payment/callback")', 1)[1].split('@app.get("/health")', 1)[0]
    assert "amount" not in callback.lower()
    assert "amount_rial" not in callback


def test_verification_uses_order_snapshot_and_provider_binding():
    block = _verification_block()
    assert 'order.provider != "zarinpal"' in block
    assert "order.amount_rial" in block
    assert "order.plan" in block
    assert "order.authority != authority" in block


def test_callback_status_and_authority_are_validated_before_entitlement():
    block = _verification_block()
    assert 'if status not in {"OK", "NOK"}' in block
    assert 'if status != "OK"' in block
    assert 'if not authority or len(authority) > 255' in block


def test_entitlement_stays_after_successful_gateway_verification_only():
    block = _verification_block()
    verify_guard = block.split('if result.code not in {100, 101}', 1)[0]
    assert 'user.plan = locked_order.plan' not in verify_guard
    assert 'user.credits = PLAN_CREDITS[locked_order.plan]' not in verify_guard
    assert 'result.code not in {100, 101}' in block


def test_double_callback_cannot_grant_entitlement_twice():
    block = _verification_block()
    assert 'if order.status == "paid"' in block
    assert 'locked_order = await session.get(PaymentOrder, order.id, with_for_update=True)' in block
    assert 'if locked_order.status == "paid"' in block


def test_payment_callback_keeps_order_amount_server_side():
    services = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
    block = services.split("async def verify_and_apply_payment", 1)[1].split("async def get_open_support_ticket", 1)[0]
    assert "order.amount_rial" in block
    assert "amount_rial=order.amount_rial" in block


def test_payment_success_requires_local_plan_and_amount_validation():
    services = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
    block = services.split("async def verify_and_apply_payment", 1)[1].split("async def get_open_support_ticket", 1)[0]
    assert 'expected_base = int(order.original_amount_toman or order.amount_toman)' in block
    assert 'expected_amount = expected_base - expected_discount' in block
    assert 'order.amount_toman != expected_amount' in block
    assert 'order.amount_rial != expected_amount * 10' in block
    assert 'if result.code not in {100, 101}:' in block


def test_payment_failure_does_not_activate_plan():
    services = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
    block = services.split("async def verify_and_apply_payment", 1)[1].split("async def get_open_support_ticket", 1)[0]
    failed_branch = block.split('if result.code not in {100, 101}:', 1)[0]
    assert 'user.plan = locked_order.plan' not in failed_branch
    assert 'user.credits = PLAN_CREDITS[locked_order.plan]' not in failed_branch


def test_verification_timeout_keeps_order_retryable():
    block = _verification_block()
    transient = block.split('except PaymentError as exc:', 1)[1].split('if result.code not in {100, 101}:', 1)[0]
    assert 'order.failure_reason = str(exc)[:1000]' in transient
    assert 'order.status = "verify_failed"' not in transient


def test_payment_success_writes_credit_transaction():
    block = _verification_block()
    assert 'CreditTransaction(' in block
    assert 'admin_user_id=None' in block


def test_payment_has_exact_server_order_amount_integrity_check():
    block = _verification_block()
    assert 'expected_base = int(order.original_amount_toman or order.amount_toman)' in block
    assert 'expected_discount = int(order.discount_toman or 0)' in block
    assert 'order.amount_toman != expected_amount' in block
    assert 'order.amount_rial != expected_amount * 10' in block


def test_callback_nok_does_not_mutate_pending_order():
    block = _verification_block()
    nok = block.split('if status != "OK":', 1)[1].split('if order.plan not in PLAN_CREDITS:', 1)[0]
    assert 'order.status =' not in nok
