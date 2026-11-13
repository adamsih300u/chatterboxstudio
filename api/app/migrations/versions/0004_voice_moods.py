"""voices get moods (several reference clips) cut from several stored sources

The single clip, original and regions on a voice move into a "neutral" mood and a source "s1".
The old columns are dropped before the new tables exist: SQLite rebuilds a table to drop a
column, and with foreign keys on that would cascade-delete rows of tables pointing at it.

Revision ID: 0004
Revises: 0003
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    old = conn.execute(sa.text("SELECT id, created, filename, original, regions, updated FROM voices")).fetchall()

    with op.batch_alter_table("voices") as batch:
        batch.drop_column("filename")
        batch.drop_column("original")
        batch.drop_column("regions")

    sources = op.create_table(
        "voice_sources",
        sa.Column("voice_id", sa.String(64), sa.ForeignKey("voices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("filename", sa.String(48), nullable=False),
        sa.Column("label", sa.String(128), nullable=False, server_default=""),
        sa.Column("created", sa.Float, nullable=False),
    )
    moods = op.create_table(
        "voice_moods",
        sa.Column("voice_id", sa.String(64), sa.ForeignKey("voices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(48), nullable=False),
        sa.Column("regions", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created", sa.Float, nullable=False, server_default="0"),
        sa.Column("updated", sa.Float, nullable=False, server_default="0"),
    )
    for voice_id, created, filename, original, regions, updated in old:
        regions = json.loads(regions) if isinstance(regions, str) else (regions or [])
        if original:
            op.bulk_insert(sources, [{"voice_id": voice_id, "id": "s1", "filename": original, "label": "", "created": created}])
            regions = [{"source": "s1", **r} for r in regions]
        op.bulk_insert(
            moods,
            [{
                "voice_id": voice_id, "id": "neutral", "name": "Neutral", "filename": filename,
                "regions": regions, "created": created, "updated": updated or created,
            }],
        )


def downgrade() -> None:
    conn = op.get_bind()
    moods = conn.execute(sa.text("SELECT voice_id, filename, regions, updated FROM voice_moods WHERE id = 'neutral'")).fetchall()
    sources = {r[0]: r[1] for r in conn.execute(sa.text("SELECT voice_id, filename FROM voice_sources WHERE id = 's1'"))}
    op.drop_table("voice_moods")
    op.drop_table("voice_sources")
    with op.batch_alter_table("voices") as batch:
        batch.add_column(sa.Column("filename", sa.String(32), nullable=False, server_default=""))
        batch.add_column(sa.Column("original", sa.String(32), nullable=False, server_default=""))
        batch.add_column(sa.Column("regions", sa.JSON, nullable=False, server_default="[]"))
    for voice_id, filename, regions, updated in moods:
        regions = json.loads(regions) if isinstance(regions, str) else (regions or [])
        flat = [{"start": r["start"], "end": r["end"]} for r in regions if r.get("source") in ("s1", "")]
        conn.execute(
            sa.text("UPDATE voices SET filename = :f, original = :o, regions = :r, updated = :u WHERE id = :id"),
            {"f": filename, "o": sources.get(voice_id, ""), "r": json.dumps(flat), "u": updated, "id": voice_id},
        )
