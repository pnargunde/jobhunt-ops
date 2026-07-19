"""Build/update MyProfile — the structured knowledge base of skills, experience,
projects, achievements, and education — from one or more resumes.

This is intentionally a SEPARATE, infrequently-run step (see docs/architecture.md),
not part of the daily run-all pipeline. Run it only when you add a new resume,
skill, or project to profile/resumes/.
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
DEFAULT_PROFILE_PATH = REPO_ROOT / "profile" / "my_profile.json"

SYSTEM_PROMPT = """You are a careful, conservative resume-data extraction assistant.
You will be given the raw text of one or more resumes belonging to the SAME person.
Merge them into a single structured JSON profile capturing everything real and
verifiable in the source text. Do NOT invent, embellish, or infer facts that are
not stated or clearly implied by the text (no fabricated employers, titles, dates,
metrics, or skills). If resumes disagree with each other (e.g. different dates for
the same role), keep the most complete/recent version, and note the discrepancy in
a "_conflicts" list rather than silently guessing.

Return a JSON object with this exact shape:
{
  "skills": [string],
  "experiences": [
    {"employer": string, "title": string, "start": string, "end": string, "bullets": [string]}
  ],
  "projects": [
    {"name": string, "description": string, "technologies": [string], "bullets": [string]}
  ],
  "education": [
    {"institution": string, "qualification": string, "start": string, "end": string}
  ],
  "certifications": [string],
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
    profile_path: Path = DEFAULT_PROFILE_PATH,
    model: str = "openai/gpt-4o-mini",
    force: bool = False,
) -> dict:
    """Parse all resumes in resumes_dir and synthesize/merge into my_profile.json.

    Returns the resulting profile dict. Raises FileNotFoundError if no resumes found.
    """
    resume_files = sorted(
        p for p in resumes_dir.glob("*") if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md")
    )
    if not resume_files:
        raise FileNotFoundError(
            f"No resumes found in {resumes_dir}. Drop your resume file(s) there and re-run."
        )

    existing = None if force else load_existing_profile(profile_path)

    resume_texts = {f.name: extract_text(f) for f in resume_files}
    user_prompt_parts = ["Here are the resume(s) to merge into one profile:\n"]
    for name, text in resume_texts.items():
        user_prompt_parts.append(f"--- {name} ---\n{text}\n")
    if existing:
        user_prompt_parts.append(
            "\nHere is the EXISTING profile JSON to merge/update with the above "
            f"(preserve anything still valid, add anything new): {json.dumps(existing)}"
        )
    user_prompt = "\n".join(user_prompt_parts)

    profile = chat_completion_json(SYSTEM_PROMPT, user_prompt, model=model)
    profile["_generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    profile["_source_files"] = list(resume_texts.keys())

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile
