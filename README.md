# Job Automation Agent

An AI agent that finds jobs on LinkedIn, Indeed, and SimplyHired, tailors your
resume to each job description, submits the application for you, and gives
you a report of everything it did — what it applied to, what succeeded, and
what failed and why.

## ⚠️ Read this before running anything

- **This automates your own accounts on LinkedIn/Indeed/SimplyHired.** All
  three prohibit bots/scraping in their Terms of Service and actively try to
  detect and block automated activity. You asked for full auto-submit
  (no human click before the final Apply), which is the fastest option but
  carries real risk of CAPTCHA walls, temporary blocks, or account
  suspension. The plan below includes rate-limiting and a "circuit breaker"
  to reduce that risk, but it cannot eliminate it. Use your own accounts,
  keep daily volume sane, and expect to occasionally need to solve a CAPTCHA
  or re-login by hand.
- **Never truthfully misrepresent your experience.** The resume tailoring
  step only reorders skills and rewrites bullet *phrasing* to mirror each
  job description's keywords. It does not invent companies, titles, dates,
  degrees, or projects you didn't do.
- **Nothing with your personal info goes into git.** Your resume, tailored
  copies, cover letters, and login sessions are all git-ignored (see
  [.gitignore](.gitignore)). They live only on your machine and in your own
  AWS account.

## What this does (end to end — the target; see "Current status" below for what's actually built today)

1. **Search** — polls LinkedIn, Indeed, and SimplyHired for jobs matching
   your title/location/keyword criteria. *(Live on all three — search/read
   only, no login on any of them. Indeed's own bot detection is
   noticeably more aggressive than the other two and will sometimes block
   a request; that's logged and skipped, not a crash, but expect lower
   real-world yield from Indeed specifically.)*
2. **Fetch** — pulls the full job description for each new posting and
   normalizes it into a common record (title, company, location, JD text,
   URL, source, posted date), and tags it with the tech skills it
   requires. *(Live.)*
3. **Tailor** — reorders your skills section to put the job's required
   skills first and renders a real PDF. Company names, job titles, dates,
   and education are never touched. *(Live, reorder-only. Bullet-phrasing
   rewrite via Claude is a planned addition, not built. Runs on demand —
   the dashboard's per-job "🎯 Tailor Resume" button — not automatically
   at scrape time, so a scrape cycle stays fast; see "Current status".)*
4. **Apply** — drives a real browser (Playwright) to open the job's Apply
   flow, upload the tailored resume, fill the form (including generic
   screening questions where possible), and submit — no human click needed.
   *(NOT built. The dashboard currently links you to the real job posting
   to apply yourself.)*
5. **Report** — logs every attempt (found / applied / failed / skipped-
   duplicate) with a reason, categorized and filterable, in a login-
   protected admin dashboard. *(Live — "applied" won't show real entries
   until step 4 exists, everything else does.)*

## Current status

**Search → tailor → dashboard is built, live, and running on real data.
The actual "click apply" bot (LinkedIn/Indeed automation) is NOT built
yet** — see the callout below. See [TECHNICAL_PLAN.txt](TECHNICAL_PLAN.txt)
for the phased build order; that file is the source of truth for "what's
done and what's next" — read it first in any future session before
writing code.

> ⚠️ **"Auto-apply" today means the pacing settings exist, not the apply
> bot.** The Settings page has apply-delay/daily-cap fields with sensible
> defaults, ready for when the apply engine (Phase 4-6) is built — but
> nothing in this repo can currently log into LinkedIn/Indeed and submit
> an application. The dashboard's "Open ↗" link takes you to the real
> job posting to apply yourself for now.

