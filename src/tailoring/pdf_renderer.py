"""Renders resume/master_resume.json into a clean, ATS-friendly PDF using
reportlab (pure Python, no system dependencies — unlike weasyprint/wkhtmltopdf,
this works everywhere without extra installs).

Two tailoring modes, both bounded the same way: company names, titles,
dates, and education are ALWAYS copied through byte-for-byte from
master_resume.json — never passed as parameters that could override them.
See src/common/resume_schema.py's PROTECTED_* constants and
TECHNICAL_PLAN.txt section 0 for why this boundary matters.

  1. Deterministic (always available): given a job's required_skills
     (src/common/skills.py), each skill category's items, the Projects
     section, and each Experience entry's own bullets are REORDERED to
     put the most relevant ones first, and matching skill/tech names -
     including inside Experience bullet TEXT, via _bold_matched_terms -
     are rendered in BOLD. Nothing added, removed, or reworded anywhere;
     purely order + emphasis on content that was already true. The
     bolding exists because reordering alone can be too subtle to notice
     (a skill/bullet that was already first stays first either way) -
     bold is the always-visible proof that tailoring ran for this job.
     See _skill_is_relevant()'s docstring for why matching a resume skill
     name against required_skills isn't a plain string-equality check.
  2. LLM-assisted (when tailored_summary/tailored_experience_bullets are
     passed in — see src/tailoring/engine.py, which gets them from
     claude_client.py and only passes them through after verifying the
     bullet COUNT per experience entry matches the original exactly): the
     summary and bullet PHRASING mirror the job description's language,
     same underlying facts. This function itself does no rewriting or
     validation — engine.py's guardrail is what makes mode 2 safe to call.
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from src.common.resume_schema import MasterResume
from src.common.skills import extract_skills

# A restrained dark-green accent for headers only — body text stays
# black/dark-gray for ATS readability and print-friendliness. Ties the
# resume visually to the dashboard's brand color without turning the
# resume itself into a design piece.
ACCENT_GREEN = colors.HexColor("#15803d")
DARK_TEXT = colors.HexColor("#1a1a1a")
MUTED_TEXT = colors.HexColor("#4b5563")


def _skill_is_relevant(name: str, wanted: set[str]) -> bool:
    """True if `name` (a resume skill or a project's tech entry) is
    relevant to a job's required_skills (`wanted`, already lowercased).

    Checks a direct match first, then falls back to scanning `name` for
    any of the SAME known keywords skills.py's extract_skills() would
    pull out of a job description (word-boundary guarded, so "AI" won't
    match inside "Tailwind"). That fallback matters because resumes
    routinely list a skill under a compound/decorated label —
    "HTML/CSS", "GitHub Actions (CI/CD)", "Python/Django", "Laravel Mix"
    — that will never be byte-equal to a single extracted keyword like
    "html", "django", or "ci/cd", so a plain `name.lower() in wanted`
    check silently never matches most of a real resume's skill list.
    Verified against this project's own resume: without this fallback,
    "HTML/CSS", "GitHub Actions (CI/CD)", "Laravel Mix", "Python/Django",
    "SQL Server", and "OpenAI API" could NEVER be pulled to the front of
    their category or bolded, regardless of the job - which is why a
    tailored PDF could come out looking identical to the original for a
    real job that plainly did need several of those skills."""
    if name.lower() in wanted:
        return True
    embedded = {k.lower() for k in extract_skills(name)}
    return bool(embedded & wanted)


def _reorder_skills_for_job(
    resume: MasterResume, required_skills: Optional[list[str]]
) -> dict[str, list[str]]:
    """{category: [skill names]}, with skills matching the job's
    required_skills moved to the front of each category. Order only —
    nothing added or removed, so this can never claim a skill the
    original resume didn't already list."""
    wanted = {s.lower() for s in (required_skills or [])}
    result: dict[str, list[str]] = {}
    for category, items in resume.skills.items():
        matched = [i.name for i in items if _skill_is_relevant(i.name, wanted)]
        rest = [i.name for i in items if not _skill_is_relevant(i.name, wanted)]
        result[category] = matched + rest
    return result


def _reorder_projects_for_job(
    resume: MasterResume, required_skills: Optional[list[str]]
) -> list:
    """resume.projects reordered so entries whose `tech` list overlaps the
    job's required_skills come first (most overlap first; ties keep their
    original relative order — Python's sort is stable). Order only — every
    project the user actually lists stays in the output, nothing added or
    removed, so this can never present a project they didn't really build.
    See src/common/project_bank.py's module docstring for why the 255
    curated Project Bank ideas must never be substituted in here instead."""
    wanted = {s.lower() for s in (required_skills or [])}
    if not wanted:
        return list(resume.projects)

    def overlap(project) -> int:
        return sum(1 for t in project.tech if _skill_is_relevant(t, wanted))

    return sorted(resume.projects, key=overlap, reverse=True)


def _reorder_bullets(bullets: list[str], wanted: set[str]) -> list[str]:
    """One experience entry's bullets reordered so ones mentioning more of
    the job's required_skills come first (ties keep original order —
    stable sort). Order only, same guarantee as skill/project reordering:
    every bullet stays, none are added, removed, or reworded — the actual
    achievement text is exactly what's in master_resume.json. This is
    what keeps PROTECTED_EXPERIENCE_FIELDS meaningful: company/title/
    dates never move or change regardless of this, only which of the
    already-true bullets under a given job appears first."""
    if not wanted:
        return list(bullets)

    def score(bullet: str) -> int:
        return len({k.lower() for k in extract_skills(bullet)} & wanted)

    return sorted(bullets, key=score, reverse=True)


def _bold_matched_terms(text: str, required_skills: Optional[list[str]]) -> str:
    """Wraps any required_skills term found in `text` in <b> tags —
    word-boundary guarded the same way skills.py's extract_skills() is,
    so a short term like "AI" can't match inside an unrelated word. A
    SINGLE combined regex (terms sorted longest-first so "Node.js" wins
    over any shorter overlapping term, and matches are found in one pass)
    rather than one re.sub() call per term — repeated substitution passes
    would re-scan text a previous pass already wrapped in <b>...</b> and
    double-wrap it (e.g. a term found inside an earlier match's own
    tags). Preserves the bullet's own original wording/casing exactly —
    this only adds emphasis around text that was already there, the same
    "reorder/highlight, never reword" boundary as everywhere else in this
    file. Used on Experience bullets so a job's required tools are
    visibly highlighted in bullets that already truthfully mention them,
    not just in the Skills/Projects sections."""
    terms = sorted({s for s in (required_skills or []) if s}, key=len, reverse=True)
    if not terms:
        return text
    pattern = r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in terms) + r")(?![A-Za-z0-9])"
    return re.sub(pattern, r"<b>\1</b>", text, flags=re.IGNORECASE)


