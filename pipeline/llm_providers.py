"""
pipeline/llm_providers.py
===========================
LLM provider abstraction — lets bootstrap.py call ANY LLM backend through
one interface, so people with paid API access (OpenAI, Anthropic, Google,
etc.) aren't stuck with Ollama-only / local-hardware-only generation.

Why this exists
----------------
The original bootstrap.py hardcodes Ollama's /api/generate HTTP shape
directly inside _call_ollama_raw(). That's fine if Ollama is your only
option, but it means switching to a paid API requires editing bootstrap.py's
internals. This module pulls the "call an LLM with a prompt, get text back"
operation out into a small provider interface, so bootstrap.py only ever
talks to a LLMProvider object — it doesn't know or care whether that's
Ollama running locally, OpenAI's API, Anthropic's API, or anything else.

Supported providers out of the box
------------------------------------
- "ollama"     : local, free, what the pipeline already used. No API key.
- "openai"     : OpenAI-compatible /chat/completions endpoint. Covers
                 OpenAI itself AND any OpenAI-compatible provider (Together,
                 Groq, Fireworks, OpenRouter, vLLM/local servers running an
                 OpenAI-compatible shim, etc.) by just changing base_url.
- "anthropic"  : Claude API via /v1/messages.

Why this matters for throughput
----------------------------------
The single biggest pain point observed in actual use of this pipeline was
Ollama-on-CPU speed: ~4-7 minutes per 100-item catalog batch, ~45 minutes
for a full 1,000-item catalog, because bootstrap.py makes ONE LLM call per
catalog item for HSN-code selection on top of the batch description call
(see _generate_batch_ollama in bootstrap.py). A fast paid API (e.g. a small
hosted model with high throughput, or a local GPU-accelerated Ollama setup)
can cut this from tens of minutes to well under one minute for the same
1,000-item catalog. People with paid API budgets should not have to accept
Ollama-on-CPU speeds as a ceiling.

Usage
-----
    from pipeline.llm_providers import get_provider

    provider = get_provider(
        kind="openai",                       # or "ollama", "anthropic"
        api_key="sk-...",                    # from env var or CLI arg
        model="gpt-4o-mini",
    )
    text = await provider.complete(prompt, temperature=0.0)

bootstrap.py's _call_ollama_raw() becomes a thin wrapper that just calls
provider.complete() — see the "Migration note" at the bottom of this file
for the exact one-line change needed in bootstrap.py.

Cost awareness
----------------
A full 1,000-item catalog makes roughly 1,000-1,100 LLM calls (one batch
description call per 100 items, plus one HSN-selection call per item). At
typical small-model pricing this is a low/negligible cost per industry
catalog (cents, not dollars, with most providers' cheap/mini-tier models),
but it is NOT zero like Ollama. Anyone choosing a paid provider should be
aware the cost is per INDUSTRY CATALOG, not per document — the catalog is
reused across all 20,000 documents in a run, so cost doesn't scale with
dataset size, only with how many distinct industries you bootstrap.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger("llm_providers")


class LLMProvider(abc.ABC):
    """Common interface every provider implements."""

    name: str = "base"

    @abc.abstractmethod
    async def complete(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> str:
        """Return the raw text completion for `prompt`. Raises on failure
        after internal retries are exhausted (caller decides fallback)."""
        ...

    @abc.abstractmethod
    async def healthcheck(self, session: Optional[aiohttp.ClientSession] = None) -> bool:
        """Return True if the provider is reachable/usable right now."""
        ...


class OllamaProvider(LLMProvider):
    """
    Local Ollama instance. Free, no API key, but throughput is limited by
    local hardware (CPU or local GPU). This is the pipeline's original
    behaviour, now expressed through the common interface.
    """

    name = "ollama"

    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434",
                 num_ctx: int = 3072):
        """
        num_ctx : context window requested from llama.cpp for this run.
            Ollama's default n_ctx (4096 here) can over-allocate the KV
            cache buffer on tight-VRAM GPUs (e.g. 6GB laptop cards),
            causing `cudaMalloc failed: out of memory` — especially right
            after a prior crashed/segfaulted llama-server left VRAM
            fragmented, even when nvidia-smi reports several GB "free".

            num_ctx must cover BOTH the prompt AND the generated output —
            bootstrap.py's description-generation prompt asks for up to
            100 items in one JSON array, which needs ~1,750+ tokens of
            output alone, so going too low (e.g. 512) causes the response
            to get cut off mid-JSON ("Unterminated string") well before
            any OOM risk even comes into play.

            3072 was empirically confirmed (on a 6GB RTX 4050 laptop GPU,
            after a clean Ollama/WSL restart) to produce complete, valid
            100-item JSON batches with no truncation and no OOM — same
            result as 4096, but with deliberate headroom below the value
            that OOM'd while VRAM was still fragmented from an earlier
            crash. Adjust up if you have more VRAM to spare, or down if
            you hit OOM again on a tighter card.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx

    async def complete(self, prompt, temperature=0.0, max_tokens=4096, session=None, retries=3) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {
                "temperature": temperature, "top_p": 1.0, "num_predict": max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            for attempt in range(retries):
                try:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            raise RuntimeError(f"Ollama HTTP {resp.status}: {body[:300]}")
                        data = await resp.json()
                        raw = data.get("response", "").strip()
                        return _strip_markdown_fence(raw)
                except Exception as e:
                    log.warning(f"[ollama] attempt {attempt + 1}/{retries}: {e}")
                    await asyncio.sleep(2 ** attempt)
            raise RuntimeError(f"Ollama: all {retries} retries exhausted")
        finally:
            if own_session:
                await session.close()

    async def healthcheck(self, session=None) -> bool:
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            async with session.get(f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200
        except Exception:
            return False
        finally:
            if own_session:
                await session.close()


class OpenAICompatibleProvider(LLMProvider):
    """
    Any OpenAI-compatible /chat/completions endpoint. Covers OpenAI itself,
    and — by changing base_url — Together.ai, Groq, Fireworks, OpenRouter,
    or a self-hosted vLLM/text-generation-inference server exposing the
    same API shape. This is deliberately generic rather than OpenAI-specific.
    """

    name = "openai"

    def __init__(self, model: str, api_key: Optional[str] = None,
                 base_url: str = "https://api.openai.com/v1"):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No API key provided. Pass api_key= explicitly or set the "
                "OPENAI_API_KEY environment variable."
            )
        self.base_url = base_url.rstrip("/")

    async def complete(self, prompt, temperature=0.0, max_tokens=4096, session=None, retries=3) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            for attempt in range(retries):
                try:
                    async with session.post(url, json=payload, headers=headers,
                                             timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
                        data = await resp.json()
                        raw = data["choices"][0]["message"]["content"].strip()
                        return _strip_markdown_fence(raw)
                except Exception as e:
                    log.warning(f"[{self.name}] attempt {attempt + 1}/{retries}: {e}")
                    await asyncio.sleep(2 ** attempt)
            raise RuntimeError(f"{self.name}: all {retries} retries exhausted")
        finally:
            if own_session:
                await session.close()

    async def healthcheck(self, session=None) -> bool:
        # Cheapest possible check: a 1-token completion. Avoids needing a
        # separate "list models" endpoint, which not all OpenAI-compatible
        # providers implement identically.
        try:
            result = await self.complete("ping", max_tokens=1, retries=1, session=session)
            return True
        except Exception:
            return False


class AnthropicProvider(LLMProvider):
    """Claude API via /v1/messages."""

    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: Optional[str] = None,
                 base_url: str = "https://api.anthropic.com"):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No API key provided. Pass api_key= explicitly or set the "
                "ANTHROPIC_API_KEY environment variable."
            )
        self.base_url = base_url.rstrip("/")

    async def complete(self, prompt, temperature=0.0, max_tokens=4096, session=None, retries=3) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            for attempt in range(retries):
                try:
                    async with session.post(url, json=payload, headers=headers,
                                             timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
                        data = await resp.json()
                        raw = "".join(
                            block.get("text", "") for block in data.get("content", [])
                            if block.get("type") == "text"
                        ).strip()
                        return _strip_markdown_fence(raw)
                except Exception as e:
                    log.warning(f"[{self.name}] attempt {attempt + 1}/{retries}: {e}")
                    await asyncio.sleep(2 ** attempt)
            raise RuntimeError(f"{self.name}: all {retries} retries exhausted")
        finally:
            if own_session:
                await session.close()

    async def healthcheck(self, session=None) -> bool:
        try:
            await self.complete("ping", max_tokens=1, retries=1, session=session)
            return True
        except Exception:
            return False


def _strip_markdown_fence(raw: str) -> str:
    """Shared cleanup: strip ```json / ``` fences some models wrap output in."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


PROVIDER_CLASSES = {
    "ollama": OllamaProvider,
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}


def get_provider(kind: str, **kwargs) -> LLMProvider:
    """
    Factory: get_provider("ollama", model="qwen2.5:7b")
              get_provider("openai", model="gpt-4o-mini", api_key="sk-...")
              get_provider("openai", model="llama-3.1-70b", base_url="https://api.together.xyz/v1", api_key="...")
              get_provider("anthropic", model="claude-sonnet-4-6", api_key="sk-ant-...")
    """
    if kind not in PROVIDER_CLASSES:
        raise ValueError(f"Unknown provider '{kind}'. Available: {list(PROVIDER_CLASSES.keys())}")
    return PROVIDER_CLASSES[kind](**kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# MIGRATION NOTE for bootstrap.py
# ──────────────────────────────────────────────────────────────────────────────
# bootstrap.py's _call_ollama_raw(session, ollama_url, model, prompt, retries)
# and the ollama_available healthcheck in run_bootstrap() can be replaced with:
#
#     from pipeline.llm_providers import get_provider
#
#     provider = get_provider(
#         kind=args.llm_provider,             # new CLI arg, default "ollama"
#         model=args.llm_model,
#         api_key=args.llm_api_key,           # None for ollama, required for paid
#         base_url=args.llm_base_url,         # optional override
#     )
#     ...
#     raw = await provider.complete(prompt, temperature=0.0, session=session)
#     ...
#     provider_available = await provider.healthcheck(session=session)
#
# This is a small, mechanical change — every call site that currently calls
# _call_ollama_raw(...) becomes provider.complete(...), and the only new
# CLI surface needed in main.py is:
#
#     --llm-provider {ollama,openai,anthropic}   (default: ollama)
#     --llm-model <model name>
#     --llm-api-key <key>                         (or read from env var)
#     --llm-base-url <url>                        (optional, for custom
#                                                    OpenAI-compatible endpoints)