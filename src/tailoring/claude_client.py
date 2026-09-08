"""Anthropic Claude wrapper for the resume tailoring engine's LLM half:
rewriting the summary and bullet PHRASING (never the underlying facts) to
mirror a job description's language. Activates automatically when
ANTHROPIC_API_KEY is set; src/tailoring/engine.py falls back to the
deterministic skill-reorder-only behavior when it isn't (see
is_available()).

Hard boundary, enforced by both the prompt AND the caller
(engine.py:tailor_resume_for_job's guardrail): company names, job titles,
dates, and education are NEVER sent for rewriting, and the number of
bullets returned per experience entry must exactly match the input - a
mismatch is treated as a failed tailoring attempt (falls back to
deterministic), not silently accepted with a wrong bullet count.
"""

from __future__ import annotations

import os
from typing import Optional, TypedDict

from src.common.resume_schema import MasterResume

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You tailor a job seeker's resume to a specific job posting. \
You may ONLY do two things:
1. Rewrite the professional summary (2-4 sentences) to emphasize skills and \
experience relevant to the job posting, using truthful information from the \
original summary only.
2. Rewrite the PHRASING of each existing bullet point to mirror the job \
posting's language and keywords - the underlying achievement/fact in each \
bullet must stay exactly the same, only word choice and emphasis change.

You must NEVER:
- Invent a skill, achievement, technology, or responsibility not already \
present in the original resume content given to you.
- Change, add, or remove a company name, job title, date, or degree - none \
of those are provided to you to rewrite, and none should appear altered in \
your output.
- Change the NUMBER of bullets for any experience entry - return exactly as \
many bullets per entry as were given to you, in the same order.
- Exaggerate scope, seniority, or impact beyond what the original bullet says.

If a bullet doesn't relate to the job posting at all, keep it close to the \
original phrasing rather than forcing a connection."""

TOOL_SCHEMA = {
    "name": "tailored_resume_content",
    "description": "Tailored resume content for one job application.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Rewritten 2-4 sentence professional summary.",
            },
            "experience_bullets": {
                "type": "array",
                "description": (
                    "One array per experience entry, SAME ORDER as given, each "
                    "containing exactly as many rewritten bullet strings as the "
                    "original entry had."
                ),
                "items": {"type": "array", "items": {"type": "string"}},
            },
        },
        "required": ["summary", "experience_bullets"],
    },
}


class TailoredContent(TypedDict):
    summary: str
    experience_bullets: list[list[str]]


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def request_tailored_content(
    resume: MasterResume, job_title: str, job_description: str
) -> Optional[TailoredContent]:
    """Returns tailored {summary, experience_bullets}, or None if the API
    isn't configured, the call fails, or the response doesn't parse.
    Callers MUST treat None as "fall back to the deterministic version,"
    never as an error to surface as a broken tailoring result — a resume
    that failed to reach Claude should still be a valid, truthful resume."""
    if not is_available():
        return None

    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()

    experience_payload = [
        {"company": e.company, "title": e.title, "bullets": e.bullets}
        for e in resume.experience
    ]

    user_message = (
        f"Job title: {job_title}\n\n"
        f"Job description:\n{job_description}\n\n"
        f"Original resume summary:\n{resume.summary}\n\n"
        "Original experience (company/title given for context only - do not "
        f"return them, only rewritten bullets):\n{experience_payload}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "tailored_resume_content"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception:  # noqa: BLE001 - any API failure falls back gracefully
        return None

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "tailored_resume_content":
            data = block.input
            if isinstance(data, dict) and "summary" in data and "experience_bullets" in data:
                return {"summary": data["summary"], "experience_bullets": data["experience_bullets"]}
    return None
