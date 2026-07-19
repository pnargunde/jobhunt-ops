# jobhunt-ops — Architecture

## What this is

`jobhunt-ops` is a **standalone Python CLI tool** (command name: `jobhunt`) that automates the daily job-hunting workflow:

1. Search jobs across portals (LinkedIn, Seek, Indeed, Jora).
2. Score each job against your profile (skills, experience, projects) with a 1-10 `matchScore`.
3. Produce a tracking report (CSV/HTML).
4. For strong matches (`matchScore > 8`), generate an ATS-friendly, tailored resume and cover letter (PDF) — using only real, existing experience, never fabricated.
5. Produce a single-page summary of jobs ready for application.

It is **not** a Copilot CLI skill/agent and is not invoked through this chat session. It runs as its own command, on demand (typically once a day), optionally schedulable via Windows Task Scheduler later. Internally it is a **deterministic pipeline**, not an autonomous agent loop — the LLM is called for four bounded, reviewable sub-tasks only (profile synthesis, match scoring, resume content selection, cover letter drafting). This matters for the "no experience is to be faked" requirement: every generated sentence must be traceable back to the structured profile data, never invented.

## Why these technical choices

| Decision | Rationale |
|---|---|
| Python | Best ecosystem fit: Playwright bindings, resume parsing (pypdf/docx2txt), Jinja2 templating, WeasyPrint for HTML→PDF, sqlite3 built-in, pandas/csv for reporting. |
| Playwright (browser automation, logged-in session) | LinkedIn/Seek/Indeed/Jora block naive scraping (CAPTCHAs, login walls, anti-bot). Using a **persistent authenticated browser profile** (user logs in manually once) is the most reliable way to read search results without fighting bot detection. User has explicitly accepted the fragility/ToS risk tradeoff. |
| GitHub Models API for LLM calls | User has GitHub Copilot access. Auth resolved automatically via `gh auth token`, falling back to a `GITHUB_MODELS_TOKEN` env var if the `gh` CLI isn't available/authenticated. Avoids needing a separate paid API key. |
| SQLite for state | Jobs must be deduped across daily runs (keyed by URL) and retain `first_seen`/`status` history. A local file DB is simplest — no server, easy backup, easy to inspect. |
| HTML templates → PDF (Playwright/Chromium `page.pdf()`) | User wants resume/cover letter templates as HTML "ready to convert to PDF as required." Originally planned via WeasyPrint, but WeasyPrint requires GTK/Pango/Cairo system libraries that are painful to install on Windows. Since Playwright (Chromium) is already a project dependency for scraping, reusing its headless browser's built-in `page.pdf()` renders the same Jinja2-templated HTML+CSS to PDF with zero extra system dependencies beyond `playwright install chromium`, and produces real selectable text (ATS-friendly), not rasterized images. |
| `profile build` is a separate, rarely-run command | Per user: the knowledge-base (MyProfile) synthesis from resumes only needs to happen when a new skill/project/resume is added — not on every daily run. It is intentionally decoupled from `run-all`. |
| CLI is a thin layer over a plain importable package | Every capability (`search_jobs`, `score_jobs`, `generate_docs`, `build_report`, `build_summary`, `build_profile`) is a normal Python function in `src/jobhunt/*.py`, independent of Typer/argparse. `cli.py` only parses args and calls these functions. This means the exact same functions can later be wrapped as a **Copilot CLI custom skill/tool** without rewriting logic. No business logic lives inside a `@app.command()` body. |

## Designing for future use as a Copilot CLI skill

Per user request, keep this option open without building it now:
- All pipeline steps are plain functions returning structured data (dicts/dataclasses), not just printing to stdout, so a skill wrapper can consume return values programmatically.
- Config is loaded from a path that can be overridden (env var `JOBHUNT_CONFIG` or `--config`), so a skill invocation can point at a specific config without depending on the current working directory.
- No step assumes an interactive terminal except the one-time portal login helper (`jobhunt login <portal>`), which necessarily needs a headed browser and human action. Everything else (`search`, `score`, `generate`, `report`, `summary`, `run-all`) is fully non-interactive and safe to invoke programmatically.
- The browser-automation step (`search`) is the least skill-friendly (needs a real browser + logged-in session on the host machine) — a future skill wrapper would likely call `search` only when run locally, or skip straight to `score`/`report`/`summary` if jobs were already collected another way.

