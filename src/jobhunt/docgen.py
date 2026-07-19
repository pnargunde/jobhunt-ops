"""Generate a tailored, ATS-friendly resume and cover letter for a given job.

Hard constraint (per user requirement — "no experience is to be faked"): the
LLM is only allowed to SELECT, REORDER, and REPHRASE content that already
exists in profile/my_profile.json. It must never invent employers, titles,
dates, metrics, or skills. Each generated document is paired with a sidecar
JSON tracing every bullet/claim back to its source profile entry, so a human
can audit the output before submitting an application.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

from jobhunt.llm import chat_completion_json

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

SELECT_SYSTEM_PROMPT = """You are a career assistant helping tailor a resume and
cover letter to a specific job, for a candidate whose ONLY source of truth is
the profile JSON provided. You must not invent or embellish any employer,
title, date, metric, skill, or achievement that is not present in the profile.
You MAY select, reorder, and rephrase (for clarity/relevance/conciseness) the
candidate's existing content to best match the job description.

Return a JSON object with this exact shape:
{
  "resume": {
    "summary": string,                     // 2-3 sentence professional summary, based only on profile content
    "skills": [string],                    // subset/reordering of profile skills most relevant to this job
    "experiences": [
      {"employer": string, "title": string, "start": string, "end": string, "bullets": [string]}
    ],
    "projects": [{"name": string, "bullets": [string]}],
    "education": [{"institution": string, "qualification": string, "start": string, "end": string}],
    "certifications": [string]
  },
  "cover_letter_paragraphs": [string],      // 2-4 body paragraphs, no salutation/closing (template adds those)
  "trace": [
    {"claim": string, "source": string}     // every non-trivial resume bullet / cover letter claim mapped to
                                             // the profile field/entry it came from, for manual audit
  ]
}
"""


def _select_content(profile: dict, job_title: str, company: str, jd_text: str, model: str) -> dict:
    user_prompt = (
        f"Candidate profile (source of truth, do not exceed this):\n{json.dumps(profile)}\n\n"
        f"Target job: {job_title} at {company}\n\nJob description:\n{jd_text}"
    )
    return chat_completion_json(SELECT_SYSTEM_PROMPT, user_prompt, model=model, temperature=0.3)


def _render_pdf(template_name: str, context: dict, out_path: Path) -> None:
    """Render a Jinja2 HTML template to PDF using headless Chromium (Playwright).

    Uses Playwright rather than WeasyPrint so no extra system libraries
    (GTK/Pango/Cairo) are required beyond `playwright install chromium`,
    which the project already depends on for scraping.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template(template_name)
    html_str = template.render(**context)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(html_str, wait_until="load")
        page.emulate_media(media="print")
        page.pdf(path=str(out_path), format="A4", print_background=True)
        browser.close()


def _count_pdf_pages(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def generate_documents(
    profile: dict,
    job_title: str,
    company: str,
    jd_text: str,
    candidate_name: str,
    contact_line: str,
    output_dir: Path,
    slug: str,
    model: str = "openai/gpt-4o-mini",
    max_pages: int = 3,
) -> dict:
    """Generate resume.pdf, cover_letter.pdf, and a trace.json sidecar for one job.

    Returns dict with resume_path, cover_letter_path, trace_path (all as str).
    """
    selection = _select_content(profile, job_title, company, jd_text, model)
    resume_data = selection["resume"]
    trace = selection.get("trace", [])

    resume_path = output_dir / "resumes" / f"{slug}.pdf"
    cover_letter_path = output_dir / "cover_letters" / f"{slug}.pdf"
    trace_path = output_dir / "resumes" / f"{slug}.trace.json"

    _render_pdf(
        "resume.html.j2",
        {"candidate_name": candidate_name, "contact_line": contact_line, **resume_data},
        resume_path,
    )

    # Trim to max_pages by dropping lowest-priority bullets/experiences if needed.
    attempts = 0
    while _count_pdf_pages(resume_path) > max_pages and attempts < 4:
        if resume_data.get("projects"):
            resume_data["projects"].pop()
        elif len(resume_data.get("experiences", [])) > 1:
            resume_data["experiences"][-1]["bullets"] = resume_data["experiences"][-1]["bullets"][:2]
        else:
            break
        _render_pdf(
            "resume.html.j2",
            {"candidate_name": candidate_name, "contact_line": contact_line, **resume_data},
            resume_path,
        )
        attempts += 1

    from datetime import date as _date

    _render_pdf(
        "cover_letter.html.j2",
        {
            "candidate_name": candidate_name,
            "contact_line": contact_line,
            "company": company,
            "job_title": job_title,
            "date": _date.today().strftime("%d %B %Y"),
            "paragraphs": selection.get("cover_letter_paragraphs", []),
        },
        cover_letter_path,
    )

    trace_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")

    return {
        "resume_path": str(resume_path),
        "cover_letter_path": str(cover_letter_path),
        "trace_path": str(trace_path),
    }


def generate_for_pending_jobs(
    conn: sqlite3.Connection,
    profile: dict,
    candidate_name: str,
    contact_line: str,
    output_dir: Path,
    min_score: int,
    model: str = "openai/gpt-4o-mini",
    max_pages: int = 3,
) -> int:
    """Generate docs for every job with match_score >= min_score lacking a resume. Returns count generated."""
    import re

    from jobhunt.db import get_jobs_for_docgen, set_generated_docs

    rows = get_jobs_for_docgen(conn, min_score)
    for row in rows:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"{row['company']}-{row['title']}").strip("-").lower()[:80]
        result = generate_documents(
            profile=profile,
            job_title=row["title"],
            company=row["company"],
            jd_text=row["jd_text"] or "",
            candidate_name=candidate_name,
            contact_line=contact_line,
            output_dir=output_dir,
            slug=slug,
            model=model,
            max_pages=max_pages,
        )
        set_generated_docs(conn, row["url"], result["resume_path"], result["cover_letter_path"])
    return len(rows)
