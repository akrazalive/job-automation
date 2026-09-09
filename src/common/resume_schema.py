"""Structured resume schema — the source of truth master_resume.json is
validated against, and what the Phase 2 tailoring engine reads/writes.

PROTECTED_FIELDS lists what the tailoring engine must never change per
the locked-in decision (see TECHNICAL_PLAN.txt section 0): company names,
job titles, dates, and education are copied through untouched. Only
`summary` phrasing, skill ORDER (never which skills exist), and bullet
PHRASING may be rewritten to mirror a job description's language. Phase 2
should diff its output against this list as a hard guardrail before ever
saving a tailored resume.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ContactInfo(BaseModel):
    name: str
    headline: str
    email: str
    phone: str
    location: str
    linkedin: Optional[str] = None


class SkillItem(BaseModel):
    name: str
    level: int  # 1-5, self-rated proficiency (from the original resume's dot meter)


class ExperienceEntry(BaseModel):
    company: str
    title: str
    location: Optional[str] = None
    date_text: str  # human-readable, e.g. "Jul 2025 - Apr 2026"
    start_date: Optional[str] = None  # "YYYY-MM" or "YYYY" if month unknown
    end_date: Optional[str] = None  # same; null means ongoing/present
    bullets: list[str]


class EducationEntry(BaseModel):
    degree: str
    institution: str
    date_text: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class ProjectEntry(BaseModel):
    name: str
    description: str
    tech: list[str]
    url: Optional[str] = None  # live link, when the project has a public one (None for private/NDA work)


class MasterResume(BaseModel):
    contact: ContactInfo
    summary: str
    skills: dict[str, list[SkillItem]]
    experience: list[ExperienceEntry]
    education: list[EducationEntry]
    projects: list[ProjectEntry]
    spoken_languages: list[SkillItem]


# Fields the Phase 2 tailoring engine must copy through unchanged for any
# given experience/education entry. Only `bullets` (phrasing, not the
# underlying facts) and the top-level `summary`/`skills` order are
# rewritable. See module docstring.
PROTECTED_EXPERIENCE_FIELDS = ("company", "title", "location", "date_text", "start_date", "end_date")
PROTECTED_EDUCATION_FIELDS = ("degree", "institution", "date_text", "start_date", "end_date")