## High-level architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                         jobhunt CLI (Typer)                            │
│                                                                          │
│  jobhunt profile build      <- run rarely, only when resumes/skills    │
│                                 change (SEPARATE from daily pipeline)   │
│                                                                          │
│  jobhunt search              ┐                                          │
│  jobhunt score                | daily pipeline, chainable individually  │
│  jobhunt generate             | or all together via:                    │
│  jobhunt report               |    jobhunt run-all                      │
│  jobhunt summary             ┘                                          │
└───────────────┬───────────────┬───────────────┬────────────────────────┘
                │               │               │
   ┌────────────▼───┐   ┌───────▼────────┐  ┌───▼─────────────────────┐
   │ Profile Builder │   │ Job Scrapers    │  │ Matcher / Docgen /       │
   │ (resumes -> KB) │   │ (Playwright,    │  │ Report / Summary         │
   │                 │   │ per-portal)     │  │ (LLM-assisted)           │
   └────────┬────────┘   └───────┬────────┘  └───────┬──────────────────┘
            │                    │                    │
            │            ┌───────▼────────┐           │
            │            │ SQLite jobs DB │◄──────────┘
            │            │ (dedupe/state) │
            │            └───────┬────────┘
            │                    │
   ┌────────▼────────────────────▼─────────┐
   │     GitHub Models LLM client            │
   │ (gh auth token / GITHUB_MODELS_TOKEN)   │
   └──────────────────┬───────────────────────┘
                       │
              ┌────────▼─────────┐
              │ HTML templates    │  resume.html.j2, cover_letter.html.j2
              │ -> WeasyPrint PDF  │
              └────────────────────┘
```

## Data flow

### Infrequent: `jobhunt profile build`
- Input: raw resumes dropped in `profile/resumes/` (PDF/DOCX/TXT), plus any prior `my_profile.json`.
- Parses text from each resume, sends to LLM to extract/merge structured data: skills, experiences (employer, title, dates, responsibilities, achievements — verbatim/faithful), projects, education, certifications.
- Output: versioned `profile/my_profile.json` — the single knowledge base of truth used by scoring and doc generation. Never auto-invents content; only reorganizes/synthesizes what's present across resumes.

### Daily: `jobhunt run-all` (or step-by-step)
1. **search** — Playwright launches with a persistent browser context (cookies/session saved under `state/browser_profile/`). User logs into each portal once manually; subsequent runs reuse the session. Runs configured search queries per portal (`config/config.yaml`), extracts: url, title, company, location, salary (if listed), portal, posted/first_seen date, and full JD text. Upserts into SQLite `jobs` table keyed by `url` — new rows get `status='new'`, existing rows preserve `first_seen` and any manually-set `status` (e.g. `applied`, `rejected`, `ignored`).
2. **score** — For jobs without a `matchScore` yet, sends JD text + `my_profile.json` to the LLM, which returns a 1-10 `matchScore` plus a short rationale (stored for transparency/debugging, not necessarily shown in the final report).
3. **generate** — For jobs where `matchScore > 8` and no resume/cover letter yet generated: LLM selects and phrases only *existing* profile content (skills/experience/projects) relevant to that JD into the Jinja2 resume/cover-letter templates. Rendered via WeasyPrint to PDF, capped at 2-3 pages, ATS-friendly (semantic HTML, standard fonts, no tables/images/icons that break parsing). Output saved under `output/<date>/resumes/` and `output/<date>/cover_letters/`.
4. **report** — Dumps the required table: `url, first_seen, portal, title, company, status, location, salary, matchScore` as CSV and HTML, sorted by `matchScore` descending.
5. **summary** — Single HTML/Markdown page listing all `matchScore > 8` jobs with links to their generated resume/cover letter PDFs — the "ready to apply" shortlist for the day.

## Configuration (`config/config.yaml`)

All search and matching parameters live in one YAML file so the user can tune the search without touching code. Planned schema:

```yaml
salary_expectation:
  min: 120000
  max: 150000
  currency: AUD
  period: annual        # annual | hourly

