"""Initial PostgreSQL schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-09 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("niche", sa.String(length=255), nullable=True),
        sa.Column("audience", sa.String(length=255), nullable=True),
        sa.Column("tone", sa.String(length=255), nullable=False),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("daily_free_used", sa.Integer(), nullable=False),
        sa.Column("daily_free_date", sa.String(length=10), nullable=True),
        sa.Column("daily_image_free_date", sa.String(length=10), nullable=True),
        sa.Column("daily_image_free_used", sa.Integer(), nullable=False),
        sa.Column("streak_days", sa.Integer(), nullable=False),
        sa.Column("last_generation_date", sa.String(length=10), nullable=True),
        sa.Column("referral_code", sa.String(length=64), nullable=False),
        sa.Column("referred_by_user_id", sa.Integer(), nullable=True),
        sa.Column("referral_rewarded", sa.Boolean(), nullable=False),
        sa.Column("referral_cycle_started_at", sa.DateTime(), nullable=True),
        sa.Column("referral_cycle_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["referred_by_user_id"], ["users.id"], name=op.f("fk_users_referred_by_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("referral_code", name=op.f("uq_users_referral_code")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_users_telegram_id")),
    )
    op.create_index(op.f("ix_users_referral_code"), "users", ["referral_code"], unique=False)
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=False)

    op.create_table(
        "generations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("cost_credits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("feedback", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_generations_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generations")),
    )
    op.create_index(op.f("ix_generations_user_id"), "generations", ["user_id"], unique=False)

    op.create_table(
        "daily_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("suggestion_date", sa.String(length=10), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_daily_suggestions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_suggestions")),
        sa.UniqueConstraint("user_id", "suggestion_date", name=op.f("uq_daily_suggestions_user_id")),
    )
    op.create_index(op.f("ix_daily_suggestions_suggestion_date"), "daily_suggestions", ["suggestion_date"], unique=False)
    op.create_index(op.f("ix_daily_suggestions_user_id"), "daily_suggestions", ["user_id"], unique=False)

    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inviter_id", sa.Integer(), nullable=False),
        sa.Column("invitee_id", sa.Integer(), nullable=False),
        sa.Column("rewarded", sa.Boolean(), nullable=False),
        sa.Column("rewarded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["invitee_id"], ["users.id"], name=op.f("fk_referrals_invitee_id_users")),
        sa.ForeignKeyConstraint(["inviter_id"], ["users.id"], name=op.f("fk_referrals_inviter_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referrals")),
        sa.UniqueConstraint("inviter_id", "invitee_id", name=op.f("uq_referrals_inviter_id")),
    )
    op.create_index(op.f("ix_referrals_invitee_id"), "referrals", ["invitee_id"], unique=False)
    op.create_index(op.f("ix_referrals_inviter_id"), "referrals", ["inviter_id"], unique=False)

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_support_tickets_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_tickets")),
    )
    op.create_index(op.f("ix_support_tickets_status"), "support_tickets", ["status"], unique=False)
    op.create_index(op.f("ix_support_tickets_user_id"), "support_tickets", ["user_id"], unique=False)

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("amount_toman", sa.Integer(), nullable=False),
        sa.Column("authority", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_payment_orders_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_orders")),
    )
    op.create_index(op.f("ix_payment_orders_authority"), "payment_orders", ["authority"], unique=False)
    op.create_index(op.f("ix_payment_orders_user_id"), "payment_orders", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_orders_user_id"), table_name="payment_orders")
    op.drop_index(op.f("ix_payment_orders_authority"), table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_index(op.f("ix_support_tickets_user_id"), table_name="support_tickets")
    op.drop_index(op.f("ix_support_tickets_status"), table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_index(op.f("ix_referrals_inviter_id"), table_name="referrals")
    op.drop_index(op.f("ix_referrals_invitee_id"), table_name="referrals")
    op.drop_table("referrals")
    op.drop_index(op.f("ix_daily_suggestions_user_id"), table_name="daily_suggestions")
    op.drop_index(op.f("ix_daily_suggestions_suggestion_date"), table_name="daily_suggestions")
    op.drop_table("daily_suggestions")
    op.drop_index(op.f("ix_generations_user_id"), table_name="generations")
    op.drop_table("generations")
    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_index(op.f("ix_users_referral_code"), table_name="users")
    op.drop_table("users")
