"""Discover and probe the workflow model once at the start of each run."""

from __future__ import annotations

import json
import re
import secrets
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import replace

from config import LLMConfig
from llm_client import LLMClient

# DeepSeek takes precedence; this list refines preference within each group.
DEFAULT_MODEL_PRIORITY = (
    "deepseek-flash",
    "deepseek-v4-flash",
    "deepseek-v4-flash-dy",
    "gpt-6-astra",
    "claude-opus-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "gpt-5.6-sol",
    "claude-sonnet-5",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-sonnet-4-6",
    "gpt-5.5",
    "glm-5.3-flash",
    "gpt-5.4-mini",
    "claude-haiku-4-5-20251001",
)
_PREFIXES = ("bm-", "private-", "data-", "zy-", "mirror-")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
_NON_TEXT = re.compile(
    r"(?:^|[-_./:])(?:image|audio|embedding|embeddings|embed|whisper|tts|stt|"
    r"dall-e|dalle|realtime|rerank|reranker|moderation|sora|video|imagen|flux|"
    r"stable-diffusion)(?:$|[-_./:])",
    re.IGNORECASE,
)


def _normalized_model(model: str) -> str:
    normalized = model.lower()
    while True:
        prefix = next((p for p in _PREFIXES if normalized.startswith(p)), None)
        if prefix is None:
            break
        normalized = normalized[len(prefix):]
    return normalized.removesuffix("-capacity-parked")


def ordered_models(models: list[str], priority: tuple[str, ...] = ()) -> list[str]:
    """Probe all DeepSeek routes first, then other text models by preference."""
    preferred = tuple(dict.fromkeys((*priority, *DEFAULT_MODEL_PRIORITY)))
    normalized_priority = tuple(dict.fromkeys(_normalized_model(m) for m in preferred))
    ranks = {model: index for index, model in enumerate(normalized_priority)}
    exact_priority = {model.lower(): index for index, model in enumerate(priority)}

    def sort_key(model: str):
        normalized = _normalized_model(model)
        family = normalized.rsplit("/", 1)[-1]
        is_deepseek = family == "deepseek" or family.startswith("deepseek-")
        # A configured exact route takes precedence over its sibling aliases.
        route_order = exact_priority.get(model.lower(), len(exact_priority))
        return (
            not is_deepseek,
            normalized not in ranks,
            "parked" in model.lower().split("-"),
            ranks.get(normalized, len(ranks)),
            route_order,
            model.lower() != normalized,
            model.lower(),
            model,
        )

    candidates = {
        model for model in models
        if isinstance(model, str) and _MODEL_ID.fullmatch(model) and not _NON_TEXT.search(model)
    }
    return sorted(candidates, key=sort_key)


def _safe_failure(exc: Exception) -> str:
    """Never include provider response bodies, credentials, or exception messages."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
    if isinstance(status, int):
        return f"HTTP {status}"
    return type(exc).__name__


def _discover_models(cfg: LLMConfig, client: LLMClient) -> list[str]:
    if not cfg.models_url:
        raise RuntimeError("LLM model discovery requires a configured models_url")
    try:
        request = urllib.request.Request(
            cfg.models_url, headers=client._headers(), method="GET",
        )
        with urllib.request.urlopen(
            request, timeout=cfg.probe_timeout_seconds, context=client._ssl_context,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("invalid model catalog")
        models = [
            row["id"] for row in payload["data"]
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        raise RuntimeError(f"LLM model discovery failed ({_safe_failure(exc)})") from None
    return models


def select_workflow_model(cfg: LLMConfig) -> LLMConfig:
    """Fetch a fresh catalog, then retain the first model that echoes valid JSON."""
    probe_cfg = replace(
        cfg, auto_select_model=False, workflow_retries=None,
        timeout=cfg.probe_timeout_seconds,
    )
    try:
        catalog_client = LLMClient(probe_cfg, retries=1)
    except Exception as exc:
        raise RuntimeError(f"LLM model discovery setup failed ({_safe_failure(exc)})") from None
    print("[theme-workflow] fetching current LLM model catalog", file=sys.stderr)
    candidates = ordered_models(_discover_models(cfg, catalog_client), cfg.model_priority)
    if not candidates:
        raise RuntimeError("LLM model discovery returned no usable text models")

    failures: Counter[str] = Counter()
    for index, candidate in enumerate(candidates, 1):
        safe_model = candidate.replace(cfg.api_key, "[REDACTED]") if cfg.api_key else candidate
        print(
            f"[theme-workflow] model probe {index}/{len(candidates)} model={safe_model}",
            file=sys.stderr,
        )
        nonce = secrets.token_hex(16)
        schema = {
            "type": "object",
            "properties": {"nonce": {"type": "string", "const": nonce}},
            "required": ["nonce"],
            "additionalProperties": False,
        }
        try:
            client = LLMClient(replace(probe_cfg, model=candidate), retries=1)
            answer = client.chat_json(
                "Reply only with the requested JSON object, copying the nonce exactly.",
                json.dumps({"nonce": nonce}),
                response_schema=schema,
                schema_name="workflow_model_probe",
                max_tokens=cfg.max_completion_tokens,
            )
            if not isinstance(answer, dict) or answer != {"nonce": nonce}:
                raise ValueError("model returned an invalid nonce response")
        except Exception as exc:
            failure = _safe_failure(exc)
            failures[failure] += 1
            print(
                f"[theme-workflow] model probe rejected model={safe_model} reason={failure}",
                file=sys.stderr,
            )
            continue
        return replace(cfg, model=candidate)
    summary = ", ".join(f"{reason}: {count}" for reason, count in sorted(failures.items()))
    raise RuntimeError(
        f"No usable workflow model passed the JSON probe ({len(candidates)} candidates; {summary})"
    )
