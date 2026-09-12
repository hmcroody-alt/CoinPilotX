#!/usr/bin/env python3
"""Ask each provider, live, whether it can be *made* to return JSON.

`services/scam_shield.py` sends OpenAI's ``response_format={"type": "json_object"}``
and then `json.loads` the reply. Routing that call needs to know which providers can
honour the same requirement, and the answer cannot be taken from documentation: this
repository has already been burned twice by trusting a vendor's own list. Gemini's
ListModels advertises `gemini-2.5-flash` to this key and `generateContent` 404s on it;
`sonar-reasoning` is in Perplexity's docs and 400s. So the capability table in
`undx_router.PROVIDERS` is populated from this probe's output, and re-running it is how
that table stays honest.

Two results are recorded per provider and they are not the same question:

* **enforced** — the request carrying the structured-output parameter was *accepted*.
  This is the one that matters. It means the provider has been told the answer must be
  JSON, so a parse failure downstream is a provider fault rather than a caller's
  optimism.
* **parsed** — the text that came back happens to `json.loads`. A provider with no JSON
  mode at all will often manage this for a prompt as easy as the one below, which is
  exactly why "parsed" must not be read as a capability. Claiming a provider on the
  strength of a lucky parse is how a security control gets routed to a model that will
  return prose the first time the input is interesting.

A provider is eligible for a structured request only when *enforced* is true. Anything
else — a 400 rejecting the parameter, a 402 because the account is unfunded, a missing
key — is UNKNOWN, and §34's rule about unknown prices applies to unknown capabilities
for the same reason: a blank is not a zero and it is not a yes.

Run it with the production credentials and nothing else:

    railway run --service CoinPilotX python3 scripts/undx_structured_output_capability_probe.py

The prompt is synthetic. That is deliberate and not incidental — `GROQ_AI_API` is
compromised pending rotation and Meta's Contributor tier trains on its inputs, so a
probe that carried real user content could not be run against either.
"""

from __future__ import annotations

import json
import os
import sys

import requests


SYSTEM = "You return JSON and nothing else. No prose, no code fences."
USER = 'Return exactly this object: {"ok": true, "n": 2}'

#: Sent to every OpenAI-compatible surface. The one parameter `scam_shield` already
#: depends on, so "does this provider accept it" is literally the migration question.
JSON_OBJECT = {"type": "json_object"}

#: Perplexity's dialect, and the reason this probe has two of them. It rejects
#: `json_object` with a 400 that names its own accepted set — `json_schema` or `text` —
#: so "Perplexity cannot do structured output" would have been the wrong conclusion from
#: the first run, drawn from a real 400 about the wrong parameter. A probe that tries one
#: spelling measures the spelling, not the capability.
JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {"schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}},
        "required": ["ok", "n"],
    }},
}


def _probe_openai_compatible(name: str, url: str, key: str, model: str,
                             extra: dict | None = None,
                             response_format: dict | None = None) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER}],
        "max_tokens": 100,
        "temperature": 0,
        "response_format": response_format or JSON_OBJECT,
    }
    payload.update(extra or {})
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    if response.status_code != 200:
        return {"enforced": False, "status": response.status_code,
                "detail": response.text[:200]}
    data = response.json()
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return {"enforced": True, "status": 200, "parsed": _parses(text), "text": text[:120]}


def _probe_claude(key: str, model: str) -> dict:
    """Anthropic's Messages API has no JSON mode, so the probe asks whether it 400s.

    Sending an unknown top-level field is the check: a provider that rejects the
    parameter cannot be told the answer must be JSON, and one that silently ignores it
    is worse, because the request looks honoured and nothing is enforcing anything.
    Either way the answer here is not "enforced".
    """
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"},
        json={"model": model, "system": SYSTEM, "max_tokens": 100, "temperature": 0,
              "messages": [{"role": "user", "content": USER}],
              "response_format": JSON_OBJECT},
        timeout=60,
    )
    if response.status_code != 200:
        return {"enforced": False, "status": response.status_code,
                "detail": response.text[:200]}
    data = response.json()
    text = "".join(part.get("text") or "" for part in (data.get("content") or []))
    # Accepted the field, which for this API means it was ignored. Recorded as
    # ignored rather than enforced.
    return {"enforced": False, "status": 200, "ignored_parameter": True,
            "parsed": _parses(text), "text": text[:120]}


