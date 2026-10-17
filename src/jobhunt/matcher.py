"""LLM-based match scoring: compares a job description against MyProfile and
returns a 1-10 matchScore plus a short rationale.
"""
from __future__ import annotations

import json
import sqlite3
import time

from jobhunt.llm import LLMRequestError, chat_completion_json

SYSTEM_PROMPT = """You are an expert technical recruiter. You will be given a
candidate's profile (skills, experience, projects) and a job description.
Rate how well the candidate matches the job on a scale of 1-10, where:
- 10 = exceptional match: candidate meets/exceeds all key technical requirements and experience level
- 7-8 = strong match: candidate meets most requirements with minor gaps
- 4-6 = partial match: some relevant skills but notable gaps
- 1-3 = poor match: largely unrelated role or major experience/skill mismatch

Base the score ONLY on what's genuinely present in the candidate's profile versus
what the job description asks for. Do not assume skills not listed.

Return a JSON object: {"match_score": integer 1-10, "rationale": short string (<= 2 sentences)}
"""

# Small delay between consecutive LLM calls when scoring a batch of jobs, to
# stay under GitHub Models' per-minute rate limit rather than relying purely
# on chat_completion()'s retry/backoff to recover after the fact.
PACING_SECONDS = 3.0


def score_job(
    profile: dict,
    job_title: str,
    company: str,
    jd_text: str,
    model: str = "openai/gpt-4o-mini",
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> tuple[int, str]:
    user_prompt = (
        f"Candidate profile:\n{json.dumps(profile)}\n\n"
        f"Job: {job_title} at {company}\n\nJob description:\n{jd_text}"
    )
    result = chat_completion_json(SYSTEM_PROMPT, user_prompt, model=model, provider=provider, api_key_env=api_key_env)
    score = int(result.get("match_score", 0))
    score = max(1, min(10, score))
    rationale = str(result.get("rationale", ""))
    return score, rationale


def score_pending_jobs(
    conn: sqlite3.Connection,
    profile: dict,
    model: str = "openai/gpt-4o-mini",
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> int:
    """Score every job in the DB that doesn't have a match_score yet.

    Paces requests to avoid bursting GitHub Models' rate limit, and skips
    (rather than aborting the whole batch on) any single job that fails after
    chat_completion()'s own retry/backoff is exhausted - that job simply stays
    unscored and will be retried on the next `jobhunt score` run.
    Returns the count of jobs successfully scored.
    """
    from jobhunt.db import get_jobs_needing_score, set_match_score

    rows = get_jobs_needing_score(conn)
    scored = 0
    for i, row in enumerate(rows):
        try:
            score, rationale = score_job(
                profile, row["title"], row["company"], row["jd_text"] or "",
                model=model, provider=provider, api_key_env=api_key_env,
            )
        except LLMRequestError as exc:
            print(f"[matcher] skipping '{row['title']}' ({row['url']}): {exc}")
            continue
        set_match_score(conn, row["url"], score, rationale)
        scored += 1
        if i < len(rows) - 1:
            time.sleep(PACING_SECONDS)
    return scored
