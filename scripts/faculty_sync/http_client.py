"""
faculty_sync.http_client
========================

Robust HTTP client with exponential backoff, retries, and jitter.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class RobustHttp:
    def __init__(
        self,
        max_retries: int = 6,
        backoff_base: float = 1.7,
        timeout: int = 25,
        user_agent: str = "AmazeCC-FacultySync/1.0 (+https://amazecc.com)",
        respect_retry_after: bool = True,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self.user_agent = user_agent
        self.respect_retry_after = respect_retry_after
        self._requests_made = 0
        self._last_status: Optional[int] = None

    # -- stats --------------------------------------------------------- #
    @property
    def requests_made(self) -> int:
        return self._requests_made

    @property
    def last_status(self) -> Optional[int]:
        return self._last_status

    # -- core request -------------------------------------------------- #
    def _sleep_for(self, attempt: int, headers) -> None:
        """Compute backoff sleep, honoring Retry-After when present."""
        if self.respect_retry_after:
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra is not None:
                try:
                    time.sleep(float(ra))
                    return
                except ValueError:
                    pass
        sleep = (self.backoff_base ** attempt) + random.uniform(0, 1.0)
        time.sleep(min(sleep, 30.0))

    def get(
        self,
        url: str,
        accept: str = "application/json",
        timeout: Optional[int] = None,
    ) -> tuple[int, Dict[str, str], bytes]:
        """
        GET a URL with retries. Returns (status, headers_dict, body_bytes).

        Raises the last exception if all retries are exhausted.
        """
        timeout = timeout or self.timeout
        last_exc: Optional[Exception] = None
        headers: Dict[str, str] = {}

        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": accept,
                    "Cache-Control": "no-cache",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read()
                    self._requests_made += 1
                    self._last_status = resp.status
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return resp.status, headers, body
            except urllib.error.HTTPError as e:
                self._requests_made += 1
                self._last_status = e.code
                headers = {k.lower(): v for k, v in e.headers.items()}
                body = e.read() if hasattr(e, "read") else b""
                # Retry on 429 / 5xx, fail fast on 4xx (except 429)
                if e.code in (429,) or 500 <= e.code < 600:
                    last_exc = e
                    if attempt < self.max_retries - 1:
                        self._sleep_for(attempt, headers)
                        continue
                raise
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries - 1:
                    self._sleep_for(attempt, {})
                    continue
                raise

        # Should not reach here
        raise last_exc or RuntimeError("unreachable")

    def get_json(self, url: str, timeout: Optional[int] = None) -> tuple[int, Dict[str, str], Any]:
        status, headers, body = self.get(url, accept="application/json", timeout=timeout)
        try:
            data = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            data = body.decode("utf-8", errors="replace")
        return status, headers, data