What works right now:
- **Working scrapers for all three sources** — [src/scrapers/simplyhired.py](src/scrapers/simplyhired.py),
  [src/scrapers/indeed.py](src/scrapers/indeed.py),
  [src/scrapers/linkedin.py](src/scrapers/linkedin.py) — search/read only,
  no login on any of them (that part is Phase 4/5's apply automation,
  still not built — see below). Each search entry in
  [config/search_criteria.yaml](config/search_criteria.yaml) runs against
  every source listed under that file's `sources` key. Current list: 10
  role titles (Software/Web/Back End/Front End Developer, Full Stack
  Engineer, Full Stack/Laravel/React/WordPress/WooCommerce Developer) x 4
  seniority levels (none/Senior/Junior/Intermediate) = 40 searches x 3
  sources = 120 actual searches per full cycle.
- **Filtered to globally-remote, posted within the last 7 days**
  ([src/common/location_filters.py](src/common/location_filters.py)) —
  jobs restricted to one country ("Remote (US only)" etc.) are filtered
  out by a text heuristic (best-effort, not perfect — see the module
  docstring), and SimplyHired's relative date stamp ("7d", "20h") /
  LinkedIn's absolute posted date are both parsed to drop anything older
  than `max_age_days` (config/search_criteria.yaml, default 7). A job
  whose date couldn't be parsed (always the case for Indeed — see that
  scraper's module docstring) is kept rather than guessed-and-dropped.
- **Automatic skill tagging** ([src/common/skills.py](src/common/skills.py))
  — each job's description is scanned for known tech keywords and shown
  as tags on the dashboard, so you can see at a glance what's required.
- **Tailored resume PDFs that actually reflect the job description**
  ([src/tailoring/](src/tailoring/)) — two modes, auto-selected per job:
  - **LLM-assisted** (when `ANTHROPIC_API_KEY` is set): Claude rewrites
    your summary and bullet *phrasing* to mirror the job description's
    language — same underlying facts, different emphasis/wording. A
    guardrail rejects the result outright if the bullet count per job
    doesn't exactly match the original, falling back to the safe mode
    below rather than risk a wrong resume.
  - **Deterministic fallback** (always available, and all that's run so
    far — no key has been provided yet): skills reordered to put the
    job's required skills first, nothing reworded.
  Company names, titles, dates, and education are never touched in
  either mode — see "Enabling Claude-assisted tailoring" below.
- **A free, no-API-cost project idea bank** ([project_bank.json](project_bank.json),
  183 curated ideas across 17 tech stacks) — the dashboard's "💡 Ideas"
  button suggests real, buildable projects matched to a job's detected
  skills. This is an idea generator, **not** a resume auto-writer —
  nothing gets inserted into a resume automatically; you build one for
  real and add it to your master resume yourself. See
  `src/common/project_bank.py`'s docstring for why that boundary is
  deliberate (auto-inserting these as claimed work would be resume
  fraud — see TECHNICAL_PLAN.txt's fifth-pass entry for the full
  reasoning).
- **The end-to-end pipeline** ([src/pipeline/ingest.py](src/pipeline/ingest.py))
  that ties scraping (all 3 sources) → filtering → skill tagging →
  dashboard storage together in one run — deliberately fast, since it
  does NOT tailor a resume for every job it finds (see next bullet).
- **A password-protected admin dashboard** ([src/dashboard](src/dashboard))
  — login screen (pre-filled locally), summary stats, a jobs-by-category
  report, a real paginated (5/10/15/20 per page, selectable) and
  filterable table (status/source/company/date/category) with
  posted-date, remote badges, and skill tags. Each row has an Open link,
  a **"🎯 Tailor Resume" button** (generates the PDF on demand, only once
  you've decided a job is worth applying to — a "Resume PDF" download
  link appears once one exists), and a **"Mark Applied" button** — there's
  no auto-apply bot yet, so this is how you record that you applied
  yourself and get it reflected in the stats. Plus a Scraping page (local
  runs only — see below) to edit search keywords/sources and trigger a
  manual scrape, and a Settings page for apply-delay defaults.
- A storage layer ([src/storage](src/storage)) with two interchangeable
  backends: local JSON (zero setup) and DynamoDB+S3 (real AWS) — same
  code either way, switched with one env var.
