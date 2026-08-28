"""shot_events

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-28

Skotthändelser (SPEC 4 och 6.3/6.4). Primärnyckeln är ett UUID som skapas på
klienten, så att samma händelse kan skickas flera gånger utan att bli en
dubblett. Borttagning är en tombstone (deleted_at) som synkas som en vanlig
händelse, aldrig en radering. created_by är tränarens kortnamn.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shot_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "match_id",
            sa.Integer(),
            sa.ForeignKey("matches.match_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("players.player_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("shot_events")
