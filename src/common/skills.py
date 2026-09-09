"""Known tech/role keywords, used both to build search queries
(config/search_criteria.yaml) and to tag a job's required skills by
matching them against its description text.

This is deterministic substring matching with word-boundary guards, not
NLP/ML — good enough for tagging, fully explainable, and has no model to
keep in sync. False positives/negatives are possible on unusual phrasing;
treat the tags as a helpful hint on the dashboard, not a guarantee.
"""

from __future__ import annotations

import re

# Curated from the user's own search keyword list plus the resume's
# existing skill set, so tagging stays consistent across search and
# display. Longer/more specific terms are listed before short acronyms
# so extract_skills() can report both without one masking the other.
SKILL_KEYWORDS = [
    # CMS / e-commerce
    "WordPress", "WooCommerce", "Shopify", "Elementor", "ACF",
    # PHP ecosystem
    "Laravel", "CodeIgniter", "PHP",
    # Frontend frameworks/libraries
    "Next.js", "React", "Vue.js", "Vue", "Angular", "TypeScript", "JavaScript",
    "jQuery", "HTML", "CSS", "Redux", "GraphQL",
    # Backend / runtime
    "Node.js", "Django", "Flask", "Python", "C#", ".NET",
    # Databases
    "MySQL", "PostgreSQL", "MongoDB", "Redis", "SQL Server", "SQLite",
    # Infra / DevOps
    "Docker", "Kubernetes", "AWS", "CI/CD", "GitHub Actions", "Git",
    # AI / ML
    "Machine Learning", "AI", "ML", "OpenAI", "ChatGPT", "GitHub Copilot",
    # Design
    "UI/UX", "Figma",
]


def extract_skills(text: str | None) -> list[str]:
    """Returns the subset of SKILL_KEYWORDS that appear in `text`, in
    SKILL_KEYWORDS order. Word-boundary guarded (via adjacent-character
    checks rather than \\b, since keywords like "Next.js" and "UI/UX"
    contain non-word characters \\b doesn't handle well) so short
    keywords like "AI" don't match inside unrelated words like "email"."""
    if not text:
        return []
    lowered = text.lower()
    found = []
    for keyword in SKILL_KEYWORDS:
        pattern = r"(?<![A-Za-z0-9])" + re.escape(keyword.lower()) + r"(?![A-Za-z0-9])"
        if re.search(pattern, lowered):
            found.append(keyword)
    return found
