"""Add durable file edit proposals and immutable library revision history."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("file_edit_proposals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("file_id", sa.String(), sa.ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("before_sha256", sa.String(), nullable=False),
        sa.Column("after_sha256", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False))
    op.create_index("ix_file_edit_proposals_file_id", "file_edit_proposals", ["file_id"])
    op.create_table("file_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("file_id", sa.String(), sa.ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.String(), nullable=False, unique=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("before_sha256", sa.String(), nullable=False),
        sa.Column("after_sha256", sa.String(), nullable=False),
        sa.Column("before_path", sa.String(), nullable=False),
        sa.Column("after_path", sa.String(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False))
    op.create_index("ix_file_revisions_file_id", "file_revisions", ["file_id"])


def downgrade():
    op.drop_table("file_revisions")
    op.drop_table("file_edit_proposals")
