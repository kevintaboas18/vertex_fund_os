"""Resilient HTTP provider base: cache-first fetch with retry/backoff.

`Provider.get_json` never raises for network/HTTP failures — it returns
`None` on exhaustion, and callers are expected to map that to
`wbj.core.nullstates.NullState.MISSING`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from wbj.providers.cache import Cache

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_REDACTED_PARAMS = frozenset({"apikey", "token", "api_key"})


def _redact_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Copy `params` with sensitive values masked, safe to put in log text."""
    if not params:
        return {}
    return {
        k: ("***" if k.lower() in _REDACTED_PARAMS else v) for k, v in params.items()
    }


#: Request parameters that must never reach a cache key: secrets, and the
#: response-format switches that do not change the data.
_CACHE_KEY_EXCLUDED_PARAMS = frozenset({"apikey", "api_key", "token", "file_type"})


def _qualified_cache_key(cache_key: str, params: dict[str, Any]) -> str:
    """A cache key that distinguishes requests differing only in params.

    The key used to be the caller's label alone, so every request to the
    same endpoint shared one entry no matter what it asked for. Raising
    the annual-history limit from 6 to 11 changed nothing: the six-row
    response came back from cache while the API served eleven. The same
    collision had already been patched once at a single call site, where
    a one-year and a three-year price window shared an `ohlcv_daily`
    entry -- this is that bug's cause rather than another instance of it.

    Secrets are excluded from the digest, so a rotated API key does not
    invalidate every cached response and no key material is written to
    disk in a filename.
    """
    material = {
        k: v for k, v in sorted(params.items())
        if k.lower() not in _CACHE_KEY_EXCLUDED_PARAMS
    }
    if not material:
        return cache_key
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"{cache_key}_{digest}"



class Provider:
    """Base class for wbj data providers.

    Subclasses build request URLs/params and call `get_json`, which
    handles cache-first serving and resilient retries uniformly.
    """

    def __init__(
        self,
        settings: Any,
        cache: Cache,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.client = client if client is not None else httpx.Client()

    def _sleep(self, seconds: float) -> None:
        """Sleep for `seconds`. Isolated so tests can monkeypatch it out."""
        time.sleep(seconds)

    def get_text(
        self,
        url: str,
        params: dict[str, Any],
        cache_key: str,
        ticker: str,
        max_age_days: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> str | None:
        """Fetch a non-JSON document (an EDGAR filing) with the same
        cache-first, retry/backoff behaviour as `get_json`.

        The cache stores JSON, so the body is wrapped in `{"text": ...}`
        on the way in and unwrapped on the way out.
        """
        payload = self.get_json(
            url, params, cache_key, ticker,
            max_age_days=max_age_days, headers=headers, _as_text=True,
        )
        if isinstance(payload, dict):
            return payload.get("text")
        return None

    @staticmethod
    def _qualified_cache_key(cache_key: str, params: dict[str, Any]) -> str:
        return _qualified_cache_key(cache_key, params)

    def get_bytes(self, url: str, headers: dict[str, str] | None = None,
                  timeout: float = 300.0) -> bytes | None:
        """Descarga un binario grande (p. ej. el zip trimestral de 13F).

        NO usa el cache de `get_json`: ese guarda JSON, y estos archivos pesan
        ~100 MB. Quien llama decide donde persistirlo. Devuelve None ante
        cualquier fallo -- nunca lanza, igual que `get_json`.
        """
        try:
            response = self.client.get(url, headers=headers, timeout=timeout,
                                       follow_redirects=True)
        except httpx.TransportError as exc:
            logger.warning("wbj provider byte fetch failed url=%s error=%s", url, exc)
            return None
        if response.status_code >= 400:
            logger.info("wbj provider byte fetch status=%d url=%s",
                        response.status_code, url)
            return None
        return response.content

    def get_json(
        self,
        url: str,
        params: dict[str, Any],
        cache_key: str,
        ticker: str,
        max_age_days: float | None = None,
        headers: dict[str, str] | None = None,
        _as_text: bool = False,
    ) -> dict | None:
        """Fetch JSON, cache-first, with retry/backoff on transient failures.

        If a cache entry exists for (ticker, cache_key) and is fresh enough
        (age <= max_age_days, or max_age_days is None), it is returned
        without touching the network. Otherwise up to 3 attempts are made
        against `url`, backing off 0.5s/1s/2s between attempts on 5xx
        responses or httpx transport errors (including timeouts). 4xx
        responses are treated as non-retryable client errors. Returns None
        (never raises) if the fetch ultimately fails; a successful response
        is written to cache before being returned.

        `headers`, if given, is passed through to the underlying request
        (e.g. a required `User-Agent` per SEC EDGAR's fair-access policy).
        Existing callers that don't pass `headers` are unaffected.
        """
        cache_key = _qualified_cache_key(cache_key, params)
        age = self.cache.age_days(ticker, cache_key)
        if age is not None and (max_age_days is None or age <= max_age_days):
            return self.cache.get(ticker, cache_key)

        safe_params = _redact_params(params)

        for attempt in range(_MAX_ATTEMPTS):
            is_last_attempt = attempt == _MAX_ATTEMPTS - 1
            try:
                response = self.client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                logger.warning(
                    "wbj provider request failed (attempt %d/%d) url=%s "
                    "params=%s error=%s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    url,
                    safe_params,
                    exc,
                )
                if not is_last_attempt:
                    self._sleep(_BACKOFF_SECONDS[attempt])
                continue

            if response.status_code < 400:
                if _as_text:
                    payload = {"text": response.text}
                    self.cache.put(ticker, cache_key, payload)
                    return payload
                try:
                    payload = response.json()
                except ValueError:
                    logger.warning(
                        "wbj provider returned malformed JSON status=%d url=%s "
                        "params=%s",
                        response.status_code,
                        url,
                        safe_params,
                    )
                    return None
                self.cache.put(ticker, cache_key, payload)
                return payload

            if response.status_code < 500:
                logger.warning(
                    "wbj provider client error status=%d url=%s params=%s",
                    response.status_code,
                    url,
                    safe_params,
                )
                return None

            logger.warning(
                "wbj provider server error (attempt %d/%d) status=%d url=%s "
                "params=%s",
                attempt + 1,
                _MAX_ATTEMPTS,
                response.status_code,
                url,
                safe_params,
            )
            if not is_last_attempt:
                self._sleep(_BACKOFF_SECONDS[attempt])

        return None