- **Deployed and live on AWS**, populated with real, filtered, current
  scraped jobs: https://6rtyzoij61.execute-api.us-east-1.amazonaws.com/ —
  login credentials are in `dashboard_login_credentials.txt`
  (git-ignored, generated at deploy time — move it to a password manager
  and delete the file).
- Your resume, digitized into a structured, schema-validated
  `resume/master_resume.json` (git-ignored — it's your real name, email,
  phone, and address).
- 210 passing tests ([tests/](tests/)).

### Run the full pipeline locally (scrape → filter → tag → save; no tailoring here — see above)

```bash
pip install -r requirements-dev.txt
playwright install chromium     # one-time browser download
python -m src.pipeline.ingest
# takes a while (40 keyword entries x 3 sources = 120 searches x anti-ban
# delays) - progress prints live, and is also visible from the
# dashboard's Scraping page while it runs
```

To write straight into the live AWS tables instead of the local file,
set `STORAGE_BACKEND=aws` plus the table/bucket env vars (see "Run the
dashboard against real AWS data" below) before running this — the
scraper itself still runs on your machine either way; only where the
results are saved changes.

### Enabling Claude-assisted resume tailoring

By default, tailoring only reorders skills (deterministic, always safe).
To have Claude also rewrite your summary and bullet *phrasing* to mirror
each job description:

1. Get an API key at [console.anthropic.com](https://console.anthropic.com/) (pay-as-you-go —
   **this costs real money per resume tailored**, one API call each time.
   Tailoring is on-demand — the dashboard's per-job "🎯 Tailor Resume"
   button, not a step `python -m src.pipeline.ingest` runs automatically —
   so the cost scales with how many jobs you actually decide to tailor,
   not with how many a scrape cycle finds.)
2. Set it before running the dashboard (or the pipeline, if you also want
   [src/tailoring/job_fetch.py](src/tailoring/job_fetch.py) available —
   it's only used by the on-demand button and by "Add Job by URL", not by
   a scrape):
   ```bash
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   uvicorn src.dashboard.app:app --reload
   ```
3. That's it — [src/tailoring/engine.py](src/tailoring/engine.py) detects
   the key automatically and switches modes per job. Nothing else changes:
   same guardrail, same protected fields, same PDF output location.

If the key is missing, invalid, or the API call fails for any reason, it
silently falls back to the deterministic skill-reorder — a broken API
call never blocks the pipeline or produces a broken resume.

### Re-tailoring one job's resume on demand ("Tailor Resume" button)

The Applications page ([/applications](src/dashboard/templates/applications.html))
has a 🎯 **Tailor Resume** button on every row. Clicking it:

1. Best-effort re-fetches the job's own URL (plain HTTP GET, not
   Playwright — see [src/tailoring/job_fetch.py](src/tailoring/job_fetch.py))
   and scans it for any *additional* skills beyond what the scraper
   already found. LinkedIn/Indeed often block or JS-wall this — that's
   fine, it just falls back to the skills already stored on the
   application, tailoring still runs.
2. Regenerates that job's PDF: skills reordered, and your **real** listed
   projects reordered by relevance — never inserts a Project Bank idea
   (see [project_bank.json](project_bank.json)) as if it were a completed
   project. Use the separate 💡 **Ideas** button for those; this project
   deliberately keeps "ideas to build" and "your submitted resume"
   walled off from each other (see [src/common/project_bank.py](src/common/project_bank.py)'s
   module docstring).
3. Shows a toast confirming the PDF updated, plus a persistent "✓
   Tailored `<timestamp>`" note on that row (survives a reload/re-filter).

This works from **both** the local dashboard and the AWS-hosted one — the
Lambda renders the PDF itself (reportlab is pure Python, no system deps).
To make that possible, a copy of `resume/master_resume.json` (your real
name/email/phone/address) lives at `s3://<ResumeBucket>/private/master_resume.json`
in the same private, SSE-encrypted, public-access-blocked bucket that
already holds every tailored PDF. Re-run this any time you edit your
resume, so the live copy stays in sync:

