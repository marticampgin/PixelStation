"""Normalize missing vectors to SQL NULL so edited memories and changed roles reindex."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE memories SET embedding = NULL WHERE embedding = 'null'")
    op.execute("UPDATE document_chunks SET embedding = NULL WHERE embedding = 'null'")


def downgrade():
    # SQL NULL was valid before this repair and remains a valid absence of a vector.
    pass
