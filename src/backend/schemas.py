"""
    Pydantic request/response models for the AutoApplier API.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

MAX_TEXT = 50_000


class Job(BaseModel):
    title: str = Field("", max_length=500)
    location: str = Field("", max_length=500)
    pay: str = Field("", max_length=500)
    description: str = Field(..., min_length=1, max_length=MAX_TEXT)
    url: Optional[str] = Field(None, max_length=2_000)


class Criteria(BaseModel):
    target_tokens: list[str] = Field(default_factory=list, max_length=200)
    banned_tokens: list[str] = Field(default_factory=list, max_length=200)
    allowed_locations: list[str] = Field(default_factory=list, max_length=200)
    minimum_hourly_rate: float = Field(0, ge=0)
    minimum_annual_salary: float = Field(0, ge=0)


class TailorRequest(BaseModel):
    job: Job
    resume_text: str = Field(..., min_length=1, max_length=MAX_TEXT)
    criteria: Optional[Criteria] = None


class LLMResult(BaseModel):
    """Strict structured-output contract for the model (see prompts/resume_builder_prompt.txt)."""
    is_qualified: bool
    gap_analysis: str
    latex_code: str


Stage = Literal["criteria", "llm", "compile", "complete"]


class TailorResult(BaseModel):
    stage: Stage
    is_qualified: bool
    reason: str = ""
    gap_analysis: str = ""
    latex_source: str = ""
    resume_pdf_base64: str = ""
    compile_log: str = ""
    page_count: int = 0          # 0 when unknown or no PDF was produced


class RequestStatus(BaseModel):
    request_id: str
    status: Literal["running", "done", "error"]
    result: Optional[TailorResult] = None
    error: Optional[str] = None
