import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

pytest.importorskip("asyncpg")

from app.config import settings
from app.db import CreditTransaction, PaymentOrder, User
from app.payments import (
    PaymentError,
    PaymentRequestResult,
    PaymentVerifyResult,
    ZarinPalClient,
    payment_url_from_authority,
    validate_payment_configuration,
)
from app.services import PLAN_CREDITS, verify_and_apply_payment


class FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeHTTPClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return FakeHTTPResponse(self.payload)

    async def aclose(self):
        return None


class FakeGateway:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def verify_payment(self, *, amount_rial, authority):
        self.calls.append((amount_rial, authority))
        return self.result


class FakeSession:
    def __init__(self, order, user, *, commit_error=None):
        self.order = order
        self.user = user
        self.commits = 0
        self.rollbacks = 0
        self.added = []
        self.commit_error = commit_error
        self._snapshot = (
            order.status,
            order.transaction_ref,
            order.failure_reason,
            order.verified_at,
            user.plan,
            user.credits,
        )

    async def get(self, model, obj_id, with_for_update=False):
        if model is User:
            return self.user
        if model is PaymentOrder:
            return self.order
        raise AssertionError(model)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rollbacks += 1
        status, ref, reason, verified_at, plan, credits = self._snapshot
        self.order.status = status
        self.order.transaction_ref = ref
        self.order.failure_reason = reason
        self.order.verified_at = verified_at
        self.user.plan = plan
        self.user.credits = credits


def make_order(**overrides):
    values = dict(
        id=7,
        user_id=11,
        plan="pro",
        amount_toman=599000,
        amount_rial=5990000,
        provider="zarinpal",
        authority="AUTH-1",
        status="pending",
    )
    values.update(overrides)
    return PaymentOrder(**values)


def make_user():
    return User(
        id=11,
        telegram_id=123456789,
        referral_code="u123456789",
        plan="free",
        credits=15,
    )


def run(coro):
    return asyncio.run(coro)


def test_zarinpal_request_and_verify_adapter_parses_success():
    old_merchant = settings.zarinpal_merchant_id
    try:
        settings.zarinpal_merchant_id = "merchant-test"
        request_http = FakeHTTPClient({"errors": [], "data": {"code": 100, "authority": "A-123"}})
        result = run(ZarinPalClient(request_http).request_payment(
            amount_rial=5990000,
            description="test",
            callback_url="https://example.com/payment/callback",
        ))
        assert isinstance(result, PaymentRequestResult)
        assert result.authority == "A-123"
        assert request_http.calls[0][1]["amount"] == 5990000

        verify_http = FakeHTTPClient({"errors": [], "data": {"code": 100, "ref_id": 12345}})
        verified = run(ZarinPalClient(verify_http).verify_payment(amount_rial=5990000, authority="A-123"))
        assert isinstance(verified, PaymentVerifyResult)
        assert verified.code == 100
        assert verified.ref_id == "12345"
    finally:
        settings.zarinpal_merchant_id = old_merchant


def test_malformed_gateway_data_is_rejected_safely():
    old = settings.zarinpal_merchant_id
    try:
        settings.zarinpal_merchant_id = "merchant-test"
        for payload in (
            {"errors": [], "data": "oops"},
            {"errors": [], "data": {"code": "abc"}},
            {"errors": [{"message": "gateway error"}], "data": {"code": 100}},
        ):
            with pytest.raises(PaymentError):
                run(ZarinPalClient(FakeHTTPClient(payload)).verify_payment(amount_rial=1000, authority="A-123"))
    finally:
        settings.zarinpal_merchant_id = old


def test_successful_payment_changes_entitlement_once_and_uses_server_amount():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user)
    gateway = FakeGateway(PaymentVerifyResult(code=100, ref_id="REF-9", message="OK"))
    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=gateway))
    assert result == "paid"
    assert ref == "REF-9"
    assert gateway.calls == [(5990000, "AUTH-1")]
    assert order.status == "paid"
    assert order.transaction_ref == "REF-9"
    assert user.plan == "pro"
    assert user.credits == PLAN_CREDITS["pro"]


