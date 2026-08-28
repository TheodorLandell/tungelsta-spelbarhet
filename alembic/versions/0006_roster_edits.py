"""roster_edits

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-28

Manuell ändring av en matchs trupp (SPEC 4 och 6.5). Ligger som ett lager
ovanpå iBIS-datan, som aldrig skrivs över: 'add' lägger till en spelare i
underlaget, 'remove' tar bort en. Ändringen påverkar både regelmotorn och
skottregistreringens spelarlista. Varje rad har en anteckning och går att
ångra (raden raderas).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "roster_edits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
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
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("roster_edits")
