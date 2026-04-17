from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def _default_data_dir() -> Path:
    return Path(os.environ.get("STEM_STUDIO_DATA", Path(__file__).resolve().parent.parent / "data"))


DATA_DIR = _default_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
STEM_DIR = DATA_DIR / "stems"
DEV_DIR = DATA_DIR / "developer"
DB_PATH = DATA_DIR / "stem_studio.db"

for d in (UPLOAD_DIR, STEM_DIR, DEV_DIR):
    d.mkdir(parents=True, exist_ok=True)


class Base(DeclarativeBase):
    pass


class DeveloperPair(Base):
    __tablename__ = "developer_pairs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    mix_path: Mapped[str] = mapped_column(Text, nullable=False)
    stems_manifest_path: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class TrainingJob(Base):
    """Segna lavori futuri di fine-tuning; nessun training eseguito in questa versione."""

    __tablename__ = "training_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pair_id: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="queued")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
