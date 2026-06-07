"""LLM client abstraction.

Same interface for Ollama (local laptop dev) and Cloudflare Workers AI (remote).
Provider chosen via env vars so no code changes are needed when moving between
environments.

Cloudflare calls go directly to the REST API instead of through litellm
because litellm's Cloudflare adapter has bugs around response-shape handling
(specifically: it crashes on tiktoken.encode(None) when the model returns
content as null, which happens with larger prompts and certain newer models).
We do still use litellm for Ollama.

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

import json
import os

# Load .env BEFORE any imports that might read env vars at import time.
# python-dotenv walks upward from the cwd to find .env, so this works
# regardless of which directory the subprocess was launched in.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import aiohttp


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


async def _complete_cloudflare(prompt: str, model: str, max_tokens: int | None) -> str:
    """Call Cloudflare Workers AI directly. Bypasses litellm.

    Cloudflare's REST shape is:
        POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/{model}
        Body: { "messages": [...], "max_tokens": N }
        Response: { "result": { "response": "..." }, "success": true, ... }
        Errors:   { "errors": [{"code": N, "message": "..."}], "success": false }
    """
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = (
        os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        or os.environ.get("CLOUDFLARE_API_KEY", "").strip()
    )
    if not account_id or not api_token:
        raise RuntimeError(
            "LLM_PROVIDER=cloudflare requires CLOUDFLARE_ACCOUNT_ID and "
            "CLOUDFLARE_API_TOKEN (or CLOUDFLARE_API_KEY) in the environment."
        )

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    )
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    body: dict = {"messages": [{"role": "user", "content": prompt}]}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            raw = await resp.text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Cloudflare returned non-JSON (HTTP {resp.status}): {raw[:300]}"
                )

            if not data.get("success", False):
                errors = data.get("errors", [])
                msg = "; ".join(
                    f"[{e.get('code', '?')}] {e.get('message', '')}" for e in errors
                ) or raw[:300]
                raise RuntimeError(f"Cloudflare API error: {msg}")

            result = data.get("result", {}) or {}
            # Workers AI text models put the response in result.response.
            # Caveat: some newer models (e.g. Llama 3.3 70B) auto-parse the
            # output when it looks like JSON and return result.response as a
            # list/dict instead of a string. Older models always return a
            # string. Downstream code expects a string, so we coerce here:
            # if Cloudflare already parsed it for us, re-serialize back to
            # JSON text so feed_pdf.py / interactions_pdf.py can json.loads()
            # it normally.
            response = result.get("response", "")
            if response is None:
                return ""
            if isinstance(response, (list, dict)):
                return json.dumps(response, ensure_ascii=False)
            return str(response)


async def _complete_ollama(prompt: str, model: str, max_tokens: int | None, temperature: float) -> str:
    """Call Ollama via litellm (which works fine for Ollama)."""
    from litellm import acompletion

    kwargs: dict = {
        "model": f"ollama/{model}",
        "messages": [{"role": "user", "content": prompt}],
        "api_base": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["num_predict"] = max_tokens

    response = await acompletion(**kwargs)
    return response.choices[0].message.content  # type: ignore


async def complete(prompt: str, max_tokens: int | None = None) -> str:
    """Send `prompt` to the configured LLM, return the response text.

    max_tokens caps the response length; pass None for the provider default.
    """
    provider = _provider()
    model_name = _model_for(provider)
    temperature = _temperature()

    if provider == "cloudflare":
        return await _complete_cloudflare(prompt, model_name, max_tokens)
    elif provider == "ollama":
        return await _complete_ollama(prompt, model_name, max_tokens, temperature)
    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER={provider!r}. Expected 'ollama' or 'cloudflare'."
        )