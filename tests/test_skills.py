from src.common.skills import extract_skills


def test_extracts_multiple_known_keywords():
    text = "We need a developer skilled in React, Node.js, and MongoDB with Docker experience."
    found = extract_skills(text)
    assert "React" in found
    assert "Node.js" in found
    assert "MongoDB" in found
    assert "Docker" in found


def test_short_acronym_does_not_false_positive_inside_word():
    # "AI" must not match inside "email" or "maintain"
    text = "Please email the maintainer about the role."
    found = extract_skills(text)
    assert "AI" not in found


def test_short_acronym_matches_as_standalone_word():
    text = "Looking for an AI engineer with ML experience."
    found = extract_skills(text)
    assert "AI" in found
    assert "ML" in found


def test_dotted_keyword_matches():
    text = "Experience with Next.js and Vue.js required."
    found = extract_skills(text)
    assert "Next.js" in found
    assert "Vue.js" in found


def test_empty_text_returns_empty_list():
    assert extract_skills(None) == []
    assert extract_skills("") == []


def test_no_matches_returns_empty_list():
    assert extract_skills("We sell artisanal candles online.") == []
