"""One-off tailoring pass for the "Senior Python and MERN Stack Engineer
(EU clients)" job lead. Hand-written summary/bullet rewrites follow the
exact same rules src/tailoring/claude_client.py's SYSTEM_PROMPT enforces
on the LLM path (mirror the job posting's language/keywords, never invent
a skill/employer/date, never change a bullet's underlying fact, never
change the bullet COUNT per entry) - written directly here instead of
going through claude_client.py because no ANTHROPIC_API_KEY is configured
locally. Skills reordering/bolding/the templated "Hands-on experience
with..." sentence still runs via the normal deterministic path in
pdf_renderer.py using required_skills below.

Run: .venv/Scripts/python.exe scripts/tailor_eu_python_mern_job.py
"""

from __future__ import annotations

from src.tailoring.engine import (
    LOCAL_OUTPUT_DIR,
    _resume_filename,
    load_master_resume,
    load_profile_photo,
)
from src.tailoring.pdf_renderer import render_resume_pdf

JOB_TITLE = "Senior Python & MERN Stack Engineer"

REQUIRED_SKILLS = [
    "Python", "FastAPI", "Django", "MongoDB", "PostgreSQL",
    "Express.js", "React", "Node.js", "DevOps",
]

# Same underlying facts as resume/master_resume.json's summary, reworded
# to mirror this job posting's own language (Python, MERN stack, EU
# clients, DevOps, 10+ years, English communication). "14 years" and the
# Netherlands/Australia/USA client locations are unchanged facts already
# present in the original resume.
TAILORED_SUMMARY = (
    "Senior Full-Stack Engineer with 14 years of experience delivering scalable "
    "Python and MERN-stack (MongoDB, Express.js, React, Node.js) solutions for "
    "international and EU-based clients. Deep expertise in Python frameworks "
    "(Django, FastAPI) alongside React, Vue, Angular, and Node.js on the "
    "frontend, and PostgreSQL, MongoDB, MySQL, SQL Server, and Redis across the "
    "data layer. Hands-on with DevOps practices - Docker, Kubernetes, and CI/CD "
    "pipelines - plus AI-assisted development tooling (GitHub Copilot, ChatGPT). "
    "Strong background in technical documentation, secure coding, data "
    "protection, and agile delivery, with excellent English communication and "
    "remote-collaboration experience across teams in the Netherlands, "
    "Australia, and the USA."
)

# One list per experience entry, same order/company/title/dates as
# master_resume.json (untouched - see PROTECTED_EXPERIENCE_FIELDS),
# exactly 2 bullets each (matching the original count per entry).
# Rewording only mirrors job-posting language where the original bullet
# already states that fact (e.g. "Python/Django" bullets reordered to
# lead with Python/Django); entries with no real overlap (LinkHive, Datum
# Square, PK Team) get light phrasing polish only, per claude_client.py's
# own rule: "If a bullet doesn't relate to the job posting at all, keep
# it close to the original phrasing rather than forcing a connection."
TAILORED_BULLETS = [
    [  # Artistry Epoxy
        "Led full-stack feature development end-to-end - from concept through "
        "deployment - using React, Node.js, and MySQL, with DevOps-oriented "
        "infrastructure and release practices",
        "Architected scalable, high-performance systems following secure "
        "coding standards for data protection",
    ],
    [  # Watkanikleasen.nl
        "Built highly scalable distributed backend services in Python/Django, "
        "paired with a Vue.js/Node.js frontend, backed by MongoDB and Redis "
        "caching",
        "Owned the full agile lifecycle - design, implementation, deployment, "
        "and after-service support - end to end",
    ],
    [  # LinkHive
        "Developed responsive web applications for desktop and mobile using "
        "JavaScript, TypeScript, React, and PHP/Laravel",
        "Worked across MySQL and PostgreSQL databases, handling testing, "
        "debugging, and performance optimization",
    ],
    [  # My Hair Care
        "Built full-stack applications with Python/Django backends, Vue.js "
        "frontends, and MySQL with Redis caching",
        "Integrated third-party APIs and followed agile methodologies within "
        "cross-functional teams",
    ],
    [  # Datum Square & IT Services
        "Built 50+ web applications using PHP, MySQL, HTML, CSS, JavaScript, "
        "and jQuery",
        "Managed project timelines and collaborated with design and backend "
        "teams",
    ],
    [  # PK Team + Discrete Logix
        "Assisted in full-stack development using HTML, CSS, JavaScript, and PHP",
        "Learned Git version control and software engineering fundamentals",
    ],
]


def main() -> None:
    resume = load_master_resume()
    photo_bytes = load_profile_photo()

    if len(TAILORED_BULLETS) != len(resume.experience):
        raise SystemExit(
            f"TAILORED_BULLETS has {len(TAILORED_BULLETS)} entries, "
            f"master_resume.json has {len(resume.experience)} - update this "
            "script's TAILORED_BULLETS to match before rendering."
        )
    for i, (entry, bullets) in enumerate(zip(resume.experience, TAILORED_BULLETS)):
        if len(bullets) != len(entry.bullets):
            raise SystemExit(
                f"Entry {i} ({entry.company}) has {len(entry.bullets)} original "
                f"bullets but TAILORED_BULLETS[{i}] has {len(bullets)} - counts "
                "must match exactly."
            )

    pdf_bytes = render_resume_pdf(
        resume,
        required_skills=REQUIRED_SKILLS,
        tailored_summary=TAILORED_SUMMARY,
        tailored_experience_bullets=TAILORED_BULLETS,
        photo_bytes=photo_bytes,
        resume_title=JOB_TITLE,
    )

    LOCAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = _resume_filename(JOB_TITLE, resume)
    out_path = LOCAL_OUTPUT_DIR / filename
    out_path.write_bytes(pdf_bytes)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
