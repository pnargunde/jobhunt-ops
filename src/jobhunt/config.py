"""Configuration loading for jobhunt.

Config path resolution order (first match wins):
1. Explicit path passed to load_config(path=...)
2. JOBHUNT_CONFIG environment variable
3. ./config/config.yaml relative to the current working directory
4. config/config.example.yaml (bundled defaults) as a last resort fallback

Kept path-flexible (not hardcoded to CWD) so this can be invoked from a
future Copilot CLI skill wrapper or any other automation context, not just
a terminal sitting in the repo root.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "config.example.yaml"
ENV_VAR = "JOBHUNT_CONFIG"


@dataclass
class SalaryExpectation:
    min: int | None = None
    max: int | None = None
    currency: str = "AUD"
    period: str = "annual"


@dataclass
class SearchConfig:
    date_range_days: int = 7
    locations: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    employment_types: list[str] = field(default_factory=lambda: ["full_time"])
    min_match_score_for_docs: int = 8


@dataclass
class PortalConfig:
    enabled: bool = True
    base_url: str = ""


@dataclass
class LLMConfig:
    model: str = "openai/gpt-4o-mini"


@dataclass
class OutputConfig:
    report_formats: list[str] = field(default_factory=lambda: ["csv", "html"])
    resume_max_pages: int = 3


@dataclass
class JobHuntConfig:
    salary_expectation: SalaryExpectation
    search: SearchConfig
    portals: dict[str, PortalConfig]
    llm: LLMConfig
    output: OutputConfig
    source_path: Path

    def enabled_portals(self) -> list[str]:
        return [name for name, cfg in self.portals.items() if cfg.enabled]


def resolve_config_path(path: str | os.PathLike | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env_path = os.environ.get(ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return EXAMPLE_CONFIG_PATH


def load_config(path: str | os.PathLike | None = None) -> JobHuntConfig:
    """Load and validate the jobhunt YAML config into a typed config object."""
    resolved = resolve_config_path(path)
    if not resolved.exists():
        raise FileNotFoundError(
            f"Config file not found at {resolved}. Copy config/config.example.yaml "
            "to config/config.yaml and edit it, or pass --config/JOBHUNT_CONFIG."
        )
    raw: dict[str, Any] = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}

    salary_raw = raw.get("salary_expectation", {}) or {}
    salary = SalaryExpectation(
        min=salary_raw.get("min"),
        max=salary_raw.get("max"),
        currency=salary_raw.get("currency", "AUD"),
        period=salary_raw.get("period", "annual"),
    )

    search_raw = raw.get("search", {}) or {}
    search = SearchConfig(
        date_range_days=search_raw.get("date_range_days", 7),
        locations=search_raw.get("locations", []) or [],
        keywords=search_raw.get("keywords", []) or [],
        exclude_keywords=search_raw.get("exclude_keywords", []) or [],
        employment_types=search_raw.get("employment_types", ["full_time"]) or ["full_time"],
        min_match_score_for_docs=search_raw.get("min_match_score_for_docs", 8),
    )

    portals_raw = raw.get("portals", {}) or {}
    portals = {
        name: PortalConfig(enabled=cfg.get("enabled", True), base_url=cfg.get("base_url", ""))
        for name, cfg in portals_raw.items()
    }

    llm_raw = raw.get("llm", {}) or {}
    llm = LLMConfig(model=llm_raw.get("model", "openai/gpt-4o-mini"))

    output_raw = raw.get("output", {}) or {}
    output = OutputConfig(
        report_formats=output_raw.get("report_formats", ["csv", "html"]),
        resume_max_pages=output_raw.get("resume_max_pages", 3),
    )

    if not search.keywords:
        raise ValueError("config.search.keywords must contain at least one search keyword")

    return JobHuntConfig(
        salary_expectation=salary,
        search=search,
        portals=portals,
        llm=llm,
        output=output,
        source_path=resolved,
    )
