# jobhunt-ops

Automated job search, match-scoring, and tailored resume/cover-letter generation.

See [docs/architecture.md](docs/architecture.md) for the system design and rationale,
and [docs/implementation-plan.md](docs/implementation-plan.md) for the phased build plan.

`jobhunt` is a standalone Python CLI (not a Copilot CLI skill) built so its core logic
is fully reusable — every command is a thin wrapper over plain importable functions,
so it can later be wrapped as a Copilot CLI custom skill without rewriting anything.

## Setup

```powershell
# From the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
playwright install chromium

# GitHub Models auth (pick one):
gh auth login          # recommended: jobhunt will call `gh auth token` automatically
# OR set $env:GITHUB_MODELS_TOKEN = "<fine-grained PAT with Models: Read-only>"

# Config
Copy-Item config\config.example.yaml config\config.yaml
notepad config\config.yaml   # set salary expectations, keywords, locations, portals
```

## One-time per portal: log in

```powershell
jobhunt login seek
jobhunt login jora
# LinkedIn/Indeed scrapers are stubs pending implementation - see docs/implementation-plan.md
```

This opens a real (headed) browser window; log in manually, then press Enter in the
terminal. Your session is saved under `state/browser_profile/<portal>/` and reused by
future headless runs.

## Build MyProfile (run rarely — only when resumes/skills/projects change)

```powershell
# Drop your resume(s) (.pdf/.docx/.txt/.md) into profile\resumes\, then:
jobhunt profile-build
```

This is intentionally **separate** from the daily pipeline below.

## Daily run

```powershell
jobhunt run-all --candidate-name "Jane Smith" --contact-line "jane@example.com | +61 4xx xxx xxx | Sydney NSW"
```

Or step by step:

```powershell
jobhunt search
jobhunt score
jobhunt generate --candidate-name "Jane Smith" --contact-line "jane@example.com | +61 4xx xxx xxx | Sydney NSW"
jobhunt report
jobhunt summary
```

Outputs land in `output/<YYYY-MM-DD>/`:
- `report.csv` / `report.html` — url, first_seen, portal, title, company, status, location, salary, matchScore
- `resumes/<company>-<title>.pdf` and `.trace.json` (audit trail of what profile content each bullet came from)
- `cover_letters/<company>-<title>.pdf`
- `summary.html` — single page of all matchScore > threshold jobs, ready to apply

## Manually track applications

```powershell
jobhunt status "<job-url>" applied
```

## Known limitations

- LinkedIn/Indeed scrapers are stubbed (high anti-bot risk) — implemented after Seek/Jora are validated end-to-end.
- Scraping relies on portal page structure; selectors may need updates if a portal changes its markup.
