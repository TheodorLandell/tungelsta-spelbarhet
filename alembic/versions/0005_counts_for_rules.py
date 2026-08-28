"""counts_for_rules på matches

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-28

Synken hämtar nu även matcher med annan CompetitionTypeID än 1 (cup,
träningsmatch) för lag A och B, så att skott kan registreras på dem. De får
counts_for_rules = False och skickas aldrig in i regelmotorn.

Befintliga matcher är alla seriematcher (CompetitionTypeID == 1) och får
därför server_default "1".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column(
            "counts_for_rules",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("matches", "counts_for_rules")