search:
  date_range_days: 7     # only consider postings within the last N days
  locations:
    - "Sydney NSW"
    - "Remote"
  keywords:              # search terms tried per portal (one search run per keyword)
    - "Senior Data Engineer"
    - "Platform Engineer"
  exclude_keywords:       # postings containing these in title are skipped
    - "Junior"
    - "Intern"
  employment_types:       # full_time | part_time | contract | casual
    - full_time
  min_match_score_for_docs: 8   # threshold to trigger resume/cover letter generation

portals:
  seek:
    enabled: true
    base_url: "https://www.seek.com.au"
  jora:
    enabled: true
    base_url: "https://au.jora.com"
  linkedin:
    enabled: true
    base_url: "https://www.linkedin.com"
  indeed:
    enabled: true
    base_url: "https://au.indeed.com"

output:
  report_formats: [csv, html]
  resume_max_pages: 3
```

- Ships as `config/config.example.yaml` (committed) and copied by the user to `config/config.yaml` (gitignored, since it may contain personal salary expectations).
- Overridable path via `--config <path>` CLI flag or `JOBHUNT_CONFIG` env var, so it isn't hardcoded to CWD — important for future skill-wrapper use too.
- `date_range_days`, `keywords`, `locations`, `salary_expectation`, and `min_match_score_for_docs` are all read by the relevant pipeline step (`search`, `matcher`, `docgen`) rather than hardcoded, so behavior changes don't require code edits.

## Safeguards against fabricated experience

- The resume/cover-letter generation prompt is explicitly constrained: it may only select, reorder, rephrase, or summarize content that exists in `my_profile.json`. It must not invent employers, titles, dates, metrics, or skills.
- Generated documents include a machine-checkable trace: each bullet/claim is tagged with the source profile entry it came from (kept in a sidecar JSON, not printed on the PDF), enabling a quick manual audit pass if desired.
- `profile build` itself is conservative — if resumes are ambiguous/conflicting, it flags the conflict rather than guessing.

## State & idempotency

- SQLite `jobs` table is the single source of truth for what's been seen/scored/generated, keyed by URL.
- Re-running `search` daily only adds new listings and refreshes fields like `status`; already-scored/generated jobs are skipped unless the user forces a re-run (`--force`).
- Manual status transitions (`applied`, `interviewing`, `rejected`, `ignored`) are preserved across runs and are user-editable directly in the report or via a small `jobhunt status <url> <state>` command.

## Repository layout (planned)

```
jobhunt-ops/
  docs/
    architecture.md            # this file
    implementation-plan.md     # phased build plan, task breakdown
  pyproject.toml
  README.md
  config/
    config.yaml                 # salary expectations, search queries per portal, portal URLs
  profile/
    resumes/                    # user drops raw resumes here (pdf/docx/txt)
    my_profile.json              # synthesized structured knowledge base (generated, gitignored)
  templates/
    resume.html.j2
    cover_letter.html.j2
  output/                        # generated reports/resumes/cover letters (gitignored)
    YYYY-MM-DD/
      report.csv / report.html
      summary.html
      resumes/<company>_<title>.pdf
      cover_letters/<company>_<title>.pdf
  state/
    jobhunt.db                   # sqlite: jobs table (gitignored)
    browser_profile/              # persistent Playwright login sessions (gitignored)
  src/jobhunt/
    cli.py                        # typer app: profile, search, score, generate, report, summary, run-all
    llm.py                        # GitHub Models client (gh auth token / env fallback)
    profile_builder.py
    scrapers/
      base.py
      linkedin.py
      seek.py
      indeed.py
      jora.py
    db.py
    matcher.py
    docgen.py
    report.py
    summary.py
  tests/
```

## Known risks / open items

- **Anti-bot fragility**: LinkedIn and Indeed are the most aggressive at detecting automation; selectors and flows will break when they change their UI. Seek and Jora are expected to be comparatively easier. Scrapers are isolated per-portal so one breaking doesn't block the others.
- **ToS risk**: user has explicitly accepted this tradeoff for personal, non-bulk, low-frequency (daily) use with their own logged-in session.
- **Salary data**: not all portals list salary; field will be blank/"Not listed" where unavailable.
- **PDF ATS-friendliness**: verified by keeping resume templates to plain semantic HTML (headings, paragraphs, lists) with no multi-column layouts, tables, or embedded images/icons in the body text.