def test_successful_payment_writes_credit_ledger_entry():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user)
    gateway = FakeGateway(PaymentVerifyResult(code=100, ref_id="REF-10", message="OK"))
    result, _ = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=gateway))
    assert result == "paid"
    assert len(session.added) == 1
    tx = session.added[0]
    assert isinstance(tx, CreditTransaction)
    assert tx.amount == 335
    assert tx.balance_after == 350
    assert "خرید پلن pro" in tx.reason


def test_failed_verification_does_not_grant_plan():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user)
    gateway = FakeGateway(PaymentVerifyResult(code=-21, ref_id=None, message="failed"))
    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=gateway))
    assert result == "verify_failed"
    assert ref is None
    assert user.plan == "free"
    assert user.credits == 15
    assert order.status == "verify_failed"


def test_gateway_error_keeps_order_pending_for_safe_retry():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user)

    class TimeoutGateway:
        async def verify_payment(self, *, amount_rial, authority):
            raise PaymentError("timeout")

    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=TimeoutGateway()))
    assert result == "verify_failed"
    assert ref is None
    assert order.status == "pending"
    assert session.commits == 1


def test_nok_callback_does_not_cancel_pending_order():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user)
    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="NOK", client=FakeGateway(PaymentVerifyResult(code=100, ref_id="REF", message="OK"))))
    assert result == "cancelled"
    assert ref is None
    assert order.status == "pending"


def test_wrong_amount_is_rejected_before_gateway_call():
    order = make_order(amount_rial=5990010)
    user = make_user()
    session = FakeSession(order, user)
    gateway = FakeGateway(PaymentVerifyResult(code=100, ref_id="REF", message="OK"))
    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=gateway))
    assert result == "verify_failed"
    assert ref is None
    assert gateway.calls == []
    assert user.plan == "free"


def test_bad_authority_does_not_mutate_order():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user)
    gateway = FakeGateway(PaymentVerifyResult(code=100, ref_id="REF", message="OK"))
    result, _ = run(verify_and_apply_payment(session, order, authority="WRONG", status="OK", client=gateway))
    assert result == "verify_failed"
    assert order.status == "pending"
    assert gateway.calls == []


def test_db_failure_rolls_back_entitlement():
    order = make_order()
    user = make_user()
    session = FakeSession(order, user, commit_error=IntegrityError("stmt", {}, Exception("unique")))
    gateway = FakeGateway(PaymentVerifyResult(code=100, ref_id="REF-9", message="OK"))
    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=gateway))
    assert result == "verify_failed"
    assert ref is None
    assert session.rollbacks == 1
    assert order.status == "pending"
    assert user.plan == "free"
    assert user.credits == 15


def test_repeated_callback_is_idempotent():
    order = make_order(status="paid", transaction_ref="REF-OLD")
    user = make_user()
    session = FakeSession(order, user)
    gateway = FakeGateway(PaymentVerifyResult(code=100, ref_id="REF-NEW", message="OK"))
    result, ref = run(verify_and_apply_payment(session, order, authority="AUTH-1", status="OK", client=gateway))
    assert result == "already_paid"
    assert ref == "REF-OLD"
    assert gateway.calls == []
    assert user.plan == "free"
    assert user.credits == 15


def test_production_never_allows_sandbox():
    old = (settings.app_env, settings.payment_enabled, settings.zarinpal_merchant_id, settings.zarinpal_sandbox, settings.public_base_url)
    try:
        settings.app_env = "production"
        settings.payment_enabled = True
        settings.zarinpal_merchant_id = "merchant-test"
        settings.zarinpal_sandbox = True
        settings.public_base_url = "https://example.com"
        with pytest.raises(PaymentError):
            validate_payment_configuration()
    finally:
        settings.app_env, settings.payment_enabled, settings.zarinpal_merchant_id, settings.zarinpal_sandbox, settings.public_base_url = old


def test_payment_url_rejects_bad_authority():
    for value in ("", "x" * 256):
        with pytest.raises(PaymentError):
            payment_url_from_authority(value)
