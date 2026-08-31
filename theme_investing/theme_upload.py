"""Upload an exact public workflow result to the theme archive Worker."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class ThemeUploadError(RuntimeError):
    pass


_TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep the upload credential on the one configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_endpoint(endpoint: str) -> None:
    parsed = urllib.parse.urlsplit(endpoint)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not parsed.netloc or (parsed.scheme != "https" and not (
        parsed.scheme == "http" and loopback
    )):
        raise ThemeUploadError(
            "theme upload endpoint must use HTTPS (HTTP is allowed only for loopback)"
        )


def _failure_kind(exc: Exception | None) -> str:
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        return type(exc.reason).__name__
    return type(exc).__name__ if exc is not None else "unknown network error"


def _retry_delay(exc: Exception, attempt: int) -> float:
    if isinstance(exc, urllib.error.HTTPError) and exc.headers:
        retry_after = exc.headers.get("Retry-After")
        try:
            return min(30.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    return min(4.0, 0.5 * (2 ** attempt))


def upload_result(
    serialized: str,
    *,
    endpoint: str,
    token: str,
    timeout: float = 30.0,
    attempts: int = 3,
) -> str:
    """POST exact result bytes with bounded, idempotent transient retries."""
    if attempts < 1:
        raise ValueError("upload attempts must be at least 1")
    _validate_endpoint(endpoint)
    body = serialized.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    headers = {
        "X-Theme-Upload-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "theme-workflow/1.0",
        "X-Content-SHA256": digest,
        "X-Idempotency-Key": digest,
    }
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    # macOS system proxy settings can be picked up implicitly by urllib even
    # when no proxy environment variables are present. The local proxy may
    # reset TLS connections to Workers, while the archive is directly
    # reachable. Use the verified CA bundle over a direct connection.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _RejectRedirects(),
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        retry_delay: float | None = None
        request = urllib.request.Request(
            endpoint, data=body, method="POST", headers=headers,
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code not in _TRANSIENT_HTTP_STATUSES:
                status = exc.code
                exc.close()
                raise ThemeUploadError(
                    f"theme result upload returned HTTP {status}"
                ) from exc
            retry_delay = _retry_delay(exc, attempt)
            last_error = ThemeUploadError(
                f"theme result upload returned HTTP {exc.code}"
            )
            exc.close()
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise ThemeUploadError(
                    "theme result upload TLS certificate verification failed"
                ) from exc
            last_error = exc
        except ssl.SSLCertVerificationError as exc:
            raise ThemeUploadError(
                "theme result upload TLS certificate verification failed"
            ) from exc
        except OSError as exc:
            last_error = exc
        else:
            if status in _TRANSIENT_HTTP_STATUSES:
                last_error = ThemeUploadError(
                    f"theme result upload returned HTTP {status}"
                )
            elif status < 200 or status >= 300:
                raise ThemeUploadError(
                    f"theme result upload returned HTTP {status}"
                )
            else:
                try:
                    data = json.loads(payload)
                except (ValueError, TypeError) as exc:
                    raise ThemeUploadError(
                        "theme result upload returned invalid JSON"
                    ) from exc
                archive_id = data.get("id") if isinstance(data, dict) else None
                if not archive_id:
                    raise ThemeUploadError(
                        "theme result upload response did not include an id"
                    )
                return str(archive_id)
        if attempt + 1 < attempts:
            time.sleep(
                retry_delay if retry_delay is not None
                else _retry_delay(last_error, attempt)
            )
    raise ThemeUploadError(
        f"theme result upload failed after {attempts} attempts "
        f"({_failure_kind(last_error)})"
    ) from last_error


def configured_upload() -> tuple[str, str] | None:
    endpoint = os.environ.get("THEME_SIGNAL_UPLOAD_URL", "").strip()
    token = os.environ.get("THEME_SIGNAL_UPLOAD_TOKEN", "").strip()
    if not endpoint and not token:
        return None
    if not endpoint or not token:
        raise ThemeUploadError(
            "set both THEME_SIGNAL_UPLOAD_URL and THEME_SIGNAL_UPLOAD_TOKEN"
        )
    return endpoint, token
