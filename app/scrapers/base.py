"""Base scraper — defines the RawOpportunity dataclass and abstract interface."""
from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class RawOpportunity:
    source_url: str
    title: str
    source_type: str = "web"  # 'web', 'instagram', 'monitored_url'
    description: str = ""
    opportunity_type: str | None = None  # grant, residency, exhibition, competition, open_call
    deadline: str | None = None
    location: str | None = None
    organization: str | None = None
    eligibility: str | None = None
    fee: str | None = None
    medium: str | None = None
    raw_data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "title": self.title,
            "source_type": self.source_type,
            "description": self.description,
            "opportunity_type": self.opportunity_type,
            "deadline": self.deadline,
            "location": self.location,
            "organization": self.organization,
            "eligibility": self.eligibility,
            "fee": self.fee,
            "medium": self.medium,
            "raw_data": self.raw_data,
        }


class BaseScraper(ABC):
    """Abstract base for all scrapers."""

    @abstractmethod
    async def scrape(self) -> list[dict]:
        """Return a list of RawOpportunity.to_dict()."""
        ...
