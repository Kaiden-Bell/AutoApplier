"""
    Pydantic request/response models for the AutoApplier API.
"""

from datetime import datetime
from typing import Literal, Optional, Union

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


# ---------- master CV structure ----------
#
# The CV is parsed once into sections that keep the user's own headings, each tagged with a
# kind. Tailoring then selects entries and bullets by ID, so code (not the model) decides which
# section an entry is printed under. See DESIGN.md §5.3.

SectionKind = Literal[
    "summary", "experience", "projects", "education", "skills", "coursework",
    "certifications", "publications", "leadership", "languages", "other",
]

# Kinds rendered as compact "Label: item, item" lines rather than entries with bullets.
LIST_KINDS = frozenset({"skills", "coursework", "languages"})

_SHORT = 300
_LONG = 2_000


# Parse output: exactly what the model returns. Kept free of defaults and length constraints so
# OpenAI strict structured outputs accepts the schema. No IDs; code assigns them.

class ParsedContact(BaseModel):
    name: str
    location: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    links: list[str]


class ParsedEntry(BaseModel):
    title: str
    organization: Optional[str]
    dates: Optional[str]
    location: Optional[str]
    tech: Optional[str]
    bullets: list[str]


class ParsedSection(BaseModel):
    heading: str
    kind: SectionKind
    entries: list[ParsedEntry]


class ParsedCV(BaseModel):
    """Strict structured-output contract for the CV parse (prompts/cv_parse_prompt.txt)."""
    contact: ParsedContact
    sections: list[ParsedSection]


# Stored form: the parse plus position-derived IDs. The extension keeps this and sends it back
# with every tailoring request; the backend reassigns the IDs on receipt, so client-sent IDs are
# never trusted.

class Contact(BaseModel):
    name: str = Field("", max_length=_SHORT)
    location: Optional[str] = Field(None, max_length=_SHORT)
    email: Optional[str] = Field(None, max_length=_SHORT)
    phone: Optional[str] = Field(None, max_length=_SHORT)
    links: list[str] = Field(default_factory=list, max_length=10)


class CVBullet(BaseModel):
    id: str = Field("", max_length=40)
    text: str = Field(..., max_length=_LONG)


class CVEntry(BaseModel):
    id: str = Field("", max_length=40)
    title: str = Field("", max_length=_SHORT)
    organization: Optional[str] = Field(None, max_length=_SHORT)
    dates: Optional[str] = Field(None, max_length=_SHORT)
    location: Optional[str] = Field(None, max_length=_SHORT)
    tech: Optional[str] = Field(None, max_length=_SHORT)
    bullets: list[CVBullet] = Field(default_factory=list, max_length=60)


class CVSection(BaseModel):
    id: str = Field("", max_length=40)
    heading: str = Field(..., max_length=_SHORT)
    kind: SectionKind
    entries: list[CVEntry] = Field(default_factory=list, max_length=80)


class MasterCV(BaseModel):
    contact: Contact
    sections: list[CVSection] = Field(..., min_length=1, max_length=40)


# Tailoring output: a selection of IDs from the MasterCV, plus optional rewording per bullet.

class BulletPick(BaseModel):
    bullet_id: str
    rewrite: Optional[str]      # null keeps the CV's wording


class EntryPick(BaseModel):
    entry_id: str
    bullets: list[BulletPick]


class SectionPick(BaseModel):
    section_id: str
    entries: list[EntryPick]


class TailoredResume(BaseModel):
    """Strict structured-output contract for tailoring (prompts/resume_builder_prompt.txt)."""
    is_qualified: bool
    gap_analysis: str
    sections: list[SectionPick]  # empty when not qualified


# ---------- API ----------

class CVParseRequest(BaseModel):
    resume_text: str = Field(..., min_length=1, max_length=MAX_TEXT)


class CVParseResult(BaseModel):
    master_cv: MasterCV
    # Bullets whose text does not appear word-for-word in the CV text; the user should check them.
    unmatched_bullets: list[str] = Field(default_factory=list)


class TailorRequest(BaseModel):
    job: Job
    master_cv: MasterCV
    criteria: Optional[Criteria] = None


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
    # Deterministic corrections made to the model's selection (resume_model.check_selection).
    selection_notes: list[str] = Field(default_factory=list)


class RequestStatus(BaseModel):
    request_id: str
    status: Literal["running", "done", "error"]
    result: Optional[Union[TailorResult, CVParseResult]] = None
    error: Optional[str] = None


# ---------- accounts and profile ----------

class ApplicationProfile(BaseModel):
    """Answers the extension uses to fill application forms (Phase E). All optional."""
    first_name: str = Field("", max_length=_SHORT)
    last_name: str = Field("", max_length=_SHORT)
    preferred_name: str = Field("", max_length=_SHORT)
    email: str = Field("", max_length=_SHORT)
    phone: str = Field("", max_length=_SHORT)
    city: str = Field("", max_length=_SHORT)
    state: str = Field("", max_length=_SHORT)
    country: str = Field("", max_length=_SHORT)
    linkedin_url: str = Field("", max_length=_SHORT)
    github_url: str = Field("", max_length=_SHORT)
    website_url: str = Field("", max_length=_SHORT)
    authorized_to_work_us: Optional[bool] = None
    needs_sponsorship: Optional[bool] = None
    willing_to_relocate: Optional[bool] = None
    earliest_start_date: str = Field("", max_length=_SHORT)
    # Voluntary demographic (EEO) questions are answered "decline to self-identify" when true.
    decline_demographic_questions: bool = True


class AccountOut(BaseModel):
    id: int
    label: str
    daily_llm_cap: int
    automation_enabled: bool


class ProfileUpdate(BaseModel):
    """PATCH /api/profile body. Omitted fields are unchanged; null clears a field."""
    notify_email: Optional[str] = Field(None, max_length=_SHORT,
                                        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    master_cv: Optional[MasterCV] = None
    criteria: Optional[Criteria] = None
    application_profile: Optional[ApplicationProfile] = None


class ProfileOut(BaseModel):
    notify_email: Optional[str] = None
    master_cv: Optional[MasterCV] = None
    criteria: Optional[Criteria] = None
    application_profile: Optional[ApplicationProfile] = None
    updated_at: Optional[datetime] = None


# ---------- discovery ----------

SourceKind = Literal["greenhouse", "lever", "ashby", "simplify"]


class AddSourceRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2_000)


class SourceOut(BaseModel):
    id: int
    kind: SourceKind
    slug: str
    name: str
    last_polled_at: Optional[datetime] = None
    last_error: Optional[str] = None
    baseline_done: bool
    open_postings: int
    new_postings_7d: int


class PostingOut(BaseModel):
    id: int
    source_id: int
    company: str
    title: str
    location: str
    pay: str
    apply_url: str
    posted_at: Optional[datetime] = None
    first_seen_at: datetime
    closed_at: Optional[datetime] = None

