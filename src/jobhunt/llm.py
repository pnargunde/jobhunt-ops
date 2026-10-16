"""Pluggable LLM client supporting multiple inference providers.

Supported providers (selected via config.llm.provider, default "github_models"):
- "github_models": free/included tier via GitHub Models, auth via `gh auth token`
  or GITHUB_MODELS_TOKEN env var. Model ids look like "openai/gpt-4o-mini".
- "openai": direct OpenAI API. Auth via OPENAI_API_KEY env var (or api_key_env
  override). Model ids look like "gpt-4o", "gpt-4.1", etc.
- "anthropic": direct Anthropic API. Auth via ANTHROPIC_API_KEY env var (or
  api_key_env override). Model ids look like "claude-3-5-sonnet-20241022".

Rationale: GitHub Models is convenient (no separate key, no billing setup) but
rate-limited and its model roster is a subset of what each vendor offers
directly. For higher-quality output (e.g. resume writing) you may want to
point straight at a vendor's flagship model using your own API key - this
module lets you do that with just a config change, no code change, and every
call site (matcher.py, docgen.py) is unaffected since they only use the
provider-agnostic chat_completion_json() function.

Kept as a small, dependency-light wrapper (stdlib `urllib` + `subprocess`) so
the package doesn't force an extra HTTP client dependency just for this.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

DEFAULT_API_KEY_ENV = {
    "github_models": "GITHUB_MODELS_TOKEN",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
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


@dataclass
class LLMResponse:
    content: str
    raw: dict


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
    - openai / anthropic: api_key_env (default OPENAI_API_KEY / ANTHROPIC_API_KEY)
      env var only - these providers have no CLI-based fallback.
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


def _post_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict,
    timeout: int,
    max_retries: int,
) -> dict:
    data = json.dumps(payload).encode("utf-8")
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt >= max_retries:
                raise LLMRequestError(f"{url} error {exc.code}: {body}") from exc
            wait_seconds = _retry_wait_seconds(exc, attempt)
            attempt += 1
            time.sleep(wait_seconds)
        except urllib.error.URLError as exc:
            if attempt >= max_retries:
                raise LLMRequestError(f"Request to {url} failed: {exc}") from exc
            attempt += 1
            time.sleep(2**attempt)


def _openai_style_completion(
    endpoint: str,
    token: str,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    response_format_json: bool,
    timeout: int,
    max_retries: int,
) -> LLMResponse:
    """Shared request/response shape for GitHub Models and OpenAI (both expose
    an OpenAI-compatible chat/completions API)."""
    payload: dict = {"model": model, "messages": messages, "temperature": temperature}
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json" if "github" in endpoint else "application/json",
    }
    raw = _post_with_retry(endpoint, headers, payload, timeout, max_retries)
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMRequestError(f"Unexpected response shape from {endpoint}: {raw}") from exc
    return LLMResponse(content=content, raw=raw)


def _anthropic_completion(
    token: str,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    response_format_json: bool,
    timeout: int,
    max_retries: int,
    max_tokens: int = 4096,
) -> LLMResponse:
    """Anthropic's Messages API: system prompt is a separate top-level field,
    not a message, and there's no first-class JSON response mode - so when
    JSON is required we append an explicit instruction (Claude follows this
    reliably in practice)."""
    system = system_prompt
    if response_format_json:
        system += "\n\nRespond with ONLY a single valid JSON object. No markdown code fences, no commentary."

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    headers = {
        "x-api-key": token,
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }
    raw = _post_with_retry(ANTHROPIC_ENDPOINT, headers, payload, timeout, max_retries)
    try:
        content = raw["content"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise LLMRequestError(f"Unexpected response shape from Anthropic: {raw}") from exc
    return LLMResponse(content=content, raw=raw)


def chat_completion(
    messages: list[dict[str, str]],
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.2,
    response_format_json: bool = False,
    timeout: int = 60,
    max_retries: int = 5,
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> LLMResponse:
    """Call the configured LLM provider's chat completion endpoint.

    Raises LLMAuthError/LLMRequestError. Retries with exponential backoff on
    HTTP 429 (rate limited) and transient 5xx errors, honoring a Retry-After
    header if the API sends one - GitHub Models' free/included tier in
    particular has fairly low per-minute rate limits, so this is expected to
    trigger occasionally on batch runs (e.g. scoring 30 jobs back-to-back)
    rather than indicating anything is broken.

    For Anthropic, `messages` must contain exactly one system message
    (optional) and one user message - use chat_completion_json() instead,
    which handles this translation for you.
    """
    token = resolve_token(provider, api_key_env)

    if provider == "anthropic":
        system_prompt = ""
        user_prompt = ""
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            elif m["role"] == "user":
                user_prompt = m["content"]
        return _anthropic_completion(
            token, system_prompt, user_prompt, model, temperature,
            response_format_json, timeout, max_retries,
        )

    endpoint = GITHUB_MODELS_ENDPOINT if provider == "github_models" else OPENAI_ENDPOINT
    return _openai_style_completion(
        endpoint, token, messages, model, temperature, response_format_json, timeout, max_retries,
    )


def chat_completion_json(
    system_prompt: str,
    user_prompt: str,
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.2,
    provider: str = "github_models",
    api_key_env: str | None = None,
) -> dict:
    """Convenience helper for prompts that expect a JSON object back.

    This is the entry point matcher.py and docgen.py use - it's fully
    provider-agnostic, so switching providers/models is a config change only.
    """
    response = chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=temperature,
        response_format_json=True,
        provider=provider,
        api_key_env=api_key_env,
    )
    content = response.content.strip()
    # Anthropic (and occasionally others) may wrap JSON in a markdown code
    # fence despite instructions - strip it defensively before parsing.
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    return json.loads(content)
