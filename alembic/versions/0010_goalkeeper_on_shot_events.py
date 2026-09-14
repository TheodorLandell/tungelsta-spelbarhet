"""goalkeeper_id på shot_events

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-14

Vilken målvakt ett motståndarskott hör till (SPEC 6.8). Sätts från den målvakt
som är vald i matchvyn när trycket görs, och är null för våra egna skott.

Befintliga motståndarskott är registrerade innan målvaktsvalet fanns och får
null. De räknas därför inte in i någon målvakts statistik – hellre en saknad
siffra än en gissad, samma regel som för mål utan periodinformation (6.7).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shot_events",
        sa.Column("goalkeeper_id", sa.Integer(), nullable=True),
    )
    # SQLite kan inte lägga till en FK i efterhand utan att bygga om tabellen.
    # batch_alter_table gör det åt oss, och är en no-op-vänlig väg på andra
    # motorer.
    with op.batch_alter_table("shot_events") as batch:
        batch.create_foreign_key(
            "fk_shot_events_goalkeeper_id",
            "players",
            ["goalkeeper_id"],
            ["player_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("shot_events") as batch:
        batch.drop_constraint("fk_shot_events_goalkeeper_id", type_="foreignkey")
    op.drop_column("shot_events", "goalkeeper_id")
