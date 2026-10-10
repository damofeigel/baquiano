"""
models.py — Pydantic models to produce a structured .json output 

"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

# Placeholder strings small models emit instead of null
_EMPTY_MARKERS = {"", "null", "none", "n/a", "na", "unknown", "not found", "not available", "-"}


class StartupInfo(BaseModel):
    """
    Core fields extracted by the finder agent per startup.
    Maps to the `actors` table (and optionally related tables).
    """
    # actors table
    name: str
    description: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    year_created: Optional[int] = None

    # Derived / extra context
    sector: Optional[str] = None          # → technological_areas
    country: Optional[str] = None         # → locations
    funding_round: Optional[str] = None   # → funding_events.round_type
    funding_amount_usd: Optional[float] = None  # → funding_events.amount

    # Provenance (optional on item level, automatically filled by FinderResult)
    source_url: Optional[str] = None      # → sources.url (fund portfolio page)
    fund_name: Optional[str] = None       # → actor name of the investor

    @field_validator(
        "description", "website", "email", "phone", "sector", "country",
        "funding_round", "source_url", "fund_name",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and v.strip().lower() in _EMPTY_MARKERS:
            return None
        return v

    @field_validator("year_created", mode="before")
    @classmethod
    def _coerce_year(cls, v):
        if v is None:
            return None
        try:
            year = int(str(v).strip()[:4])
        except (ValueError, TypeError):
            return None
        return year if 1800 <= year <= 2100 else None

    @field_validator("funding_amount_usd", mode="before")
    @classmethod
    def _coerce_amount(cls, v):
        # Unparseable amounts become null instead of failing the whole record
        if v is None or isinstance(v, (int, float)):
            return v
        try:
            return float(str(v).replace(",", "").replace("$", "").strip())
        except ValueError:
            return None


class FinderResult(BaseModel):
    """Output envelope written to output/startups.json"""
    source_url: str
    fund_name: str
    startups: list[StartupInfo]
    errors: list[str] = []

    @model_validator(mode="after")
    def _propagate_provenance(self) -> FinderResult:
        """Propagate top-level source_url and fund_name to children if missing."""
        for s in self.startups:
            if not s.source_url:
                s.source_url = self.source_url
            if not s.fund_name:
                s.fund_name = self.fund_name
        return self