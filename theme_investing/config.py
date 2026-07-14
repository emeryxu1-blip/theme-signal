"""Configuration loading for switchable local/production profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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


def _load_env(env_path: Path | None = None) -> dict:
    return json.loads((env_path or ENV_PATH).read_text(encoding="utf-8"))


def active_profile_name(env: dict, kind: str) -> str:
    selected = env.get("active_profiles", {})
    if isinstance(selected, dict) and selected.get(kind):
        return str(selected[kind])
    return "production" if kind == "llm" else (env.get("active_scene") or "c")


def selected_llm_profile(env: dict) -> tuple[str, dict]:
    name = active_profile_name(env, "llm")
    profiles = env.get("llm_profiles", {})
    if isinstance(profiles, dict) and isinstance(profiles.get(name), dict):
        return name, profiles[name]
    return "production", env.get("chatgpt_api", {})


def selected_quote_profile(env: dict) -> tuple[str, dict]:
    name = active_profile_name(env, "quote")
    profiles = env.get("quote_profiles", {})
    if isinstance(profiles, dict) and isinstance(profiles.get(name), dict):
        return name, profiles[name]
    return name, {}


def load_llm_config(env: dict | None = None) -> LLMConfig:
    env = env or _load_env()
    _, profile = selected_llm_profile(env)
    provider = profile.get("provider", "litellm_openai_compatible")
    if provider == "anthropic":
        base = profile.get("base_url", "https://api.anthropic.com").rstrip("/")
        messages_path = profile.get("messages_path", "/v1/messages")
        models_path = profile.get("models_path", "/v1/models")
        return LLMConfig(
            base_url=f"{base}{messages_path}",
            api_key=profile.get("api_key", ""),
            model=profile.get("model", "claude-opus-4-8"),
            timeout=float(profile.get("timeout_seconds", 600)),
            provider=provider,
            auth_header=profile.get("request", {}).get("auth_header", "x-api-key"),
            auth_prefix="",
            thinking=profile.get("request", {}).get("thinking"),
            messages_url=f"{base}{messages_path}",
            models_url=f"{base}{models_path}",
        )

    active = profile.get("active_environment", "internal_equ")
    base = profile["environments"][active]["base_url"].rstrip("/")
    protocols = profile.get("protocols", {})
    return LLMConfig(
        base_url=f"{base}/{protocols.get('chat_completions', 'v1/chat/completions')}",
        api_key=profile.get("api_key", ""),
        model=profile.get("default_model", "gpt-5.2"),
        timeout=float(profile.get("timeout_seconds", 600)),
        provider=provider,
        auth_header="Authorization",
        auth_prefix="Bearer ",
        trace_header=profile.get("trace_header"),
        messages_url=f"{base}/{protocols.get('messages', 'v1/messages')}",
        models_url=f"{base}/{protocols.get('models', 'models')}",
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

    if scene == "c":
        template = auth.get("cookie_value_template", "sessionid={sessionid}; userid={userid}")
        headers[auth.get("header", "Cookie")] = template.format(
            sessionid=env.get("sessionid", ""), userid=env.get("userid", "")
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
        )
    # sandbox uses AIME_API_KEY at execution time; this config records endpoints.
    return QuoteConfig(
        scene=scene, profile=profile_name,
        snapshot_url=endpoints["snapshot"], multi_kline_url=endpoints["multi_kline"],
        series_url=endpoints.get("series"), relation_list_url=endpoints.get("relation_list"),
        single_tick_url=endpoints.get("single_tick"), headers=headers,
    )
