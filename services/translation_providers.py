"""Provider-neutral adapters for PulseSoc content translation."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


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
                from google.auth.transport.requests import Request
                from google.oauth2 import service_account

                info = json.loads(self.config.credentials_json)
                credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=[self._scope]
                )
                credentials.refresh(Request())
                return {"Authorization": f"Bearer {credentials.token}"}, {}
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
                    raise ProviderError(
                        "provider_unavailable" if retryable else "provider_rejected",
                        "Google Cloud Translation did not accept the request.",
                        retryable=retryable,
                    )
            except ProviderError:
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
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


def configured_provider(name: str | None = None) -> TranslationProvider:
    selected = (name or os.getenv("TRANSLATION_PRIMARY_PROVIDER", "google")).strip().lower()
    if selected != "google":
        raise ProviderError("unsupported_provider", "The configured translation provider is not implemented.")
    return GoogleAdvancedProvider()


__all__ = [
    "GoogleAdvancedProvider", "GoogleConfig", "ProviderError", "TranslationProvider",
    "configured_provider",
]
