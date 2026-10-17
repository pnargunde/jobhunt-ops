# jobhunt-ops

Automated job search, match-scoring, and tailored resume/cover-letter generation.

See [docs/architecture.md](docs/architecture.md) for the system design and rationale,
and [docs/implementation-plan.md](docs/implementation-plan.md) for the phased build plan.

`jobhunt` is a standalone Python CLI (not a Copilot CLI skill) built so its core logic
is fully reusable — every command is a thin wrapper over plain importable functions,
so it can later be wrapped as a Copilot CLI custom skill without rewriting anything.

## Setup

PowerShell:
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

Git Bash (MINGW64):
```bash
# From the repo root
python -m venv .venv
source .venv/Scripts/activate
pip install -e .[dev]
playwright install chromium

# GitHub Models auth (pick one):
gh auth login          # recommended: jobhunt will call `gh auth token` automatically
# OR: export GITHUB_MODELS_TOKEN="<fine-grained PAT with Models: Read-only>"

# Config
cp config/config.example.yaml config/config.yaml
```

> **Important:** `jobhunt` is a console-script command installed by `pip install -e .` —
> always run it as `jobhunt <command>`, never `python jobhunt <command>` (that makes
> Python look for a file literally named `jobhunt` and fails). Make sure the venv is
> activated first, otherwise the `jobhunt` command won't be on your PATH. If it's still
> not found after installing, you can fall back to `python -m jobhunt.cli <command>`
> from the repo root.

## One-time per portal: log in

```powershell
jobhunt login seek
jobhunt login jora
jobhunt login linkedin
# Indeed scraper is still a stub pending implementation - see docs/implementation-plan.md
```

This opens a real (headed) browser window; log in manually, then press Enter in the
terminal. Your session is saved under `state/browser_profile/<portal>/` and reused by
future headless runs.

## Build MyProfile (run rarely — only when your career summary/skills/projects change)

```powershell
# Recommended: maintain one file, profile\ProfileDump.txt, with your full career
# summary (name, contact details, roles, tech stack, achievements, certifications,
# education). Make sure your name and contact info (email/phone/location) appear
# somewhere in it - they're extracted automatically for the resume/cover-letter
# header. Update the file whenever you gain a new skill/project, then re-run:
jobhunt profile-build

# Optional: you can still supplement with additional resume files
# (.pdf/.docx/.txt/.md) dropped in profile\resumes\ - they get merged in too.

```

This is intentionally **separate** from the daily pipeline below.

## Daily run

```powershell
jobhunt run-all
```

Or step by step:

```powershell
jobhunt search
jobhunt score
jobhunt generate
jobhunt report
jobhunt summary
```

Outputs land in `output/<YYYY-MM-DD>/`:
- `report.csv` / `report.html` — url, first_seen, portal, title, company, status, location, salary, matchScore
- `resumes/<company>-<title>.html` and `.trace.json` (audit trail of what profile content each bullet came from) — open in a browser and print/"Save as PDF" when ready to apply
- `cover_letters/<company>-<title>.html` — same, print/"Save as PDF" manually
- `summary.html` — single page of all matchScore > threshold jobs, ready to apply

## Manually track applications

```powershell
jobhunt status "<job-url>" applied
```

## Known limitations

- Indeed scraper is stubbed (high anti-bot risk) — implemented last after LinkedIn/Seek/Jora are validated end-to-end.
- LinkedIn is the most aggressive at detecting automation: if `jobhunt search` reports a logged-out/blocked session, re-run `jobhunt login linkedin` to refresh cookies. Consider light usage (few keywords/locations per day) to reduce the chance of rate limiting or account flags.
- Scraping relies on portal page structure; selectors may need updates if a portal changes its markup.
