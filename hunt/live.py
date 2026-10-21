"""Ask the posting URL whether it is still open. Used only on the shortlist, not every board row."""

from __future__ import annotations

import urllib.error
import urllib.request

from hunt.screen import looks_closed
from hunt.sources import USER_AGENT


def check_live(url: str, timeout: int = 12) -> str:
    """Return open, closed, or unknown. A missing URL is closed."""
    if not (url or "").strip():
        return "closed"
    code, sample = _fetch(url, "HEAD", timeout)
    if code in {None, 405} or (code is not None and code >= 500):
        code, sample = _fetch(url, "GET", timeout)
    if code is None:
        return "unknown"
    if code in {404, 410} or looks_closed(sample):
        return "closed"
    if 200 <= code < 400 or code in {401, 403}:
        return "open"
    return "unknown"


def _fetch(url: str, method: str, timeout: int) -> tuple[int | None, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            sample = response.read(512).decode("utf-8", "replace") if method == "GET" else ""
            return response.status, sample
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, ""
