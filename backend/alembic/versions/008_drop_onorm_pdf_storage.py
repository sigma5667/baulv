"""drop ÖNORM PDF storage for copyright compliance

Revision ID: 008
Revises: 007
Create Date: 2026-04-15

BauLV no longer stores copyrighted ÖNORM PDF text on its servers. This
migration removes:

    * ``onorm_chunks`` — held the extracted ÖNORM text from uploaded PDFs.
      Dropping this table also deletes any text that may have been ingested
      under the old upload flow.
    * ``onorm_dokumente.file_path`` — pointed at the on-disk ÖNORM PDF.

The ``onorm_dokumente`` and ``onorm_regeln`` tables themselves are kept:
they store only metadata (norm number, title, trade) and references to the
mathematical calculation rules, which are not copyrightable.

Downgrade restores the dropped column and table structure but cannot
restore any deleted PDF text — that is intentional. There is no business
reason to ever re-enable PDF storage in BauLV.
"""

from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the chunk table first — it has an FK into onorm_dokumente.
    # Use IF EXISTS so this is a no-op on a green database that never
    # had the legacy table created.
    op.execute("DROP TABLE IF EXISTS onorm_chunks CASCADE")

    # Drop the file_path column from onorm_dokumente. Use a batch-style
    # check so the migration is idempotent across environments where the
    # column may already be absent.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "onorm_dokumente" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("onorm_dokumente")}
        if "file_path" in cols:
            op.drop_column("onorm_dokumente", "file_path")


def downgrade() -> None:
    # Recreate file_path (nullable — we cannot restore the old paths).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "onorm_dokumente" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("onorm_dokumente")}
        if "file_path" not in cols:
            op.add_column(
                "onorm_dokumente",
                sa.Column("file_path", sa.String(length=1000), nullable=True),
            )

    # Recreate the chunk table skeleton. We cannot restore the deleted
    # text — by design.
    op.create_table(
        "onorm_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "dokument_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onorm_dokumente.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("section_number", sa.String(length=50), nullable=True),
        sa.Column("section_title", sa.String(length=255), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )
