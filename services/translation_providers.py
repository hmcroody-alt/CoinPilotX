"""Provider-neutral adapters for PulseSoc content translation."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from services import undx_capabilities


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _record_character_spend(provider: str, text: str) -> None:
    """Record one billed Google Translation request of `len(text)` characters.

    §22: translation is AI spend. Google Cloud Translation v3 bills per character
    of submitted text, and until this existed the only trace of it was the HTTP
    log — so a month that translated ten million characters and a month that
    translated none produced identical spend reports.

    **Characters of the text we submitted**, not of the text we got back. That is
    what Google meters, and the two differ by a lot: German output is routinely
    30-40% longer than English input. Billing on the response would have
    overstated spend on exactly the language pairs used most.

    `mime_type='text/html'` is not discounted, either here or by Google — markup
    counts as characters. Stripping tags before counting would have produced a
    number that was tidier and wrong.

    Called only after `_request` has returned, so it counts requests Google
    accepted. A 429 or 401 raises before this point and is not billed, and the
    retry loop means a request that was refused twice and accepted once records
    once rather than three times.

    `undx_capabilities` has no price for this provider, so a call records as
    `uncosted_calls=1` with `cost_micro_usd=0`. That is the §34 answer: Google's
    per-million-character rate is public but has not been read and dated into the
    table, and inventing it here would make the month's total look complete while
    being wrong. `unpriced_providers()` still names `('translation', 'google')`,
    which is the list that enumerates the remaining work.

    `input_tokens` is deliberately left at zero. The character count goes in as
    `units`, which `record_spend` uses to price and does not persist — the ledger's
    only volume columns are `input_tokens` / `output_tokens` / `reasoning_tokens`,
    and a character is not a token. `month_snapshot` sums `input_tokens` across
    every kind into one per-provider figure, so putting characters there would
    corrupt the token total of a provider that also does chat. The consequence is
    real and is recorded in the census rather than hidden: until Google's
    per-million-character rate is in the table, the character volume of a
    translation is used and discarded, and the durable row carries the call count
    alone.

    Never raises — `record_spend` guarantees that, and a translation must not be
    lost to a bookkeeping failure.
    """
    undx_capabilities.record_spend(
        undx_capabilities.CALL_KIND_TRANSLATION, provider, units=len(text or ""),
    )


class TranslationProvider(Protocol):
    name: str

    def translate(self, text: str, source_language: str, target_language: str, *, mime_type: str = "text/plain") -> dict[str, Any]: ...
    def detect_language(self, text: str) -> dict[str, Any]: ...
    def supported_languages(self, display_language: str = "en") -> list[dict[str, Any]]: ...
    def health(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GoogleConfig:
    project_id: str
    location: str = "global"
    credentials_json: str = ""
    api_key: str = ""
    timeout_seconds: float = 10.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "GoogleConfig":
        return cls(
            project_id=os.getenv("GOOGLE_CLOUD_PROJECT_ID", "").strip(),
            location=os.getenv("GOOGLE_CLOUD_TRANSLATION_LOCATION", "global").strip() or "global",
            credentials_json=os.getenv("GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON", "").strip(),
            api_key=os.getenv("GOOGLE_CLOUD_TRANSLATION_API_KEY", "").strip(),
            timeout_seconds=max(1.0, min(float(os.getenv("TRANSLATION_REQUEST_TIMEOUT_SECONDS", "10") or 10), 30.0)),
            max_retries=max(0, min(int(os.getenv("TRANSLATION_MAX_RETRIES", "2") or 2), 3)),
        )

    @property
    def configured(self) -> bool:
        return bool(self.project_id and (self.credentials_json or self.api_key))


class GoogleAdvancedProvider:
    name = "google"
    provider_version = "translation-v3"
    _scope = "https://www.googleapis.com/auth/cloud-translation"

    def __init__(self, config: GoogleConfig | None = None, *, session=requests):
        self.config = config or GoogleConfig.from_env()
        self._session = session

    @property
    def parent(self) -> str:
        return f"projects/{self.config.project_id}/locations/{self.config.location}"

    def _authorization(self) -> tuple[dict[str, str], dict[str, str]]:
        if self.config.credentials_json:
            try:
                credentials = _cached_service_account_credentials(self.config.credentials_json, self._scope)
                return {"Authorization": f"Bearer {credentials.token}"}, {}
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError("invalid_credentials", "Google translation credentials could not be loaded.") from exc
        if self.config.api_key:
            return {}, {"key": self.config.api_key}
        raise ProviderError("provider_not_configured", "Google Cloud Translation is not configured.")

    def _request(self, method: str, suffix: str, *, payload: dict | None = None, params: dict | None = None) -> dict:
        if not self.config.configured:
            raise ProviderError("provider_not_configured", "Google Cloud Translation is not configured.")
        headers, auth_params = self._authorization()
        headers["Content-Type"] = "application/json"
        query = {**auth_params, **(params or {})}
        url = f"https://translation.googleapis.com/v3/{self.parent}{suffix}"
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._session.request(
                    method, url, headers=headers, params=query, json=payload,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code < 400:
                    return response.json()
                retryable = response.status_code in {408, 429, 500, 502, 503, 504}
                if not retryable or attempt >= self.config.max_retries:
                    if response.status_code == 429:
                        raise ProviderError("provider_quota_exceeded", "Google Cloud Translation quota exceeded.", retryable=True)
                    if response.status_code in {401, 403}:
                        raise ProviderError("invalid_credentials", "Google Cloud Translation rejected the credentials.")
                    raise ProviderError(
                        "provider_unavailable" if retryable else "provider_rejected",
                        "Google Cloud Translation did not accept the request.",
                        retryable=retryable,
                    )
            except ProviderError:
                raise
            except requests.Timeout as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise ProviderError("provider_timeout", "Google Cloud Translation timed out.", retryable=True) from exc
            except requests.ConnectionError as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise ProviderError("provider_unavailable", "Google Cloud Translation is unavailable.", retryable=True) from exc
            if attempt < self.config.max_retries:
                time.sleep(0.15 * (2 ** attempt))
        raise ProviderError("provider_unavailable", "Google Cloud Translation is unavailable.", retryable=True) from last_error

    def translate(self, text: str, source_language: str, target_language: str, *, mime_type: str = "text/plain") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": [text],
            "targetLanguageCode": target_language,
            "mimeType": mime_type,
        }
        if source_language and source_language != "auto":
            payload["sourceLanguageCode"] = source_language
        response = self._request("POST", ":translateText", payload=payload)
        # Above the response check, not below it. A 2xx that carries no usable
        # translation was still a request Google accepted and charged for; the
        # `invalid_provider_response` below is our judgement about the body, not
        # theirs about the bill.
        _record_character_spend(self.name, text)
        translations = response.get("translations") or []
        if not translations or not str(translations[0].get("translatedText") or "").strip():
            raise ProviderError("invalid_provider_response", "Google returned no translated text.")
        item = translations[0]
        return {
            "translated_text": str(item["translatedText"]),
            "detected_language": str(item.get("detectedLanguageCode") or source_language or "auto"),
            "provider": self.name,
            "provider_version": self.provider_version,
        }

    def detect_language(self, text: str) -> dict[str, Any]:
        response = self._request("POST", ":detectLanguage", payload={"content": text, "mimeType": "text/plain"})
        # Detection is billed per character at the same rate as translation, so it
        # is metered on the same footing. Leaving it out would have made a "cheap"
        # detect-then-translate flow look half as expensive as it is, since every
        # translation of unknown-language text pays for both.
        _record_character_spend(self.name, text)
        languages = response.get("languages") or []
        if not languages:
            raise ProviderError("invalid_provider_response", "Google returned no detected language.")
        return {"language": str(languages[0].get("languageCode") or ""), "confidence": languages[0].get("confidence")}

    def supported_languages(self, display_language: str = "en") -> list[dict[str, Any]]:
        response = self._request("GET", "/supportedLanguages", params={"displayLanguageCode": display_language})
        return [
            {
                "code": str(item.get("languageCode") or ""),
                "display_name": str(item.get("displayName") or item.get("languageCode") or ""),
                "translation_support": bool(item.get("supportSource") or item.get("supportTarget")),
                "source_support": bool(item.get("supportSource")),
                "target_support": bool(item.get("supportTarget")),
            }
            for item in response.get("languages") or []
            if item.get("languageCode")
        ]

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": self.config.configured}


# Cached credential objects, keyed by a hash of the service-account blob. Google
# access tokens live ~1h; refreshing only when invalid replaces the previous
# behaviour of re-parsing the JSON and minting a token on every request.
_CREDENTIALS_CACHE: dict[str, Any] = {}


def _cached_service_account_credentials(credentials_json: str, scope: str):
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    key = hashlib.sha256(credentials_json.encode("utf-8")).hexdigest()
    credentials = _CREDENTIALS_CACHE.get(key)
    if credentials is None:
        info = json.loads(credentials_json)
        private_key = info.get("private_key")
        # Railway/dotenv often stores the blob with escaped newlines; tolerate it.
        if isinstance(private_key, str) and "\\n" in private_key and "\n" not in private_key:
            info["private_key"] = private_key.replace("\\n", "\n")
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[scope])
        _CREDENTIALS_CACHE.clear()
        _CREDENTIALS_CACHE[key] = credentials
    if not credentials.valid:
        credentials.refresh(Request())
    return credentials


# Cached provider instance, invalidated whenever the env-derived config changes
# (GoogleConfig is a frozen dataclass, so equality compares every field).
_PROVIDER_CACHE: dict[str, GoogleAdvancedProvider] = {}


def configured_provider(name: str | None = None) -> TranslationProvider:
    selected = (name or os.getenv("TRANSLATION_PRIMARY_PROVIDER", "google")).strip().lower()
    if selected != "google":
        raise ProviderError("unsupported_provider", "The configured translation provider is not implemented.")
    config = GoogleConfig.from_env()
    cached = _PROVIDER_CACHE.get(selected)
    if cached is None or cached.config != config:
        cached = GoogleAdvancedProvider(config)
        _PROVIDER_CACHE[selected] = cached
    return cached


__all__ = [
    "GoogleAdvancedProvider", "GoogleConfig", "ProviderError", "TranslationProvider",
    "configured_provider",
]
