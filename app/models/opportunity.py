"""Opportunity ORM model."""
import hashlib
from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # 'web', 'instagram', 'monitored_url'
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    opportunity_type: Mapped[str | None] = mapped_column(String, nullable=True)  # grant, residency, exhibition, competition, open_call
    deadline: Mapped[str | None] = mapped_column(String, nullable=True)  # ISO date
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    organization: Mapped[str | None] = mapped_column(String, nullable=True)
    eligibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    fee: Mapped[str | None] = mapped_column(String, nullable=True)
    medium: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_sent: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[int] = mapped_column(Integer, default=0)
    email_sent_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    @staticmethod
    def compute_url_hash(url: str) -> str:
        return hashlib.sha256(url.strip().lower().encode()).hexdigest()

    @staticmethod
    def compute_title_hash(title: str) -> str:
        import re
        normalized = title.strip().lower()
        normalized = re.sub(r'[^\w\s]', '', normalized)
        stop_words = ['call for', 'opportunity', 'open call', '2024', '2025', '2026', 'the', 'a', 'an']
        for sw in sorted(stop_words, key=lambda sw: (-len(sw), sw)):
            normalized = normalized.replace(sw, '')
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return hashlib.sha256(normalized.encode()).hexdigest()
