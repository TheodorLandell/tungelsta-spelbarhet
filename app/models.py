from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Match(Base):
    __tablename__ = "matches"

    match_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team: Mapped[str] = mapped_column(String(1))           # 'A' | 'B'
    competition_id: Mapped[int] = mapped_column(Integer)
    kickoff: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))        # 'played' | 'scheduled' | 'cancelled'
    round_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opponent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON)

    appearances: Mapped[list["Appearance"]] = relationship(back_populates="match")


class Appearance(Base):
    __tablename__ = "appearances"

    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.match_id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.player_id"), primary_key=True)
    player_name: Mapped[str] = mapped_column(String(128))
    shirt_no: Mapped[str | None] = mapped_column(String(8), nullable=True)

    match: Mapped["Match"] = relationship(back_populates="appearances")
    player: Mapped["Player"] = relationship(back_populates="appearances")


class Player(Base):
    __tablename__ = "players"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    shirt_no: Mapped[str | None] = mapped_column(String(8), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime)

    appearances: Mapped[list["Appearance"]] = relationship(back_populates="player")
    overrides: Mapped[list["Override"]] = relationship(back_populates="player")


class Override(Base):
    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.player_id"))
    kind: Mapped[str] = mapped_column(String(32))          # 'unlock' | 'set_matches_left'
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(Text)
    data_snapshot: Mapped[datetime] = mapped_column(DateTime)

    player: Mapped["Player"] = relationship(back_populates="overrides")


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    matches_added: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    ok: Mapped[bool] = mapped_column(Boolean)
