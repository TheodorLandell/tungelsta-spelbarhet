"""player_teams

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-28

Lagtillhörighet per spelare (många-till-många). Fylls av synken. Används bara
som filter i gränssnittet, aldrig av regelmotorn.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_teams",
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.player_id"), primary_key=True),
        sa.Column("team", sa.String(1), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("player_teams")
