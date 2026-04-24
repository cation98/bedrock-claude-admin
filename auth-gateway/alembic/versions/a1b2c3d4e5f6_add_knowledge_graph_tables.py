"""add knowledge graph tables

Revision ID: a1b2c3d4e5f6
Revises: a7b8c9d0e1f2
Create Date: 2026-04-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("concept_name", sa.String(200), nullable=False),
        sa.Column("concept_type", sa.String(50), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name"),
    )

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_node_id", sa.Integer(), nullable=False),
        sa.Column("target_node_id", sa.Integer(), nullable=False),
        sa.Column("edge_type", sa.String(50), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("co_occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_node_id", "target_node_id", "edge_type", name="uq_edge_nodes_type"),
    )

    op.create_table(
        "knowledge_mentions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("context_snippet", sa.String(200), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("mentioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_mentions_node_id", "knowledge_mentions", ["node_id"])
    op.create_index("ix_knowledge_mentions_username", "knowledge_mentions", ["username"])

    op.create_table(
        "knowledge_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_date", sa.String(10), nullable=False),
        sa.Column("granularity", sa.String(10), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("department_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prev_mention_count", sa.Integer(), nullable=True),
        sa.Column("growth_rate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_date", "granularity", "node_id", name="uq_snapshot_date_gran_node"),
    )

    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("target_department", sa.String(100), nullable=True),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("connections", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_taxonomy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("knowledge_node_id", sa.Integer(), nullable=False),
        sa.Column("workflow_template_id", sa.Integer(), nullable=False),
        sa.Column("workflow_step_id", sa.String(100), nullable=False),
        sa.Column("mapped_by", sa.String(100), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_node_id", "workflow_template_id", "workflow_step_id", name="uq_taxonomy_mapping"),
    )

    op.create_table(
        "workflow_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("canvas_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_personal", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "prompt_audit_conversations",
        sa.Column("knowledge_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pac_knowledge_extracted_at",
        "prompt_audit_conversations",
        ["knowledge_extracted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pac_knowledge_extracted_at", "prompt_audit_conversations")
    op.drop_column("prompt_audit_conversations", "knowledge_extracted_at")
    op.drop_table("workflow_instances")
    op.drop_table("knowledge_taxonomy")
    op.drop_table("workflow_templates")
    op.drop_table("knowledge_snapshots")
    op.drop_index("ix_knowledge_mentions_username", "knowledge_mentions")
    op.drop_index("ix_knowledge_mentions_node_id", "knowledge_mentions")
    op.drop_table("knowledge_mentions")
    op.drop_table("knowledge_edges")
    op.drop_table("knowledge_nodes")
