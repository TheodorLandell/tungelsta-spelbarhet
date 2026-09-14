"""match_events och stats_final_ts

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14

Två ändringar som hör ihop (SPEC 6.7):

1. match_events – mål och utvisningar per period från iBIS Events[]. Enda
   källan till vilken period ett mål gjordes i.

2. matches.stats_final_ts – vilket FinalResultCreatedTS som gällde när
   appearances senast hämtades. Trupper publiceras före matchstart, så
   statistiken skrevs även mitt under matchen, och den gamla regeln "hämta
   inte om en färdigrapporterad match" frös då halvfärdiga siffror.

   Kolumnen börjar som null för alla befintliga matcher, vilket gör att nästa
   synk hämtar om statistiken en gång och rättar dem.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("stats_final_ts", sa.String(length=32), nullable=True),
    )

    op.create_table(
        "match_events",
        sa.Column("match_event_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("period", sa.Integer(), nullable=True),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("assist_player_id", sa.Integer(), nullable=True),
        sa.Column("penalty_minutes", sa.Integer(), nullable=True),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column("second", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.PrimaryKeyConstraint("match_event_id"),
    )
    op.create_index(
        "ix_match_events_match_id", "match_events", ["match_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_match_events_match_id", table_name="match_events")
    op.drop_table("match_events")
    op.drop_column("matches", "stats_final_ts")
