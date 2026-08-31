"""Configuration loading for switchable local/production profiles."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from pathlib import Path

from ainvest_auth import c_side_ca_file, c_side_cookie_values, c_side_verify_tls

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / "Skills" / "env.json"


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float
    provider: str = "litellm_openai_compatible"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    trace_header: str | None = None
    thinking: dict | None = None
    messages_url: str | None = None
    models_url: str | None = None
    reasoning_effort: str | None = None
    max_completion_tokens: int = 8000
    verify_tls: bool = True
    ca_file: str | None = None


@dataclass
class QuoteConfig:
    scene: str
    snapshot_url: str
    multi_kline_url: str
    headers: dict
    series_url: str | None = None
    relation_list_url: str | None = None
    single_tick_url: str | None = None
    profile: str = "local"
    endpoint_family_credentials: dict | None = None
    ca_file: str | None = None
    verify_tls: bool = True

    def ssl_context(self) -> ssl.SSLContext:
        """Build the configured quote TLS context."""
        if not self.verify_tls:
            return ssl._create_unverified_context()
        return ssl.create_default_context(cafile=self.ca_file or None)


def _load_env(env_path: Path | None = None) -> dict:
    return json.loads((env_path or ENV_PATH).read_text(encoding="utf-8"))


def load_env(env_path: Path | None = None) -> dict:
    """Public alias for reading the env.json config into a dict."""
    return _load_env(env_path)


def active_profile_name(env: dict, kind: str) -> str:
    selected = env.get("active_profiles", {})
    if isinstance(selected, dict) and selected.get(kind):
        return str(selected[kind])
    return "production" if kind == "llm" else (env.get("active_scene") or "c")


def selected_llm_profile(env: dict) -> tuple[str, dict]:
    name = active_profile_name(env, "llm")
    profiles = env.get("llm_profiles", {})
    if isinstance(profiles, dict) and profiles:
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            raise ValueError(f"LLM profile {name!r} is not configured")
        return name, profile
    legacy = env.get("chatgpt_api")
    if isinstance(legacy, dict) and legacy:
        return "production", legacy
    raise ValueError("no LLM profile is configured")


def selected_quote_profile(env: dict) -> tuple[str, dict]:
    name = active_profile_name(env, "quote")
    profiles = env.get("quote_profiles", {})
    if isinstance(profiles, dict) and isinstance(profiles.get(name), dict):
        return name, profiles[name]
    return name, {}


def load_llm_config(env: dict | None = None) -> LLMConfig:
    env = _load_env() if env is None else env
    _, profile = selected_llm_profile(env)
    provider = profile.get("provider", "litellm_openai_compatible")
    if provider == "anthropic":
        base = profile.get("base_url", "https://api.anthropic.com").rstrip("/")
        messages_path = profile.get("messages_path", "/v1/messages")
        models_path = profile.get("models_path", "/v1/models")
        return LLMConfig(
            base_url=base,
            api_key=profile.get("api_key", ""),
            model=profile.get("model", "claude-opus-4-8"),
            timeout=float(profile.get("timeout_seconds", 600)),
            provider=provider,
            auth_header=profile.get("request", {}).get("auth_header", "x-api-key"),
            auth_prefix="",
            thinking=profile.get("request", {}).get("thinking"),
            messages_url=f"{base}{messages_path}",
            models_url=f"{base}{models_path}",
            max_completion_tokens=int(profile.get("max_completion_tokens", 8000)),
            verify_tls=bool(profile.get("verify_tls", True)),
            ca_file=profile.get("ca_file"),
        )

    environments = profile.get("environments")
    selected_environment: dict = {}
    if isinstance(environments, dict) and environments:
        active = profile.get("active_environment", "internal_equ")
        selected_environment = environments.get(active)
        if not isinstance(selected_environment, dict):
            raise ValueError(
                f"LLM environment {active!r} is not configured for the selected profile"
            )
        if not selected_environment.get("base_url"):
            raise ValueError(f"LLM environment {active!r} has no base_url")
        base = str(selected_environment["base_url"]).rstrip("/")
    else:
        # A direct base URL keeps a single-environment profile (for example the
        # office-Wi-Fi gateway) compact while preserving multi-environment
        # production profiles.
        if not profile.get("base_url"):
            raise ValueError("selected LLM profile has no base_url")
        base = str(profile["base_url"]).rstrip("/")
    protocols = profile.get("protocols", {})
    request = {
        **(profile.get("request") or profile.get("auth") or {}),
        **(selected_environment.get("request") or {}),
    }
    return LLMConfig(
        base_url=f"{base}/{protocols.get('chat_completions', 'v1/chat/completions')}",
        api_key=selected_environment.get("api_key", profile.get("api_key", "")),
        model=selected_environment.get(
            "default_model", profile.get("default_model", "gpt-5.6-sol")
        ),
        timeout=float(selected_environment.get(
            "timeout_seconds", profile.get("timeout_seconds", 600)
        )),
        provider=provider,
        auth_header=request.get("auth_header", request.get("header", "Authorization")),
        auth_prefix=request.get("auth_prefix", request.get("prefix", "Bearer ")),
        trace_header=selected_environment.get(
            "trace_header", profile.get("trace_header")
        ),
        messages_url=f"{base}/{protocols.get('messages', 'v1/messages')}",
        models_url=f"{base}/{protocols.get('models', 'models')}",
        reasoning_effort=selected_environment.get(
            "reasoning_effort", profile.get("reasoning_effort")
        ),
        max_completion_tokens=int(selected_environment.get(
            "max_completion_tokens", profile.get("max_completion_tokens", 8000)
        )),
        verify_tls=bool(selected_environment.get(
            "verify_tls", profile.get("verify_tls", True)
        )),
        ca_file=selected_environment.get("ca_file", profile.get("ca_file")),
    )


def _default_headers(env: dict) -> dict:
    headers = dict(env.get("default_headers", {}))
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept-Language", "en")
    headers.setdefault("X-Auth-ProgId", "7080")
    return headers


def load_quote_config(env: dict | None = None) -> QuoteConfig:
    env = env or _load_env()
    profile_name, profile = selected_quote_profile(env)
    if not profile:
        # Legacy scene configuration fallback.
        scene = env.get("active_scene", "c")
        profile = env.get("scenes", {}).get(scene, {})
    scene = profile.get("scene", env.get("active_scene", "c"))
    endpoints = profile.get("endpoints", {})
    if not endpoints:
        endpoints = env["scenes"][scene]["endpoints"]
    headers = _default_headers(env)
    auth = profile.get("auth", env.get("scenes", {}).get(scene, {}).get("auth", {}))

    ca_file = c_side_ca_file(env) if scene == "c" else None
    verify_tls = c_side_verify_tls(env) if scene == "c" else True
    if scene == "c":
        sessionid, userid = c_side_cookie_values(env)
        template = auth.get("cookie_value_template", "sessionid={sessionid}; userid={userid}")
        headers[auth.get("header", "Cookie")] = template.format(
            sessionid=sessionid, userid=userid
        )
    elif scene == "b":
        # Indicator APIs should use index-api key; quote APIs should use quoteag key.
        creds = profile.get("credentials", {})
        index_key = creds.get("index_api_apikey", env.get("apikey", ""))
        quote_key = creds.get("quoteag_apikey", env.get("apikey", ""))
        headers[auth.get("header", "apikey")] = index_key
        return QuoteConfig(
            scene=scene, profile=profile_name,
            snapshot_url=endpoints["snapshot"], multi_kline_url=endpoints["multi_kline"],
            series_url=endpoints.get("series"), relation_list_url=endpoints.get("relation_list"),
            single_tick_url=endpoints.get("single_tick"), headers=headers,
            endpoint_family_credentials={"index_api": index_key, "quoteag": quote_key},
            ca_file=ca_file, verify_tls=verify_tls,
        )
    # sandbox uses AIME_API_KEY at execution time; this config records endpoints.
    return QuoteConfig(
        scene=scene, profile=profile_name,
        snapshot_url=endpoints["snapshot"], multi_kline_url=endpoints["multi_kline"],
        series_url=endpoints.get("series"), relation_list_url=endpoints.get("relation_list"),
        single_tick_url=endpoints.get("single_tick"), headers=headers,
        ca_file=ca_file, verify_tls=verify_tls,
    )
