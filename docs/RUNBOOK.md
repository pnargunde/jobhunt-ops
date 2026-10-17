# jobhunt-ops Runbook

Concise command reference for day-to-day use. All commands shown as:

```powershell
python -m jobhunt.cli <command> [options]
```

(use this form in dev — the `jobhunt` console-script entry point may not be
on PATH depending on how Python/pip installed it; `python -m jobhunt.cli`
always works once the package is installed with `pip install -e .`)

## One-time setup

```powershell
pip install -e .
playwright install chromium
copy config\config.example.yaml config\config.yaml   # then edit it
copy .env.example .env                                # then fill in real keys
```

Edit `config/config.yaml`: salary expectations, search keywords/locations,
enabled portals, LLM provider/model. Edit `.env`: API key for whichever LLM
provider you set in config (see `llm.provider`); `github_models` needs none
(falls back to `gh auth token`).

## Commands

| Command | What it does |
|---|---|
| `login <portal>` | One-time interactive login (opens a real browser window) for a job portal (`seek`, `jora`, `linkedin`, `indeed`). Saves the session under `state/browser_profile/` so later `search` runs can reuse it headlessly. Re-run only when a session expires. |
| `profile-build` | Builds/updates `profile/my_profile.json` from `profile/ProfileDump.txt` (primary) + any resumes in `profile/resumes/` (supplemental). Merges with the existing profile unless `--force`. The LLM also extracts `candidate_name`/`contact_line` (full name, email/phone/location) directly from the source text — make sure these appear somewhere in ProfileDump.txt or a resume. Run this **infrequently** — only when you update ProfileDump.txt or add a new skill/project/resume. Not part of `run-all`. |
| `search [--portal NAME]` | Scrapes all enabled portals (or one, via `--portal`) for configured keywords/locations, fetches full job descriptions, upserts into the SQLite jobs table (deduped by URL; preserves `first_seen`/manually-set `status`). |
| `score` | Sends every unscored job's JD + your profile to the LLM, gets back a 1-10 `matchScore` + rationale, stores it. |
| `generate [--force]` | For jobs with `matchScore >= min_match_score_for_docs` lacking docs: LLM selects/rephrases (never invents) profile content into a tailored resume + cover letter, using the candidate_name/contact_line extracted by `profile-build`. Writes `.html` files (open in browser, print/"Save as PDF" yourself) plus a `trace.json` audit sidecar. `--force` regenerates even already-done jobs (e.g. after a template/prompt change). Errors with a clear message if `profile-build` didn't find a name/contact details in your source text yet. |
| `report` | Writes `report.csv`/`report.html`: `url, first_seen, portal, title, company, status, location, salary, matchScore`, sorted by matchScore. Also prints a table of the top 20 to the terminal. |
| `summary` | Writes a single `summary.html` listing all jobs above the matchScore threshold, with links to their generated resume/cover-letter HTML — the "ready to apply" shortlist. |
| `status <url> <new_status>` | Manually set a job's status (e.g. `applied`, `interviewing`, `rejected`, `ignored`). |
| `purge --portal NAME [--yes]` | Deletes all stored jobs for one portal (e.g. to discard a bad scrape without touching other portals). Prompts for confirmation unless `--yes`. |
| `run-all` | Runs `search → score → generate → report → summary` in sequence. Does **not** include `profile-build` (run that separately, including the first time to extract your candidate_name/contact_line). |

### Common options (most commands)

- `--config PATH` — explicit config file (else `JOBHUNT_CONFIG` env var, else `config/config.yaml`).
- `--model NAME` — override `llm.model` from config for this run.
- `--provider NAME` — override `llm.provider`: `github_models | openai | anthropic | google_genai | bedrock`.
- `--api-key-env NAME` — override which env var holds the API key for the chosen provider.

## Typical daily flow

```powershell
python -m jobhunt.cli run-all
```

equivalent to:

```powershell
python -m jobhunt.cli search
python -m jobhunt.cli score
python -m jobhunt.cli generate
python -m jobhunt.cli report
python -m jobhunt.cli summary
```

Then open `output/<today>/summary.html`, print any resume/cover-letter HTML to PDF as needed, and apply.

## Infrequent / one-off commands

```powershell
python -m jobhunt.cli login seek
python -m jobhunt.cli profile-build
python -m jobhunt.cli profile-build --force
python -m jobhunt.cli status https://... applied
python -m jobhunt.cli purge --portal linkedin --yes
```