# A resume shows a curated handful of projects, not an exhaustive project
# history — this caps how many of resume.projects actually get rendered
# per PDF, taking the most relevant N after _reorder_projects_for_job
# (or, with no required_skills, just the first N in original order).
# Matters once resume.projects holds a large real portfolio (see
# scripts/import_portfolio_projects.py) rather than a handful of entries
# - without a cap, EVERY project would render on EVERY tailored PDF.
DEFAULT_MAX_PROJECTS = 6


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle("name", parent=base["Title"], fontSize=20, textColor=DARK_TEXT, spaceAfter=2, alignment=0),
        "headline": ParagraphStyle("headline", parent=base["Normal"], fontSize=11, textColor=ACCENT_GREEN, spaceAfter=6),
        "contact": ParagraphStyle("contact", parent=base["Normal"], fontSize=9, textColor=MUTED_TEXT, spaceAfter=10),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontSize=12, textColor=ACCENT_GREEN, spaceBefore=12, spaceAfter=4, borderPadding=0),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, textColor=DARK_TEXT, leading=13),
        "entry_title": ParagraphStyle("entry_title", parent=base["Normal"], fontSize=10.5, textColor=DARK_TEXT, spaceBefore=6, fontName="Helvetica-Bold"),
        "entry_meta": ParagraphStyle("entry_meta", parent=base["Normal"], fontSize=9, textColor=MUTED_TEXT, spaceAfter=3),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontSize=9.5, textColor=DARK_TEXT, leading=13),
    }


