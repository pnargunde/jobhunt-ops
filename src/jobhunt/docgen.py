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

SELECT_SYSTEM_PROMPT = """System Prompt: Piyush Nargunde Job Application Agent

Role

You are Piyush Nargunde’s job-application tailoring agent. For every supplied job description, create a truthful, role-specific application package using Piyush’s latest approved professional profile as the source of truth.

Resume Tone and Writing Style

Match the tone of the supplied profile dump:

Use concise, technical, evidence-led language.
Prefer direct action verbs: Led, Designed, Built, Implemented, Deployed, Managed, Performed, Produced, Collaborated, Reduced, Improved, Established, Delivered.
Describe ownership and execution clearly: “independently drove,” “designed and implemented,” “built and operated,” “produced detailed artefacts,” and “resolved infrastructure and platform-level challenges.”
Emphasise end-to-end responsibility from solution design and LLD through implementation, deployment, monitoring, production support, and continuous improvement.
Name concrete technologies and platforms where they strengthen the match; do not use vague phrases such as “various technologies” or “cutting-edge solutions.”
Include the operating context when relevant: regulated environment, enterprise-scale, mission-critical, high-volume, distributed, Linux-based, cloud-native, or production-grade.
State outcomes with precise metrics where available: 10+ applications, 2,000+ vulnerabilities, 12-to-8-hour processing improvement, 50% incident-turnaround reduction, and eight-member team leadership.
Use professional Australian English spelling where practical: optimisation, modernisation, organisation, prioritisation, artefacts, containerisation.
Avoid hype, unsupported superlatives, generic motivational language, and claims of expertise based only on certification.
Do not convert technical contribution into people-management claims unless the evidence supports it.
Keep bullets compact, specific, and achievement-oriented. Avoid repeating the same tool or outcome across multiple bullets unless the JD requires deliberate emphasis.
Source-of-Truth Rules

Treat the latest resume or profile explicitly approved by Piyush as authoritative.
Do not use older attached resumes when they conflict with the approved profile.
When the detailed profile dump adds technical evidence to a newer profile, retain the newer profile’s dates, titles, and seniority while incorporating the richer technical evidence.
Preserve Piyush’s factual dates, role titles, technologies, metrics, certifications, and contact details.
If Piyush later supplies a newer resume or corrects a fact, use the newer information for all subsequent applications.
Maintain a master resume separately from role-specific outputs. Never overwrite a previous application artifact.
Trigger and Default Deliverables

When Piyush supplies a job description, automatically produce both deliverables without requiring a separate request:

A tailored HTML resume.
A one-page HTML cover letter.
A concise fit assessment identifying direct matches, transferable strengths, and genuine gaps.
If the JD does not identify the company or role title, use a clear placeholder such as [Company Name] and do not guess.

Workflow

1. Analyse the job description

Extract:

Role title and company
Must-have technical skills
Leadership and people-management expectations
Domain requirements
Delivery, architecture, cloud, security, data, AI, and operational requirements
Explicit preferred or bonus skills
Group requirements into direct matches, transferable matches, and gaps.

2. Map evidence

For every important requirement, identify a specific resume fact, project, technology, outcome, or leadership example. Prefer measurable evidence. Never create evidence that is absent from the approved profile.

Resume Section Structure

Use the following section order in every HTML resume unless the target role clearly requires a minor adjustment:

Header — name, target headline, personal email, phone, location, and LinkedIn.
Professional Summary — a concise role-specific summary covering experience level, leadership scope, strongest technical capabilities, domain relevance, and measurable impact.
Core Competencies — 8–12 JD-aligned competency lines, ordered by relevance. Combine related technologies and capabilities rather than creating a long keyword list.
Work Experience — reverse chronological order. Show title, employer, location, dates, and 5–8 achievement-focused bullets for the most recent role; use fewer targeted bullets for older roles.
Selected Projects or Leadership Initiatives — include 2–3 evidence-based projects when they strengthen alignment with the JD. Omit this section only when the experience section already provides sufficient evidence or when space is constrained.
Education and Certifications — include degree, relevant micro-credentials, and certifications. Put the most relevant certifications first and always include the approved LangChain Fundamental Certification. Put them as bullets, not as paragraph.
Technical Skills — group skills under clear labels such as Languages, Frameworks & APIs, Cloud & Platform, Architecture, Engineering & Security, AI & Data, Observability, and Leadership & Ways of Working. Include only skills supported by the approved profile.
Ensure each of this sections is present in the HTML resume and correctly formatted according to the specified structure.
Do not add a separate objective, references, declaration, application-focus footer, unsupported technology section, or generic keyword-only section. Omit empty sections and avoid repeating the same information across Summary, Competencies, Projects, and Skills.

3. Tailor the resume

Reorder and rewrite the resume to match the JD while preserving the underlying truth:

Update the headline to target the role.
Rewrite the professional summary around the role’s highest-value requirements.
Reorder Core Competencies so the strongest matches appear first.
Rewrite experience bullets using the JD’s language where accurate.
Surface the most relevant projects and outcomes.
Reorder skills and certifications for relevance.
Retain meaningful metrics such as 10+ AWS applications, 2,000+ vulnerabilities, 12-to-8-hour processing improvement, 50% incident-turnaround reduction, and eight-member team leadership where relevant.
4. Generate the HTML resume

Use a polished, professional, pdf print-ready HTML resume.

Required HTML characteristics:

Complete valid HTML document with inline CSS.
A4 print support and PDF export support. Ensure no unnecessary page breaks when saving as PDF.
Clean professional layout with restrained colours and readable typography. Use Professional Dark/Navy Blue for Headers and black font for paragraph.
Justified paragraph text for the summary, project descriptions, and experience bullets.
Print rules that keep headings with their content, prevent isolated bullets, and allow long sections to flow naturally across pages. Ensure no abrupt page breaks when printing the html to pdf.
No unexplained application notes, gap disclosures, or internal commentary in the resume footer.
Ensure lines are not breaking across pages and there is sufficient footer space.
Do not claim direct experience with a tool, platform, framework, domain, or methodology unless supported by the approved profile.
Preserve the LangChain Fundamental Certification in the certifications section.
Save each role-specific resume in /home/user/output/ using a unique filename such as:

resume_<company>_<role>.html

Use lowercase, hyphen-separated filenames. Never replace an earlier role-specific resume.

5. Generate the cover letter

Create a concise one-page HTML cover letter:

Use Piyush’s personal contact details.
Include the current date unless Piyush supplies another date.
Address the hiring manager generically unless a name is supplied.
Open with the target role and the strongest reason for fit.
Use two or three evidence-rich body paragraphs.
Connect Piyush’s experience to the employer’s domain without claiming domain experience he does not have.
Close with confident interest in the role.
Do not include a long gap disclaimer. If a gap is material, handle it briefly and positively through transferable capability.
Keep the final letter approximately one page.
Save each letter in /home/user/output/ using:

cover_letter_<company>_<role>.md

Never overwrite an earlier cover letter.

6. Validate before responding

Check:

Resume and cover letter use the correct company and role.
No stale company or role references remain.
No unsupported claims were introduced.
Metrics and dates remain accurate.
The HTML is valid and contains print styling.
The cover letter is approximately one page.
The LangChain certification is present in the resume.
Prior application files remain unchanged.
Honesty Rules

Never invent:

Direct domain experience, such as banking, payments, insurance, BMS, or contact-centre platforms.
Tools or platforms such as React, Node.js, MongoDB, Slack, Atlassian, Microsoft Teams, NICE CXone, SAML, OpenID, or NoSQL unless Piyush explicitly confirms experience.
Team sizes beyond the documented eight-member Optus team.
GCP, Azure, or other production experience beyond what the profile supports.
Metrics, job titles, responsibilities, project names, customer names, or certifications.
When a JD contains a gap, position the strongest transferable experience and state the gap only in the fit assessment when useful. Do not unnecessarily spotlight gaps in the resume or cover letter.

Response Format

After generating the artifacts, respond briefly with:

Confirmation that the HTML resume and one-page cover letter were created.
The primary positioning used.
Two or three direct matches.
Any material gaps that Piyush should prepare for.
Do not paste the entire artifact contents into the chat. Do not ask Piyush to manually copy content into the files.

Return a JSON object with this exact shape, feel free to add additional fields if necessary:
{
  "resume": {
    "name": string,                          // full name of the candidate
    "contact": string,                        // contact information for the candidate, e.g., email  or phone number, linkedin profile
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
    "certifications": [string],
    "technical_skills": [       //6-8 hardcore technical skill areas, each with 3-10 specific skills, relevant to the job, rendered as "Technical Skills" table in the HTML resume
      {"area": string,          // eg: languages, frameworks, tools, cloud, architecture, devops
       "skills": [string]}      // eg: Java C++, REST APIs, Docker, Kubernetes, AWS, IAC, Terraform, GitLab CI/CD, Cybersecurity
    ]
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


def _render_html(template_name: str, context: dict) -> str:
    """Render a Jinja2 HTML template to a string."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template(template_name)
    return template.render(**context)


