"""DeepSeek and OpenAI-compatible gateway client for the agent workflow."""

from __future__ import annotations

import http.client
import json
import random
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid

from config import LLMConfig

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMRequestError(RuntimeError):
    """Request failure annotated for bounded workflow-level recovery."""

    def __init__(
        self, message: str, *, retryable: bool, status_code: int | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def _json_from_text(raw: str):
    """Parse a complete JSON value, allowing only an enclosing Markdown fence."""
    cleaned = _FENCE.sub("", raw).strip()
    return json.loads(cleaned)


class LLMClient:
    def __init__(self, cfg: LLMConfig, *, retries: int = 3):
        self.cfg = cfg
        self.retries = max(1, retries)
        if cfg.verify_tls:
            self._ssl_context = ssl.create_default_context(cafile=cfg.ca_file or None)
            if not cfg.ca_file:
                # Python.org macOS builds can be installed without their
                # OpenSSL certificate symlink. Supplement the system store with
                # certifi when available while keeping verification enabled.
                try:
                    import certifi
                except ImportError:
                    pass
                else:
                    self._ssl_context.load_verify_locations(cafile=certifi.where())
        else:
            self._ssl_context = ssl._create_unverified_context()

    def _headers(self) -> dict[str, str]:
        if not self.cfg.api_key:
            raise ValueError("LLM API key is missing")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            self.cfg.auth_header: f"{self.cfg.auth_prefix}{self.cfg.api_key}",
        }
        if self.cfg.trace_header:
            headers[self.cfg.trace_header] = str(uuid.uuid4())
        return headers

    def _error_detail(self, exc: urllib.error.HTTPError) -> str:
        # Gateway error bodies can echo request data. Keep exceptions useful
        # without copying potentially sensitive prompts or credentials to logs.
        reason = str(exc.reason or "request rejected")
        if self.cfg.api_key:
            reason = reason.replace(self.cfg.api_key, "[REDACTED]")
        return reason[:200]

    @staticmethod
    def _retry_delay(exc: Exception, attempt: int) -> float:
        if isinstance(exc, urllib.error.HTTPError) and exc.headers:
            retry_after = exc.headers.get("Retry-After")
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except (TypeError, ValueError):
                pass
        base = min(8.0, float(2 ** attempt))
        return base + random.uniform(0.0, base * 0.25)

    @staticmethod
    def _content(payload: dict) -> str:
        if not isinstance(payload, dict):
            raise ValueError("response payload is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("response has no completion choice")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("LLM response reached the completion-token limit")
        if choice.get("finish_reason") == "content_filter":
            raise RuntimeError("LLM response was blocked by a content filter")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("response choice has no message")
        if message.get("refusal"):
            raise RuntimeError("LLM refused the request")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                part.get("text", "") for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            joined = "".join(parts)
            if joined.strip():
                return joined
        raise ValueError("response message has no text content")

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        json_mode: bool = False,
        response_schema: dict | None = None,
        schema_name: str = "workflow_response",
    ) -> str:
        completion_limit = (
            self.cfg.max_completion_tokens if max_tokens is None else max_tokens
        )
        if completion_limit <= 0:
            raise ValueError("max completion tokens must be greater than zero")
        effort = (
            self.cfg.reasoning_effort
            if reasoning_effort is None else reasoning_effort
        )
        is_deepseek = self.cfg.provider == "deepseek"
        body = {
            "model": self.cfg.model,
            "stream": False,
            "max_tokens" if is_deepseek else "max_completion_tokens": completion_limit,
            "messages": [
                {"role": "system" if is_deepseek else "developer", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if is_deepseek:
            thinking = self.cfg.thinking or {"type": "enabled"}
            body["thinking"] = thinking
            if thinking.get("type") == "disabled" or effort == "none":
                body["thinking"] = {"type": "disabled"}
                body["temperature"] = temperature
            elif effort:
                body["reasoning_effort"] = effort
        elif effort:
            body["reasoning_effort"] = effort
        else:
            # Current reasoning models do not consistently support sampling
            # controls. Preserve temperature only for profiles that do not set
            # an explicit reasoning effort.
            body["temperature"] = temperature
        if response_schema is not None:
            if not isinstance(response_schema, dict):
                raise ValueError("response schema must be a JSON object")
            if is_deepseek:
                # DeepSeek supports JSON object output, not OpenAI's strict
                # JSON schema response format. Keep the desired shape in the
                # instruction; workflow validators still check each result.
                body["response_format"] = {"type": "json_object"}
                body["messages"][0]["content"] += (
                    "\nReturn only a JSON object conforming to this JSON schema:\n"
                    + json.dumps(response_schema, ensure_ascii=False)
                )
            else:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": response_schema,
                    },
                }
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            req = urllib.request.Request(
                self.cfg.base_url,
                data=data,
                headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    req, timeout=self.cfg.timeout, context=self._ssl_context,
                ) as resp:
                    try:
                        payload = json.loads(resp.read().decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise RuntimeError("LLM gateway returned invalid JSON") from exc
                    try:
                        return self._content(payload)
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError("LLM gateway returned a malformed completion") from exc
            except urllib.error.HTTPError as exc:
                detail = self._error_detail(exc)
                if exc.code not in _TRANSIENT_HTTP_STATUSES:
                    raise LLMRequestError(
                        f"LLM request rejected with HTTP {exc.code}: {detail}",
                        retryable=False,
                        status_code=exc.code,
                    ) from exc
                last_err = exc
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.RemoteDisconnected,
            ) as exc:
                last_err = exc
            if attempt + 1 < self.retries:
                time.sleep(self._retry_delay(last_err, attempt))
        raise LLMRequestError(
            f"LLM request failed after {self.retries} attempts: {last_err}",
            retryable=True,
            status_code=(
                last_err.code
                if isinstance(last_err, urllib.error.HTTPError)
                else None
            ),
        ) from last_err

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        response_schema: dict | None = None,
        schema_name: str = "workflow_response",
    ):
        """Return parsed JSON. Retries once with a stricter instruction on parse failure."""
        sys_json = system
        if "json" not in f"{system}\n{user}".lower():
            sys_json += "\nReturn only valid JSON, with no Markdown fences."
        last_error: Exception | None = None
        strict_schema = response_schema is not None and self.cfg.provider != "deepseek"
        parse_attempts = 1 if strict_schema else 2
        for attempt in range(parse_attempts):
            raw = self.chat(
                sys_json,
                user,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                json_mode=True,
                response_schema=response_schema,
                schema_name=schema_name,
            )
            try:
                parsed = _json_from_text(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("LLM JSON response must be an object")
                return parsed
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < parse_attempts:
                    user += (
                        "\n\nThe previous reply was invalid. Return one valid JSON "
                        "object only, with no prose or Markdown."
                    )
        raise ValueError("LLM did not return a valid JSON object") from last_error
