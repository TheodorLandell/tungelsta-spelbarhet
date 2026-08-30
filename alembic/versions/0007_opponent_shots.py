"""motståndarens skott på shot_events

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-30

Skott ska kunna registreras för motståndarlaget, men bara på lagnivå (SPEC 6.1).

  - side ('egen' | 'motstandare') på shot_events. Befintliga rader är egna
    skott och får server_default "egen".
  - player_id blir nullbart: motståndarens skott har ingen spelare.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shot_events") as batch:
        batch.add_column(
            sa.Column("side", sa.String(16), nullable=False, server_default="egen")
        )
        batch.alter_column(
            "player_id", existing_type=sa.Integer(), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("shot_events") as batch:
        batch.alter_column(
            "player_id", existing_type=sa.Integer(), nullable=False
        )
        batch.drop_column("side")
