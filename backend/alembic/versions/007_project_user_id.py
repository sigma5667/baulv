"""add user_id to projects for multi-tenancy

Revision ID: 007
Revises: 006
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add user_id column as nullable first (existing rows need a value)
    op.add_column(
        "projects",
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Assign existing projects to the first user (if any exist)
    op.execute(
        """
        UPDATE projects
        SET user_id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1)
        WHERE user_id IS NULL
          AND EXISTS (SELECT 1 FROM users)
        """
    )

    # Delete orphan projects that have no user to assign to
    op.execute(
        """
        DELETE FROM projects WHERE user_id IS NULL
        """
    )

    # Now make it NOT NULL and add FK + index
    op.alter_column("projects", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_projects_user_id",
        "projects",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_constraint("fk_projects_user_id", "projects", type_="foreignkey")
    op.drop_column("projects", "user_id")
