"""Fetch and normalize the input article URL (best-effort, bounded)."""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request

_SSL_CTX = ssl._create_unverified_context()
_MAX_CHARS = 12000
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_WS = re.compile(r"\s+")


def fetch_article(url: str, *, timeout: float = 25.0) -> dict:
    """Return ``{"url","title","text","ok"}``. Never raises; falls back to empty text."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; theme-workflow/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        return {"url": url, "title": "", "text": "", "ok": False, "error": str(exc)}

    title_m = _TITLE.search(html)
    title = _WS.sub(" ", _TAG.sub("", title_m.group(1))).strip() if title_m else ""
    body = _SCRIPT.sub(" ", html)
    text = _WS.sub(" ", _TAG.sub(" ", body)).strip()
    return {"url": url, "title": title, "text": text[:_MAX_CHARS], "ok": bool(text)}
