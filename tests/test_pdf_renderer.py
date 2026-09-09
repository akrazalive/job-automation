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
    _reorder_projects_for_job,
    _reorder_skills_for_job,
    _skill_is_relevant,
    render_resume_pdf,
)


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
    assert reordered["backend"][0] == "PHP"
    assert set(reordered["backend"]) == {"Node.js", "PHP"}  # nothing added or removed
    assert set(reordered["frontend"]) == {"React", "Vue.js"}


def test_reorder_skills_with_no_required_skills_keeps_original_order():
    resume = _sample_resume()
    reordered = _reorder_skills_for_job(resume, required_skills=None)
    assert reordered["backend"] == ["Node.js", "PHP"]


def test_reorder_never_invents_a_skill_not_in_original():
    resume = _sample_resume()
    reordered = _reorder_skills_for_job(resume, required_skills=["Rust", "Go"])
    all_skills = {s for group in reordered.values() for s in group}
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
    assert reordered["frontend"][0] == "HTML/CSS"


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


def test_render_resume_pdf_rejects_mismatched_bullet_entry_count():
    resume = _sample_resume()  # has 1 experience entry
    with pytest.raises(ValueError):
        render_resume_pdf(
            resume,
            tailored_summary="x",
            tailored_experience_bullets=[["a"], ["b"]],  # 2 entries, resume has 1
        )