```bash
$env:AWS_REGION = "us-east-1"
$env:S3_BUCKET_NAME = "job-automation-resumes-607581913131-prod"
python scripts/upload_master_resume.py
```

The PDF header also shows a headshot (`resume/photo.jpg`, git-ignored —
drop your own photo there) if one exists, falling back to plain centered
text if it doesn't. Same private-bucket pattern as the resume itself —
sync it with:

```bash
$env:AWS_REGION = "us-east-1"
$env:S3_BUCKET_NAME = "job-automation-resumes-607581913131-prod"
python scripts/upload_profile_photo.py
```

### Run the dashboard locally

```bash
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
$env:DASHBOARD_USERNAME="admin"; $env:DASHBOARD_PASSWORD="pick-something"; $env:SESSION_SECRET="pick-something-random"
uvicorn src.dashboard.app:app --reload
# open http://127.0.0.1:8000 — the login form is pre-filled locally
```

⚠️ **`uvicorn` must be the one inside `.venv`, not a global install.** If
you see `ModuleNotFoundError: No module named 'itsdangerous'` (or any
other package this project installed), it means the `uvicorn` on your
PATH is a *different* Python than the one `pip install -r
requirements-dev.txt` used. Fix: either run `.venv\Scripts\Activate.ps1`
first (Windows) / `source .venv/bin/activate` (macOS/Linux) so `uvicorn`
resolves to the venv's copy, or skip activation entirely and call
`.venv\Scripts\python.exe -m uvicorn src.dashboard.app:app --reload`
directly.

Running locally (not on Lambda) also enables the Settings page's "Scrape
now" button and keyword editing, and pre-fills the login form — the same
dashboard shown read-only (and blank login) on AWS becomes interactive
here, since scraping is safe to run from your own IP but not from AWS
(see "Where automation runs" below), and the AWS login page is reachable
by anyone with the URL so it never pre-fills your password into the page
source.

### Run the dashboard against real AWS data

By default (`STORAGE_BACKEND` unset or `local`) the dashboard above reads
`data/local_applications.json`. To point that same local server at the
live DynamoDB tables / S3 bucket instead — so you see the real jobs the
deployed dashboard sees, without deploying — set `STORAGE_BACKEND=aws`
plus every table/bucket name below before `uvicorn` starts:

```bash
set STORAGE_BACKEND=aws
set AWS_REGION=us-east-1
set DYNAMODB_JOBS_TABLE=job-automation-jobs-prod
set DYNAMODB_APPLICATIONS_TABLE=job-automation-applications-prod
set S3_BUCKET_NAME=job-automation-resumes-607581913131-prod
set PROJECT_BANK_TABLE=job-automation-project-bank-prod
uvicorn src.dashboard.app:app --reload
```

⚠️ **All four table/bucket vars are required together** — `STORAGE_BACKEND=aws`
with only some of them set doesn't error, it silently reads an empty
result from whichever one is missing (e.g. the Projects page rendering
zero stacks while jobs/applications still work fine), since each
data-access module checks its own env var independently. If a page looks
sparse in AWS mode, this is the first thing to check.
`src/common/project_bank.py` does default `PROJECT_BANK_TABLE` to the
real deployed table name (`job-automation-project-bank-prod`) if it's
unset, as a safety net — but the other three have no such default, so set
all of them explicitly.

This also requires AWS credentials the process can see (`aws configure`,
or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars) with read access
to those tables/bucket.

### Deploy the dashboard to AWS (free tier)

**Already deployed and live, populated with real jobs:**
https://6rtyzoij61.execute-api.us-east-1.amazonaws.com/ (stack
`job-automation-dashboard`, region `us-east-1`) — log in with the
credentials in `dashboard_login_credentials.txt`. To redeploy after a
code change:

