# jobhunt-ops — Implementation Plan

Timeline: flexible, target ~7 days (not a hard deadline). Build in phases so a usable slice exists early and remaining work (esp. brittle scrapers) can be added incrementally.

See [architecture.md](/C:/dev/jobhunt-ops/docs/architecture.md) for the system design this plan implements.

## Phase 0 — Project scaffold
- `pyproject.toml` with dependencies: `typer`, `playwright`, `pypdf`, `python-docx`, `jinja2`, `weasyprint`, `pyyaml`, `pandas` (or plain `csv`), `rich` (nice CLI output).
- Directory layout per architecture doc: `config/`, `profile/resumes/`, `templates/`, `output/`, `state/`, `src/jobhunt/`, `tests/`.
- `.gitignore`: `state/`, `output/`, `profile/my_profile.json`, `profile/resumes/*`, `.venv/`, `__pycache__/`.
- `config/config.example.yaml` — salary expectations, date-range filter, keywords/locations/exclude-keywords, employment types, per-portal enable flags, match-score threshold, output formats/page cap. Full schema documented in [architecture.md](/C:/dev/jobhunt-ops/docs/architecture.md). User copies to `config/config.yaml` (gitignored) and edits.
- Config loading supports `--config <path>` / `JOBHUNT_CONFIG` env var override, not just CWD-relative, to support future non-CLI (skill) invocation.
- Entry point: `jobhunt` console script -> `jobhunt.cli:app`.
- Design rule (per user): keep the tool flexible enough to later be wrapped as a Copilot CLI custom skill. All logic lives in plain importable functions in `src/jobhunt/*.py`; `cli.py` is a thin Typer wrapper that only parses args and calls those functions.

## Phase 1 — LLM client + Profile Builder (infrequent command)
- `src/jobhunt/llm.py`: resolves auth via `gh auth token` (subprocess), falls back to `GITHUB_MODELS_TOKEN` env var; wraps GitHub Models chat completions endpoint with retry/backoff and JSON-mode helper.
- `src/jobhunt/profile_builder.py`:
  - Extract text from resumes in `profile/resumes/` (pypdf for PDF, python-docx for DOCX, plain read for TXT).
  - Prompt LLM to merge/synthesize into structured `my_profile.json` schema: `skills[]`, `experiences[]` (employer, title, start/end, bullets), `projects[]`, `education[]`, `certifications[]`, `achievements[]`.
  - Conflict handling: if resumes disagree (e.g. different dates for same role), flag in a `_conflicts` field rather than silently picking one.
  - Command: `jobhunt profile build [--force]`. Explicitly documented as run-rarely, NOT part of `run-all`.

## Phase 2 — State DB + one scraper end-to-end (Seek or Jora first)
- `src/jobhunt/db.py`: SQLite schema —
  ```sql
  CREATE TABLE jobs (
    url TEXT PRIMARY KEY,
    portal TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    salary TEXT,
    jd_text TEXT,
    first_seen TEXT,
    last_seen TEXT,
    status TEXT DEFAULT 'new',
    match_score INTEGER,
    match_rationale TEXT,
    resume_path TEXT,
    cover_letter_path TEXT
  );
  ```
- `src/jobhunt/scrapers/base.py`: common interface (`search(query, location) -> list[JobListing]`), persistent Playwright context stored under `state/browser_profile/<portal>/`.
- `src/jobhunt/scrapers/seek.py` (and/or `jora.py`): implement search-results-page scraping + JD detail fetch. User logs in manually first time (`jobhunt login seek` opens a headed browser).
- Upsert logic in `db.py`: insert new, update `last_seen`/JD text on existing, never overwrite `status` once user has changed it manually.
- Command: `jobhunt search [--portal seek] [--query "..."]`.

## Phase 3 — Matcher + Report
- `src/jobhunt/matcher.py`: for jobs with `match_score IS NULL`, call LLM with JD text + `my_profile.json` summary, parse 1-10 score + rationale, persist.
- `src/jobhunt/report.py`: query DB, emit `output/<date>/report.csv` and `.html` with exact columns requested: `url, first_seen, portal, title, company, status, location, salary, matchScore`, sorted by matchScore desc.
- Commands: `jobhunt score`, `jobhunt report`.

## Phase 4 — Docgen (resume + cover letter) + Summary
- `templates/resume.html.j2`, `templates/cover_letter.html.j2`: user-provided desired format converted to Jinja2 (placeholders for selected experience/skills/projects tailored per job).
- `src/jobhunt/docgen.py`:
  - For jobs with `match_score > 8` and no `resume_path` yet: LLM selects/phrases relevant `my_profile.json` content (no fabrication — constrained prompt + validation pass checking generated claims trace to profile entries).
  - Render HTML -> PDF via headless Chromium (Playwright `page.pdf()`) — avoids WeasyPrint's GTK/Pango system-library requirement, which is painful on Windows; Chromium is already a project dependency. Enforce 2-3 page cap (iterative trim if over).
  - Save under `output/<date>/resumes/` and `.../cover_letters/`, record paths in DB.
- `src/jobhunt/summary.py`: single-page HTML/Markdown of all `matchScore > 8` jobs with links to generated docs.
- Commands: `jobhunt generate`, `jobhunt summary`.

## Phase 5 — CLI orchestration + remaining portals
- `jobhunt run-all` chains: search (all enabled portals) -> score -> generate -> report -> summary. Does **not** include `profile build`.
- Add LinkedIn and Indeed scrapers (expected hardest due to anti-bot measures) — budget extra time/iteration here; isolate failures so one portal breaking doesn't stop others (`run-all` continues on a per-portal try/except with logged warnings).
- `jobhunt status <url> <new_status>` helper to manually update status (applied/interviewing/rejected/ignored).

## Phase 6 — Docs & verification
- `README.md`: setup (Python/Playwright install, `playwright install`, `gh auth login`, first-time portal logins), config file explanation, daily usage (`jobhunt run-all`), infrequent profile rebuild (`jobhunt profile build`).
- End-to-end dry run with a sample resume + sample/mocked job data to confirm the full pipeline runs without errors before relying on live scraping.

## Task tracking

Granular tasks and dependencies are tracked in the session's SQL `todos`/`todo_deps` tables for this build session, mirroring the phases above.
