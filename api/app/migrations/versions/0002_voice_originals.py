"""voices keep their untrimmed original and the keep-regions used to cut the clip

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voices", sa.Column("original", sa.String(32), nullable=False, server_default=""))
    op.add_column("voices", sa.Column("regions", sa.JSON, nullable=False, server_default="[]"))
    op.add_column("voices", sa.Column("updated", sa.Float, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("voices", "updated")
    op.drop_column("voices", "regions")
    op.drop_column("voices", "original")
