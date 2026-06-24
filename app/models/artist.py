"""Artist profile ORM model."""
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ArtistProfile(Base):
    __tablename__ = "artist_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, default="Chandrashekar K")
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    mediums: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    themes: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON array
    portfolio_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cv_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
