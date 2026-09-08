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

## What this does (end to end)

1. **Search** — polls LinkedIn, Indeed, and SimplyHired for jobs matching
   your title/location/keyword criteria.
2. **Fetch** — pulls the full job description for each new posting and
   normalizes it into a common record (title, company, location, JD text,
   URL, source, posted date).
3. **Tailor** — sends your master resume + the job description to Claude,
   which reorders your skills section and rewrites bullet emphasis to mirror
   the JD's language. Company names, job titles, dates, and education are
   never touched.
4. **Apply** — drives a real browser (Playwright) to open the job's Apply
   flow, upload the tailored resume, fill the form (including generic
   screening questions where possible), and submit — no human click needed.
5. **Report** — logs every attempt (applied / failed / skipped-duplicate)
   with a reason, so you can see it all in one place: an admin
   dashboard reading from your own AWS account.

## Current status

**Admin dashboard is built and runnable.** Scraping, tailoring, and apply
code don't exist yet — those are the next phases. See
[TECHNICAL_PLAN.txt](TECHNICAL_PLAN.txt) for the phased build order; that
file is the source of truth for "what's done and what's next" — read it
first in any future session before writing code.

What works right now:
- A FastAPI admin dashboard ([src/dashboard](src/dashboard)) — summary
  counts + filterable table of applications (status/source/company/date).
