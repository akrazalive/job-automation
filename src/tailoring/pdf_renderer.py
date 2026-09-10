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
"professional" color: a dark header band (name + the title being
applied for, white text) at the top, a light sidebar (headshot photo,
contact details, and per-category skills with dot-meter proficiency,
matching SkillItem.level) down the left, and Profile/Education/
Employment in the main column next to it. The dot-meter style itself
(plain colored "●" text runs, not an image) was read directly out of the
original .docx's XML (word/document.xml run properties + shape fills),
not guessed. The original's five pictogram icons (person/envelope/
phone/house/LinkedIn) are NOT reused here — they came from a downloaded/
purchased Word template whose icon-reuse license is unknown, so
contact-detail badges use a plain colored square + 1-2 letters instead
(the LinkedIn "in" badge in the original already uses exactly this
style, just extended here to email/phone/address too).

ONE PAGE, always — direct feedback: "let us keep the resume to one page
only" (a real 2-page render with a persistent sidebar was making the
same photo/contact/skills visibly repeat, which read as a mistake, not
a feature). Two things make one page realistic at this content density:
no Projects section (direct feedback: "no need of projects I guess" -
resume.projects is simply never rendered here now; the data itself, and
_reorder_projects_for_job, are untouched in case that ever changes back
- see git history for the removed rendering code), and font sizes/
spacing tuned tight enough that a real render of the operator's actual
6 experience entries fits on 1 page - checked against that real render,
not assumed (test_render_resume_pdf_fits_one_page_at_realistic_density
encodes the same check as a regression test, same idea as the sidebar's
own one-page density test below). The "first"/"later" two-PageTemplate
machinery described next is still kept as a safety net for content that
someday doesn't fit (a future resume with many more bullets, say) - it
degrades to a correctly-laid-out 2nd page rather than a reportlab
LayoutError crash, it just should never actually trigger in practice.

The sidebar is deliberately NOT a normal Platypus story frame (unlike
the main column): it's built ONCE as a list of flowables (see
_sidebar_flowables, photo first per direct feedback: "photo should be
first thing in the sidebar") and then drawn fresh on every single page
from _draw_background via a throwaway Frame + Frame.addFromList — the
standard reportlab technique for "static" content (headers/footers/
letterhead) that repeats identically on every page regardless of how
the main column happens to paginate. This is what makes an overflow 2nd
page (see above - should be rare) both correct and safe rather than
broken: letting the sidebar be a real frame in the page's frame LIST
(like main) would put it back in the same frame-cycling trap the
"PROFILE renders inside the sidebar" bug below came from, since
reportlab always restarts a new page at frame[0] of the active
PageTemplate — main must own that slot on every page so overflow keeps
landing in the main column, not the sidebar's. Because of that, the
sidebar's own content must fully fit ONE page's sidebar height on its
own (nothing here paginates it across pages) — see
test_sidebar_content_fits_a_single_page_at_realistic_density, sized off
the operator's actual skills/languages counts, not guessed.

The photo (resume/photo.jpg locally, or the private S3 copy on AWS —
see src/tailoring/engine.py:load_profile_photo) is drawn circular-clipped
via _draw_circular_photo, wrapped in _CircularPhotoFlowable so it lays
out as an ordinary (taller, per feedback) sidebar item instead of fixed
header decoration; sidebar content below it shifts down gracefully with
no photo at all.

