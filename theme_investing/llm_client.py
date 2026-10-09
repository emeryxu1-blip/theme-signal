"""DeepSeek and OpenAI-compatible gateway client for the agent workflow."""

from __future__ import annotations

import http.client
import json
import random
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from config import LLMConfig
from llm_errors import LLMWorkflowAbort

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMCompletionLimitError(RuntimeError):
    """Incomplete model output that requires a smaller workflow request.

    Transport retries cannot recover the same oversized completion, and partial
    output must never be accepted as evidence.
    """


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


class _RunState:
    """Shared cancellation for all model calls in one workflow run."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.failure: str | None = None

    def check(self):
        if self.cancelled.is_set():
            raise LLMWorkflowAbort(self.failure or "Model workflow was cancelled") from None

    def fail(self, message: str):
        with self.lock:
            if self.failure is None:
                self.failure = message
            self.cancelled.set()
        self.check()


class LLMClient:
    def __init__(self, cfg: LLMConfig, *, retries: int = 3):
        self.cfg = cfg
        self.retries = max(1, retries)
        self._run_state = _RunState()
        workflow_retries = cfg.workflow_retries
        if workflow_retries is not None and (
            isinstance(workflow_retries, bool)
            or not isinstance(workflow_retries, int)
            or workflow_retries < 0
        ):
            raise ValueError("workflow_retries must be a nonnegative integer")
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

    def begin_workflow(self) -> None:
        """Probe current availability anew before each workflow invocation."""
        self._run_state = _RunState()
        if not self.cfg.auto_select_model:
            return
        from model_selection import select_workflow_model
        try:
            self.cfg = select_workflow_model(self.cfg)
        except Exception as exc:
            self._run_state.fail(
                "No working office model could be selected at startup: "
                + self._failure_detail(exc)
            )
        print(
            f"[theme-workflow] selected model={self.cfg.model} "
            f"workflow_retries={self.cfg.workflow_retries}",
            file=sys.stderr, flush=True,
        )

    def _failure_detail(self, exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            return f"HTTP {exc.code}"
        if isinstance(exc, LLMRequestError) and exc.status_code is not None:
            return f"HTTP {exc.status_code}"
        # Exceptions may include private input, invalid model output, or keys.
        # Error types are enough to diagnose retries without logging that data.
        return type(exc).__name__

    @staticmethod
    def _wait_for_retry(state: _RunState, delay: float) -> None:
        state.cancelled.wait(delay)
        state.check()

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
        base = min(8.0, float(2 ** min(attempt, 3)))
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
            raise LLMCompletionLimitError(
                "LLM response reached the completion-token limit"
            )
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

    def _request_body(
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
    ) -> dict:
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
        return body

    def _send_once(self, data: bytes) -> str:
        req = urllib.request.Request(
            self.cfg.base_url, data=data, headers=self._headers(), method="POST",
        )
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

    def _execute_workflow(self, data: bytes, *, parse_json: bool):
        state = self._run_state
        attempts = self.cfg.workflow_retries + 1
        for attempt in range(attempts):
            state.check()
            try:
                raw = self._send_once(data)
                state.check()
                result = _json_from_text(raw) if parse_json else raw
                if parse_json and not isinstance(result, dict):
                    raise ValueError("LLM JSON response must be an object")
                state.check()
                return result
            except Exception as exc:
                detail = self._failure_detail(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                if attempt + 1 == attempts:
                    state.fail(
                        f"Model {self.cfg.model} failed after {attempts} attempts "
                        f"({self.cfg.workflow_retries} retries); stopping the entire "
                        f"workflow. Last failure: {detail}"
                    )
                print(
                    f"[theme-workflow] model={self.cfg.model} failed ({detail}); "
                    f"retry {attempt + 1}/{self.cfg.workflow_retries}",
                    file=sys.stderr, flush=True,
                )
                self._wait_for_retry(state, self._retry_delay(exc, attempt))

    def _execute(self, body: dict, *, parse_json: bool = False):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if self.cfg.workflow_retries is not None:
            return self._execute_workflow(data, parse_json=parse_json)
        return self._execute_legacy(data)

    def _execute_legacy(self, data: bytes) -> str:
        last_err = None
        for attempt in range(self.retries):
            try:
                return self._send_once(data)
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

    def chat(
        self, system: str, user: str, *, temperature: float = 0.3,
        max_tokens: int | None = None, reasoning_effort: str | None = None,
        json_mode: bool = False, response_schema: dict | None = None,
        schema_name: str = "workflow_response",
    ) -> str:
        body = self._request_body(
            system, user, temperature=temperature, max_tokens=max_tokens,
            reasoning_effort=reasoning_effort, json_mode=json_mode,
            response_schema=response_schema, schema_name=schema_name,
        )
        return self._execute(body)

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
        """Parse JSON within the workflow budget, or legacy one-repair policy."""
        sys_json = system
        if "json" not in f"{system}\n{user}".lower():
            sys_json += "\nReturn only valid JSON, with no Markdown fences."
        if self.cfg.workflow_retries is not None:
            body = self._request_body(
                sys_json, user, temperature=temperature, max_tokens=max_tokens,
                reasoning_effort=reasoning_effort, json_mode=True,
                response_schema=response_schema, schema_name=schema_name,
            )
            return self._execute(body, parse_json=True)
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