- A storage layer ([src/storage](src/storage)) with two interchangeable
  backends: a local JSON file (zero setup, seeded with sample data — what
  you're looking at if you run it today) and a DynamoDB+S3 backend that
  activates the moment `infra/aws` is deployed — no app code changes
  needed, just `STORAGE_BACKEND=aws` in `.env`.
- An AWS SAM template ([infra/aws/template.yaml](infra/aws/template.yaml))
  that deploys the dashboard as a Lambda behind an HTTP API, plus the 2
  DynamoDB tables and the private S3 bucket — all sized to stay in AWS's
  free tier.
- Your resume, digitized into a structured, schema-validated
  `resume/master_resume.json` (git-ignored — it's your real name, email,
  phone, and address). The docx-to-structured-data step is done; turning
  it back into a downloadable tailored resume file (Phase 2) is next.
- A working SimplyHired scraper ([src/scrapers/simplyhired.py](src/scrapers/simplyhired.py))
  — searches by [config/search_criteria.yaml](config/search_criteria.yaml),
  pulls the full job description for each result, and normalizes it into
  a `Job` record. Selectors are pinned to SimplyHired's `data-testid`
  attributes (verified against the live site, not guessed). No login
  needed for this site, so it's the lowest-risk one to start with.
- **The dashboard is deployed and live on AWS**:
  https://6rtyzoij61.execute-api.us-east-1.amazonaws.com/ — currently
  shows all zeros since nothing writes into DynamoDB yet.
- 19 passing tests ([tests/](tests/)) — parsing/dashboard logic is tested
  against fixtures/local data, so the suite runs fast and doesn't hit the
  live site or AWS on every run.

Not wired up yet: scraped jobs currently only land in a local JSON file
(`data/found_jobs_simplyhired.json`, git-ignored) — they don't appear on
the dashboard yet. That wiring (and Indeed/LinkedIn scrapers, and the
tailoring engine) is next.

### Run the scraper locally

```bash
pip install -r requirements-dev.txt
playwright install chromium     # one-time browser download
python -m src.scrapers.simplyhired
# writes data/found_jobs_simplyhired.json
```

### Run it locally right now

```bash
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn src.dashboard.app:app --reload
# open http://127.0.0.1:8000
```

It'll boot with sample seed data (no AWS needed) so you can see exactly
what the report will look like once real applications start flowing in.

### Deploy the dashboard to AWS (free tier)

**Already deployed and live:** https://6rtyzoij61.execute-api.us-east-1.amazonaws.com/
(stack `job-automation-dashboard`, region `us-east-1`). It currently shows
all zeros — expected, since no scraper writes into DynamoDB yet (see
"Current status" above). To redeploy after a code change:

```bash
cd infra/aws
python prepare_lambda_src.py   # ALWAYS run this first — see why below
sam build
sam deploy
```

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

## Architecture

```
                 ┌─────────────────┐
 search config → │   Scrapers       │  (LinkedIn / Indeed / SimplyHired,
                 │ (Playwright)     │   Playwright, persisted login session)
                 └────────┬─────────┘
                          │ new job postings
                          ▼
                 ┌─────────────────┐
                 │  Dedupe check    │──── already seen? → skip, log
                 │ (DynamoDB)       │
                 └────────┬─────────┘
                          │ new job
                          ▼
                 ┌─────────────────┐
 master_resume   │ Tailoring Engine │  Claude API rewrites skills/bullets
 .json  ───────► │                  │  to mirror the JD's keywords
                 └────────┬─────────┘
                          │ tailored resume (docx/pdf)
                          ▼
                 ┌─────────────────┐        ┌───────────────┐
                 │  Apply Engine    │───────►│   AWS S3      │ tailored resume
                 │ (Playwright)     │        │  (free tier)  │ + cover letter
                 └────────┬─────────┘        └───────────────┘
                          │ result: applied / failed / blocked
                          ▼
                 ┌─────────────────┐
                 │   DynamoDB       │  full application log
                 │  (free tier)     │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  Admin Dashboard │  what was applied, what failed & why,
                 │  (small web app) │  filters by site/date/company
                 └─────────────────┘
```

## Tech stack

- **Python 3.11+** — scraping, automation, orchestration
- **Playwright** — browser automation for search + apply flows
- **Anthropic Claude API** — resume tailoring (skills reorder, bullet rewrite)
- **AWS Free Tier** — S3 (resume storage), DynamoDB (job/application log),
  EC2 t2/t3.micro or a scheduled task (worker), API Gateway + Lambda or a
  small FastAPI app (dashboard backend)
- **Dashboard** — lightweight web UI reading DynamoDB (framework TBD in
  Phase 6, see technical plan)

## Repo layout

```
job-automation/
├── README.md
├── TECHNICAL_PLAN.txt         # full build plan — read this first
├── .gitignore / .samignore
├── requirements.txt / requirements-dev.txt
├── .env.example                # copy to .env, fill in your own keys — never commit .env
├── src/
│   ├── common/
│   │   └── job_schema.py       # Job / Application pydantic models   ✅ built
│   ├── storage/
│   │   ├── base.py             # ApplicationStore interface          ✅ built
│   │   ├── local_store.py      # local JSON backend (no AWS needed)  ✅ built
│   │   └── dynamo_store.py     # DynamoDB + S3 backend                ✅ built
│   ├── dashboard/
│   │   ├── app.py              # FastAPI app + Lambda handler        ✅ built
│   │   └── templates/index.html
│   ├── scrapers/                # linkedin.py, indeed.py, simplyhired.py   ⏳ Phase 1
│   ├── tailoring/                # Claude-based rewrite engine              ⏳ Phase 2
│   ├── apply/                     # per-site application submitters         ⏳ Phase 4-6
│   └── scheduler/                  # run.py entrypoint                       ⏳ Phase 8
├── infra/aws/
│   └── template.yaml            # AWS SAM: DynamoDB + S3 + Lambda dashboard ✅ built
├── resume/                      # master_resume.json + docx template        ⏳ Phase 0
└── tests/                       # 9 passing                                 ✅
```

## Setup (Windows PowerShell)

```powershell
cd D:\PROJECTS\job-automation
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q                        # 19 tests should pass
```

**Run the dashboard** (local sample data, no AWS needed):
```powershell
uvicorn src.dashboard.app:app --reload
# open http://127.0.0.1:8000
```

**Run the dashboard against real AWS data** (the live deployed tables —
see "Deploy the dashboard to AWS" above for the URL) instead of local
sample data:
```powershell
$env:STORAGE_BACKEND = "aws"
$env:AWS_REGION = "us-east-1"
$env:DYNAMODB_JOBS_TABLE = "job-automation-jobs-prod"
$env:DYNAMODB_APPLICATIONS_TABLE = "job-automation-applications-prod"
$env:S3_BUCKET_NAME = "job-automation-resumes-607581913131-prod"
uvicorn src.dashboard.app:app --reload
```
(Or copy `.env.example` to `.env` with the same values and a tool like
`python-dotenv` picks it up — either works.) It'll show all zeros right
now since nothing writes into DynamoDB yet.

**Run the job scraper** (SimplyHired, no login needed):
```powershell
playwright install chromium      # one-time browser download, ~115 MB
python -m src.scrapers.simplyhired
# writes data\found_jobs_simplyhired.json — doesn't touch the dashboard yet
```

macOS/Linux: same commands, but `source .venv/bin/activate` instead of
`.venv\Scripts\Activate.ps1`, and `export VAR=value` instead of
`$env:VAR = "value"`.

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