```bash
cd infra/aws
python prepare_lambda_src.py   # ALWAYS run this first — see why below
sam build
sam deploy --stack-name job-automation-dashboard --resolve-s3 --capabilities CAPABILITY_IAM --region us-east-1 \
  --parameter-overrides "DashboardUsername=admin" "DashboardPassword=<same-or-new-password>" "SessionSecret=<same-secret>"
```

No `samconfig.toml` was saved (deploys so far used explicit flags, not
`sam deploy --guided`), so **every** deploy needs the full
`--parameter-overrides` — reuse the values from
`dashboard_login_credentials.txt` to keep the same login, or pass a new
`DashboardPassword` to rotate it (keep `SessionSecret` the same, or every
existing session gets logged out).

⚠️ **Always run `prepare_lambda_src.py` before `sam build`.** The Lambda's
`CodeUri` points at `infra/aws/.lambda_src/`, an explicitly allow-listed
staging copy that script builds (only `src/common`, `src/storage`,
`src/dashboard` + a trimmed `requirements.txt`) — not at the repo root.
That's deliberate: pointing `CodeUri` at the repo root and trusting
`.samignore` to exclude personal files was tried first and failed in
practice — verified `sam build` still copied the real resume and a stray
AWS credentials CSV into the local build artifact despite `.samignore`
listing them. Caught before `sam deploy` (the step that actually uploads
to AWS) ran on that artifact, and fixed by not relying on an ignore-file
at all. Don't re-point `CodeUri` at the repo root without re-reading
TECHNICAL_PLAN.txt's Phase 7 notes on this first.

**Prerequisites** (already installed and configured on this machine): AWS
CLI, AWS SAM CLI, and `aws configure` pointed at an IAM user (not root)
with `AdministratorAccess`. On a fresh machine: install both via
`winget install -e --id Amazon.AWSCLI` and
`winget install -e --id Amazon.SAM-CLI`, then `aws configure`.

Nothing in this stack scrapes or applies to jobs — it's read-only storage
+ a report viewer; see the next section for why the actual automation
runs locally instead.

Decisions locked in so far:

| Decision | Choice |
|---|---|
| Apply mode | Full-auto submit (no human confirmation step) on LinkedIn & Indeed |
| Language | Python |
| Job sources (v1) | LinkedIn, Indeed, SimplyHired |
| Resume tailoring | Reorder skills + rewrite bullet phrasing only (no invented projects) |
| Hosting/storage | AWS Free Tier for storage/dashboard only (S3 + DynamoDB) — the browser automation itself runs locally, see below |
| Where automation runs | **Locally, on your machine/home IP** — not AWS. LinkedIn/Indeed treat datacenter IPs (like EC2) as a red flag independent of timing, so the risky part stays on your own network. |

> **RESOLVED 2026-09-11:** the "Apply mode" row above (full-auto submit)
> was decided early on, but every session since has pointed toward — and
> this one confirmed explicitly — the semi-auto option instead: this repo
> finds jobs, tags skills, and tailors a resume on demand; you click
> "Open" and apply yourself, then "Mark Applied" to track it. Phase 4/5's
> actual LOGIN + AUTO-SUBMIT bot is NOT wanted for now — don't build it
> without the user raising it again specifically.

## Architecture

