"""Add optional extended brand profile fields.

Revision ID: 0003_add_extended_brand_profile
Revises: 0002_add_generation_status
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_extended_brand_profile"
down_revision: Union[str, Sequence[str], None] = "0002_add_generation_status"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("users", sa.Column("main_offer", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("differentiator", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("content_goal", sa.String(length=255), nullable=True))

def downgrade() -> None:
    op.drop_column("users", "content_goal")
    op.drop_column("users", "differentiator")
    op.drop_column("users", "main_offer")
