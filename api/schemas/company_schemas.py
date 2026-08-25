"""
api/schemas/company_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for the Company Intelligence directory.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from services.company_contacts import VALID_KINDS


class CompanyCardSchema(BaseModel):
    """One company as it appears in the directory grid."""
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    domain: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    tier: str | None = None
    tier_label: str | None = None
    industry: str | None = None
    description: str | None = None
    hq_city: str | None = None
    hq_country: str | None = None
    office_cities: list[str] = Field(default_factory=list)
    employee_count_range: str | None = None
    founded_year: int | None = None
    github_org: str | None = None
    careers_url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    open_roles: int = 0


class CompanyDirectorySchema(BaseModel):
    """A page of the directory."""
    companies: list[CompanyCardSchema]
    total: int
    offset: int
    limit: int
    city: str | None = None


class ContactSchema(BaseModel):
    """A stored talent contact."""
    id: str
    kind: str
    name: str | None = None
    title: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    source_url: str | None = None
    verified: bool = False
    is_curated: bool = False
    created_at: str | None = None


class ContactCreateSchema(BaseModel):
    """
    A contact the user is saving to their own list for a company.

    At least one of ``name``, ``email`` or ``linkedin_url`` must be present —
    a contact you cannot reach is not a contact.
    """
    kind: str = Field(
        "recruiter",
        description=f"One of: {', '.join(sorted(VALID_KINDS))}",
    )
    name: str | None = Field(None, max_length=128)
    title: str | None = Field(None, max_length=128)
    email: str | None = Field(None, max_length=256)
    linkedin_url: str | None = Field(None, max_length=512)
    notes: str | None = None
    source_url: str | None = Field(
        None, max_length=512,
        description="Where this contact came from — a job post, a careers page, an email",
    )
    verified: bool = Field(
        False, description="Set only when you have confirmed the contact works"
    )


class ContactCandidateSchema(BaseModel):
    """
    A contact found on a public page by the sourced lookup.

    Always ``verified: false`` and always carries ``source_url``: these are
    read off real pages, never inferred from a naming pattern.
    """
    model_config = ConfigDict(extra="allow")

    kind: str
    name: str | None = None
    title: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    verified: bool = False


class ContactDiscoverySchema(BaseModel):
    """Result of the sourced web lookup for a company's talent contacts."""
    company: str | None = None
    careers_url: str | None = None
    candidates: list[ContactCandidateSchema] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    available: bool = True
    message: str | None = None