```
                 ┌─────────────────┐
 search config → │   Scrapers       │  (SimplyHired, LinkedIn, Indeed all
                 │ (Playwright)     │   live — search/read only, no login;
                 └────────┬─────────┘   apply automation is Phase 4/5, NOT built)
                          │ new job postings
                          ▼
                 ┌─────────────────┐
                 │  Dedupe check    │──── already seen? → skip
                 │ (local/DynamoDB) │
                 └────────┬─────────┘
                          │ new job
                          ▼
                 ┌─────────────────┐
                 │ Filters          │  globally-remote-only + posted-within-
                 │ (location_filters│  N-days (src/common/location_filters.py,
                 │  .py, posted_at) │  simplyhired.py:parse_date_stamp)
                 └────────┬─────────┘
                          │ passes filters
                          ▼
                 ┌─────────────────┐
                 │ Skill tagging    │  matches JD text against a known
                 │ (src/common/     │  tech-keyword list → tags shown
                 │  skills.py)      │  on the dashboard
                 └────────┬─────────┘
                          │
                          ▼
                 ┌─────────────────┐        ┌───────────────┐
 master_resume   │ Tailoring Engine │───────►│ Claude API    │ (optional: rewrites
 .json  ───────► │ (engine.py)      │◄───────│ ANTHROPIC_    │  summary/bullet
                 │                  │ mode2  │ API_KEY)      │  phrasing; guardrail-
                 └────────┬─────────┘        └───────────────┘  checked before use
                          │ tailored resume PDF (reportlab;
                          │ mode1 = skill-reorder only, always available)
                          ▼
                 ┌─────────────────┐        ┌───────────────┐
                 │  save_application│───────►│   AWS S3      │ tailored PDF
                 │  (ingest.py)     │        │  (free tier)  │
                 └────────┬─────────┘        └───────────────┘
                          │ Application(status=pending, ...)
                          ▼
                 ┌─────────────────┐
                 │   DynamoDB       │  full job/application log
                 │  (free tier)     │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  Admin Dashboard │  login-gated; stats, category report,
                 │  (FastAPI/Lambda)│  filters, skill tags, resume downloads
                 └─────────────────┘

Not built yet: the box that would sit between "Admin Dashboard" (you click
Apply) and the job site actually being applied to — see the ⚠️ callout
above "Current status".
```

## Tech stack

- **Python 3.13** — scraping, automation, orchestration, the dashboard
- **Playwright** — browser automation for search (SimplyHired live)
- **BeautifulSoup** — HTML parsing (kept separate from Playwright so
  parsing logic is unit-testable against fixtures, no live network needed)
- **reportlab** — tailored resume PDF generation, pure Python, no system
  deps
- **FastAPI + Jinja2 + vanilla JS** — the dashboard, server-rendered with
  light client-side filtering; **itsdangerous** for signed session
  cookies (login)
