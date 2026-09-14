"""Add Studio+ pricing/config support and persistent bot runtime controls."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006_studio_plus_controls"
down_revision: Union[str, Sequence[str], None] = "0005_expand_payment_order"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "bot_controls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_bot_controls_key"),
    )
    op.create_index("ix_bot_controls_key", "bot_controls", ["key"], unique=True)

def downgrade() -> None:
    op.drop_index("ix_bot_controls_key", table_name="bot_controls")
    op.drop_table("bot_controls")
