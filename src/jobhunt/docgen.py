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
candidate's existing content to best match the job description. Make sure resume is ATS-friendly, with scannable bullets and a clear structure.

The resume MUST include a "Projects" section with AT LEAST 3 entries (4 if
good material exists), chosen for relevance to the job description, subject
to this priority order:
1. If the profile's own "projects" list has entries, prefer those.
2. If it doesn't (or doesn't have enough), derive project entries from the
   candidate's work experience: pick 3-4 of their most substantial, concrete
   initiatives/achievements (from an experience's "achievements" or "bullets")
   that are most relevant to this job, and present each as a distinct project
   with its own name/title (you may phrase a short descriptive name for it,
   e.g. "BSS Platform Migration" if the underlying bullet describes one).
   Every fact in a derived project MUST already exist verbatim or
   near-verbatim in that experience's bullets/achievements - rephrasing for
   clarity/conciseness is fine, adding new claims is not.
3. Only skip the 3-project minimum if the profile genuinely does not contain
   enough distinct, relevant material to responsibly form 3 projects without
   fabricating - never invent content just to hit the minimum.
For every derived project, set "source_employer" to the employer it was drawn
from (or null if pulled from the profile's own standalone "projects" list),
so it stays traceable back to the source in trace.json. Each project must be
presented as a single cohesive PARAGRAPH (5-7 sentences, not bullets) covering:
the project's scope/context, the actions the candidate personally took, and
the outcome/impact achieved - all facts must already exist verbatim or
near-verbatim in the profile (rephrasing for flow is fine, adding new claims
is not). Optionally include a short "tech_line" list of the technologies
involved, drawn only from that experience's tech_stack or the project's own
listed tech.

Match the density, structure, and tone of a strong, THOROUGH professional
resume that fills close to its full page budget rather than a sparse one - do
not under-fill. Use every substantial fact available in the profile rather
than over-condensing: a punchy 5-7 sentence summary weaving in the target role
title and the candidate's strongest, most relevant real achievements; skills
organized into 5-8 labeled categories (e.g. "Cloud & Infrastructure", "CI/CD &
DevOps", "Languages & Scripting") presented under a "Core Competencies"
heading instead of one flat list; and experience bullets that lead with an
action verb and keep any real metrics/numbers already present in the profile
(never add new ones). Give the most recent/relevant 1-2 roles 6-8 bullets
each, and older/less relevant roles at least 4-5 bullets - draw generously
from the profile's own "bullets"/"achievements" lists for that experience
rather than compressing them down. Do not invent a category label's content -
every item placed under a category must be a skill/tech_stack string that
already exists in the profile.

Return a JSON object with this exact shape:
{
  "resume": {
    "headline": string,                    // short target-role subtitle for under the name, e.g. "Technical Lead — Cloud & Platform Engineering", grounded in the candidate's actual experience and the job title
    "summary": string,                     // 5-7 sentence professional summary, based only on profile content
    "skills_grouped": [
      {"category": string, "items": [string]}  // 5-8 categories, each with 3-10 profile skills/tech_stack items, rendered as a "Core Competencies" table
    ],
    "experiences": [
      {"employer": string, "title": string, "start": string, "end": string, "bullets": [string]} // 6-8 bullets for the most recent/relevant 1-2 roles, 4-5 for older roles, drawn generously from profile experiences, rephrased for clarity/conciseness if needed, but never inventing new claims
    ],
    "projects": [
      {"name": string, "tech_line": [string], "source_employer": string | null,
       "summary": string}   // 5-7 sentence paragraph: scope, actions taken, outcome/impact - no bullets
    ],
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


def _select_content(
    profile: dict,
    job_title: str,
    company: str,
    jd_text: str,
    model: str,
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> dict:
    user_prompt = (
        f"Candidate profile (source of truth, do not exceed this):\n{json.dumps(profile)}\n\n"
        f"Target job: {job_title} at {company}\n\nJob description:\n{jd_text}"
    )
    return chat_completion_json(
        SELECT_SYSTEM_PROMPT, user_prompt, model=model, temperature=0.3,
        provider=provider, api_key_env=api_key_env,
    )


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
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> dict:
    """Generate resume.pdf, cover_letter.pdf, and a trace.json sidecar for one job.

    Returns dict with resume_path, cover_letter_path, trace_path (all as str).
    """
    selection = _select_content(profile, job_title, company, jd_text, model, provider=provider, api_key_env=api_key_env)
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

    # Trim to max_pages by dropping lowest-priority content if needed.
    # Projects are a requested feature (min 2 entries) so they're protected
    # until only 2 remain; experience bullets are trimmed first since resumes
    # commonly compress older/less relevant roles before cutting projects
    # entirely.
    attempts = 0
    while _count_pdf_pages(resume_path) > max_pages and attempts < 6:
        experiences = resume_data.get("experiences", [])
        projects = resume_data.get("projects", [])
        trimmed_a_bullet = False
        for exp in reversed(experiences):
            if len(exp.get("bullets", [])) > 3:
                exp["bullets"] = exp["bullets"][:-1]
                trimmed_a_bullet = True
                break
        if trimmed_a_bullet:
            attempts += 1
            _render_pdf(
                "resume.html.j2",
                {"candidate_name": candidate_name, "contact_line": contact_line, **resume_data},
                resume_path,
            )
            continue
        if len(projects) > 2:
            resume_data["projects"].pop()
        elif len(experiences) > 1:
            experiences[-1]["bullets"] = experiences[-1]["bullets"][:2]
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
    force: bool = False,
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> int:
    """Generate docs for every job with match_score >= min_score lacking a resume.

    If force=True, regenerate even jobs that already have a resume_path
    (e.g. after a template/prompt change).
    Returns count generated.
    """
    import re

    from jobhunt.db import get_jobs_for_docgen, set_generated_docs

    rows = get_jobs_for_docgen(conn, min_score, force=force)
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
            provider=provider,
            api_key_env=api_key_env,
        )
        set_generated_docs(conn, row["url"], result["resume_path"], result["cover_letter_path"])
    return len(rows)
