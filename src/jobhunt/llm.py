"""Pluggable LLM client supporting multiple inference providers.

Supported providers (selected via config.llm.provider, default "github_models"):
- "github_models": free/included tier via GitHub Models, auth via `gh auth token`
  or GITHUB_MODELS_TOKEN env var. Model ids look like "openai/gpt-4o-mini".
- "openai": direct OpenAI API. Auth via OPENAI_API_KEY env var (or api_key_env
  override). Model ids look like "gpt-4o", "gpt-4.1", etc.
- "anthropic": direct Anthropic API. Auth via ANTHROPIC_API_KEY env var (or
  api_key_env override). Model ids look like "claude-3-5-sonnet-20241022".
- "google_genai": Google's Gemini API. Auth via GOOGLE_API_KEY env var (or
  api_key_env override). Model ids look like "gemini-2.5-flash-lite".
- "bedrock": AWS Bedrock. No single flat API key - auth via the standard AWS
  credential chain (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN
  env vars, ~/.aws/credentials, or an IAM role) plus AWS_REGION. Model ids
  look like "anthropic.claude-3-5-sonnet-20241022-v2:0".

All API keys/secrets are read from environment variables, populated from a
gitignored .env file at the repo root (see .env.example) via python-dotenv,
loaded once in config.py - never hardcoded or passed on the CLI.

Rationale: GitHub Models is convenient (no separate key, no billing setup) but
rate-limited and its model roster is a subset of what each vendor offers
directly. For higher-quality output (e.g. resume writing) you may want to
point straight at a vendor's flagship model using your own API key - this
module lets you do that with just a config change, no code change, and every
call site (matcher.py, docgen.py) is unaffected since they only use the
provider-agnostic chat_completion_json() function.

Request/response plumbing (HTTP calls, retries/backoff, provider-specific
payload shapes) is delegated to LangChain's chat model classes
(`langchain_openai.ChatOpenAI`, `langchain_anthropic.ChatAnthropic`,
`langchain_google_genai.ChatGoogleGenerativeAI`, `langchain_aws.ChatBedrockConverse`)
rather than hand-rolled here - this buys us battle-tested retry/backoff,
timeout handling, and a consistent message/response interface across
providers, while `gh`-CLI token resolution (GitHub Models has no first-class
LangChain integration) stays custom below.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# GitHub Models exposes an OpenAI-compatible Chat Completions API at this
# base URL, so it's served via ChatOpenAI with a custom base_url rather than
# a dedicated LangChain integration.
GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"

# Providers authenticated with a single flat API key/token, resolved via
# resolve_token() below. Bedrock is deliberately excluded - it has no single
# "key", it uses the AWS SDK's own credential chain (see _build_chat_model).
DEFAULT_API_KEY_ENV = {
    "github_models": "GITHUB_MODELS_TOKEN",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}

# Common Windows install locations for the GitHub CLI, used as a fallback when
# `gh` isn't resolvable via PATH from the current process (this happens more
# often than you'd expect: PATH updates made in one shell session, or via the
# Windows installer, don't always propagate to every terminal/process - e.g.
# a PowerShell/Git Bash session opened before `gh` was installed, or a GUI
# tool that inherited an old environment snapshot).
_WINDOWS_GH_FALLBACK_PATHS = [
    r"C:\Program Files\GitHub CLI\gh.exe",
    r"C:\Program Files (x86)\GitHub CLI\gh.exe",
]


class LLMAuthError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


def _token_from_env(env_var: str) -> str | None:
    return os.environ.get(env_var)


def _find_gh_executable() -> str | None:
    """Locate the `gh` binary: PATH first, then common Windows install
    locations (including under LOCALAPPDATA, where some install methods put
    it) as a fallback."""
    found = shutil.which("gh")
    if found:
        return found
    candidates = list(_WINDOWS_GH_FALLBACK_PATHS)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(str(Path(local_app_data) / "Programs" / "GitHub CLI" / "gh.exe"))
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _token_from_gh_cli() -> str | None:
    gh_path = _find_gh_executable()
    if not gh_path:
        return None
    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def resolve_token(provider: str = "github_models", api_key_env: str | None = None) -> str:
    """Resolve the API key/token for the given provider.

    - github_models: api_key_env (default GITHUB_MODELS_TOKEN) env var first,
      else `gh auth token`.
    - openai / anthropic / google_genai: api_key_env (default
      OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY) env var only -
      these providers have no CLI-based fallback.
    - bedrock: not supported here - it has no single flat key, see
      _build_chat_model, which uses the AWS SDK's own credential chain
      instead of calling this function.
    """
    env_var = api_key_env or DEFAULT_API_KEY_ENV.get(provider, "GITHUB_MODELS_TOKEN")
    token = _token_from_env(env_var)
    if token:
        return token

    if provider != "github_models":
        raise LLMAuthError(
            f"No API key found for provider '{provider}'. Set the {env_var} "
            "environment variable to your API key from that provider's dashboard."
        )

    gh_path = _find_gh_executable()
    if not gh_path:
        raise LLMAuthError(
            "Could not find the GitHub CLI (`gh`) on PATH or in common install locations, "
            f"and {env_var} is not set. Install/reopen a fresh terminal after "
            "installing GitHub CLI, or set the environment variable directly "
            "(a personal access token with 'models: read' scope)."
        )

    token = _token_from_gh_cli()
    if token:
        return token

    raise LLMAuthError(
        f"Found GitHub CLI at '{gh_path}' but `gh auth token` did not return a token. "
        f"Run `gh auth login` (or `gh auth refresh`) to authenticate, or set the "
        f"{env_var} environment variable directly."
    )


def _build_chat_model(
    provider: str,
    model: str,
    api_key_env: str | None,
    temperature: float,
    timeout: int,
    max_retries: int,
) -> BaseChatModel:
    """Construct the LangChain chat model for the given provider.

    GitHub Models and OpenAI both speak the OpenAI Chat Completions wire
    format, so both go through ChatOpenAI (GitHub Models via a custom
    base_url); Anthropic and Google Gemini have their own LangChain
    integrations. Bedrock uses the AWS SDK's own credential chain (env vars,
    ~/.aws/credentials, or an IAM role) rather than resolve_token() - there's
    no single flat API key to resolve. Retry/backoff on rate limits and
    transient 5xx errors, and request timeouts, are handled by the underlying
    provider SDK that each LangChain class wraps - no hand-rolled retry loop
    needed here.
    """
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=model,
            temperature=temperature,
            region_name=os.environ.get("AWS_REGION"),
        )

    if provider == "anthropic":
        return ChatAnthropic(
            model=model,
            api_key=resolve_token(provider, api_key_env),
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=4096,
        )

    if provider == "google_genai":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=resolve_token(provider, api_key_env),
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    kwargs: dict = {
        "model": model,
        "api_key": resolve_token(provider, api_key_env),
        "temperature": temperature,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if provider == "github_models":
        kwargs["base_url"] = GITHUB_MODELS_BASE_URL
    return ChatOpenAI(**kwargs)


# Providers that support OpenAI-style structured JSON output
# (response_format={"type": "json_object"}). Others get an explicit
# "respond with JSON only" instruction appended to the system prompt instead.
_JSON_MODE_PROVIDERS = {"github_models", "openai"}


def chat_completion_json(
    system_prompt: str,
    user_prompt: str,
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.2,
    provider: str = "github_models",
    api_key_env: str | None = None,
    timeout: int = 60,
    max_retries: int = 5,
) -> dict:
    """Convenience helper for prompts that expect a JSON object back.

    This is the entry point matcher.py, docgen.py, and profile_builder.py use
    - it's fully provider-agnostic, so switching providers/models is a config
    change only (config.llm.provider: github_models | openai | anthropic |
    google_genai | bedrock). Internally dispatches to a LangChain chat model
    per provider (see _build_chat_model), so request/response plumbing and
    retry/backoff are LangChain's, not hand-rolled.
    """
    effective_system_prompt = system_prompt
    extra_kwargs: dict = {}
    if provider in _JSON_MODE_PROVIDERS:
        extra_kwargs["response_format"] = {"type": "json_object"}
    else:
        # Anthropic, Google Gemini, and Bedrock don't share OpenAI's
        # response_format kwarg - fall back to an explicit instruction
        # (all three follow this reliably in practice).
        effective_system_prompt += (
            "\n\nRespond with ONLY a single valid JSON object. No markdown code fences, no commentary."
        )

    chat_model = _build_chat_model(provider, model, api_key_env, temperature, timeout, max_retries)
    messages = [SystemMessage(content=effective_system_prompt), HumanMessage(content=user_prompt)]
    try:
        if extra_kwargs:
            response = chat_model.invoke(messages, **extra_kwargs)
        else:
            response = chat_model.invoke(messages)
    except Exception as exc:  # LangChain surfaces provider SDK errors (auth, rate limit, etc.) as-is
        raise LLMRequestError(f"LLM request to provider '{provider}' (model '{model}') failed: {exc}") from exc

    content = response.content
    if isinstance(content, list):
        # Some providers (e.g. Anthropic) may return a list of content blocks
        # rather than a plain string.
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    content = content.strip()
    # Anthropic (and occasionally others) may wrap JSON in a markdown code
    # fence despite instructions - strip it defensively before parsing.
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMRequestError(f"LLM response was not valid JSON: {content!r}") from exc
