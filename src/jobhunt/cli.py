"""jobhunt CLI — thin Typer wrapper over the importable functions in this
package (config, db, llm, profile_builder, matcher, docgen, report, summary,
scrapers). No business logic should live here; every command just parses
args, loads config/db, and calls a plain function. This keeps the same
functions reusable from a future Copilot CLI custom skill wrapper.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.table import Table

from jobhunt import db as db_module
from jobhunt import docgen as docgen_module
from jobhunt import matcher as matcher_module
from jobhunt import profile_builder as profile_module
from jobhunt import report as report_module
from jobhunt import summary as summary_module
from jobhunt.config import JobHuntConfig, load_config
from jobhunt.scrapers import base as scraper_base
from jobhunt.scrapers import indeed, jora, linkedin, seek

app = typer.Typer(help="Automated job search, match scoring, and resume/cover-letter generation.")

REPO_ROOT = Path(__file__).resolve().parents[2]

SCRAPERS = {
    "seek": seek,
    "jora": jora,
    "linkedin": linkedin,
    "indeed": indeed,
}


def _output_dir_for_today() -> Path:
    return REPO_ROOT / "output" / date.today().isoformat()


def _load_profile_or_exit() -> dict:
    profile = profile_module.load_existing_profile()
    if profile is None:
        rprint(
            "[red]No profile found.[/red] Run [bold]jobhunt profile build[/bold] first "
            "(after adding resumes to profile/resumes/)."
        )
        raise typer.Exit(code=1)
    return profile


@app.command("profile-build")
def profile_build(
    force: bool = typer.Option(False, "--force", help="Rebuild from scratch, ignoring existing profile."),
    model: Optional[str] = typer.Option(None, help="Override the configured LLM model."),
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
) -> None:
    """Build/update MyProfile from resumes in profile/resumes/.

    Run this INFREQUENTLY — only when you add a new resume, skill, or project.
    It is intentionally NOT part of `run-all`.
    """
    cfg = load_config(config_path)
    profile = profile_module.build_profile(model=model or cfg.llm.model, force=force)
    rprint(f"[green]Profile built/updated:[/green] {profile_module.DEFAULT_PROFILE_PATH}")
    rprint(f"Skills: {len(profile.get('skills', []))}, Experiences: {len(profile.get('experiences', []))}, "
           f"Projects: {len(profile.get('projects', []))}")
    if profile.get("_conflicts"):
        rprint(f"[yellow]Conflicts flagged for review:[/yellow] {profile['_conflicts']}")


@app.command()
def login(portal: str = typer.Argument(..., help="Portal to log into: seek, jora, linkedin, indeed")) -> None:
    """One-time interactive login: opens a headed browser for the given portal so
    you can log in manually. Session is saved for future headless `search` runs.
    """
    if portal not in SCRAPERS:
        rprint(f"[red]Unknown portal '{portal}'.[/red] Choices: {list(SCRAPERS)}")
        raise typer.Exit(code=1)
    context = scraper_base.open_persistent_context(portal, headless=False)
    page = context.new_page()
    portal_module = SCRAPERS[portal]
    page.goto(getattr(portal_module, "base_url", "about:blank"))
    rprint(f"[cyan]Log in to {portal} in the opened browser window, then press Enter here to finish.[/cyan]")
    input()
    scraper_base.close_persistent_context(context)
    rprint(f"[green]Session saved for {portal}.[/green]")


@app.command()
def search(
    portal: Optional[str] = typer.Option(None, help="Limit to a single portal (default: all enabled)."),
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
) -> None:
    """Search configured keywords/locations across enabled portals and upsert results into the DB."""
    cfg = load_config(config_path)
    portals_to_run = [portal] if portal else cfg.enabled_portals()

    with db_module.connect() as conn:
        for portal_name in portals_to_run:
            if portal_name not in SCRAPERS:
                rprint(f"[yellow]Skipping unknown portal '{portal_name}'.[/yellow]")
                continue
            portal_cfg = cfg.portals.get(portal_name)
            base_url = portal_cfg.base_url if portal_cfg else ""
            module = SCRAPERS[portal_name]
            try:
                context = scraper_base.open_persistent_context(portal_name, headless=True)
                page = context.new_page()
                for keyword in cfg.search.keywords:
                    for location in cfg.search.locations or [""]:
                        try:
                            listings = module.search(page, keyword, location, base_url) if base_url else module.search(page, keyword, location)
                        except NotImplementedError as exc:
                            rprint(f"[yellow]{portal_name}: {exc}[/yellow]")
                            continue
                        for listing in listings:
                            if any(x.lower() in listing.title.lower() for x in cfg.search.exclude_keywords):
                                continue
                            db_module.upsert_job(conn, listing)
                        rprint(f"[green]{portal_name}[/green] '{keyword}' in '{location}': {len(listings)} listing(s)")
                scraper_base.close_persistent_context(context)
            except Exception as exc:  # noqa: BLE001 - one portal failing must not stop the rest
                rprint(f"[red]Search failed for portal '{portal_name}': {exc}[/red]")
                continue


@app.command()
def score(
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
    model: Optional[str] = typer.Option(None),
) -> None:
    """Score all unscored jobs in the DB against MyProfile (1-10 matchScore)."""
    cfg = load_config(config_path)
    profile = _load_profile_or_exit()
    with db_module.connect() as conn:
        count = matcher_module.score_pending_jobs(conn, profile, model=model or cfg.llm.model)
    rprint(f"[green]Scored {count} job(s).[/green]")


@app.command()
def generate(
    candidate_name: str = typer.Option(..., help="Your full name for the resume/cover letter header."),
    contact_line: str = typer.Option(..., help="e.g. 'email@example.com | +61 4xx xxx xxx | Sydney, NSW'"),
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
    model: Optional[str] = typer.Option(None),
) -> None:
    """Generate tailored resume + cover letter PDFs for jobs above the matchScore threshold."""
    cfg = load_config(config_path)
    profile = _load_profile_or_exit()
    output_dir = _output_dir_for_today()
    with db_module.connect() as conn:
        count = docgen_module.generate_for_pending_jobs(
            conn,
            profile,
            candidate_name,
            contact_line,
            output_dir,
            min_score=cfg.search.min_match_score_for_docs,
            model=model or cfg.llm.model,
            max_pages=cfg.output.resume_max_pages,
        )
    rprint(f"[green]Generated documents for {count} job(s) in {output_dir}[/green]")


@app.command()
def report(
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
) -> None:
    """Write the job tracking report (CSV/HTML) for today."""
    cfg = load_config(config_path)
    output_dir = _output_dir_for_today()
    with db_module.connect() as conn:
        paths = report_module.build_report(conn, output_dir, cfg.output.report_formats)
        rows = report_module.build_report_rows(conn)

    table = Table(title="Job Report")
    for col in report_module.COLUMNS:
        table.add_column(col)
    for row in rows[:20]:
        table.add_row(*(str(row[c]) for c in report_module.COLUMNS))
    rprint(table)
    for fmt, path in paths.items():
        rprint(f"[green]{fmt.upper()} report:[/green] {path}")


@app.command()
def summary(
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
) -> None:
    """Write the single-page summary of jobs ready for application."""
    cfg = load_config(config_path)
    output_dir = _output_dir_for_today()
    with db_module.connect() as conn:
        path = summary_module.build_summary(conn, cfg.search.min_match_score_for_docs, output_dir)
    rprint(f"[green]Summary written:[/green] {path}")


@app.command()
def status(url: str, new_status: str) -> None:
    """Manually update a job's status (e.g. applied, interviewing, rejected, ignored)."""
    with db_module.connect() as conn:
        job = db_module.get_job(conn, url)
        if job is None:
            rprint(f"[red]No job found with url {url}[/red]")
            raise typer.Exit(code=1)
        db_module.set_status(conn, url, new_status)
    rprint(f"[green]Updated status for {url} -> {new_status}[/green]")


@app.command("run-all")
def run_all(
    candidate_name: str = typer.Option(..., help="Your full name for the resume/cover letter header."),
    contact_line: str = typer.Option(..., help="e.g. 'email@example.com | +61 4xx xxx xxx | Sydney, NSW'"),
    config_path: Optional[str] = typer.Option(None, "--config", envvar="JOBHUNT_CONFIG"),
) -> None:
    """Run the full daily pipeline: search -> score -> generate -> report -> summary.

    Does NOT include `profile-build` (run that separately/infrequently).
    """
    search(portal=None, config_path=config_path)
    score(config_path=config_path, model=None)
    generate(candidate_name=candidate_name, contact_line=contact_line, config_path=config_path, model=None)
    report(config_path=config_path)
    summary(config_path=config_path)


if __name__ == "__main__":
    app()
