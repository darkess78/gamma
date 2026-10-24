"""Stream state schema registered with the authority's backed-up migration."""
from sqlalchemy import Column, Index, Integer, Table, Text, UniqueConstraint


def register_stream_tables(metadata):
    temporary = Table("stream_temp_memory", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("bucket", Text, nullable=False), Column("key", Text, nullable=False),
        Column("value", Text, nullable=False),
        Column("metadata_json", Text, nullable=False, server_default="{}"),
        Column("created_at", Text, nullable=False), Column("updated_at", Text, nullable=False),
        UniqueConstraint("bucket", "key"))
    goals = Table("stream_self_goals", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("title", Text, nullable=False), Column("description", Text, nullable=False),
        Column("status", Text, nullable=False), Column("source", Text, nullable=False),
        Column("metadata_json", Text, nullable=False, server_default="{}"),
        Column("created_at", Text, nullable=False), Column("updated_at", Text, nullable=False))
    Index("ix_stream_temp_memory_bucket", temporary.c.bucket)
    Index("ix_stream_temp_memory_updated_at", temporary.c.updated_at)
    Index("ix_stream_self_goals_status", goals.c.status)
    Index("ix_stream_self_goals_updated_at", goals.c.updated_at)
    trust = Table("stream_viewer_trust", metadata,
        Column("platform", Text, primary_key=True), Column("platform_user_id", Text, primary_key=True),
        Column("display_name", Text), Column("trust_level", Text, nullable=False, server_default="normal"),
        Column("notes", Text), Column("pronunciation_alias", Text),
        Column("created_at", Text, nullable=False), Column("updated_at", Text, nullable=False))
    Index("ix_stream_viewer_trust_trust_level", trust.c.trust_level)
