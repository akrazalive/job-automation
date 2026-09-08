"""Renders resume/master_resume.json into a clean, ATS-friendly PDF using
reportlab (pure Python, no system dependencies — unlike weasyprint/wkhtmltopdf,
this works everywhere without extra installs).

Two tailoring modes, both bounded the same way: company names, titles,
dates, and education are ALWAYS copied through byte-for-byte from
master_resume.json — never passed as parameters that could override them.
See src/common/resume_schema.py's PROTECTED_* constants and
TECHNICAL_PLAN.txt section 0 for why this boundary matters.

  1. Deterministic (always available): given a job's required_skills
     (src/common/skills.py), each skill category's items are REORDERED to
     put matching skills first — nothing added, removed, or reworded.
  2. LLM-assisted (when tailored_summary/tailored_experience_bullets are
     passed in — see src/tailoring/engine.py, which gets them from
     claude_client.py and only passes them through after verifying the
     bullet COUNT per experience entry matches the original exactly): the
     summary and bullet PHRASING mirror the job description's language,
     same underlying facts. This function itself does no rewriting or
     validation — engine.py's guardrail is what makes mode 2 safe to call.
"""

from __future__ import annotations

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

# A restrained dark-green accent for headers only — body text stays
# black/dark-gray for ATS readability and print-friendliness. Ties the
# resume visually to the dashboard's brand color without turning the
# resume itself into a design piece.
ACCENT_GREEN = colors.HexColor("#15803d")
DARK_TEXT = colors.HexColor("#1a1a1a")
MUTED_TEXT = colors.HexColor("#4b5563")


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
        matched = [i.name for i in items if i.name.lower() in wanted]
        rest = [i.name for i in items if i.name.lower() not in wanted]
        result[category] = matched + rest
    return result


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

    story.append(Paragraph("Skills", s["section"]))
    reordered = _reorder_skills_for_job(resume, required_skills)
    for category, names in reordered.items():
        label = category.replace("_", " ").title()
        story.append(Paragraph(f"<b>{label}:</b> {', '.join(names)}", s["body"]))

    story.append(Paragraph("Experience", s["section"]))
    for i, entry in enumerate(resume.experience):
        title_line = f"{entry.title} — {entry.company}"
        meta_bits = [entry.date_text]
        if entry.location:
            meta_bits.append(entry.location)
        story.append(Paragraph(title_line, s["entry_title"]))
        story.append(Paragraph(" &nbsp;|&nbsp; ".join(meta_bits), s["entry_meta"]))
        bullets = tailored_experience_bullets[i] if tailored_experience_bullets is not None else entry.bullets
        story.append(
            ListFlowable(
                [ListItem(Paragraph(b, s["bullet"])) for b in bullets],
                bulletType="bullet", start="•", leftIndent=14, bulletFontSize=9,
            )
        )

    story.append(Paragraph("Education", s["section"]))
    for edu in resume.education:
        story.append(Paragraph(edu.degree, s["entry_title"]))
        story.append(Paragraph(f"{edu.institution} &nbsp;|&nbsp; {edu.date_text}", s["entry_meta"]))

    if resume.projects:
        story.append(Paragraph("Projects", s["section"]))
        for proj in resume.projects:
            story.append(Paragraph(proj.name, s["entry_title"]))
            story.append(Paragraph(proj.description, s["body"]))
            story.append(Paragraph(f"<i>Tech: {', '.join(proj.tech)}</i>", s["entry_meta"]))

    if resume.spoken_languages:
        story.append(Paragraph("Languages", s["section"]))
        story.append(Paragraph(", ".join(l.name for l in resume.spoken_languages), s["body"]))

    story.append(Spacer(1, 6))

    doc.build(story)
    return buf.getvalue()
