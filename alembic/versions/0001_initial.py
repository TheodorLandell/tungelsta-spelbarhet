"""initial

Revision ID: 0001
Revises:
Create Date: 2026-08-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("match_id", sa.Integer(), primary_key=True),
        sa.Column("team", sa.String(1), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("kickoff", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("round_name", sa.String(64), nullable=True),
        sa.Column("opponent", sa.String(128), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
    )
    op.create_table(
        "players",
        sa.Column("player_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("shirt_no", sa.String(8), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "appearances",
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.match_id"), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.player_id"), primary_key=True),
        sa.Column("player_name", sa.String(128), nullable=False),
        sa.Column("shirt_no", sa.String(8), nullable=True),
    )
    op.create_table(
        "overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.player_id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("value", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("data_snapshot", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sync_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("matches_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("overrides")
    op.drop_table("appearances")
    op.drop_table("sync_log")
    op.drop_table("players")
    op.drop_table("matches")