def _probe_gemini(key: str, model: str) -> dict:
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
              "contents": [{"role": "user", "parts": [{"text": USER}]}],
              "generationConfig": {"temperature": 0, "maxOutputTokens": 100,
                                   "responseMimeType": "application/json"}},
        timeout=60,
    )
    if response.status_code != 200:
        return {"enforced": False, "status": response.status_code,
                "detail": response.text[:200]}
    data = response.json()
    candidate = (data.get("candidates") or [{}])[0]
    text = "".join(part.get("text") or ""
                   for part in ((candidate.get("content") or {}).get("parts") or []))
    return {"enforced": True, "status": 200, "parsed": _parses(text), "text": text[:120]}


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except Exception:
        return False
    return True


PROBES = [
    ("openai", "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini",
     lambda key, model: _probe_openai_compatible(
         "openai", "https://api.openai.com/v1/chat/completions", key, model)),
    ("claude", "CLAUDE_AI_API", "CLAUDE_MODEL", "claude-haiku-4-5",
     _probe_claude),
    ("gemini", "Gemini_AI_API", "GEMINI_MODEL", "gemini-flash-lite-latest",
     _probe_gemini),
    ("deepseek", "DEEPSEEK_AI_API", "DEEPSEEK_MODEL", "deepseek-chat",
     lambda key, model: _probe_openai_compatible(
         "deepseek", "https://api.deepseek.com/chat/completions", key, model)),
    ("groq", "GROQ_AI_API", "GROQ_MODEL", "llama-3.1-8b-instant",
     lambda key, model: _probe_openai_compatible(
         "groq", "https://api.groq.com/openai/v1/chat/completions", key, model)),
    # `reasoning_effort` is "minimal", not "none", and that is the second thing this
    # probe found rather than a detail of the probe. `undx_router.META_REASONING_EFFORTS`
    # lists "none" first and its comment says the list came from a 400 naming the full
    # set — but the live API rejects it for muse-spark-1.3 and names six values without
    # it. An operator who sets META_MUSE_REASONING_EFFORT=none passes the router's own
    # validation and 400s every Meta call.
    ("meta", "META_MODEL_API_KEY", "META_MUSE_MODEL", "muse-spark-1.3",
     lambda key, model: _probe_openai_compatible(
         "meta", "https://api.meta.ai/v1/chat/completions", key, model,
         extra={"reasoning_effort": "minimal"})),
    ("perplexity", "PERPLEXITY_API_KEY", "PERPLEXITY_MODEL", "sonar",
     lambda key, model: _probe_openai_compatible(
         "perplexity", "https://api.perplexity.ai/chat/completions", key, model,
         response_format=JSON_SCHEMA)),
]


def main() -> int:
    results: dict[str, dict] = {}
    for name, key_env, model_env, default_model, probe in PROBES:
        key = os.getenv(key_env) or ""
        model = os.getenv(model_env) or default_model
        if not key:
            results[name] = {"enforced": False, "status": "no_credential"}
        else:
            try:
                results[name] = probe(key, model)
            except Exception as exc:  # noqa: BLE001 - a live probe reports, never raises
                results[name] = {"enforced": False, "status": "exception",
                                 "detail": str(exc)[:200]}
        results[name]["model"] = model
        row = results[name]
        verdict = "ENFORCED" if row.get("enforced") else "unknown"
        print(f"{name:12} {verdict:9} status={row.get('status')} "
              f"parsed={row.get('parsed')} model={model}")
        if row.get("detail"):
            print(f"{'':12} detail={row['detail']}")

    eligible = sorted(n for n, r in results.items() if r.get("enforced"))
    print()
    print(f"structured-output eligible: {eligible}")
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