Company names, titles, dates, and education are ALWAYS copied through
byte-for-byte from master_resume.json — never passed as parameters that
could override them. See src/common/resume_schema.py's PROTECTED_*
constants and TECHNICAL_PLAN.txt section 0 for why this boundary
matters. Two exceptions by design: the header band's role line
(render_resume_pdf's `resume_title` renders there in place of the
resume's own generic contact.headline when a specific job title is
known - the whole point of a *tailored* resume - falls back to
contact.headline when it isn't), and the Profile summary's one appended
templated sentence (see _summary_with_matched_tools below).

  1. Deterministic (always available): given a job's required_skills
     (src/common/skills.py), each skill category's items and each
     Experience entry's own bullets are REORDERED to put the most
     relevant ones first, and matching skill/tech names - including
     inside Experience bullet TEXT and the Profile summary paragraph,
     via _bold_matched_terms - are rendered in BOLD. Nothing reworded
     anywhere; purely order + emphasis on content that was already true,
     with ONE deliberate exception: _summary_with_matched_tools appends
     a single templated sentence to the Profile paragraph naming the
     candidate's own matched skills by name (direct feedback: "make sure
     my profile summary contains a templated text tha[t] says I have
     worked on th[e]se tools mentioned in the job description") - safe
     specifically because every name in that sentence is copied verbatim
     from the resume's own skills list (never the job posting's wording,
     never a skill the resume doesn't actually list), so it restates an
     already-true fact more explicitly rather than claiming a new one.
     The bolding on everything else exists because reordering alone can
     be too subtle to notice (a skill/bullet that was already first
     stays first either way) - bold is the always-visible proof that
     tailoring ran for this job. See _skill_is_relevant()'s docstring for
     why matching a resume skill name against required_skills isn't a
     plain string-equality check.
  2. LLM-assisted (when tailored_summary/tailored_experience_bullets are
     passed in — see src/tailoring/engine.py, which gets them from
     claude_client.py and only passes them through after verifying the
     bullet COUNT per experience entry matches the original exactly): the
     summary and bullet PHRASING mirror the job description's language,
     same underlying facts. This function itself does no rewriting or
     validation — engine.py's guardrail is what makes mode 2 safe to
     call. _summary_with_matched_tools still runs on top of
     tailored_summary too, same as mode 1.
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
    Flowable,
    Frame,
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
# column respects a right-hand margin. The main column lives in a real
# reportlab Frame (so it CAN paginate across 2+ pages if content ever
# doesn't fit on 1 - see module docstring's "safety net", not the normal
# case); the sidebar does NOT (see module docstring) — it's redrawn fresh
# on every page from a throwaway Frame built with these same
# SIDEBAR_WIDTH/padding numbers, so the two stay visually aligned despite
# being unrelated to reportlab's own page-to-page frame bookkeeping.
PAGE_MARGIN = 0.5 * inch
HEADER_HEIGHT = 0.82 * inch
SIDEBAR_WIDTH = 2.15 * inch
MAIN_WIDTH = LETTER[0] - SIDEBAR_WIDTH - PAGE_MARGIN
PHOTO_DIAMETER = 1.05 * inch  # bigger than the old header-band photo, per direct feedback


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
    not just in the Skills section."""
    terms = sorted({s for s in (required_skills or []) if s}, key=len, reverse=True)
    if not terms:
        return text
    pattern = r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in terms) + r")(?![A-Za-z0-9])"
    return re.sub(pattern, r"<b>\1</b>", text, flags=re.IGNORECASE)


def _matched_skill_names(resume: MasterResume, required_skills: Optional[list[str]]) -> list[str]:
    """Resume skill NAMES, exactly as written in master_resume.json (so
    always a true statement about the candidate — never the job
    posting's own raw required_skills strings, which may be normalized/
    lowercased and don't necessarily match how the resume itself phrases
    a skill), that overlap the job's required_skills. Original
    resume/category order, de-duplicated (a skill could theoretically
    appear under more than one category). Empty when there's no job
    context at all (required_skills falsy) — see
    _summary_with_matched_tools for why that matters."""
    wanted = {s.lower() for s in (required_skills or [])}
    if not wanted:
        return []
    seen: set[str] = set()
    names: list[str] = []
    for items in resume.skills.values():
        for item in items:
            if item.name not in seen and _skill_is_relevant(item.name, wanted):
                seen.add(item.name)
                names.append(item.name)
    return names


def _join_with_and(items: list[str]) -> str:
    """"a" / "a and b" / "a, b, and c" — plain Oxford-comma English list
    join, used only by _summary_with_matched_tools's templated sentence."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _summary_with_matched_tools(
    summary_text: str, resume: MasterResume, required_skills: Optional[list[str]]
) -> str:
    """Appends ONE templated sentence naming the candidate's own matched
    skills (see _matched_skill_names) to the end of the Profile summary
    paragraph — direct feedback: "make sure my profile summary contains
    a templated text tha[t] says I have worked on th[e]se tools mentioned
    in the job description". This is the one deliberate exception to this
    file's usual "reorder/highlight, never ADD" rule (see module
    docstring): safe specifically because every name in the sentence is
    copied verbatim from the resume's OWN skills list, never the job
    posting's wording and never a skill the resume doesn't actually
    list — it restates an already-true fact more explicitly, it doesn't
    claim a new one. Returns summary_text UNCHANGED when there's no job
    context or no overlap at all (required_skills empty, or none of the
    job's required_skills match anything this resume actually lists) —
    a vague/empty claim would be worse than no added sentence."""
    names = _matched_skill_names(resume, required_skills)
    if not names:
        return summary_text
    # Plain ASCII punctuation only (a comma, not an em dash) - besides
    # sidestepping a pdfplumber/reportlab text-extraction quirk with "—"
    # seen in testing, a resume's text is also routinely run through an
    # ATS parser, where plain punctuation is the safer choice anyway.
    sentence = f"Hands-on experience with {_join_with_and(names)}, directly matching what this role calls for."
    separator = " " if summary_text.rstrip().endswith((".", "!", "?")) else ". "
    return summary_text.rstrip() + separator + sentence


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


