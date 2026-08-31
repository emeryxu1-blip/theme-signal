"""C-side AInvest session login and safe local-session persistence.

This mirrors AInvest's current web email/password flow. Interactive challenges are
surfaced to the caller; this module never attempts to solve or bypass them.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

VISITOR_LOGIN_URL = "https://user.ainvest.com/auth/visitor/login"
ACCOUNT_CHECK_URL = "https://user.ainvest.com/auth/user/v2/checkAccount"
LOGIN_URL = "https://user.ainvest.com/auth/user/v3/login"
LOGIN_UKEY = "72c55701353cb5555c8805408e7d8dbd"

# The browser-compatible signing key is public client material, but its PEM
# shape belongs in ignored local storage rather than a public source repository.
DEFAULT_PASSWORD_SIGNING_KEY_PATH = (
    Path(__file__).resolve().parent.parent / "Skills" / "ainvest-password-signing-key.pem"
)


class AInvestAuthError(RuntimeError):
    """A C-side login could not establish a usable session."""


class AInvestChallengeError(AInvestAuthError):
    """AInvest requested a captcha or other interactive challenge."""


_CHALLENGE_CODES = {"-100005", "-100007", "-100008"}


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _raise_if_challenged(response: dict) -> None:
    text = json.dumps(response, ensure_ascii=False).lower()
    code = str(response.get("errorCode", response.get("code", "")))
    if code in _CHALLENGE_CODES or any(
        token in text for token in ("captcha", "slider", "challenge", "cloudflare")
    ):
        raise AInvestChallengeError(
            "AInvest requires interactive verification; complete it on the website and try again."
        )


def _response_value(response: dict, *names: str) -> str | None:
    expected = {name.lower() for name in names}
    for key, value in _walk(response):
        if str(key).lower() in expected and isinstance(value, (str, int)) and str(value):
            return str(value)
    return None


def _tls_context(ca_file: str | None, verify_tls: bool) -> ssl.SSLContext:
    if not verify_tls:
        return ssl._create_unverified_context()
    try:
        return ssl.create_default_context(cafile=ca_file or None)
    except (OSError, ssl.SSLError) as exc:
        raise AInvestAuthError(f"unable to load configured TLS CA file: {exc}") from exc


def _post(opener, url: str, body: dict, timeout: float, *, headers: dict | None = None,
          form: bool = False) -> dict:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if form:
        data = urllib.parse.urlencode(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise AInvestAuthError(f"AInvest authentication returned HTTP {exc.code}") from exc
        _raise_if_challenged(parsed)
        message = parsed.get("i18nMsg") or parsed.get("status_msg") or "authentication failed"
        raise AInvestAuthError(f"AInvest authentication failed: {message}") from exc
    except urllib.error.URLError as exc:
        raise AInvestAuthError(f"AInvest authentication request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AInvestAuthError("AInvest authentication returned non-JSON data") from exc
    if not isinstance(parsed, dict):
        raise AInvestAuthError("AInvest authentication returned an invalid response")
    _raise_if_challenged(parsed)
    if parsed.get("status") == 466 or parsed.get("errorCode"):
        message = parsed.get("i18nMsg") or parsed.get("status_msg") or "authentication failed"
        raise AInvestAuthError(f"AInvest authentication failed: {message}")
    return parsed


def _password_signing_key_path(configured: str | None = None) -> Path:
    raw_path = configured or os.environ.get("AINVEST_PASSWORD_SIGNING_KEY_FILE")
    key_path = Path(raw_path).expanduser() if raw_path else DEFAULT_PASSWORD_SIGNING_KEY_PATH
    if not key_path.is_absolute():
        key_path = Path(__file__).resolve().parent.parent / key_path
    if not key_path.is_file():
        raise AInvestAuthError(
            "AInvest password signing key is missing; configure "
            "password_signing_key_file or AINVEST_PASSWORD_SIGNING_KEY_FILE"
        )
    return key_path


def _sign_password(
    password: str,
    timestamp: str,
    signing_key_file: str | None = None,
) -> str:
    """Return the browser-compatible ``timestamp + RSA-SHA256 signature`` value."""
    key_path = _password_signing_key_path(signing_key_file)
    try:
        completed = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(key_path)],
            input=password.encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        if completed.returncode:
            raise AInvestAuthError("OpenSSL could not sign the account password")
        return timestamp + base64.b64encode(completed.stdout).decode("ascii")
    except FileNotFoundError as exc:
        raise AInvestAuthError("/usr/bin/openssl is required for AInvest C-side login") from exc


def _login_settings(env: dict) -> tuple[str | None, dict, dict]:
    profiles = env.get("quote_profiles")
    if isinstance(profiles, dict):
        active = env.get("active_profiles", {}).get("quote")
        if active and isinstance(profiles.get(active), dict):
            profile = profiles[active]
            if profile.get("scene", env.get("active_scene")) == "c":
                auth = profile.get("auth", {})
                return str(active), profile, auth if isinstance(auth, dict) else {}
    scene = env.get("active_scene", "c")
    profile = env.get("scenes", {}).get(scene, {}) if scene == "c" else {}
    auth = profile.get("auth", {}) if isinstance(profile, dict) else {}
    return None, profile if isinstance(profile, dict) else {}, auth if isinstance(auth, dict) else {}


def _configured_login(env: dict) -> tuple[str | None, str | None, dict]:
    _, profile, auth = _login_settings(env)
    login = auth.get("login", {}) if isinstance(auth.get("login"), dict) else {}
    root_login = env.get("ainvest_c_login", {})
    credentials = profile.get("credentials", {}) if isinstance(profile.get("credentials"), dict) else {}
    username = (login.get("username") or login.get("email") or auth.get("username")
                or credentials.get("username") or root_login.get("username") or root_login.get("email"))
    password = (login.get("password") or auth.get("password") or credentials.get("password")
                or root_login.get("password"))
    return (str(username) if username else None, str(password) if password else None, login)


def c_side_auto_login_enabled(env: dict) -> bool:
    _, _, auth = _login_settings(env)
    login = auth.get("login", {}) if isinstance(auth.get("login"), dict) else {}
    root_login = env.get("ainvest_c_login", {})
    return bool(login.get("auto_login", auth.get("auto_login", root_login.get("auto_login", False))))


def c_side_ca_file(env: dict) -> str | None:
    _, _, auth = _login_settings(env)
    login = auth.get("login", {}) if isinstance(auth.get("login"), dict) else {}
    value = login.get("ca_file") or auth.get("ca_file")
    return str(value) if value else None


def c_side_verify_tls(env: dict) -> bool:
    _, _, auth = _login_settings(env)
    login = auth.get("login", {}) if isinstance(auth.get("login"), dict) else {}
    return bool(login.get("verify_tls", auth.get("verify_tls", True)))


def c_side_cookie_values(env: dict) -> tuple[str, str]:
    _, profile, _ = _login_settings(env)
    return str(profile.get("sessionid", env.get("sessionid", ""))), str(
        profile.get("userid", env.get("userid", ""))
    )


def login_c_side(env: dict, *, timeout: float = 20.0) -> tuple[str, str]:
    """Perform AInvest's current web email/password flow."""
    _, _, auth = _login_settings(env)
    username, password, login = _configured_login(env)
    if not username or not password:
        raise AInvestAuthError("C-side login requires configured email and password in Skills/env.json")

    timeout = float(login.get("timeout_seconds", timeout))
    udid = str(login.get("udid") or uuid.uuid4())
    fingerprint = str(login.get("fingerprint") or udid)
    verify_tls = bool(login.get("verify_tls", auth.get("verify_tls", True)))
    ca_file = login.get("ca_file") or auth.get("ca_file")
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies),
        urllib.request.HTTPSHandler(context=_tls_context(str(ca_file) if ca_file else None, verify_tls)),
    )

    visitor = _post(
        opener, str(login.get("visitor_login_url", VISITOR_LOGIN_URL)),
        {"udid": udid, "clientType": "WEB"}, timeout,
        headers={"fingerprint": fingerprint}, form=True,
    )
    visitor_id = _response_value(visitor, "userId", "userid")
    token = _response_value(visitor, "token", "sessionid")
    cookie_map = {cookie.name.lower(): cookie.value for cookie in cookies}
    visitor_id = visitor_id or cookie_map.get("userid")
    token = token or cookie_map.get("sessionid")
    if not visitor_id or not token:
        raise AInvestAuthError("visitor login did not establish a session")

    check = _post(
        opener, str(login.get("account_check_url", ACCOUNT_CHECK_URL)),
        {"type": "EMAIL", "email": username}, timeout,
        headers={"token": token, "userid": visitor_id},
    )
    existing = check.get("data")
    if existing is False or existing in (0, "0"):
        raise AInvestAuthError("AInvest account was not found")

    timestamp = str(int(time.time() * 1000))
    response = _post(
        opener, str(login.get("login_url", LOGIN_URL)),
        {
            "type": "EMAIL", "loginType": "ACCOUNT_PWD", "email": username,
            "signedPwd": _sign_password(
                password,
                timestamp,
                str(login.get("password_signing_key_file") or "") or None,
            ),
            "visitorId": visitor_id, "token": token,
        }, timeout, headers={
            "ukey": str(login.get("ukey", LOGIN_UKEY)),
            "fingerprint": fingerprint,
        },
    )
    cookie_map = {cookie.name.lower(): cookie.value for cookie in cookies}
    sessionid = cookie_map.get("sessionid") or _response_value(response, "token", "sessionid")
    userid = cookie_map.get("userid") or _response_value(response, "userId", "userid")
    if not sessionid or not userid:
        raise AInvestAuthError("C-side login completed without both sessionid and userid cookies")
    return sessionid, userid


