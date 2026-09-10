import pytest

from src.common.resume_schema import (
    ContactInfo,
    EducationEntry,
    ExperienceEntry,
    MasterResume,
    ProjectEntry,
    SkillItem,
)
from src.tailoring.pdf_renderer import (
    DEFAULT_MAX_PROJECTS,
    _bold_matched_terms,
    _dot_meter,
    _draw_circular_photo,
    _reorder_bullets,
    _reorder_projects_for_job,
    _reorder_skills_for_job,
    _skill_is_relevant,
    render_resume_pdf,
)


def _tiny_jpeg_bytes() -> bytes:
    """A real, tiny, valid JPEG (not PII - solid color, generated here)
    for testing photo rendering without needing an actual photo file."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (20, 30), color=(120, 60, 60)).save(buf, format="JPEG")
    return buf.getvalue()


def _sample_resume(projects: list[ProjectEntry] | None = None) -> MasterResume:
    return MasterResume(
        contact=ContactInfo(
            name="Test Person", headline="Full Stack Engineer", email="test@example.com",
            phone="+1 555 0100", location="Remote", linkedin="https://linkedin.com/in/test",
        ),
        summary="A test summary.",
        skills={
            "frontend": [SkillItem(name="React", level=5), SkillItem(name="Vue.js", level=4)],
            "backend": [SkillItem(name="Node.js", level=5), SkillItem(name="PHP", level=3)],
        },
        experience=[
            ExperienceEntry(
                company="Test Co", title="Developer", location="Remote",
                date_text="2020 - Present", start_date="2020", end_date=None,
                bullets=["Did a thing.", "Did another thing."],
            )
        ],
        education=[
            EducationEntry(degree="BS Computer Science", institution="Test University", date_text="2016 - 2020")
        ],
        projects=projects or [],
        spoken_languages=[SkillItem(name="English", level=5)],
    )


def test_reorder_skills_moves_matches_to_front_without_dropping_any():
    resume = _sample_resume()
    reordered = _reorder_skills_for_job(resume, required_skills=["PHP"])
    assert reordered["backend"][0].name == "PHP"
    assert {i.name for i in reordered["backend"]} == {"Node.js", "PHP"}  # nothing added or removed
    assert {i.name for i in reordered["frontend"]} == {"React", "Vue.js"}


def test_reorder_skills_with_no_required_skills_keeps_original_order():
    resume = _sample_resume()
    reordered = _reorder_skills_for_job(resume, required_skills=None)
    assert [i.name for i in reordered["backend"]] == ["Node.js", "PHP"]


def test_reorder_never_invents_a_skill_not_in_original():
    resume = _sample_resume()
    reordered = _reorder_skills_for_job(resume, required_skills=["Rust", "Go"])
    all_skills = {i.name for group in reordered.values() for i in group}
    assert "Rust" not in all_skills
    assert "Go" not in all_skills


def test_skill_is_relevant_matches_exact_name():
    assert _skill_is_relevant("PHP", {"php"}) is True
    assert _skill_is_relevant("PHP", {"react"}) is False


def test_skill_is_relevant_matches_keyword_embedded_in_compound_label():
    # A resume very plausibly lists a compound/decorated skill name that
    # will never be byte-equal to a single extracted job keyword - this
    # is the actual bug behind a real report of "the tailored resume
    # looks identical to the original": several real skills could never
    # move to the front (or get bolded) because of this before the fix.
    assert _skill_is_relevant("HTML/CSS", {"html"}) is True
    assert _skill_is_relevant("HTML/CSS", {"css"}) is True
    assert _skill_is_relevant("GitHub Actions (CI/CD)", {"ci/cd"}) is True
    assert _skill_is_relevant("Laravel Mix", {"laravel"}) is True
    assert _skill_is_relevant("Python/Django", {"django"}) is True


def test_skill_is_relevant_does_not_false_positive_on_short_acronyms():
    # "AI" must not match merely because it's a substring of "Tailwind".
    assert _skill_is_relevant("Tailwind CSS", {"ai"}) is False


def test_reorder_skills_moves_compound_labeled_skill_to_front():
    resume = _sample_resume()
    resume.skills["frontend"] = [
        SkillItem(name="HTML/CSS", level=4), SkillItem(name="React", level=5),
    ]
    reordered = _reorder_skills_for_job(resume, required_skills=["CSS"])
    assert reordered["frontend"][0].name == "HTML/CSS"


def test_render_resume_pdf_produces_a_valid_pdf():
    resume = _sample_resume()
    pdf_bytes = render_resume_pdf(resume, required_skills=["PHP"])
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500  # not a trivially empty document


def test_render_resume_pdf_does_not_mutate_input_resume():
    resume = _sample_resume()
    original_order = list(resume.skills["backend"])
    render_resume_pdf(resume, required_skills=["PHP"])
    assert resume.skills["backend"] == original_order


def test_render_resume_pdf_accepts_tailored_content():
    resume = _sample_resume()
    pdf_bytes = render_resume_pdf(
        resume,
        tailored_summary="A tailored summary mentioning React and Node.js.",
        tailored_experience_bullets=[["Rewrote bullet one.", "Rewrote bullet two."]],
    )
    assert pdf_bytes.startswith(b"%PDF")


def _sample_projects() -> list[ProjectEntry]:
    return [
        ProjectEntry(name="A - PHP tool", description="d", tech=["PHP", "MySQL"]),
        ProjectEntry(name="B - React app", description="d", tech=["React", "TypeScript"]),
        ProjectEntry(name="C - no overlap", description="d", tech=["Rust"]),
    ]


def test_reorder_projects_moves_best_overlap_first_without_dropping_any():
    resume = _sample_resume(projects=_sample_projects())
    reordered = _reorder_projects_for_job(resume, required_skills=["React", "TypeScript"])
    assert reordered[0].name == "B - React app"
    assert {p.name for p in reordered} == {p.name for p in resume.projects}  # nothing added or removed


def test_reorder_projects_with_no_required_skills_keeps_original_order():
    resume = _sample_resume(projects=_sample_projects())
    reordered = _reorder_projects_for_job(resume, required_skills=None)
    assert [p.name for p in reordered] == [p.name for p in resume.projects]


def test_reorder_projects_never_invents_a_project():
    resume = _sample_resume(projects=_sample_projects())
    reordered = _reorder_projects_for_job(resume, required_skills=["Django", "AWS"])
    assert len(reordered) == len(resume.projects)
    assert {p.name for p in reordered} == {p.name for p in resume.projects}


def test_reorder_projects_matches_compound_tech_label():
    resume = _sample_resume(projects=[
        ProjectEntry(name="A - Django tool", description="d", tech=["Python/Django", "PostgreSQL"]),
        ProjectEntry(name="B - no overlap", description="d", tech=["Rust"]),
    ])
    reordered = _reorder_projects_for_job(resume, required_skills=["Django"])
    assert reordered[0].name == "A - Django tool"


def test_render_resume_pdf_caps_projects_rendered():
    # A resume can hold a large real portfolio (see
    # scripts/import_portfolio_projects.py); rendering all of them on
    # every PDF would be unusable, so only the top max_projects render.
    resume = _sample_resume(projects=[
        ProjectEntry(name=f"Project {i}", description="d", tech=["React"]) for i in range(20)
    ])
    assert len(resume.projects) > DEFAULT_MAX_PROJECTS
    pdf_bytes = render_resume_pdf(resume, required_skills=["React"])
    assert pdf_bytes.startswith(b"%PDF")
    # Every project's name is distinct ("Project 0".."Project 19"), so a
    # cheap proxy for "how many got rendered" is impractical without a PDF
    # text extractor here — instead verify the cap via the same selection
    # function render_resume_pdf uses internally.
    selected = _reorder_projects_for_job(resume, required_skills=["React"])[:5]
    assert len(selected) == 5


def test_project_entry_url_is_optional_and_defaults_to_none():
    resume = _sample_resume(projects=[ProjectEntry(name="No URL project", description="d", tech=["React"])])
    assert resume.projects[0].url is None


def test_project_entry_accepts_url():
    resume = _sample_resume(projects=[
        ProjectEntry(name="Live project", description="d", tech=["React"], url="https://example.com")
    ])
    pdf_bytes = render_resume_pdf(resume)
    assert pdf_bytes.startswith(b"%PDF")
    assert resume.projects[0].url == "https://example.com"


def test_bold_matched_terms_wraps_matching_term_word_boundary_safe():
    text = "Built features using React, Node.js, and MySQL"
    result = _bold_matched_terms(text, ["React", "MySQL"])
    assert "<b>React</b>" in result
    assert "<b>MySQL</b>" in result
    assert "Node.js" in result and "<b>Node.js</b>" not in result  # not requested, not bolded


def test_bold_matched_terms_does_not_false_positive_on_short_acronyms():
    text = "Worked with Tailwind CSS extensively"
    result = _bold_matched_terms(text, ["AI"])
    assert "<b>" not in result  # "AI" must not match inside "Tailwind"


def test_bold_matched_terms_does_not_double_wrap_overlapping_matches():
    # A single combined regex pass, not one re.sub per term - otherwise a
    # second pass could re-match text a prior pass already wrapped.
    text = "Experience with Node.js and general Node tooling"
    result = _bold_matched_terms(text, ["Node.js"])
    assert result.count("<b>") == 1
    assert "<b><b>" not in result


def test_bold_matched_terms_preserves_text_exactly_when_nothing_matches():
    text = "Did a thing with Rust and Go"
    assert _bold_matched_terms(text, ["React", "Vue"]) == text


def test_bold_matched_terms_handles_empty_required_skills():
    text = "Did a thing"
    assert _bold_matched_terms(text, None) == text
    assert _bold_matched_terms(text, []) == text


def test_reorder_bullets_moves_most_relevant_first():
    bullets = [
        "Participated in agile ceremonies and sprint planning",
        "Built features using React, Node.js, and MySQL",
    ]
    reordered = _reorder_bullets(bullets, wanted={"react", "node.js"})
    assert reordered[0] == "Built features using React, Node.js, and MySQL"
    assert set(reordered) == set(bullets)  # nothing added or removed


def test_reorder_bullets_with_no_required_skills_keeps_original_order():
    bullets = ["First bullet.", "Second bullet."]
    assert _reorder_bullets(bullets, wanted=set()) == bullets


def test_reorder_bullets_never_drops_a_bullet():
    bullets = ["Uses Python.", "Uses nothing relevant.", "Uses React."]
    reordered = _reorder_bullets(bullets, wanted={"react"})
    assert len(reordered) == len(bullets)
    assert set(reordered) == set(bullets)


def test_render_resume_pdf_bolds_matched_terms_in_profile_summary():
    # Direct feedback: "I want the profile summary in the resume to be
    # updated accordingly" - deterministic mode's answer is bolding
    # matched required_skills terms in the summary text, same treatment
    # already given to bullets/skills/projects, no ANTHROPIC_API_KEY
    # needed. Checked via the actual PDF's extracted text formatting
    # (pdfplumber, like the other layout regression tests here) rather
    # than the raw Paragraph markup, so this fails if the bolding never
    # actually reaches the rendered page. Uses "Kubernetes" (absent from
    # every other section of the fixture resume - skills/bullets/
    # projects) so a passing assertion can only be explained by the
    # summary paragraph itself getting bolded, not some unrelated bolded
    # occurrence elsewhere on the page (e.g. a matched sidebar skill row).
    import pdfplumber
    from io import BytesIO

    resume = _sample_resume()
    resume.summary = "Backend engineer experienced with Kubernetes and MySQL."
    pdf_bytes = render_resume_pdf(resume, required_skills=["Kubernetes"])

    pdf = pdfplumber.open(BytesIO(pdf_bytes))
    words = pdf.pages[0].extract_words(extra_attrs=["fontname"])
    matches = [w for w in words if w["text"] == "Kubernetes"]
    assert matches, "summary text didn't render at all"
    assert all("Bold" in w["fontname"] for w in matches)


def test_render_resume_pdf_tailors_experience_bullets_by_required_skills():
    resume = _sample_resume()
    resume.experience[0].bullets = [
        "Participated in agile ceremonies.",
        "Did a thing with PHP.",
    ]
    baseline = render_resume_pdf(resume, required_skills=[])
    tailored = render_resume_pdf(resume, required_skills=["PHP"])
    assert baseline != tailored  # bolding/reordering actually changes the output


def test_render_resume_pdf_rejects_mismatched_bullet_entry_count():
    resume = _sample_resume()  # has 1 experience entry
    with pytest.raises(ValueError):
        render_resume_pdf(
            resume,
            tailored_summary="x",
            tailored_experience_bullets=[["a"], ["b"]],  # 2 entries, resume has 1
        )


def test_dot_meter_renders_correct_dot_count():
    assert _dot_meter(3) == "●●●"
    assert _dot_meter(5) == "●●●●●"
    assert _dot_meter(1) == "●"


def test_dot_meter_clamps_out_of_range_values():
    assert _dot_meter(0) == ""
    assert _dot_meter(-1) == ""
    assert _dot_meter(9) == "●●●●●"  # capped at 5, never invents extra proficiency


def test_render_resume_pdf_two_column_design_produces_valid_pdf():
    resume = _sample_resume(projects=_sample_projects())
    pdf_bytes = render_resume_pdf(resume, required_skills=["PHP", "React"])
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_render_resume_pdf_handles_content_spanning_multiple_pages():
    # Regression test for a real layout bug: overflowing main-column
    # content was landing inside the (empty) sidebar frame on page 2+
    # instead of continuing in the main column, because reportlab's
    # default frame-cycling restarts from frame[0] when the last frame
    # in a PageTemplate overflows. Fixed via NextPageTemplate switching
    # every page after the first to a main-column-only template. This
    # test just needs a resume large enough to force a page break and
    # confirm doc.build() doesn't raise (LayoutError, in particular) and
    # still produces a well-formed PDF.
    resume = _sample_resume(projects=_sample_projects() * 3)
    resume.experience = resume.experience * 6
    for entry in resume.experience:
        entry.bullets = entry.bullets + ["Extra bullet padding to force overflow onto another page."] * 4
    pdf_bytes = render_resume_pdf(resume, required_skills=["PHP", "React"], max_projects=20)
    assert pdf_bytes.startswith(b"%PDF")


def test_render_resume_pdf_default_caps_at_six_projects():
    # Direct feedback: "as we are moving to 2nd page you can keep 6
    # projects" - the operator's full curated set, now that the sidebar
    # persists on page 2+ instead of that page being reclaimed as bare
    # full-width space to force everything onto exactly 2 pages.
    assert DEFAULT_MAX_PROJECTS == 6


class _FakeCanvas:
    """Minimal stand-in for reportlab's canvas - just enough surface for
    _draw_circular_photo to exercise its real clipping/drawing calls
    without needing an actual PDF document/page underneath it."""

    def __init__(self):
        self.calls: list[str] = []

    def saveState(self):
        self.calls.append("saveState")

    def restoreState(self):
        self.calls.append("restoreState")

    def beginPath(self):
        return _FakePath()

    def clipPath(self, path, stroke=0, fill=0):
        self.calls.append("clipPath")

    def drawImage(self, img, x, y, width, height, mask="auto"):
        self.calls.append("drawImage")


class _FakePath:
    def circle(self, cx, cy, r):
        pass


def test_draw_circular_photo_draws_image_for_valid_bytes():
    canvas = _FakeCanvas()
    _draw_circular_photo(canvas, _tiny_jpeg_bytes(), cx=10, cy=10, diameter=20)
    assert "drawImage" in canvas.calls
    assert canvas.calls[0] == "saveState"
    assert canvas.calls[-1] == "restoreState"  # always restores, even on the happy path


def test_draw_circular_photo_silently_does_nothing_for_corrupt_bytes():
    # A corrupt/unreadable photo must never break the rest of the PDF -
    # this is called from inside a page-decoration callback with no
    # upstream error handling of its own.
    canvas = _FakeCanvas()
    _draw_circular_photo(canvas, b"not a real image", cx=10, cy=10, diameter=20)
    assert canvas.calls == []


def test_render_resume_pdf_accepts_photo_bytes_and_produces_valid_pdf():
    resume = _sample_resume()
    pdf_bytes = render_resume_pdf(resume, photo_bytes=_tiny_jpeg_bytes())
    assert pdf_bytes.startswith(b"%PDF")


def test_render_resume_pdf_without_photo_still_renders():
    resume = _sample_resume()
    pdf_bytes = render_resume_pdf(resume, photo_bytes=None)
    assert pdf_bytes.startswith(b"%PDF")


def test_render_resume_pdf_main_content_never_lands_in_sidebar_column():
    # Regression test for a real, otherwise-invisible bug: main-column
    # content (Profile/Education/Employment) was rendering at the SAME
    # x-coordinate as the sidebar's own headers on page 2+ - reportlab's
    # frame-cycling restarting from frame[0] when NextPageTemplate was
    # placed mid-story (right after the sidebar's FrameBreak) rather than
    # as the very first flowable. "pdf_bytes.startswith(b'%PDF')" alone
    # could never catch this - it takes checking actual text coordinates
    # (pdfplumber) to see it, which is exactly how this was found and
    # confirmed fixed.
    import pdfplumber

    resume = _sample_resume(projects=_sample_projects() * 3)
    resume.experience = resume.experience * 6
    for entry in resume.experience:
        entry.bullets = entry.bullets + ["Extra bullet padding to force overflow onto another page."] * 4
    pdf_bytes = render_resume_pdf(resume, required_skills=["PHP", "React"], max_projects=20)

    from io import BytesIO

    pdf = pdfplumber.open(BytesIO(pdf_bytes))
    assert len(pdf.pages) >= 2, "test needs content that actually overflows to page 2+"

    page1_words = pdf.pages[0].extract_words()
    sidebar_x = next(w["x0"] for w in page1_words if w["text"] == "BACKEND")

    for page in pdf.pages[1:]:
        words = page.extract_words()
        for w in words:
            if w["text"] in ("PROFILE", "EDUCATION", "EMPLOYMENT", "PROJECTS"):
                assert w["x0"] != sidebar_x, (
                    f"{w['text']!r} rendered at the sidebar's own x-position "
                    f"({sidebar_x}) on a later page - it belongs in the main column"
                )


def test_render_resume_pdf_no_header_band_on_later_pages_but_sidebar_persists():
    # Direct feedback: "no need of header on 2nd page" (the dark name/
    # title band is page-1-only) BUT "I don't see the sidebar in 2nd
    # page. We need sidebar in 2nd page" - the sidebar (photo + contact +
    # skills) must actually repeat on every later page too, not vanish.
    import pdfplumber
    from io import BytesIO

    resume = _sample_resume(projects=_sample_projects() * 3)
    resume.contact.name = "Zzyxq Wobblesworth"  # distinctive - can't collide with other fixture text ("Test Co" etc.)
    resume.experience = resume.experience * 6
    for entry in resume.experience:
        entry.bullets = entry.bullets + ["Extra bullet padding to force overflow onto another page."] * 4
    pdf_bytes = render_resume_pdf(resume, photo_bytes=_tiny_jpeg_bytes(), max_projects=20)

    pdf = pdfplumber.open(BytesIO(pdf_bytes))
    assert len(pdf.pages) >= 2, "test needs content that actually overflows to page 2+"
    assert "Zzyxq" in pdf.pages[0].extract_text()  # sanity check: header IS on page 1

    for page in pdf.pages[1:]:
        words = [w["text"] for w in page.extract_words()]
        assert "Zzyxq" not in words  # the dark header band must not repeat
        assert "PERSONAL" in words  # but the sidebar itself must repeat
        assert len(page.images) > 0  # ...including the photo


def test_sidebar_content_fits_a_single_page_at_realistic_density():
    # Regression test for the ACTUAL root cause behind the "PROFILE
    # renders in the sidebar" bug above: it wasn't really about
    # NextPageTemplate ordering in isolation (that repro'd fine with a
    # handful of short paragraphs) - it only appeared once the sidebar's
    # own content was large enough to overflow ITS frame's height (the
    # real resume's 4 skill categories + languages, ~40 rows, didn't fit
    # in one page at the template's ORIGINAL spacing). This is now doubly
    # important: render_resume_pdf redraws the sidebar via
    # Frame.addFromList (see its module docstring) on every page, which -
    # unlike a normal Platypus story frame - does NOT auto-paginate on
    # overflow, it just silently stops adding and drops whatever didn't
    # fit. So "fits in exactly one page's sidebar frame" isn't just a
    # nice-to-have here, it's the only thing standing between a real
    # resume and silently losing skill categories off the bottom with no
    # error at all. Includes a photo (_CircularPhotoFlowable), since
    # production always includes one whenever resume/photo.jpg exists -
    # the realistic worst case, not the easier no-photo one.
    import pdfplumber
    from io import BytesIO

    from src.common.resume_schema import SkillItem
    from src.tailoring import pdf_renderer as pr

    resume = _sample_resume()
    resume.skills = {
        "backend": [SkillItem(name=f"Backend Skill {i}", level=5) for i in range(5)],
        "databases": [SkillItem(name=f"Database {i}", level=5) for i in range(5)],
        "frontend": [SkillItem(name=f"Frontend Skill {i}", level=5) for i in range(9)],
        "build_tools": [SkillItem(name=f"Build Tool {i}", level=5) for i in range(12)],
    }
    resume.spoken_languages = [SkillItem(name=n, level=lvl) for n, lvl in [
        ("English", 5), ("Urdu", 5), ("Pashto", 5), ("Hindi", 4), ("Arabic", 3),
    ]]

    styles = pr._styles()
    sidebar = pr._sidebar_flowables(resume, None, styles, photo_bytes=_tiny_jpeg_bytes())

    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
    from reportlab.lib.pagesizes import LETTER

    page_w, page_h = LETTER
    frame_height_first = page_h - pr.HEADER_HEIGHT - pr.PAGE_MARGIN
    sidebar_frame = Frame(
        0, pr.PAGE_MARGIN, pr.SIDEBAR_WIDTH, frame_height_first, id="sidebar",
        leftPadding=10, rightPadding=8, topPadding=10, bottomPadding=10,
    )
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=LETTER, topMargin=pr.PAGE_MARGIN, bottomMargin=pr.PAGE_MARGIN,
        leftMargin=pr.PAGE_MARGIN, rightMargin=pr.PAGE_MARGIN,
    )
    doc.addPageTemplates([PageTemplate(id="only", frames=[sidebar_frame])])
    doc.build(sidebar)

    pdf = pdfplumber.open(BytesIO(buf.getvalue()))
    assert len(pdf.pages) == 1, (
        f"sidebar content needs {len(pdf.pages)} pages at realistic density - "
        "it must fit in exactly 1, or the real render silently drops content "
        "off the bottom (Frame.addFromList doesn't auto-paginate)"
    )
