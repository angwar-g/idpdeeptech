"""Quick smoke test for the LLM provider configured in .env.

Usage:
    python3 test_llm.py
    python3 test_llm.py "your custom prompt"

Reads .env via llm_client (which calls load_dotenv on import), prints the
resolved provider/model, fires one short call, prints the response and the
elapsed time. Exit code 0 on success, 1 on failure.
"""
import asyncio
import logging
import sys
import time

# Silence the noisy "Fatal error on SSL transport" / "Event loop is closed"
# tracebacks that aiohttp emits on Linux during shutdown. The actual HTTP call
# succeeded; this is just the TLS close-notify failing because the socket has
# already been torn down by the event loop. Cosmetic only.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from llm_client import _provider, _model_for, complete


async def main() -> int:
    provider = _provider()
    model = _model_for(provider)
    prompt = sys.argv[1] if len(sys.argv) > 1 else (
        "Reply with exactly: hello from cloudflare. No other words."
    )

    print(f"Provider: {provider}")
    print(f"Model:    {model}")
    print(f"Prompt:   {prompt!r}")
    print("Calling...")

    t0 = time.monotonic()
    try:
        text = await complete(prompt, max_tokens=64)
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"FAILED after {elapsed:.2f}s: {type(e).__name__}: {e}")
        return 1

    elapsed = time.monotonic() - t0
    print(f"Got response in {elapsed:.2f}s:")
    print("---")
    print(text)
    print("---")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))