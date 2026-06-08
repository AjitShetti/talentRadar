"""
api/schemas/match_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for resume-to-job-description matching endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreBreakdownSchema(BaseModel):
    """Match score breakdown by category."""
    skills: float = Field(..., ge=0, le=100, description="Skill match score (0-100)")
    experience: float = Field(..., ge=0, le=100, description="Experience match score (0-100)")
    education: float = Field(..., ge=0, le=100, description="Education match score (0-100)")
    semantic: float = Field(..., ge=0, le=100, description="Semantic similarity score (0-100)")


class MatchRequestSchema(BaseModel):
    """Resume-to-job-description matching request."""
    resume_text: str = Field(
        ...,
        min_length=10,
        max_length=50000,
        description="Full resume text (plain text or HTML)",
    )
    job_description: str = Field(
        ...,
        min_length=10,
        max_length=20000,
        description="Full job description text",
    )


class BatchMatchRequestSchema(BaseModel):
    """Batch matching request for multiple resumes."""
    resume_texts: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of resume texts to match against the job description",
    )
    job_description: str = Field(
        ...,
        min_length=10,
        max_length=20000,
        description="Job description text to match all resumes against",
    )


class SingleMatchResultSchema(BaseModel):
    """Result for a single resume-JD match."""
    match_percentage: float = Field(..., ge=0, le=100, description="Overall match percentage (0-100)")
    breakdown: ScoreBreakdownSchema = Field(..., description="Score breakdown by category")
    matched_skills: list[str] = Field(default_factory=list, description="Skills found in both resume and JD")
    missing_skills: list[str] = Field(default_factory=list, description="Skills required by JD but not in resume")
    extra_skills: list[str] = Field(default_factory=list, description="Skills in resume but not required by JD")
    confidence: float = Field(..., ge=0, le=1, description="Confidence in the match (0-1)")
    processing_time_ms: float = Field(..., ge=0, description="Processing time in milliseconds")
    warnings: list[str] = Field(default_factory=list, description="Any warnings during processing")


class BatchMatchResultSchema(BaseModel):
    """Result for a single match in a batch request."""
    match_percentage: float = Field(..., ge=0, le=100)
    breakdown: ScoreBreakdownSchema
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    extra_skills: list[str] = []
    confidence: float = Field(..., ge=0, le=1)
    resume_index: int = Field(..., ge=0, description="Index of the resume in the input list")


class BatchMatchResponseSchema(BaseModel):
    """Batch matching response."""
    results: list[BatchMatchResultSchema] = Field(..., description="Match results sorted by score (best first)")
    total_processed: int = Field(..., ge=0, description="Number of resumes processed")
    top_match_percentage: float | None = Field(None, description="Best match percentage, or None if no results")


class MatchWeightsSchema(BaseModel):
    """Scoring weights configuration."""
    skills: float = Field(0.40, ge=0, le=1, description="Weight for skills matching")
    experience: float = Field(0.25, ge=0, le=1, description="Weight for experience matching")
    education: float = Field(0.15, ge=0, le=1, description="Weight for education matching")
    semantic: float = Field(0.20, ge=0, le=1, description="Weight for semantic similarity")