- **AWS Free Tier** — S3 (resume storage), DynamoDB (job/application log),
  API Gateway + Lambda (dashboard, via **Mangum**'s ASGI adapter)
- **AWS SAM** — infrastructure as code for the AWS resources above
- **Anthropic Claude API** (`anthropic` SDK, model `claude-sonnet-5`) —
  optional bullet-phrasing rewrite, active when `ANTHROPIC_API_KEY` is
  set (see "Enabling Claude-assisted resume tailoring" above); not used
  in any run so far since no key has been provided yet

## Repo layout

```
job-automation/
├── README.md
├── TECHNICAL_PLAN.txt         # full build plan — read this first
├── .gitignore / .samignore
├── requirements.txt / requirements-dev.txt
├── .env.example                # copy to .env, fill in your own keys — never commit .env
├── config/
│   ├── search_criteria.yaml    # 40 searches x sources (simplyhired/indeed/linkedin) ✅
│   └── apply_settings.yaml     # apply-delay/cap defaults, scrape schedule      ✅
├── src/
│   ├── common/
│   │   ├── job_schema.py       # Job / Application models (+ posted_at)        ✅
│   │   ├── resume_schema.py    # MasterResume model + PROTECTED_* guardrails    ✅
│   │   ├── skills.py           # keyword-based required-skills tagging         ✅
│   │   ├── location_filters.py # globally-remote-vs-restricted heuristic       ✅
│   │   └── dedupe.py           # job_id hashing + local seen-jobs cache         ✅
│   ├── storage/                # ApplicationStore: local JSON + DynamoDB/S3,    ✅
│   │                            # both with real cursor pagination
│   ├── scrapers/
│   │   ├── base.py             # shared Playwright session, anti-ban delays,    ✅
│   │   │                        # looks_blocked() bot-challenge detection
│   │   ├── simplyhired.py      # live, incl. parse_date_stamp()                ✅
│   │   ├── linkedin.py         # live, guest search (no login), parse_date_iso() ✅
│   │   └── indeed.py           # live but frequently bot-blocked - see its docstring ✅
│   ├── tailoring/
│   │   ├── pdf_renderer.py      # reportlab PDF, skill-reorder + LLM-content modes ✅
│   │   ├── engine.py             # mode selection + LLM-result guardrail        ✅
│   │   └── claude_client.py       # Anthropic wrapper (needs ANTHROPIC_API_KEY) ✅
│   ├── pipeline/
│   │   └── ingest.py            # scrape (all sources) → filter → tag → save;   ✅
│   │                              # NO tailoring here - see dashboard's on-demand button
│   ├── apply/                    # per-site application submitters (LOGIN required) ⏳ Phase 4-6
│   └── dashboard/
│       ├── app.py                # FastAPI app: auth, settings, pagination API ✅
│       └── templates/            # base.html, login.html, settings.html, index.html ✅
├── infra/aws/
│   ├── template.yaml             # AWS SAM: DynamoDB x2 + S3 + Lambda dashboard ✅
│   └── prepare_lambda_src.py     # explicit allow-list staging - see its docstring ✅
├── resume/                       # master_resume.json (git-ignored) + output/   ✅
└── tests/                        # 61 passing                                  ✅
```

## Setup (Windows PowerShell)

```powershell
cd D:\PROJECTS\job-automation
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q                        # 75 tests should pass
```

Then see "Run the full pipeline locally" and "Run the dashboard locally"
above for how to actually use it — this section only covers install +
tests. macOS/Linux: same commands, but `source .venv/bin/activate`
instead of `.venv\Scripts\Activate.ps1`, and `export VAR=value` instead
of `$env:VAR = "value"`.

## Using the dashboard

- **Login** — every page except `/healthz` requires signing in. On the
  AWS deploy, credentials are in `dashboard_login_credentials.txt`
  (git-ignored); locally, whatever you set `DASHBOARD_USERNAME`/
  `DASHBOARD_PASSWORD` to — and the form is pre-filled there (never on
  AWS, see "Run the dashboard locally" above for why).
- **Dashboard (`/`)** — summary stat cards, a "jobs by category" report
  (found vs. applied, with a share bar), and a paginated (5/page by
  default — change it with the "Rows per page" selector: 5/10/15/20;
  Prev/Next), filterable table (status, source, company, date) showing
  each job's posted date, category, remote badge, and required-skill tags.
- **Actions column** — an "Open ↗" button to the real posting, a "Resume
  PDF" button, a **"💡 Ideas"** button, and either a "Mark Applied" button
  or a "✓ Applied" flag. There's no auto-apply bot yet (see the callout
  above) — "Mark Applied" is how you record that *you* applied yourself,
  moving a job from "Pending" into "Applied" in the stats.
- **💡 Ideas button** — shows 2-3 real, buildable project ideas matched to
  that job's detected skills, pulled from [project_bank.json](project_bank.json)
  (255 curated ideas, exactly 15 per stack across 17 tech stacks —
  WordPress, WooCommerce, Laravel, React, Django, Python ML/AI, etc).
  **This is an idea generator, not a resume auto-writer** — nothing here
  gets inserted into a resume automatically. Build one for real, then add
  it to `resume/master_resume.json` yourself so it's legitimately part of
  your work history; only then can the tailoring engine pick it for matching
  jobs. See `src/common/project_bank.py`'s docstring for why this
  boundary is deliberate, not a missing feature.
- **Resume downloads** — same PDF works from either storage backend: a
  presigned S3 URL on the AWS deploy, the local file straight off disk
  when running locally.
- **Settings (`/settings`)** — apply-delay min/max (defaults: 3-12
  minutes), the scrape cron description, the current search keyword
  list plus a form to add more, and a "Scrape now" button with a live
  log. **Only active when running locally** — on the AWS deploy this
  page is read-only with an explanation, since scraping must run from
  your own IP (see "Where automation runs" above), not AWS.
