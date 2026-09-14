from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_payment_order_has_verification_fields():
    db = (ROOT / "app" / "db.py").read_text()
    for field in ("amount_rial", "provider", "authority", "transaction_ref", "status", "failure_reason", "verified_at"):
        assert f"{field}: Mapped" in db


def test_payment_migration_adds_lifecycle_fields_and_unique_refs():
    migration = (ROOT / "alembic" / "versions" / "0005_expand_payment_order.py").read_text()
    for field in ("amount_rial", "provider", "transaction_ref", "failure_reason", "updated_at", "verified_at"):
        assert f'"{field}"' in migration
    assert 'unique=True' in migration


def test_payment_order_snapshots_toman_to_rial():
    services = (ROOT / "app" / "services.py").read_text()
    assert "amount_rial = amount_toman * 10" in services
    assert 'provider="zarinpal"' in services


def test_zarinpal_adapter_uses_v4_request_and_verify_endpoints():
    payments = (ROOT / "app" / "payments.py").read_text()
    assert '"/pg/v4/payment/request.json"' in payments
    assert '"/pg/v4/payment/verify.json"' in payments
    assert '"merchant_id"' in payments
    assert '"callback_url"' in payments
    assert '"authority"' in payments
    assert 'code != 100' in payments


def test_payment_configuration_requires_https_callback_when_enabled():
    payments = (ROOT / "app" / "payments.py").read_text()
    assert 'payment_enabled' in payments
    assert 'callback.lower().startswith("https://")' in payments


def test_payment_start_persists_authority_only_before_verification():
    services = (ROOT / "app" / "services.py").read_text()
    assert 'order.status = "pending"' in services
    assert 'order.status = "request_failed"' in services
    assert 'order.authority = checkout.authority' in services
    # Starting checkout must not grant plan/credits.
    start_block = services.split("async def start_payment", 1)[1].split("async def activate_plan", 1)[0]
    assert "activate_plan(" not in start_block


def test_payment_authority_and_transaction_ref_are_unique_in_orm_too():
    db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    assert 'authority: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)' in db
    assert 'transaction_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)' in db


def test_concurrent_payment_starts_reuse_recent_pending_order():
    services = (ROOT / "app" / "services.py").read_text()
    block = services.split("async def create_payment_order", 1)[1].split("async def start_payment", 1)[0]
    assert "with_for_update=True" in block
    assert 'PaymentOrder.status == "pending"' in block
    assert "payment_order_reuse_minutes" in block
    assert 'if existing.plan != plan:' in block
