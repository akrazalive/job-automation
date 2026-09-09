"""Renders resume/master_resume.json into a PDF using reportlab (pure
Python, no system dependencies — unlike weasyprint/wkhtmltopdf, this
works everywhere without extra installs, including inside the AWS
Lambda dashboard).

Two-column "designed CV" layout, structurally styled after the
operator's own original Word template (Full Stack Developer -
Complete.docx, git-ignored PII) but tuned for print density rather than
matching it byte-for-byte, and re-themed in a deep navy (#1B3B5F/#14293F
- see ACCENT_HEX/ACCENT_COLOR/BAND_COLOR) instead of the original's
maroon per direct feedback asking for a different, more conventionally
"professional" color: a dark header band (headshot photo + name/title,
white text) on page 1 only, a light sidebar (contact details +
per-category skills with dot-meter proficiency, matching SkillItem.level)
on the left of page 1, and Profile/Education/Employment/Projects in the
main column, which reclaims the full page width from page 2 onward once
there's no more header/sidebar to share it with (see the
"first"/"later" PageTemplate split below). The dot-meter style itself
(plain colored "●" text runs, not an image) was read directly out of the
original .docx's XML (word/document.xml run properties + shape fills),
not guessed. The original's five pictogram icons (person/envelope/
phone/house/LinkedIn) are NOT reused here — they came from a downloaded/
purchased Word template whose icon-reuse license is unknown, so
contact-detail badges use a plain colored square + 1-2 letters instead
(the LinkedIn "in" badge in the original already uses exactly this
style, just extended here to email/phone/address too).

The photo (resume/photo.jpg locally, or the private S3 copy on AWS —
see src/tailoring/engine.py:load_profile_photo) is drawn circular-clipped
into the header band via _draw_circular_photo; the header falls back to
centered text with no photo if none is available. Font sizes/spacing
throughout are deliberately tight and DEFAULT_MAX_PROJECTS defaults to
4 (not the whole project pool) specifically so a resume with a full 6
experience entries still fits 2 pages — checked against a real render
of the actual resume, not assumed.

Company names, titles, dates, and education are ALWAYS copied through
byte-for-byte from master_resume.json — never passed as parameters that
could override them. See src/common/resume_schema.py's PROTECTED_*
constants and TECHNICAL_PLAN.txt section 0 for why this boundary
matters.

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
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Table,
    TableStyle,
)

from src.common.resume_schema import MasterResume
from src.common.skills import extract_skills

# Palette — a deep navy replaces the earlier maroon (matched off the
# original .docx) per direct feedback asking for a different, more
# conventionally "professional" color. Single source of truth for the
# header band and every accent/badge/link below, so a future re-theme
# only touches here. ACCENT_HEX is kept alongside ACCENT_COLOR because
# reportlab's Paragraph mini-markup (<font color="...">) takes a hex
# string, not a Color object — this keeps both in sync from one value
# instead of a hardcoded string drifting out of sync with the theme.
ACCENT_HEX = "#1B3B5F"                        # deep navy
ACCENT_COLOR = colors.HexColor(ACCENT_HEX)    # section headers, dot meters, badges, links
BAND_COLOR = colors.HexColor("#14293F")       # header band background (darker navy)
SIDEBAR_BG = colors.HexColor("#EEF2F6")       # sidebar background tint (cool light gray-blue)
DARK_TEXT = colors.HexColor("#0F1115")        # main-column body text
BLACK_TEXT = colors.HexColor("#000000")       # education / sidebar plain text
WHITE_TEXT = colors.HexColor("#FFFFFF")       # header band text
MUTED_TEXT = colors.HexColor("#4b5563")       # dates/meta lines

# Layout geometry. The sidebar and header band run edge-to-edge (x=0),
# matching the original .docx's full-bleed color blocks; only the main
# column respects a right-hand margin. Two independent reportlab Frames
# (not a single Table) hold the sidebar/main content — a resume this
# large (up to 6 experience entries + 6 projects) routinely spans 2+
# pages, and a single-row 2-column Table can't split its own cell
# content across a page boundary (verified: reportlab raises
# LayoutError "too large on page 2" for exactly this shape). Two Frames
# on one PageTemplate each paginate independently and correctly — this
# is reportlab's documented mechanism for multi-page column layouts.
PAGE_MARGIN = 0.5 * inch
HEADER_HEIGHT = 0.82 * inch
SIDEBAR_WIDTH = 2.15 * inch
MAIN_WIDTH = LETTER[0] - SIDEBAR_WIDTH - PAGE_MARGIN
PHOTO_DIAMETER = 0.72 * inch
PHOTO_LEFT_MARGIN = 0.32 * inch


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
) -> dict[str, list]:
    """{category: [SkillItem, ...]}, with skills matching the job's
    required_skills moved to the front of each category. Order only —
    nothing added or removed, so this can never claim a skill the
    original resume didn't already list. Returns SkillItem objects (not
    just names) since the sidebar's dot-meter needs each skill's own
    .level too."""
    wanted = {s.lower() for s in (required_skills or [])}
    result: dict[str, list] = {}
    for category, items in resume.skills.items():
        matched = [i for i in items if _skill_is_relevant(i.name, wanted)]
        rest = [i for i in items if not _skill_is_relevant(i.name, wanted)]
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
# - without a cap, EVERY project would render on EVERY tailored PDF. 4
# (not the earlier 6) is what actually fits a 2-page resume alongside 6
# experience entries at this template's density - verified by rendering
# the real resume and checking the actual page count, not guessed.
DEFAULT_MAX_PROJECTS = 4


def _dot_meter(level: int) -> str:
    """A skill's 1-5 proficiency (SkillItem.level, from the original
    resume's dot meter) as a run of filled-dot characters. Matches the
    original .docx exactly: it also just used a plain text run of "●"
    characters colored maroon, not an image or drawn shape — verified in
    its own XML (a run of e.g. "●●●" for a level-3 skill, no unfilled
    trailing dots shown)."""
    return "●" * max(0, min(level, 5))


def _draw_circular_photo(canvas, photo_bytes: bytes, cx: float, cy: float, diameter: float) -> None:
    """Draws `photo_bytes` clipped to a circle centered at (cx, cy) —
    the standard reportlab technique for a headshot: clip the canvas to
    a circular path, draw the image "cover"-fit (scaled so its shorter
    side fills the circle, longer side cropped, never stretched/
    distorted) into that circle's bounding box, then restore state so
    the clip doesn't affect anything drawn afterward. Silently draws
    nothing if `photo_bytes` can't be decoded as an image — a corrupt or
    missing photo should never break the rest of the PDF."""
    try:
        img = ImageReader(BytesIO(photo_bytes))
        iw, ih = img.getSize()
    except Exception:
        return

    scale = diameter / min(iw, ih)
    draw_w, draw_h = iw * scale, ih * scale
    x, y = cx - draw_w / 2, cy - draw_h / 2

    canvas.saveState()
    path = canvas.beginPath()
    path.circle(cx, cy, diameter / 2)
    canvas.clipPath(path, stroke=0, fill=0)
    canvas.drawImage(img, x, y, width=draw_w, height=draw_h, mask="auto")
    canvas.restoreState()


def _styles() -> dict[str, ParagraphStyle]:
    # Sizes/spacing here are deliberately tight - a 2-page cap (the user's
    # explicit ask) with 6 experience entries + 4 projects doesn't have
    # room for the looser spacing an earlier version of this template
    # used (which ran to 3 pages with 6 projects). Every value below was
    # checked against a real render of the actual resume, not guessed.
    base = getSampleStyleSheet()
    return {
        "sidebar_section": ParagraphStyle(
            "sidebar_section", parent=base["Normal"], fontSize=9.5, textColor=ACCENT_COLOR,
            fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=2,
        ),
        "sidebar_item": ParagraphStyle(
            "sidebar_item", parent=base["Normal"], fontSize=7.5, textColor=BLACK_TEXT, leading=9.5,
        ),
        "sidebar_dots": ParagraphStyle(
            "sidebar_dots", parent=base["Normal"], fontSize=7, textColor=ACCENT_COLOR, alignment=2, leading=9.5,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"], fontSize=12.5, textColor=ACCENT_COLOR,
            fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3, borderPadding=0,
        ),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, textColor=DARK_TEXT, leading=11.8),
        "entry_title": ParagraphStyle(
            "entry_title", parent=base["Normal"], fontSize=9.5, textColor=DARK_TEXT,
            spaceBefore=5, fontName="Helvetica-Bold",
        ),
        "entry_meta": ParagraphStyle("entry_meta", parent=base["Normal"], fontSize=8, textColor=MUTED_TEXT, spaceAfter=2),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontSize=8.5, textColor=DARK_TEXT, leading=11.3),
        "edu": ParagraphStyle("edu", parent=base["Normal"], fontSize=9, textColor=BLACK_TEXT, leading=11.8),
    }


_NO_PAD = [
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
]


def _badge(letters: str) -> Table:
    """A small solid-maroon square with 1-2 white letters, used as a
    lightweight stand-in for the original .docx's per-field pictogram
    icons (see module docstring for why those aren't reused directly).
    The LinkedIn field in the original ALREADY uses exactly this
    letter-badge style ("in" on a colored square) - this just applies
    the same treatment uniformly to email/phone/address too."""
    t = Table([[letters]], colWidths=[0.2 * inch], rowHeights=[0.2 * inch])
    t.setStyle(TableStyle(_NO_PAD + [
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_COLOR),
        ("TEXTCOLOR", (0, 0), (-1, -1), WHITE_TEXT),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
    ]))
    return t


def _contact_row(letters: str, text: str, style: ParagraphStyle) -> Table:
    badge = _badge(letters)
    para = Paragraph(text, style)
    row = Table([[badge, para]], colWidths=[0.3 * inch, SIDEBAR_WIDTH - 0.55 * inch])
    row.setStyle(TableStyle(_NO_PAD + [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("RIGHTPADDING", (0, 0), (0, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return row


def _skill_row(name: str, level: int, matched: bool, styles: dict[str, ParagraphStyle]) -> Table:
    name_text = f"<b>{name}</b>" if matched else name
    name_col_width = SIDEBAR_WIDTH - 0.9 * inch
    row = Table(
        [[Paragraph(name_text, styles["sidebar_item"]), Paragraph(_dot_meter(level), styles["sidebar_dots"])]],
        colWidths=[name_col_width, 0.65 * inch],
    )
    row.setStyle(TableStyle(_NO_PAD + [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return row


def _sidebar_flowables(resume: MasterResume, required_skills: Optional[list[str]], styles: dict) -> list:
    wanted = {s.lower() for s in (required_skills or [])}
    c = resume.contact
    flow: list = [Paragraph("PERSONAL DETAILS", styles["sidebar_section"])]
    flow.append(_contact_row("@", c.email, styles["sidebar_item"]))
    flow.append(_contact_row("T", c.phone, styles["sidebar_item"]))
    flow.append(_contact_row("A", c.location, styles["sidebar_item"]))
    if c.linkedin:
        flow.append(_contact_row("in", c.linkedin, styles["sidebar_item"]))

    reordered = _reorder_skills_for_job(resume, required_skills)
    for category, items in reordered.items():
        label = category.replace("_", " ").title()
        flow.append(Paragraph(label.upper(), styles["sidebar_section"]))
        for item in items:
            flow.append(_skill_row(item.name, item.level, _skill_is_relevant(item.name, wanted), styles))

    if resume.spoken_languages:
        flow.append(Paragraph("LANGUAGES", styles["sidebar_section"]))
        for lang in resume.spoken_languages:
            flow.append(_skill_row(lang.name, lang.level, False, styles))

    return flow


def _main_flowables(
    resume: MasterResume,
    required_skills: Optional[list[str]],
    tailored_summary: Optional[str],
    tailored_experience_bullets: Optional[list[list[str]]],
    max_projects: int,
    styles: dict,
) -> list:
    wanted = {s.lower() for s in (required_skills or [])}
    flow: list = []

    flow.append(Paragraph("PROFILE", styles["section"]))
    flow.append(Paragraph(tailored_summary or resume.summary, styles["body"]))

    flow.append(Paragraph("EDUCATION", styles["section"]))
    for edu in resume.education:
        flow.append(Paragraph(edu.degree, styles["entry_title"]))
        flow.append(Paragraph(f"{edu.institution} &nbsp;|&nbsp; {edu.date_text}", styles["entry_meta"]))

    flow.append(Paragraph("EMPLOYMENT", styles["section"]))
    for i, entry in enumerate(resume.experience):
        title_line = f"{entry.title} — {entry.company}"
        meta_bits = [entry.date_text]
        if entry.location:
            meta_bits.append(entry.location)
        flow.append(Paragraph(title_line, styles["entry_title"]))
        flow.append(Paragraph(" &nbsp;|&nbsp; ".join(meta_bits), styles["entry_meta"]))
        bullets = tailored_experience_bullets[i] if tailored_experience_bullets is not None else entry.bullets
        bullets = _reorder_bullets(bullets, wanted)
        rendered_bullets = [_bold_matched_terms(b, required_skills) for b in bullets]
        flow.append(
            ListFlowable(
                [ListItem(Paragraph(b, styles["bullet"])) for b in rendered_bullets],
                bulletType="bullet", start="•", leftIndent=12, bulletFontSize=8,
            )
        )

    if resume.projects:
        flow.append(Paragraph("PROJECTS", styles["section"]))
        selected = _reorder_projects_for_job(resume, required_skills)[:max_projects]
        for proj in selected:
            flow.append(Paragraph(proj.name, styles["entry_title"]))
            if proj.url:
                flow.append(Paragraph(
                    f'<link href="{proj.url}"><font color="{ACCENT_HEX}">{proj.url}</font></link>', styles["entry_meta"],
                ))
            flow.append(Paragraph(proj.description, styles["body"]))
            rendered_tech = [f"<b>{t}</b>" if _skill_is_relevant(t, wanted) else t for t in proj.tech]
            flow.append(Paragraph(f"<i>Tech: {', '.join(rendered_tech)}</i>", styles["entry_meta"]))

    return flow


def render_resume_pdf(
    resume: MasterResume,
    required_skills: Optional[list[str]] = None,
    tailored_summary: Optional[str] = None,
    tailored_experience_bullets: Optional[list[list[str]]] = None,
    max_projects: int = DEFAULT_MAX_PROJECTS,
    photo_bytes: Optional[bytes] = None,
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
    doc = BaseDocTemplate(
        buf, pagesize=LETTER,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
    )
    styles = _styles()

    page_w, page_h = LETTER
    frame_bottom = PAGE_MARGIN
    # Page 1 reserves room at the top for the header band; page 2+ has no
    # header/photo at all (per direct feedback: "no need of header on 2nd
    # page, no need for photo on 2nd page"), so its frame reclaims that
    # space AND the sidebar's width - continuation pages are plain white,
    # full-width, giving more room per page (helps the 2-page target too).
    frame_height_first = page_h - HEADER_HEIGHT - PAGE_MARGIN
    frame_height_later = page_h - 2 * PAGE_MARGIN

    sidebar_frame = Frame(
        0, frame_bottom, SIDEBAR_WIDTH, frame_height_first, id="sidebar",
        leftPadding=10, rightPadding=8, topPadding=10, bottomPadding=10, showBoundary=0,
    )
    main_frame_first = Frame(
        SIDEBAR_WIDTH, frame_bottom, MAIN_WIDTH, frame_height_first, id="main",
        leftPadding=16, rightPadding=PAGE_MARGIN, topPadding=10, bottomPadding=10, showBoundary=0,
    )
    # Own geometry (full page width, no sidebar offset, taller since no
    # header) - not the same Frame object as main_frame_first reused,
    # since reportlab Frames carry internal fill state.
    main_frame_later = Frame(
        PAGE_MARGIN, frame_bottom, page_w - 2 * PAGE_MARGIN, frame_height_later, id="main-later",
        leftPadding=0, rightPadding=0, topPadding=10, bottomPadding=10, showBoundary=0,
    )

    c = resume.contact

    def _draw_background(canvas, doc_):
        # Header band, photo, and sidebar tint are PAGE 1 ONLY page
        # decoration - drawn directly (not as flowables) so they can span
        # edge-to-edge; doc_.page is reportlab's own current-page-number
        # tracker (1-indexed), the standard way an onPage callback tells
        # pages apart. See module docstring for the color source.
        if doc_.page != 1:
            return
        canvas.saveState()
        canvas.setFillColor(SIDEBAR_BG)
        canvas.rect(0, frame_bottom, SIDEBAR_WIDTH, frame_height_first, fill=1, stroke=0)
        canvas.setFillColor(BAND_COLOR)
        canvas.rect(0, page_h - HEADER_HEIGHT, page_w, HEADER_HEIGHT, fill=1, stroke=0)
        canvas.setFillColor(WHITE_TEXT)

        band_mid_y = page_h - HEADER_HEIGHT / 2
        if photo_bytes:
            # Photo on the left of the band, name/title left-aligned next
            # to it - the standard "photo CV" layout. Falls back to
            # centered text (no photo) below when there isn't one, e.g. a
            # fresh clone that hasn't run scripts/upload_profile_photo.py
            # / doesn't have resume/photo.jpg locally.
            photo_cx = PHOTO_LEFT_MARGIN + PHOTO_DIAMETER / 2
            _draw_circular_photo(canvas, photo_bytes, photo_cx, band_mid_y, PHOTO_DIAMETER)
            text_x = PHOTO_LEFT_MARGIN + PHOTO_DIAMETER + 0.25 * inch
            canvas.setFont("Helvetica-Bold", 20)
            canvas.drawString(text_x, band_mid_y + 8, c.name)
            canvas.setFont("Helvetica", 11.5)
            canvas.drawString(text_x, band_mid_y - 12, c.headline)
        else:
            canvas.setFont("Helvetica-Bold", 22)
            canvas.drawCentredString(page_w / 2, band_mid_y + 8, c.name)
            canvas.setFont("Helvetica", 12)
            canvas.drawCentredString(page_w / 2, band_mid_y - 12, c.headline)
        canvas.restoreState()

    # Page 1 uses BOTH frames (sidebar then main); page 2+ uses ONLY a
    # main-column frame. This matters because reportlab's default frame-
    # cycling, when the main frame overflows onto a new page, restarts
    # from frame[0] of the SAME template - which would dump overflowing
    # main content back into the (empty, light-colored) sidebar frame
    # instead of continuing in the main column.
    #
    # Verified TWO real failure modes here, not one - the second only
    # showed up once checked with pdfplumber's actual word coordinates,
    # not by eye:
    #   1. A 3-page render put "Fullstack Developer — Watkanikleasen.nl…"
    #      inside the sidebar's light-colored background on page 2.
    #   2. NextPageTemplate("later") placed right after the sidebar's
    #      FrameBreak (i.e. mid-story) looked like it should only affect
    #      the NEXT page turn, but empirically it disrupted FrameBreak's
    #      own same-page frame-advance too: "PROFILE"/"EDUCATION"/the
    #      start of "EMPLOYMENT" rendered at x=10pt - the SAME x as the
    #      sidebar's "PERSONAL"/"BACKEND" headers - i.e. inside
    #      sidebar_frame on page 2, even though sidebar_frame was meant
    #      to be used only once, on page 1. Confirmed by isolating a
    #      minimal repro: moving NextPageTemplate("later") to be the
    #      VERY FIRST flowable in the whole story (before any sidebar
    #      content, not after the FrameBreak) fixed it - queued that far
    #      ahead, it only affects the actual next NEW page, with no
    #      interference on frame-advances within page 1 itself.
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[sidebar_frame, main_frame_first], onPage=_draw_background),
        PageTemplate(id="later", frames=[main_frame_later], onPage=_draw_background),
    ])

    sidebar = _sidebar_flowables(resume, required_skills, styles)
    main = _main_flowables(
        resume, required_skills, tailored_summary, tailored_experience_bullets, max_projects, styles,
    )
    story = [NextPageTemplate("later")] + sidebar + [FrameBreak()] + main

    doc.build(story)
    return buf.getvalue()