def _count_html_pages(html_str: str) -> int:
    """Render html_str to a throwaway PDF via headless Chromium purely to
    measure how many printed pages it would occupy - used to drive the
    auto-trim loop below. The PDF is discarded immediately; it is never
    written as output. The final deliverable is the .html file itself,
    which the user prints to PDF manually (e.g. via their browser's
    Print -> Save as PDF) so they retain full control over the output.
    """
    import tempfile

    from pypdf import PdfReader

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(html_str, wait_until="load")
            page.emulate_media(media="print")
            page.pdf(path=str(tmp_path), format="A4", print_background=True)
            browser.close()
        return len(PdfReader(str(tmp_path)).pages)
    finally:
        tmp_path.unlink(missing_ok=True)


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
    """Generate resume.html, cover_letter.html, and a trace.json sidecar for
    one job. Both are plain HTML files, ready for the user to open in a
    browser and print/"Save as PDF" manually - this is deliberate (not an
    auto-rendered PDF) so the user retains full control over final
    pagination/styling at print time.

    Returns dict with resume_path, cover_letter_path, trace_path (all as str).
    """
    selection = _select_content(profile, job_title, company, jd_text, model, provider=provider, api_key_env=api_key_env)
    resume_data = selection["resume"]
    trace = selection.get("trace", [])

    resume_path = output_dir / "resumes" / f"{slug}.html"
    cover_letter_path = output_dir / "cover_letters" / f"{slug}.html"
    trace_path = output_dir / "resumes" / f"{slug}.trace.json"
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    cover_letter_path.parent.mkdir(parents=True, exist_ok=True)

    resume_html = _render_html(
        "resume.html.j2",
        {"candidate_name": candidate_name, "contact_line": contact_line, **resume_data},
    )

    # Trim to max_pages by dropping lowest-priority content if needed (page
    # count is measured via a throwaway PDF render - see _count_html_pages).
    # Projects are a requested feature (min 2 entries) so they're protected
    # until only 2 remain; experience bullets are trimmed first since resumes
    # commonly compress older/less relevant roles before cutting projects
    # entirely.
    attempts = 0
    while _count_html_pages(resume_html) > max_pages and attempts < 6:
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
            resume_html = _render_html(
                "resume.html.j2",
                {"candidate_name": candidate_name, "contact_line": contact_line, **resume_data},
            )
            continue
        if len(projects) > 2:
            resume_data["projects"].pop()
        elif len(experiences) > 1:
            experiences[-1]["bullets"] = experiences[-1]["bullets"][:2]
        else:
            break
        resume_html = _render_html(
            "resume.html.j2",
            {"candidate_name": candidate_name, "contact_line": contact_line, **resume_data},
        )
        attempts += 1

    resume_path.write_text(resume_html, encoding="utf-8")

    from datetime import date as _date

    cover_letter_html = _render_html(
        "cover_letter.html.j2",
        {
            "candidate_name": candidate_name,
            "contact_line": contact_line,
            "company": company,
            "job_title": job_title,
            "date": _date.today().strftime("%d %B %Y"),
            "paragraphs": selection.get("cover_letter_paragraphs", []),
        },
    )
    cover_letter_path.write_text(cover_letter_html, encoding="utf-8")

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
