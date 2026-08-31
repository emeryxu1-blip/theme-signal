"""Optional Anthropic-protocol adapter for explicitly configured profiles.

This file is intentionally separate from llm_client.py. The default workflow uses
the LiteLLM OpenAI-compatible request method. Select this adapter only when the
chosen env.json profile has ``provider="anthropic"``.
"""
from __future__ import annotations

import json
import re

import anthropic


class LocalClaudeClient:
    def __init__(self, cfg):
        self.cfg = cfg
        # An Anthropic profile may target a LiteLLM proxy (ANTHROPIC_BASE_URL) that
        # authenticates via `Authorization: Bearer <key>`. Passing api_key=
        # would make the SDK send an `x-api-key` header, which the proxy
        # rejects with 401 "No api key passed in"; auth_token sends Bearer.
        self.client = anthropic.Anthropic(
            auth_token=cfg.api_key,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
            max_retries=2,
        )

    def chat(self, system: str, user: str, *, max_tokens: int = 4000, **_kwargs) -> str:
        response = self.client.messages.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            thinking=self.cfg.thinking or {"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude refused the local request")
        return "".join(block.text for block in response.content if block.type == "text")

    def chat_json(self, system: str, user: str, **kwargs):
        raw = self.chat(
            system + "\nReturn only valid JSON. Do not use markdown fences.",
            user,
            **kwargs,
        ).strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(1))