class _CircularPhotoFlowable(Flowable):
    """Wraps _draw_circular_photo as an ordinary reportlab Flowable so the
    headshot can be laid out as the FIRST item in the sidebar's flowable
    list (direct feedback: "photo should be first thing in the sidebar")
    instead of fixed page-band decoration — reportlab accounts for its
    height like any other sidebar content, and everything below it (the
    contact rows, skill categories) simply shifts down when there's no
    photo at all. Centers itself horizontally in whatever width the
    sidebar frame actually hands it at wrap() time, so it stays centered
    even if SIDEBAR_WIDTH or its padding ever changes."""

    def __init__(self, photo_bytes: bytes, diameter: float):
        super().__init__()
        self.photo_bytes = photo_bytes
        self.diameter = diameter
        self._avail_width = diameter

    def wrap(self, availWidth, availHeight):
        self._avail_width = availWidth
        return availWidth, self.diameter + 8  # +8pt breathing room before the next item

    def draw(self):
        cx = self._avail_width / 2
        cy = self.diameter / 2
        _draw_circular_photo(self.canv, self.photo_bytes, cx, cy, self.diameter)


def _styles() -> dict[str, ParagraphStyle]:
    # Sizes/spacing here are deliberately tight - a hard ONE-page target
    # (direct feedback) with 6 real experience entries + a full sidebar
    # (photo + contact + skills) doesn't have room for looser spacing.
    # Every value below was checked against a real render of the actual
    # resume, not guessed.
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


def _sidebar_flowables(
    resume: MasterResume,
    required_skills: Optional[list[str]],
    styles: dict,
    photo_bytes: Optional[bytes] = None,
) -> list:
    wanted = {s.lower() for s in (required_skills or [])}
    c = resume.contact
    flow: list = []
    if photo_bytes:
        flow.append(_CircularPhotoFlowable(photo_bytes, PHOTO_DIAMETER))
    flow.append(Paragraph("PERSONAL DETAILS", styles["sidebar_section"]))
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
    styles: dict,
) -> list:
    wanted = {s.lower() for s in (required_skills or [])}
    flow: list = []

    flow.append(Paragraph("PROFILE", styles["section"]))
    # _summary_with_matched_tools appends the templated "Hands-on
    # experience with X, Y, and Z" sentence FIRST, then _bold_matched_terms
    # (same helper the bullets below use) runs over the whole paragraph
    # including that new sentence - so the names it just added get bolded
    # too. Mode 2's tailored_summary (actual rewritten PHRASING, when
    # Claude's guardrail-checked rewrite is available - see engine.py)
    # goes through both passes exactly the same as mode 1's resume.summary.
    summary_text = tailored_summary or resume.summary
    summary_text = _summary_with_matched_tools(summary_text, resume, required_skills)
    summary_text = _bold_matched_terms(summary_text, required_skills)
    flow.append(Paragraph(summary_text, styles["body"]))

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

    # No Projects section - direct feedback: "no need of projects I
    # guess" (see module docstring). resume.projects is simply never
    # touched here now.
    return flow


