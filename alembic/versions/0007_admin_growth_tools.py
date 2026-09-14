"""Add admin growth, support, and coupon tools."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_admin_growth_tools"
down_revision: Union[str, Sequence[str], None] = "0006_studio_plus_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_tickets", sa.Column("priority", sa.String(length=20), nullable=False, server_default="normal"))
    op.add_column("support_tickets", sa.Column("assigned_admin_id", sa.Integer(), nullable=True))
    op.add_column("support_tickets", sa.Column("first_response_at", sa.DateTime(), nullable=True))
    op.create_index("ix_support_tickets_priority", "support_tickets", ["priority"])
    op.create_index("ix_support_tickets_assigned_admin_id", "support_tickets", ["assigned_admin_id"])
    op.create_foreign_key(
        "fk_support_tickets_assigned_admin_id_users",
        "support_tickets",
        "users",
        ["assigned_admin_id"],
        ["id"],
    )

    op.add_column("payment_orders", sa.Column("original_amount_toman", sa.Integer(), nullable=True))
    op.add_column("payment_orders", sa.Column("coupon_code", sa.String(length=64), nullable=True))
    op.add_column("payment_orders", sa.Column("discount_toman", sa.Integer(), nullable=True))
    op.execute("UPDATE payment_orders SET original_amount_toman = amount_toman WHERE original_amount_toman IS NULL")
    op.execute("UPDATE payment_orders SET discount_toman = 0 WHERE discount_toman IS NULL")
    op.alter_column("payment_orders", "original_amount_toman", nullable=False, server_default="0")
    op.alter_column("payment_orders", "discount_toman", nullable=False, server_default="0")

    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_toman", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applicable_plan", sa.String(length=50), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_coupons_code"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["users.id"], name="fk_coupons_created_by_admin_id_users"),
    )
    op.create_index("ix_coupons_code", "coupons", ["code"], unique=True)
    op.create_index("ix_coupons_active", "coupons", ["active"])
    op.create_index("ix_coupons_created_by_admin_id", "coupons", ["created_by_admin_id"])

    op.create_table(
        "coupon_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("coupon_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("payment_order_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], name="fk_coupon_redemptions_coupon_id_coupons"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_coupon_redemptions_user_id_users"),
        sa.ForeignKeyConstraint(["payment_order_id"], ["payment_orders.id"], name="fk_coupon_redemptions_payment_order_id_payment_orders"),
        sa.UniqueConstraint("payment_order_id", name="uq_coupon_redemptions_payment_order"),
    )
    op.create_index("ix_coupon_redemptions_coupon_id", "coupon_redemptions", ["coupon_id"])
    op.create_index("ix_coupon_redemptions_user_id", "coupon_redemptions", ["user_id"])
    op.create_index("ix_coupon_redemptions_payment_order_id", "coupon_redemptions", ["payment_order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_coupon_redemptions_payment_order_id", table_name="coupon_redemptions")
    op.drop_index("ix_coupon_redemptions_user_id", table_name="coupon_redemptions")
    op.drop_index("ix_coupon_redemptions_coupon_id", table_name="coupon_redemptions")
    op.drop_table("coupon_redemptions")
    op.drop_index("ix_coupons_created_by_admin_id", table_name="coupons")
    op.drop_index("ix_coupons_active", table_name="coupons")
    op.drop_index("ix_coupons_code", table_name="coupons")
    op.drop_table("coupons")
    op.drop_column("payment_orders", "discount_toman")
    op.drop_column("payment_orders", "coupon_code")
    op.drop_column("payment_orders", "original_amount_toman")
    op.drop_constraint("fk_support_tickets_assigned_admin_id_users", "support_tickets", type_="foreignkey")
    op.drop_index("ix_support_tickets_assigned_admin_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_priority", table_name="support_tickets")
    op.drop_column("support_tickets", "first_response_at")
    op.drop_column("support_tickets", "assigned_admin_id")
    op.drop_column("support_tickets", "priority")