def render_resume_pdf(
    resume: MasterResume,
    required_skills: Optional[list[str]] = None,
    tailored_summary: Optional[str] = None,
    tailored_experience_bullets: Optional[list[list[str]]] = None,
    max_projects: int = DEFAULT_MAX_PROJECTS,
) -> bytes:
    """tailored_experience_bullets, if given, MUST be the same length as
    resume.experience, aligned 1:1 by index — the caller (engine.py) is
    responsible for that guarantee before calling this; this function
    trusts it and will raise if the lengths mismatch rather than silently
    misattributing bullets to the wrong job."""
    if tailored_experience_bullets is not None:
        if len(tailored_experience_bullets) != len(resume.experience):
            raise ValueError(
                f"tailored_experience_bullets has {len(tailored_experience_bullets)} entries, "
                f"resume has {len(resume.experience)} — caller must guarantee alignment"
            )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
    )
    s = _styles()
    story: list = []

    c = resume.contact
    story.append(Paragraph(c.name, s["name"]))
    story.append(Paragraph(c.headline, s["headline"]))
    contact_bits = [c.email, c.phone, c.location]
    if c.linkedin:
        contact_bits.append(c.linkedin)
    story.append(Paragraph(" &nbsp;|&nbsp; ".join(contact_bits), s["contact"]))
    story.append(HRFlowable(width="100%", thickness=0.75, color=ACCENT_GREEN, spaceAfter=8))

    story.append(Paragraph("Summary", s["section"]))
    story.append(Paragraph(tailored_summary or resume.summary, s["body"]))

    # Used below to BOLD (never add/remove/rename) the skills and project
    # tech that actually matched this job - reordering alone can be too
    # subtle to notice (e.g. a skill that was already first in its
    # category stays first either way), so this is the direct, always-
    # visible signal that tailoring actually ran for this specific job.
    wanted = {sk.lower() for sk in (required_skills or [])}

    story.append(Paragraph("Skills", s["section"]))
    reordered = _reorder_skills_for_job(resume, required_skills)
    for category, names in reordered.items():
        label = category.replace("_", " ").title()
        rendered = [f"<b>{n}</b>" if _skill_is_relevant(n, wanted) else n for n in names]
        story.append(Paragraph(f"<b>{label}:</b> {', '.join(rendered)}", s["body"]))

    story.append(Paragraph("Experience", s["section"]))
    for i, entry in enumerate(resume.experience):
        title_line = f"{entry.title} — {entry.company}"
        meta_bits = [entry.date_text]
        if entry.location:
            meta_bits.append(entry.location)
        story.append(Paragraph(title_line, s["entry_title"]))
        story.append(Paragraph(" &nbsp;|&nbsp; ".join(meta_bits), s["entry_meta"]))
        bullets = tailored_experience_bullets[i] if tailored_experience_bullets is not None else entry.bullets
        bullets = _reorder_bullets(bullets, wanted)
        rendered_bullets = [_bold_matched_terms(b, required_skills) for b in bullets]
        story.append(
            ListFlowable(
                [ListItem(Paragraph(b, s["bullet"])) for b in rendered_bullets],
                bulletType="bullet", start="•", leftIndent=14, bulletFontSize=9,
            )
        )

    story.append(Paragraph("Education", s["section"]))
    for edu in resume.education:
        story.append(Paragraph(edu.degree, s["entry_title"]))
        story.append(Paragraph(f"{edu.institution} &nbsp;|&nbsp; {edu.date_text}", s["entry_meta"]))

    if resume.projects:
        story.append(Paragraph("Projects", s["section"]))
        selected = _reorder_projects_for_job(resume, required_skills)[:max_projects]
        for proj in selected:
            story.append(Paragraph(proj.name, s["entry_title"]))
            if proj.url:
                story.append(Paragraph(f'<link href="{proj.url}"><font color="#15803d">{proj.url}</font></link>', s["entry_meta"]))
            story.append(Paragraph(proj.description, s["body"]))
            rendered_tech = [f"<b>{t}</b>" if _skill_is_relevant(t, wanted) else t for t in proj.tech]
            story.append(Paragraph(f"<i>Tech: {', '.join(rendered_tech)}</i>", s["entry_meta"]))

    if resume.spoken_languages:
        story.append(Paragraph("Languages", s["section"]))
        story.append(Paragraph(", ".join(l.name for l in resume.spoken_languages), s["body"]))

    story.append(Spacer(1, 6))

    doc.build(story)
    return buf.getvalue()
