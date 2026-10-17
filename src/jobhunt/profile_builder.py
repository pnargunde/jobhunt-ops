"""Build/update MyProfile — the structured knowledge base of skills, experience,
projects, achievements, and education.

Primary source: a single free-form career summary/dump file at
profile/ProfileDump.txt (recommended — one file to keep updated over time).
Optionally supplemented by additional resumes (.pdf/.docx/.txt/.md) dropped in
profile/resumes/, which are merged in alongside the dump.

This is intentionally a SEPARATE, infrequently-run step (see docs/architecture.md),
not part of the daily run-all pipeline. Run it only when you update ProfileDump.txt
or add a new skill/project/resume.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader
import docx

from jobhunt.llm import chat_completion_json

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESUMES_DIR = REPO_ROOT / "profile" / "resumes"
DEFAULT_PROFILE_DUMP_PATH = REPO_ROOT / "profile" / "ProfileDump.txt"
DEFAULT_PROFILE_PATH = REPO_ROOT / "profile" / "my_profile.json"

SYSTEM_PROMPT = """You are a careful, conservative resume-data extraction assistant.
You will be given free-form career text (a career summary/dump and/or one or more
resumes) belonging to the SAME person. Merge it all into a single structured JSON
profile capturing everything real and verifiable in the source text. Do NOT invent,
embellish, or infer facts that are not stated or clearly implied by the text (no
fabricated employers, titles, dates, metrics, or skills). If sources disagree with
each other (e.g. different dates for the same role), keep the most complete/recent
version, and note the discrepancy in a "_conflicts" list rather than silently
guessing.

Return a JSON object with this exact shape:
{
  "candidate_name": string,       // the candidate's full name, as it appears in the source text
  "title_line": string,             // a single line combining contact details and urls present
                                   // in the source text (e.g. email, phone, location, linkedin url), in the
                                   // style "email@example.com | +61 4xx xxx xxx | Sydney, NSW | linkedin.com/in/username" -
                                   // use "|" as the separator; omit any piece not present, don't invent one
  "professional_summary": string,
  "technicalskills": [string],
  "core competencies": [string],
  "certifications": [string],
  "experiences": [
    {"employer": string, "title": string, "start": string, "end": string,
     "skills_deployed": [string], "tech_stack": [string], "bullets": [string],
     "achievements": [string]}
  ],
  "projects": [
    {"name": string, "description": string, "technologies": [string], "bullets": [string]}
  ],
  "education and certifications": [
    {"institution": string, "qualification": string, "start": string, "end": string},
  ],
  "achievements": [string],
  "_conflicts": [string]
}
"""


def _extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_text_from_docx(path: Path) -> str:
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    if suffix == ".docx":
        return _extract_text_from_docx(path)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"Unsupported resume file type: {path.name} (expected .pdf, .docx, .txt, .md)")


def load_existing_profile(profile_path: Path = DEFAULT_PROFILE_PATH) -> dict | None:
    if profile_path.exists():
        return json.loads(profile_path.read_text(encoding="utf-8"))
    return None


def build_profile(
    resumes_dir: Path = DEFAULT_RESUMES_DIR,
    profile_dump_path: Path = DEFAULT_PROFILE_DUMP_PATH,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    model: str = "openai/gpt-4o-mini",
    provider: str = "github_models",
    api_key_env: str | None = None,
    force: bool = False,
) -> dict:
    """Synthesize/merge profile/ProfileDump.txt (primary) plus any resumes in
    resumes_dir (supplemental) into my_profile.json.

    candidate_name/contact_line (used later by `generate` for the resume/
    cover letter header) are extracted by the LLM from the source text
    itself (see SYSTEM_PROMPT) - make sure your name and contact details
    (email/phone/location) appear somewhere in profile/ProfileDump.txt or a
    resume so they're picked up.

    Returns the resulting profile dict. Raises FileNotFoundError if neither a
    profile dump file nor any resumes are found.
    """
    sources: dict[str, str] = {}
    if profile_dump_path.exists():
        sources[profile_dump_path.name] = extract_text(profile_dump_path)

    resume_files = sorted(
        p for p in resumes_dir.glob("*") if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md")
    )
    for f in resume_files:
        sources[f.name] = extract_text(f)

    if not sources:
        raise FileNotFoundError(
            f"No profile source found. Create {profile_dump_path} (recommended, a single "
            f"career summary you keep updated) and/or drop resume file(s) into {resumes_dir}."
        )

    existing = None if force else load_existing_profile(profile_path)

    user_prompt_parts = ["Here is the career source material to merge into one profile:\n"]
    for name, text in sources.items():
        user_prompt_parts.append(f"--- {name} ---\n{text}\n")
    if existing:
        user_prompt_parts.append(
            "\nHere is the EXISTING profile JSON to merge/update with the above "
            f"(preserve anything still valid, add anything new): {json.dumps(existing)}"
        )
    user_prompt = "\n".join(user_prompt_parts)

    profile = chat_completion_json(
        SYSTEM_PROMPT, user_prompt, model=model, provider=provider, api_key_env=api_key_env,
    )
    profile["_generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    profile["_source_files"] = list(sources.keys())

    # If this pass didn't find a name/contact line (e.g. source text omitted
    # them) but a previous run did, keep the previously extracted value
    # rather than losing it.
    if not profile.get("candidate_name") and existing:
        profile["candidate_name"] = existing.get("candidate_name")
    if not profile.get("contact_line") and existing:
        profile["contact_line"] = existing.get("contact_line")

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile
