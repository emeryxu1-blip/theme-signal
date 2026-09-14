#!/usr/bin/env python3
"""Execute or dry-run one AInvest quote request.

The script accepts exactly one request body source: `--template`, `--body-file`,
or `--body-json`. It infers the endpoint when possible, chooses URLs for the
selected scene (`sandbox`, `b`, or `c`), and prints a redacted dry-run payload or
sends the request.

Authentication is intentionally scene-specific: sandbox reads `AIME_API_KEY`
unless `--auth-env` is supplied; B-side requests use caller-provided apikeys;
C-side requests use a caller-provided Cookie. B/C auth can be loaded from the
shared local `Skills/env.json` config, and command-line auth values override it.
Never store real auth values in templates, docs, generated files, or command
history intended for sharing.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "assets" / "request-templates"
ENV_CONFIG_PATH = ROOT.parent / "env.json"

ENDPOINT_NAMES = ("snapshot", "series", "multi_kline", "single_tick", "relation_list")

ENDPOINT_URLS = {
    "sandbox": {
        "snapshot": "https://open.ainvest.com/market/extquote/index/indicator/v2/snapshot",
        "series": "https://open.ainvest.com/market/extquote/index/indicator/v2/series",
        "relation_list": "https://open.ainvest.com/market/extquote/index/relation/v1/list",
        "multi_kline": "https://open.ainvest.com/market/extquote/ag/quote/v2/multi_kline",
        "single_tick": "https://open.ainvest.com/market/extquote/ag/quote/v2/single_tick",
    },
    "b": {
        "snapshot": "http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/snapshot",
        "series": "http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/series",
        "relation_list": "http://quote-apisix-gateway.hxapisix/index_api/relation/v1/list",
        "multi_kline": "http://quote-apisix-gateway.hxapisix/quote/v2/multi_kline",
        "single_tick": "http://quote-apisix-gateway.hxapisix/quote/v2/single_tick",
    },
    "c": {
        "snapshot": "https://extquote.ainvest.com/index_api/indicator/v2/snapshot",
        "series": "https://extquote.ainvest.com/index_api/indicator/v2/series",
        "relation_list": "https://extquote.ainvest.com/index_api/relation/v1/list",
        "multi_kline": "https://quote.ainvest.com/quote/v2/multi_kline",
        "single_tick": "https://quote.ainvest.com/quote/v2/single_tick",
    },
}

AUTH_PROFILES = {
    "sandbox": {
        "env": "AIME_API_KEY",
        "header": "Authorization",
        "prefix": "Bearer ",
        "placeholder": "Bearer <AIME_API_KEY>",
    },
    "b": {
        "header": "apikey",
        "prefix": "",
        "indicator_placeholder": "<CALLER_PROVIDED_INDEX_API_APIKEY>",
        "quote_placeholder": "<CALLER_PROVIDED_QUOTEAG_APIKEY>",
    },
    "c": {
        "header": "Cookie",
        "prefix": "",
        "placeholder": "<CALLER_PROVIDED_COOKIE>",
    },
}

INDICATOR_ENDPOINTS = {"snapshot", "series", "relation_list"}
QUOTE_ENDPOINTS = {"multi_kline", "single_tick"}

TEMPLATE_ENDPOINT_HINTS = {
    "multi-kline-minute.json": "multi_kline",
    "single-tick.json": "single_tick",
    "series-chain-history.json": "series",
    "series-etf-30d.json": "series",
    "series-prompt-self-ratings.json": "series",
    "relation-etf-holding.json": "relation_list",
    "relation-index-components.json": "relation_list",
    "relation-prompt-components.json": "relation_list",
    "relation-group-components.json": "relation_list",
    "relation-group-list.json": "relation_list",
    "relation-prompt-holding-empty.json": "relation_list",
}


def load_json_text(text, source):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} is not valid JSON: {exc}") from exc


def load_env_config(path):
    if not path:
        return {}

    config_path = Path(path)
    if not config_path.exists():
        return {}

    config = load_json_text(config_path.read_text(encoding="utf-8"), str(config_path))
    if not isinstance(config, dict):
        raise ValueError(f"{config_path} must contain a JSON object")
    return config


def scene_config(env_config, scene):
    profiles = env_config.get("quote_profiles", {})
    active = env_config.get("active_profiles", {}).get("quote")
    if isinstance(profiles, dict) and active and isinstance(profiles.get(active), dict):
        profile = profiles[active]
        if profile.get("scene", env_config.get("active_scene")) == scene:
            return profile
    scenes = env_config.get("scenes", {})
    if not isinstance(scenes, dict):
        return {}
    config = scenes.get(scene, {})
    return config if isinstance(config, dict) else {}


def default_header_value(env_config, name, fallback):
    headers = env_config.get("default_headers", {})
    if isinstance(headers, dict) and headers.get(name):
        return str(headers[name])
    return fallback


def configured_scene(env_config):
    profiles = env_config.get("quote_profiles", {})
    active = env_config.get("active_profiles", {}).get("quote")
    if isinstance(profiles, dict) and active and isinstance(profiles.get(active), dict):
        scene = profiles[active].get("scene")
        if scene:
            return str(scene)
    return env_config.get("active_scene") or env_config.get("default_scene")


def resolve_scene(args, env_config):
    scene = args.scene or configured_scene(env_config) or "sandbox"
    if scene not in ENDPOINT_URLS:
        raise ValueError(
            f"Unknown scene {scene!r}. Expected one of: {', '.join(sorted(ENDPOINT_URLS))}"
        )
    return scene


def configured_endpoint_url(env_config, scene, endpoint_name):
    config = scene_config(env_config, scene)
    endpoints = config.get("endpoints", {})
    if isinstance(endpoints, dict) and endpoints.get(endpoint_name):
        return str(endpoints[endpoint_name])
    return ENDPOINT_URLS[scene][endpoint_name]


def config_value(env_config, scene, key):
    config = scene_config(env_config, scene)
    containers = [
        (config.get("credentials", {}), f"env.json scenes.{scene}.credentials.{key}"),
        (config, f"env.json scenes.{scene}.{key}"),
        (env_config, f"env.json {key}"),
    ]
    for container, source in containers:
        if isinstance(container, dict) and container.get(key):
            return str(container[key]), source
    return None, None


def b_config_auth_value(env_config, endpoint_name):
    config = scene_config(env_config, "b")
    auth_config = config.get("auth", {})
    endpoint_keys = {}
    if isinstance(auth_config, dict):
        endpoint_keys = auth_config.get("endpoint_credential_keys", {})

    candidate_keys = []
    if isinstance(endpoint_keys, dict) and endpoint_keys.get(endpoint_name):
        candidate_keys.append(endpoint_keys[endpoint_name])

    if endpoint_name in INDICATOR_ENDPOINTS:
        candidate_keys.append("index_api_apikey")
    elif endpoint_name in QUOTE_ENDPOINTS:
        candidate_keys.append("quoteag_apikey")

    if isinstance(auth_config, dict) and auth_config.get("default_credential_key"):
        candidate_keys.append(auth_config["default_credential_key"])
    candidate_keys.append("apikey")

    seen = set()
    for key in candidate_keys:
        if key in seen:
            continue
        seen.add(key)
        value, source = config_value(env_config, "b", key)
        if value:
            return value, source

    return None, "env.json b apikey"


def c_config_auth_value(env_config):
    config = scene_config(env_config, "c")
    auth_config = config.get("auth", {})

    for key in ("cookie", "auth_value"):
        value, source = config_value(env_config, "c", key)
        if value:
            return value, source

    sessionid, _ = config_value(env_config, "c", "sessionid")
    userid, _ = config_value(env_config, "c", "userid")
    if sessionid and userid:
        template = "sessionid={sessionid}; userid={userid}"
        if isinstance(auth_config, dict) and auth_config.get("cookie_value_template"):
            template = str(auth_config["cookie_value_template"])
        return template.format(sessionid=sessionid, userid=userid), "env.json c sessionid/userid"
    if sessionid:
        return f"sessionid={sessionid}", "env.json c sessionid"
    if userid:
        return f"userid={userid}", "env.json c userid"

    return None, "env.json c cookie"


def sandbox_auth_env(args, env_config):
    if args.auth_env:
        return args.auth_env
    config = scene_config(env_config, "sandbox")
    auth_config = config.get("auth", {})
    if isinstance(auth_config, dict) and auth_config.get("env"):
        return str(auth_config["env"])
    return AUTH_PROFILES["sandbox"]["env"]


def resolve_template_path(value):
    path = Path(value)
    if path.exists():
        return path

    candidate = TEMPLATE_DIR / value
    if candidate.exists():
        return candidate

    if not value.endswith(".json"):
        candidate = TEMPLATE_DIR / f"{value}.json"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(f"Template not found: {value}")


def load_body(args):
    sources = [bool(args.template), bool(args.body_file), bool(args.body_json)]
    if sum(sources) != 1:
        raise ValueError(
            "Provide exactly one of --template, --body-file, or --body-json."
        )

    if args.template:
        path = resolve_template_path(args.template)
        return load_json_text(path.read_text(encoding="utf-8"), str(path)), path.name

    if args.body_file:
        path = Path(args.body_file)
        return load_json_text(path.read_text(encoding="utf-8"), str(path)), path.name

    return load_json_text(args.body_json, "--body-json"), None


def infer_endpoint(payload, template_name=None):
    if template_name in TEMPLATE_ENDPOINT_HINTS:
        return TEMPLATE_ENDPOINT_HINTS[template_name]

    if isinstance(payload.get("code_list"), list) and "indicator" not in payload:
        time_range = payload.get("time_range")
        code_count = 0
        for item in payload.get("code_list", []):
            if isinstance(item, dict) and isinstance(item.get("codes"), list):
                code_count += len(item["codes"])
        if (
            isinstance(time_range, dict)
            and time_range.get("trade_date") == 0
            and "count" in time_range
            and "end_time" in time_range
            and code_count == 1
        ):
            return "single_tick"
        return "multi_kline"

    if (
        "relation" in payload
        and "symbol" in payload
        and "symbol_type" in payload
        and "indicator" not in payload
    ):
        return "relation_list"

    if isinstance(payload.get("time_range"), dict) and isinstance(
        payload.get("symbol"), dict
    ):
        return "series"

    if isinstance(payload.get("indicator"), list):
        return "snapshot"

    raise ValueError("Cannot infer endpoint. Pass --endpoint explicitly.")


def b_auth_value(args, endpoint_name, env_config):
    if endpoint_name in INDICATOR_ENDPOINTS:
        if args.index_api_apikey or args.auth_value:
            return (
                args.index_api_apikey or args.auth_value,
                "caller-provided index-api apikey",
            )
    if endpoint_name in QUOTE_ENDPOINTS:
        if args.quoteag_apikey or args.auth_value:
            return args.quoteag_apikey or args.auth_value, "caller-provided quoteag apikey"
    if args.auth_value:
        return args.auth_value, "caller-provided apikey"
    return b_config_auth_value(env_config, endpoint_name)


def resolve_auth_value(args, endpoint_name, scene, env_config):
    if scene == "sandbox":
        env_name = sandbox_auth_env(args, env_config)
        return os.environ.get(env_name), env_name
    if scene == "b":
        return b_auth_value(args, endpoint_name, env_config)
    if args.auth_value:
        return args.auth_value, "caller-provided cookie"
    return c_config_auth_value(env_config)


def build_headers(scene, auth_value, accept_language, auth_prog_id):
    profile = AUTH_PROFILES[scene]
    headers = {
        "Content-Type": "application/json",
        "Accept-Language": accept_language,
        "X-Auth-ProgId": auth_prog_id,
    }
    if auth_value:
        headers[profile["header"]] = f"{profile['prefix']}{auth_value}"
    return headers


def redact_headers(headers, scene, endpoint_name):
    redacted = dict(headers)
    profile = AUTH_PROFILES[scene]
    if scene == "b" and endpoint_name in INDICATOR_ENDPOINTS:
        redacted[profile["header"]] = profile["indicator_placeholder"]
    elif scene == "b" and endpoint_name in QUOTE_ENDPOINTS:
        redacted[profile["header"]] = profile["quote_placeholder"]
    else:
        redacted[profile["header"]] = profile["placeholder"]
    return redacted


def post_json(endpoint, payload, headers, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, load_json_text(body, "response")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw_body": body}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 0, {
            "error": "request_failed",
            "reason": str(exc.reason),
        }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch AInvest OpenAPI quote or relation data from a template or JSON body."
    )
    parser.add_argument(
        "--scene",
        choices=sorted(ENDPOINT_URLS),
        help="Access scene: sandbox, b, or c. Defaults to env.json active_scene, then sandbox.",
    )
    parser.add_argument(
        "--endpoint",
        choices=ENDPOINT_NAMES,
        help="Endpoint name. If omitted, infer from the payload.",
    )
    parser.add_argument(
        "--template",
        help="Template filename/path under assets/request-templates, for example stock-detail.json",
    )
    parser.add_argument("--body-file", help="Path to a JSON request body")
    parser.add_argument("--body-json", help="Inline JSON request body")
    parser.add_argument(
        "--auth-value",
        help="Caller-provided auth value for b/c scenes: apikey for b, Cookie value for c",
    )
    parser.add_argument(
        "--index-api-apikey",
        help="B-side apikey for index_api indicator and relation endpoints",
    )
    parser.add_argument(
        "--quoteag-apikey",
        help="B-side apikey for quote/v2 multi_kline and single_tick",
    )
    parser.add_argument(
        "--auth-env", help="Sandbox-only environment variable holding AIME Claw API key"
    )
    parser.add_argument(
        "--api-key-env", dest="auth_env", help="Deprecated alias for --auth-env"
    )
    parser.add_argument(
        "--accept-language", help="Accept-Language header value"
    )
    parser.add_argument(
        "--auth-prog-id",
        help="X-Auth-ProgId header value. Defaults to env.json, then 7080.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_CONFIG_PATH),
        help="Shared AInvest env config. Defaults to ../env.json.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Ignore the shared env config and use only command-line values.",
    )
    parser.add_argument(
        "--timeout", type=float, default=20.0, help="Request timeout in seconds"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print endpoint, headers, and body without sending the request",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        env_config = {} if args.no_env_file else load_env_config(args.env_file)
        scene = resolve_scene(args, env_config)
        payload, template_name = load_body(args)
        endpoint_name = args.endpoint or infer_endpoint(payload, template_name)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    endpoint = configured_endpoint_url(env_config, scene, endpoint_name)
    if scene != "sandbox" and args.auth_env:
        print(
            "error: --auth-env is only supported for sandbox scene; use --auth-value for b/c scenes",
            file=sys.stderr,
        )
        return 2
    if scene != "b" and (args.index_api_apikey or args.quoteag_apikey):
        print(
            "error: --index-api-apikey and --quoteag-apikey are only supported for b scene",
            file=sys.stderr,
        )
        return 2
    if (
        scene == "b"
        and args.auth_value
        and (args.index_api_apikey or args.quoteag_apikey)
    ):
        print(
            "error: use either --auth-value or endpoint-family apikey args, not both",
            file=sys.stderr,
        )
        return 2

    auth_value, auth_source = resolve_auth_value(args, endpoint_name, scene, env_config)
    accept_language = args.accept_language or default_header_value(
        env_config, "Accept-Language", "en"
    )
    auth_prog_id = args.auth_prog_id or default_header_value(
        env_config, "X-Auth-ProgId", "7080"
    )
    headers = build_headers(
        scene, auth_value, accept_language, auth_prog_id
    )

    if args.dry_run:
        output = {
            "scene": scene,
            "endpoint_name": endpoint_name,
            "endpoint": endpoint,
            "auth_source": auth_source,
            "headers": redact_headers(headers, scene, endpoint_name),
            "body": payload,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    if not auth_value:
        if scene == "sandbox":
            print(
                f"error: missing required environment variable {auth_source} for scene sandbox",
                file=sys.stderr,
            )
        elif scene == "b" and endpoint_name in INDICATOR_ENDPOINTS:
            print(
                "error: missing required env.json apikey, --index-api-apikey, or --auth-value for b scene index-api endpoint",
                file=sys.stderr,
            )
        elif scene == "b" and endpoint_name in QUOTE_ENDPOINTS:
            print(
                "error: missing required env.json apikey, --quoteag-apikey, or --auth-value for b scene quote endpoint",
                file=sys.stderr,
            )
        else:
            print(
                f"error: missing required env.json cookie fields or --auth-value for scene {scene}",
                file=sys.stderr,
            )
        return 2

    status, response = post_json(endpoint, payload, headers, args.timeout)
    output = {
        "http_status": status,
        "scene": scene,
        "endpoint_name": endpoint_name,
        "endpoint": endpoint,
        "response": response,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
