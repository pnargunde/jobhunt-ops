"""GitHub Models LLM client.

Auth resolution order:
1. GITHUB_MODELS_TOKEN environment variable (explicit override / non-interactive hosts)
2. `gh auth token` (uses the local GitHub CLI's already-authenticated session)

Kept as a small, dependency-light wrapper (stdlib `urllib` + `subprocess`) so
the package doesn't force an extra HTTP client dependency just for this.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"


class LLMAuthError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str
    raw: dict


def _token_from_env() -> str | None:
    return os.environ.get("GITHUB_MODELS_TOKEN")


def _token_from_gh_cli() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def resolve_token() -> str:
    token = _token_from_env()
    if token:
        return token
    token = _token_from_gh_cli()
    if token:
        return token
    raise LLMAuthError(
        "Could not resolve a GitHub Models token. Either run `gh auth login` "
        "(GitHub CLI) or set the GITHUB_MODELS_TOKEN environment variable."
    )


def chat_completion(
    messages: list[dict[str, str]],
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.2,
    response_format_json: bool = False,
    timeout: int = 60,
) -> LLMResponse:
    """Call the GitHub Models chat completions endpoint. Raises LLMAuthError/LLMRequestError."""
    token = resolve_token()
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_MODELS_ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMRequestError(f"GitHub Models API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise LLMRequestError(f"GitHub Models API request failed: {exc}") from exc

    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMRequestError(f"Unexpected GitHub Models response shape: {raw}") from exc

    return LLMResponse(content=content, raw=raw)


def chat_completion_json(
    system_prompt: str,
    user_prompt: str,
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.2,
) -> dict:
    """Convenience helper for prompts that expect a JSON object back."""
    response = chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=temperature,
        response_format_json=True,
    )
    return json.loads(response.content)