- **Project bank (`/projects`)** — browse all 255 project ideas grouped
  by stack (collapsible), with full **add / edit / delete**. Works on
  **both** local and AWS (it's a plain data write, not scraping) —
  changes locally write to `project_bank.json`; changes on the deployed
  dashboard write directly to the live DynamoDB table, so the next
  suggestion query picks up your edits immediately either way.

## Checking the AWS database directly

The dashboard is the easiest way (it's reading the exact same data), but
if you want to look at the raw DynamoDB tables:

**Option A — AWS Console (no install needed):**
1. Direct link (skips the navigation entirely):
   https://us-east-1.console.aws.amazon.com/dynamodbv2/home?region=us-east-1#table?name=job-automation-applications-prod
   — or go to [console.aws.amazon.com/dynamodbv2](https://console.aws.amazon.com/dynamodbv2/), make sure the region
   selector (top right) says **US East (N. Virginia) us-east-1**, **Tables**
   in the left sidebar → `job-automation-applications-prod`.
2. Click the **"Explore table items"** button. ⚠️ Not the **Permissions**
   tab — that shows IAM policy JSON, not your data, and looks like an
   empty table if you land there by accident.
3. `job-automation-jobs-prod` exists too but isn't currently written to —
   all the real data is in `-applications-`.
4. Still seeing nothing on the right tab/region? Check the **account ID**
   next to the region selector matches `607581913131` — a different AWS
   account (if you have more than one) would show a genuinely empty
   console with no error.

**Option B — AWS CLI** (already installed/configured on this machine):
```powershell
aws dynamodb scan --table-name job-automation-applications-prod --region us-east-1 --max-items 10
```
Add `--select COUNT` instead of browsing items if you just want a total
count. S3's tailored PDFs: `aws s3 ls s3://job-automation-resumes-607581913131-prod/resumes/`.

## Can LinkedIn/Indeed/SimplyHired's own APIs be used to apply instead of browser automation?

Short answer: **no, not for this** — asked and checked directly (2026-09-08):

- **LinkedIn**: their Talent Solutions APIs are B2B/employer-partner-only
  (job posting, ATS integrations), require a formal partnership
  application, and explicitly prohibit exactly this use case — automating
  applications on behalf of an individual job seeker.
- **Indeed**: has a publisher API, but it's for *syndicating job listings
  to a website* (read-only search), not for submitting applications as a
  candidate. No public apply API exists.
- **SimplyHired**: no public API of any kind for third parties.

There's no official, sanctioned path to auto-apply across these
platforms as an individual — browser automation (Playwright), with the
anti-ban pacing already designed into this repo (see "Safety limits"
below), remains the only technical option, and it's inherently
ToS-violating regardless of how carefully it's built. That's unchanged
from the original plan; Phase 4-6 (not built yet) is still the path.

## Safety limits (planned, see TECHNICAL_PLAN.txt section 6 for full detail)

Ranked by how much each actually reduces ban risk (delay length alone is
the *smallest* factor — don't rely on it):

1. **Runs on your local machine/home IP**, not AWS — datacenter IPs are a
   red flag to LinkedIn/Indeed on their own, regardless of timing.
2. **Consistent browser fingerprint** — one persistent, stealth-patched
   browser profile reused every run, not a fresh one per job.
3. **Randomized behavior, not fixed delays** — jobs are found in a
   separate "traverse" pass and applied to in a later "apply" pass, with
   a randomized (not flat) gap between them, plus a simulated dwell/scroll
   on the job page before applying.
4. **Daily/weekly application caps** per site, conservative by default.
5. **Circuit breaker** — any CAPTCHA/block signal pauses that site for a
   cooldown window and is logged as "blocked" (never retried in a tight
   loop), surfaced prominently on the dashboard.

This reduces risk substantially but doesn't eliminate it — full-auto
submit always carries some residual chance of a temporary block, which is
the tradeoff you've accepted for speed.
