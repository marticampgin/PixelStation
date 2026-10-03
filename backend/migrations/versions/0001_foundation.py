"""Frozen initial persistent core schema, including deterministic poker state."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = [
    "CREATE TABLE attachments (\n\tid VARCHAR NOT NULL, \n\tfilename VARCHAR NOT NULL, \n\tsha256 VARCHAR NOT NULL, \n\tpath VARCHAR NOT NULL, \n\tsize INTEGER NOT NULL, \n\tmedia_type VARCHAR NOT NULL, \n\textension VARCHAR NOT NULL, \n\tsource VARCHAR NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tparse_status VARCHAR NOT NULL, \n\tparse_error VARCHAR, \n\tparser VARCHAR NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "CREATE INDEX ix_attachments_sha256 ON attachments (sha256)",
    "CREATE TABLE conversations (\n\tid VARCHAR(32) NOT NULL, \n\ttitle VARCHAR NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tupdated_at VARCHAR NOT NULL, \n\tarchived BOOLEAN NOT NULL, \n\tsummary TEXT NOT NULL, \n\tsummary_message_count INTEGER NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "CREATE INDEX ix_conversations_archived ON conversations (archived)",
    "CREATE INDEX ix_conversations_updated_at ON conversations (updated_at)",
    "CREATE TABLE game_sessions (\n\tid VARCHAR NOT NULL, \n\tgame VARCHAR NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tupdated_at VARCHAR NOT NULL, \n\tstate JSON NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "CREATE TABLE harness_runs (\n\tid VARCHAR NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\treport JSON NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "CREATE TABLE scheduled_jobs (\n\tid VARCHAR NOT NULL, \n\tnext_run VARCHAR NOT NULL, \n\tlast_run VARCHAR, \n\tPRIMARY KEY (id)\n)",
    'CREATE TABLE settings (\n\t"key" VARCHAR NOT NULL, \n\tvalue JSON NOT NULL, \n\tPRIMARY KEY ("key")\n)',
    "CREATE TABLE document_chunks (\n\tid VARCHAR NOT NULL, \n\tattachment_id VARCHAR NOT NULL, \n\ttext TEXT NOT NULL, \n\tnumber INTEGER NOT NULL, \n\tlocation VARCHAR NOT NULL, \n\tpage INTEGER, \n\theading VARCHAR NOT NULL, \n\tembedding JSON, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(attachment_id) REFERENCES attachments (id) ON DELETE CASCADE\n)",
    "CREATE INDEX ix_document_chunks_attachment_id ON document_chunks (attachment_id)",
    "CREATE TABLE messages (\n\tid VARCHAR(32) NOT NULL, \n\tconversation_id VARCHAR(32) NOT NULL, \n\trole VARCHAR(20) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tmodel VARCHAR, \n\tcreated_at VARCHAR NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tattachment_ids JSON NOT NULL, \n\tmemory_ids JSON NOT NULL, \n\ttraces JSON NOT NULL, \n\tfeedback VARCHAR, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE\n)",
    "CREATE INDEX ix_messages_conversation_id ON messages (conversation_id)",
    "CREATE TABLE poker_actions (\n\tid VARCHAR NOT NULL, \n\tsession_id VARCHAR NOT NULL, \n\thand_number INTEGER NOT NULL, \n\tseat INTEGER NOT NULL, \n\tstage VARCHAR NOT NULL, \n\taction VARCHAR NOT NULL, \n\tamount INTEGER NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(session_id) REFERENCES game_sessions (id) ON DELETE CASCADE\n)",
    "CREATE INDEX ix_poker_actions_session_id ON poker_actions (session_id)",
    "CREATE TABLE agent_runs (\n\tid VARCHAR NOT NULL, \n\tconversation_id VARCHAR(32), \n\tmessage_id VARCHAR(32), \n\troute VARCHAR NOT NULL, \n\tmodel VARCHAR, \n\tstatus VARCHAR NOT NULL, \n\tstarted_at VARCHAR NOT NULL, \n\tfinished_at VARCHAR, \n\tlatency_ms INTEGER NOT NULL, \n\tevidence JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE SET NULL, \n\tFOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE SET NULL\n)",
    "CREATE TABLE memories (\n\tid VARCHAR(32) NOT NULL, \n\ttext TEXT NOT NULL, \n\tcategory VARCHAR NOT NULL, \n\tscope VARCHAR NOT NULL, \n\tsource_conversation_id VARCHAR(32), \n\tsource_message_id VARCHAR(32), \n\tcreated_at VARCHAR NOT NULL, \n\tupdated_at VARCHAR NOT NULL, \n\tlast_accessed_at VARCHAR, \n\timportance DOUBLE NOT NULL, \n\tconfidence DOUBLE NOT NULL, \n\tpinned BOOLEAN NOT NULL, \n\ttags JSON NOT NULL, \n\tembedding JSON, \n\tstart_date VARCHAR, \n\tend_date VARCHAR, \n\texpiry VARCHAR, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_conversation_id) REFERENCES conversations (id) ON DELETE SET NULL, \n\tFOREIGN KEY(source_message_id) REFERENCES messages (id) ON DELETE SET NULL\n)",
    "CREATE INDEX ix_memories_category ON memories (category)",
    "CREATE TABLE friction_events (\n\tid VARCHAR NOT NULL, \n\trun_id VARCHAR, \n\tkind VARCHAR NOT NULL, \n\tdetails TEXT NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tregression JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE SET NULL\n)",
    "CREATE INDEX ix_friction_events_kind ON friction_events (kind)",
    "CREATE TABLE memory_links (\n\tid VARCHAR NOT NULL, \n\tsource_id VARCHAR(32) NOT NULL, \n\ttarget_id VARCHAR(32) NOT NULL, \n\trelation VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_id) REFERENCES memories (id) ON DELETE CASCADE, \n\tFOREIGN KEY(target_id) REFERENCES memories (id) ON DELETE CASCADE\n)",
    "CREATE INDEX ix_memory_links_source_id ON memory_links (source_id)",
    "CREATE TABLE memory_revisions (\n\tid VARCHAR NOT NULL, \n\tmemory_id VARCHAR(32) NOT NULL, \n\ttext TEXT NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(memory_id) REFERENCES memories (id) ON DELETE CASCADE\n)",
    "CREATE INDEX ix_memory_revisions_memory_id ON memory_revisions (memory_id)",
]
TABLES = [
    "attachments",
    "conversations",
    "game_sessions",
    "harness_runs",
    "scheduled_jobs",
    "settings",
    "document_chunks",
    "messages",
    "poker_actions",
    "agent_runs",
    "memories",
    "friction_events",
    "memory_links",
    "memory_revisions",
]


def upgrade():
    bind = op.get_bind()
    for statement in SCHEMA:
        bind.exec_driver_sql(statement)
    for table in ("memories", "document_chunks"):
        bind.exec_driver_sql(f"CREATE VIRTUAL TABLE {table}_fts USING fts5(id UNINDEXED, text)")
        bind.exec_driver_sql(
            f"CREATE TRIGGER {table}_ai AFTER INSERT ON {table} BEGIN INSERT INTO {table}_fts(id,text) VALUES(new.id,new.text); END"
        )
        bind.exec_driver_sql(
            f"CREATE TRIGGER {table}_ad AFTER DELETE ON {table} BEGIN DELETE FROM {table}_fts WHERE id=old.id; END"
        )
        bind.exec_driver_sql(
            f"CREATE TRIGGER {table}_au AFTER UPDATE OF text ON {table} BEGIN DELETE FROM {table}_fts WHERE id=old.id; INSERT INTO {table}_fts(id,text) VALUES(new.id,new.text); END"
        )


def downgrade():
    for table in ("memories", "document_chunks"):
        op.execute(f"DROP TABLE IF EXISTS {table}_fts")
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE IF EXISTS "{table}"')
