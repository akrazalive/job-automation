import pytest

from src.common.resume_schema import (
    ContactInfo,
    EducationEntry,
    ExperienceEntry,
    MasterResume,
    SkillItem,
)
from src.tailoring.pdf_renderer import _reorder_skills_for_job, render_resume_pdf


def _sample_resume() -> MasterResume:
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
        projects=[],
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


def test_render_resume_pdf_rejects_mismatched_bullet_entry_count():
    resume = _sample_resume()  # has 1 experience entry
    with pytest.raises(ValueError):
        render_resume_pdf(
            resume,
            tailored_summary="x",
            tailored_experience_bullets=[["a"], ["b"]],  # 2 entries, resume has 1
        )
