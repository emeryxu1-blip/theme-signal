"""ChatGPT gateway client (OpenAI-compatible LiteLLM proxy)."""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request

from config import LLMConfig

# Production-only client: this module intentionally remains OpenAI-compatible.
# Local Claude testing uses anthropic_local_client.LocalClaudeClient instead.

_SSL_CTX = ssl._create_unverified_context()
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMClient:
    def __init__(self, cfg: LLMConfig, *, retries: int = 3):
        self.cfg = cfg
        self.retries = retries

    def chat(self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 4000) -> str:
        body = {
            "model": self.cfg.model,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.api_key}"}
        last_err = None
        for attempt in range(self.retries):
            req = urllib.request.Request(self.cfg.base_url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout, context=_SSL_CTX) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    return payload["choices"][0]["message"]["content"]
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError) as exc:
                last_err = exc
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"LLM request failed after {self.retries} tries: {last_err}")

    def chat_json(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 4000):
        """Return parsed JSON. Retries once with a stricter instruction on parse failure."""
        sys_json = system + "\nRespond with ONLY valid JSON. No prose, no markdown fences."
        for attempt in range(2):
            raw = self.chat(sys_json, user, temperature=temperature, max_tokens=max_tokens)
            cleaned = _FENCE.sub("", raw).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                # salvage the outermost JSON object/array
                m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
                user = user + "\n\nYour previous reply was not valid JSON. Return valid JSON only."
        raise ValueError("LLM did not return valid JSON")
