"""Validates resume/master_resume.json against the schema. This file
contains real PII and is git-ignored, so this test is skipped (not
failed) in any environment where it isn't present, e.g. CI."""

import json
from pathlib import Path

import pytest

from src.common.resume_schema import MasterResume

RESUME_PATH = Path("resume/master_resume.json")

pytestmark = pytest.mark.skipif(
    not RESUME_PATH.exists(), reason="resume/master_resume.json not present (git-ignored, local only)"
)


@pytest.fixture
def resume() -> MasterResume:
    data = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    return MasterResume(**data)


def test_resume_parses(resume: MasterResume):
    assert resume.contact.name
    assert resume.contact.email


def test_experience_reverse_chronological(resume: MasterResume):
    # Not a strict date parse (some entries are year-only) - just checks
    # the list wasn't accidentally reordered/truncated.
    assert len(resume.experience) == 6
    assert resume.experience[0].company == "Artistry Epoxy"
    assert resume.experience[-1].company == "PK Team + Discrete Logix"


def test_every_experience_entry_has_bullets(resume: MasterResume):
    for entry in resume.experience:
        assert entry.bullets, f"{entry.company} has no bullets"


def test_skill_levels_in_range(resume: MasterResume):
    for group in resume.skills.values():
        for skill in group:
            assert 1 <= skill.level <= 5, f"{skill.name} has out-of-range level {skill.level}"
    for lang in resume.spoken_languages:
        assert 1 <= lang.level <= 5