def render_resume_pdf(
    resume: MasterResume,
    required_skills: Optional[list[str]] = None,
    tailored_summary: Optional[str] = None,
    tailored_experience_bullets: Optional[list[list[str]]] = None,
    photo_bytes: Optional[bytes] = None,
    resume_title: Optional[str] = None,
) -> bytes:
    """tailored_experience_bullets, if given, MUST be the same length as
    resume.experience, aligned 1:1 by index — the caller (engine.py) is
    responsible for that guarantee before calling this; this function
    trusts it and will raise if the lengths mismatch rather than silently
    misattributing bullets to the wrong job.

    resume_title, when given, replaces resume.contact.headline as the
    role line under the name in the header band — normally the job title
    this particular PDF is being tailored for (see engine.py), so the
    resume visibly reads as "for this role" rather than a generic title.
    Falls back to resume.contact.headline when not given (e.g. an
    untargeted/master render with no specific job in play)."""
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
    # header at all (per direct feedback: "no need of header on 2nd
    # page"), so its main frame reclaims that vertical space - taller,
    # same SIDEBAR_WIDTH-offset x/width as page 1 on BOTH, since the
    # sidebar itself (see below) now persists on every page.
    frame_height_first = page_h - HEADER_HEIGHT - PAGE_MARGIN
    frame_height_later = page_h - 2 * PAGE_MARGIN

    main_frame_first = Frame(
        SIDEBAR_WIDTH, frame_bottom, MAIN_WIDTH, frame_height_first, id="main",
        leftPadding=16, rightPadding=PAGE_MARGIN, topPadding=10, bottomPadding=10, showBoundary=0,
    )
    # Own geometry (taller, since no header) - not the same Frame object
    # as main_frame_first reused, since reportlab Frames carry internal
    # fill state. x/width intentionally match main_frame_first exactly
    # (both offset by SIDEBAR_WIDTH) so the main column doesn't visibly
    # shift between page 1 and page 2+.
    main_frame_later = Frame(
        SIDEBAR_WIDTH, frame_bottom, MAIN_WIDTH, frame_height_later, id="main-later",
        leftPadding=16, rightPadding=PAGE_MARGIN, topPadding=10, bottomPadding=10, showBoundary=0,
    )

    c = resume.contact
    header_title = resume_title or c.headline
    # Built ONCE, reused identically on every page's sidebar redraw below
    # - see module docstring for why this is deliberately NOT a Platypus
    # story frame. photo_bytes is None-safe (_sidebar_flowables just
    # skips the photo item when there isn't one).
    sidebar_flowables = _sidebar_flowables(resume, required_skills, styles, photo_bytes)

    def _draw_background(canvas, doc_):
        # doc_.page is reportlab's own current-page-number tracker
        # (1-indexed), the standard way an onPage callback tells pages
        # apart. See module docstring for the color source.
        canvas.saveState()
        sidebar_height = frame_height_first if doc_.page == 1 else frame_height_later
        canvas.setFillColor(SIDEBAR_BG)
        canvas.rect(0, frame_bottom, SIDEBAR_WIDTH, sidebar_height, fill=1, stroke=0)

        if doc_.page == 1:
            # Header band is PAGE 1 ONLY - direct feedback: "no need of
            # header on 2nd page". No photo here any more (it moved into
            # the sidebar, see module docstring) so name/title are always
            # centered now - there's no photo pushing them right.
            canvas.setFillColor(BAND_COLOR)
            canvas.rect(0, page_h - HEADER_HEIGHT, page_w, HEADER_HEIGHT, fill=1, stroke=0)
            canvas.setFillColor(WHITE_TEXT)
            band_mid_y = page_h - HEADER_HEIGHT / 2
            canvas.setFont("Helvetica-Bold", 22)
            canvas.drawCentredString(page_w / 2, band_mid_y + 8, c.name)
            canvas.setFont("Helvetica", 12)
            canvas.drawCentredString(page_w / 2, band_mid_y - 12, header_title)

        # Sidebar CONTENT (photo/contact/skills) redrawn fresh on every
        # page from a throwaway Frame, not carried over via reportlab's
        # own frame-to-frame flow - see module docstring for why. A copy
        # of sidebar_flowables is passed each time since addFromList()
        # consumes (pops from) the list it's given.
        sidebar_frame = Frame(
            0, frame_bottom, SIDEBAR_WIDTH, sidebar_height,
            leftPadding=10, rightPadding=8, topPadding=10, bottomPadding=10, showBoundary=0,
        )
        sidebar_frame.addFromList(list(sidebar_flowables), canvas)
        canvas.restoreState()

    # Two single-frame templates (main column only - the sidebar is page
    # decoration, not a story frame, see above) so overflowing main
    # content always continues in a main-shaped frame at index 0 of
    # whichever template governs the next page. This is the fix for a
    # real, previously-verified bug: reportlab always restarts a new page
    # at frame[0] of the active PageTemplate, so if anything else ever
    # occupied that slot, overflowing "PROFILE"/"EMPLOYMENT" content would
    # render there instead - see
    # test_render_resume_pdf_main_content_never_lands_in_sidebar_column.
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[main_frame_first], onPage=_draw_background),
        PageTemplate(id="later", frames=[main_frame_later], onPage=_draw_background),
    ])

    main = _main_flowables(
        resume, required_skills, tailored_summary, tailored_experience_bullets, styles,
    )
    story = [NextPageTemplate("later")] + main

    doc.build(story)
    return buf.getvalue()
