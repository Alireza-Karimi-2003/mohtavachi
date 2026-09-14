"""Add generation status.

Revision ID: 0002_add_generation_status
Revises: 0001_initial_schema
Create Date: 2026-09-09 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_generation_status"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generations",
        sa.Column("status", sa.String(length=20), nullable=True, server_default="success"),
    )


def downgrade() -> None:
    op.drop_column("generations", "status")
