"""shirt_seen på players

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-14

När det nuvarande tröjnumret observerades i en matchtrupp (SPEC 3.6).

Tröjnummer kommer från två källor som skriver samma fält: lagets Players[]
(en registrering) och matchernas lineups (vad spelaren faktiskt bar). Utan en
tidsstämpel kan en äldre match skriva över en nyare, och en eftersläpande
registrering skriva över båda.

Null betyder att numret inte kommer från någon lineup, utan från lagtruppen
eller är okänt. Nästa synk fyller i stämpeln för alla som har en lineup.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("shirt_seen", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "shirt_seen")