def persist_c_session(env_path: Path, env: dict, sessionid: str, userid: str) -> dict:
    """Atomically persist C-side cookies without modifying unrelated settings."""
    profile_name, _, _ = _login_settings(env)
    updated = json.loads(json.dumps(env))
    if profile_name:
        target = updated["quote_profiles"][profile_name]
    else:
        target = updated.setdefault("scenes", {}).setdefault("c", {})
    target["sessionid"] = sessionid
    target["userid"] = userid
    # Keep legacy root fields synchronized for older scripts.
    updated["sessionid"] = sessionid
    updated["userid"] = userid

    encoded = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    env_path = Path(env_path)
    mode = env_path.stat().st_mode & 0o777 if env_path.exists() else 0o600
    with tempfile.NamedTemporaryFile(dir=env_path.parent, prefix=f".{env_path.name}.", delete=False) as temp:
        temp.write(encoded)
        temp.flush()
        os.fsync(temp.fileno())
        os.fchmod(temp.fileno(), mode)
        temp_name = temp.name
    try:
        os.replace(temp_name, env_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return updated


def refresh_c_session_if_configured(env: dict, env_path: Path, *, dry_run: bool = False) -> dict:
    if dry_run or not c_side_auto_login_enabled(env):
        return env
    sessionid, userid = login_c_side(env)
    return persist_c_session(Path(env_path), env, sessionid, userid)
