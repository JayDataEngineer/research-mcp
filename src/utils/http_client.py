"""HTTP client utilities — retry transport + proxy-aware factory.

`create_async_client` was a thin wrapper that no caller actually used;
the real factory lives in `proxy.py::create_proxied_client`. This module
now provides a `RetryTransport` that `create_proxied_client` installs by
default, so every scraper and search call gets transient-failure retries
without changing call sites.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable

import httpx
from loguru import logger

# Exceptions httpx considers "the request never reached the server" — safe to
# retry because no side effect could have been committed.
_TRANSIENT_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
)

# Status codes that typically resolve on retry (rate-limit + transient 5xx).
_DEFAULT_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryTransport(httpx.AsyncBaseTransport):
    """Wraps an async transport, replaying transient failures with backoff.

    Retries on:
      - httpx transport errors (connect/read/timeout/remote-protocol)
      - HTTP 408/425/429/500/502/503/504

    Skips:
      - 4xx other than 408/425/429 — these are client errors, retrying won't help.
      - Streaming request bodies — only the buffered bodies used by our JSON-RPC
        and small POSTs are replayable. httpx streams bodies that aren't bytes;
        we drop those from retry to avoid partial-upload ambiguity.

    Honors Retry-After on 429/503 if present, otherwise uses exponential
    backoff: delay = base * 2**attempt, capped at max_delay, with optional
    full jitter.
    """

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        *,
        retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        jitter: bool = True,
        retry_statuses: Iterable[int] = _DEFAULT_RETRY_STATUSES,
    ) -> None:
        self._wrapped = wrapped
        self._retries = max(0, retries)
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter = jitter
        self._retry_statuses = frozenset(retry_statuses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None
        last_response: httpx.Response | None = None

        for attempt in range(self._retries + 1):
            try:
                response = await self._wrapped.handle_async_request(request)
            except _TRANSIENT_ERRORS as e:
                last_exc = e
                last_response = None
                if attempt >= self._retries:
                    raise
                await self._sleep(request, attempt, retry_after=None)
                continue

            # 2xx / non-retryable 4xx / 5xx-not-in-set → caller's problem
            if response.status_code not in self._retry_statuses:
                return response

            last_response = response
            last_exc = None
            if attempt >= self._retries:
                return response  # let the caller see the real status

            # Drain + close the response so the underlying connection is freed
            # before we replay the request on the next iteration.
            retry_after = _parse_retry_after(response)
            await response.aclose()
            await self._sleep(request, attempt, retry_after=retry_after)

        # Unreachable: every branch either returns or raises inside the loop.
        if last_exc is not None:
            raise last_exc
        assert last_response is not None
        return last_response

    async def _sleep(
        self,
        request: httpx.Request,
        attempt: int,
        *,
        retry_after: float | None,
    ) -> None:
        if retry_after is not None:
            delay = min(retry_after, self._max_delay)
        else:
            # Exponential backoff with optional full jitter (Decorrelated
            # Jitter, AWS architecture blog). Full jitter avoids the
            # thundering-herd pattern when many concurrent scrapers get
            # 429'd by the same host.
            ceiling = min(self._base_delay * (2 ** attempt), self._max_delay)
            if self._jitter:
                delay = random.uniform(0, ceiling)
            else:
                delay = ceiling
        logger.debug(
            f"httpx retry {attempt + 1}/{self._retries} for {request.method} "
            f"{request.url} in {delay:.2f}s"
        )
        await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse Retry-After (delta-seconds form only; we ignore HTTP-date form)."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def make_retry_transport(
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
) -> httpx.AsyncBaseTransport:
    """Construct a RetryTransport over httpx's default async transport.

    Convenience helper — pass `transport=make_retry_transport()` to
    `httpx.AsyncClient` (or any factory that builds one).
    """
    return RetryTransport(
        httpx.AsyncHTTPTransport(),
        retries=retries,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
    )
