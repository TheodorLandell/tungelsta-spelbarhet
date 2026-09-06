"""date_missing på matches

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-06

Matcher utan bestämt datum i iBIS (MatchTimeMissing, eller platshållaren
1 januari 00:00) markeras med date_missing = True. De skickas aldrig in i
regelmotorn – oavsett kickoff – och visas sist i matchlistan.

Befintliga matcher antas ha riktiga datum → server_default "0". Nästa synk
sätter flaggan rätt för alla matcher.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column(
            "date_missing",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("matches", "date_missing")
