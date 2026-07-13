"""LLM backend abstraction for the summarization phase.

Provides:
  - build_summary_settings(): assemble the SUMMARY_SETTINGS dict that
    4.01summary.py consumes, from the flat constants in config/summaryconfig.py
    plus environment overrides set by 4.00summary_wrapper_hf.py
    (CHUNK_SUMMARY_MODEL, HF_ENDPOINT_URL, HF_TOKEN).
  - run_summary_backend(backend, text, params): send one summarization request
    with retry/backoff and return the summary text.

Supported backends:
  - "huggingface" / "tgi": a Hugging Face Inference Endpoint (TGI). Requires
    HF_ENDPOINT_URL (and usually HF_TOKEN) in the environment. Uses
    huggingface_hub.InferenceClient.
  - "openai": the OpenAI API (or any OpenAI-compatible server via the
    OPENAI_BASE_URL environment variable). Requires OPENAI_API_KEY.
  - "ollama": a local Ollama server (OLLAMA_URL, default http://localhost:11434).
    Uses urllib only, no extra dependency.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Dict, Tuple

DEFAULT_SYSTEM_PROMPT = "You are a concise technical summarizer."
RETRY_DELAYS = (2, 4, 8)  # seconds between attempts (len == max retries)


def _split_backend(model_str: str) -> Tuple[str, str]:
    """Resolve (backend, model) from a model string.

    "huggingface:tgi" -> ("huggingface", "tgi"); a bare model name uses the
    HF endpoint when HF_ENDPOINT_URL is set, otherwise the OpenAI API.
    """
    model_str = (model_str or "").strip()
    if ":" in model_str:
        backend, model = model_str.split(":", 1)
        return backend.strip().lower(), model.strip()
    if os.getenv("HF_ENDPOINT_URL"):
        return "huggingface", model_str
    return "openai", model_str


def build_summary_settings() -> Dict[str, dict]:
    """Build the nested settings dict from flat summaryconfig constants."""
    from config import summaryconfig as cfg

    chunk_model_str = os.getenv("CHUNK_SUMMARY_MODEL", cfg.CHUNK_SUMMARY_MODEL)
    chunk_backend, chunk_model = _split_backend(chunk_model_str)

    file_model_str = os.getenv("FILE_SUMMARY_MODEL", cfg.FILE_SUMMARY_MODEL)
    file_backend, file_model = _split_backend(file_model_str)

    return {
        "chunk": {
            "backend": chunk_backend,
            "model": chunk_model,
            "system_prompt_template": cfg.CHUNK_SUMMARY_PROMPT_TEMPLATE,
            "size": cfg.CHUNK_SUMMARY_SIZE,
            "temperature": cfg.CHUNK_SUMMARY_TEMPERATURE,
        },
        "file": {
            "backend": file_backend,
            "model": file_model,
            "system_prompt": cfg.FILE_SUMMARY_PROMPT,
            "size": cfg.FILE_SUMMARY_SIZE,
            "temperature": cfg.FILE_SUMMARY_TEMPERATURE,
        },
    }


def _chat_huggingface(text: str, params: dict, system_prompt: str) -> str:
    try:
        from huggingface_hub import InferenceClient
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "huggingface_hub is required for the 'huggingface' backend. "
            "Install it with: pip install huggingface-hub"
        ) from exc

    endpoint_url = os.getenv("HF_ENDPOINT_URL")
    if not endpoint_url:
        raise RuntimeError(
            "HF_ENDPOINT_URL is not set. Run summaries through "
            "4.00summary_wrapper_hf.py, or export HF_ENDPOINT_URL manually."
        )

    client = InferenceClient(model=endpoint_url, token=os.getenv("HF_TOKEN"))
    response = client.chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        max_tokens=int(params.get("size") or 256),
        temperature=float(params.get("temperature") or 0.7),
    )
    return (response.choices[0].message.content or "").strip()


def _chat_openai(text: str, params: dict, system_prompt: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "The 'openai' package is required for the 'openai' backend. "
            "Install it with: pip install openai"
        ) from exc

    client = OpenAI()  # honors OPENAI_API_KEY / OPENAI_BASE_URL env vars
    response = client.chat.completions.create(
        model=params.get("model") or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        max_tokens=int(params.get("size") or 256),
        temperature=float(params.get("temperature") or 0.7),
    )
    return (response.choices[0].message.content or "").strip()


def _chat_ollama(text: str, params: dict, system_prompt: str) -> str:
    base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    payload = {
        "model": params.get("model") or "llama3",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "options": {
            "temperature": float(params.get("temperature") or 0.7),
            "num_predict": int(params.get("size") or 256),
        },
    }
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return (body.get("message", {}).get("content") or "").strip()


_BACKENDS = {
    "huggingface": _chat_huggingface,
    "tgi": _chat_huggingface,
    "hf": _chat_huggingface,
    "openai": _chat_openai,
    "ollama": _chat_ollama,
}


def run_summary_backend(backend: str, text: str, params: dict) -> str:
    """Run one summarization request with retry/backoff.

    Args:
        backend: one of "huggingface"/"tgi"/"hf", "openai", "ollama".
        text: the content to summarize (user message).
        params: dict with model, size, temperature, and either system_prompt
            or system_prompt_template (unformatted templates fall back to the
            generic prompt).
    """
    handler = _BACKENDS.get((backend or "").strip().lower())
    if handler is None:
        raise ValueError(
            f"Unknown summary backend '{backend}'. "
            f"Valid backends: {sorted(set(_BACKENDS))}"
        )

    system_prompt = params.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    last_error = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return handler(text, params, system_prompt)
        except Exception as exc:  # network / rate-limit / server errors
            last_error = exc
    raise RuntimeError(
        f"Summary backend '{backend}' failed after {1 + len(RETRY_DELAYS)} attempts: {last_error}"
    ) from last_error
