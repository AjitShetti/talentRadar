"""
api/schemas/ai_core_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for AI Core features: 
- LLM-as-a-judge matching
- Learning path generation
- Resume tailoring
"""

from pydantic import BaseModel, Field

class EvaluationResult(BaseModel):
    match_score: int = Field(..., description="Overall match score out of 100", ge=0, le=100)
    missing_skills: list[str] = Field(default_factory=list, description="Skills missing from the resume")
    reasoning: str = Field(..., description="Detailed explanation of the match score and missing skills")

class EvaluateCandidateRequest(BaseModel):
    resume_text: str = Field(..., description="The candidate's resume text")
    job_description: str = Field(..., description="The target job description text")

class EvaluateCandidateResponse(BaseModel):
    task_id: str = Field(..., description="The Celery task ID to poll for status")

class GenerateLearningPathRequest(BaseModel):
    missing_skills: list[str] = Field(..., description="List of missing skills to base the learning path on")

class GenerateLearningPathResponse(BaseModel):
    learning_path_markdown: str = Field(..., description="Markdown formatted learning plan")

class TailorResumeRequest(BaseModel):
    resume_text: str = Field(..., description="The candidate's base resume text")
    job_description: str = Field(..., description="The target job description text")
