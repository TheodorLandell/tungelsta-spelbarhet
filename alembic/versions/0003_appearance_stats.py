"""appearance stats och målvaktsmarkering

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-28

Lägger till Goals, Assists och PenaltyMinutes på appearances (källa: iBIS
lineups) samt is_goalkeeper på players. Alla får server_default så att
befintliga rader fylls med 0 / false och uppdateras vid nästa synk.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appearances",
        sa.Column("goals", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "appearances",
        sa.Column("assists", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "appearances",
        sa.Column("penalty_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "players",
        sa.Column("is_goalkeeper", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("players", "is_goalkeeper")
    op.drop_column("appearances", "penalty_minutes")
    op.drop_column("appearances", "assists")
    op.drop_column("appearances", "goals")
