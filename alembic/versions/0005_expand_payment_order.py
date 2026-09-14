"""Expand payment orders for provider lifecycle and verification data.

Revision ID: 0005_expand_payment_order
Revises: 0004_add_admin_controls
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_expand_payment_order"
down_revision: Union[str, Sequence[str], None] = "0004_add_admin_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_orders", sa.Column("amount_rial", sa.Integer(), nullable=True))
    op.add_column("payment_orders", sa.Column("provider", sa.String(length=30), nullable=False, server_default="zarinpal"))
    op.add_column("payment_orders", sa.Column("transaction_ref", sa.String(length=255), nullable=True))
    op.add_column("payment_orders", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.add_column("payment_orders", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.add_column("payment_orders", sa.Column("verified_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE payment_orders SET amount_rial = amount_toman * 10 WHERE amount_rial IS NULL")
    op.alter_column("payment_orders", "amount_rial", nullable=False)

    op.drop_index("ix_payment_orders_authority", table_name="payment_orders")
    op.create_index("ix_payment_orders_authority", "payment_orders", ["authority"], unique=True)
    op.create_index("ix_payment_orders_transaction_ref", "payment_orders", ["transaction_ref"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_payment_orders_transaction_ref", table_name="payment_orders")
    op.drop_index("ix_payment_orders_authority", table_name="payment_orders")
    op.create_index("ix_payment_orders_authority", "payment_orders", ["authority"], unique=False)
    op.drop_column("payment_orders", "verified_at")
    op.drop_column("payment_orders", "updated_at")
    op.drop_column("payment_orders", "failure_reason")
    op.drop_column("payment_orders", "transaction_ref")
    op.drop_column("payment_orders", "provider")
    op.drop_column("payment_orders", "amount_rial")
