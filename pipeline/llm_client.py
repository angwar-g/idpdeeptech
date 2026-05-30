"""LLM client abstraction.

Same interface for Ollama (local laptop dev) and Cloudflare Workers AI (remote).
Provider chosen via env vars so no code changes are needed when moving between
environments.

Environment variables:
    LLM_PROVIDER         "ollama" (default) or "cloudflare"
    LLM_MODEL            Override the default model for the chosen provider.
                         For ollama: anything Ollama serves (default: "mistral").
                         For cloudflare: a Workers AI model slug like
                         "@cf/meta/llama-3.1-8b-instruct" (default).
    LLM_TEMPERATURE      Float, default 0 (deterministic).

Cloudflare-only:
    CLOUDFLARE_ACCOUNT_ID   Your account ID, found in the dashboard URL.
    CLOUDFLARE_API_TOKEN    A Workers AI token (Profile > API Tokens, with
                            "Workers AI Read" permission).

Ollama-only:
    OLLAMA_BASE_URL      Defaults to http://localhost:11434.

Usage:
    from llm_client import complete
    text = await complete(prompt, max_tokens=1500)
"""

from __future__ import annotations

import os
from litellm import acompletion


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "ollama").lower().strip()


def _model_for(provider: str) -> str:
    override = os.environ.get("LLM_MODEL", "").strip()
    if override:
        return override
    if provider == "cloudflare":
        return "@cf/meta/llama-3.1-8b-instruct"
    return "mistral"


def _temperature() -> float:
    raw = os.environ.get("LLM_TEMPERATURE", "0").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


async def complete(prompt: str, max_tokens: int | None = None) -> str:
    """Send `prompt` to the configured LLM, return the response text.

    max_tokens caps the response length; pass None for the provider default.
    """
    provider = _provider()
    model_name = _model_for(provider)
    temperature = _temperature()

    kwargs: dict = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }

    if provider == "ollama":
        # litellm uses model="ollama/<name>" and api_base.
        kwargs["model"] = f"ollama/{model_name}"
        kwargs["api_base"] = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        if max_tokens is not None:
            kwargs["num_predict"] = max_tokens

    elif provider == "cloudflare":
        # litellm supports Cloudflare via model="cloudflare/<slug>" once
        # CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are set in the env.
        # litellm reads those env vars automatically; we just pass the model.
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if not account_id or not api_token:
            raise RuntimeError(
                "LLM_PROVIDER=cloudflare requires CLOUDFLARE_ACCOUNT_ID and "
                "CLOUDFLARE_API_TOKEN in the environment."
            )
        kwargs["model"] = f"cloudflare/{model_name}"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER={provider!r}. Expected 'ollama' or 'cloudflare'."
        )

    response = await acompletion(**kwargs)
    return response.choices[0].message.content  # type: ignore
