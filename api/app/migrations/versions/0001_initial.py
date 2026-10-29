"""initial schema: voices, generations, settings

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created", sa.Float, nullable=False),
        sa.Column("filename", sa.String(32), nullable=False),
    )
    op.create_table(
        "generations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("model", sa.String(32), nullable=False, server_default=""),
        sa.Column("voice", sa.String(256), nullable=False, server_default=""),
        sa.Column("language", sa.String(16), nullable=False, server_default="en"),
        sa.Column("created", sa.Float, nullable=False),
        sa.Column("segments", sa.JSON, nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
    )
    op.create_index("ix_generations_created", "generations", ["created"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_generations_created", table_name="generations")
    op.drop_table("generations")
    op.drop_table("voices")
