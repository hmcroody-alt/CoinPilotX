"""Configuration that has drifted away from what the fabric believes about itself.

Every other control in this subsystem checks whether a call *worked*. This one
checks whether the settings the controls depend on still mean what the controls
assume — because each of the failures below leaves the whole deployment green.
The three that are not hypothetical happened here:

  A Railway variable was set to a JSON configuration document that contained an
  API key rather than to the key. `undx_router._api_key` refuses to send it, so
  the provider reports "not configured" — indistinguishable, on every surface
  we have, from a provider nobody set up. It is not the same thing at all: one
  is a decision, the other is a provider that was paid for and is not running.

  Six `ANTHROPIC_*` variables point at `https://api.meta.ai` with
  `ANTHROPIC_MODEL=muse-spark-1.3-contributor`. The router already ignores them,
  deliberately. But they are live credentials aimed at a vendor's *training*
  tier, and anything in this codebase that reaches for the conventional
  Anthropic environment — a library, a future contributor, a copied snippet —
  gets that tier instead of the one the privacy matrix believes is in use.

  Nine chat calls in six modules went straight to a vendor's API, with their own
  model defaults, outside the router entirely — including a second provider
  router in `services/pulse_ai_provider_router.py` that reimplemented five
  adapters and three transports. This was found by writing the check, not before
  it. The consequences are the ones every control here assumes away: their spend
  is invisible to the ledger, so a budget is a budget over part of the spend;
  their failures never reach the breaker, so they keep paying full timeouts
  into a provider the router has already rested; and nothing classifies what
  they send, so a user's pasted message being screened for fraud reaches
  OpenAI without a privacy ceiling ever being consulted.

  Two are left, in `services/scam_shield.py` and
  `services/telegram_text_router.py`. The count is kept here rather than in a
  commit message because it is the number this check exists to drive to zero,
  and a stale "nine" reads as either a fixed problem or an unfixed one depending
  on which the reader is hoping for. The second router is gone: it now grounds
  and verifies, and delegates execution, which is why both of its
  `PULSE_AI_*_MODEL` defaults stopped appearing below — they were the pair that
  disagreed with `undx_router.PROVIDERS` about Claude and Gemini, naming two
  models that had been retired upstream and were 404ing on every request.

Two kinds of check, kept apart on purpose
-----------------------------------------
`check()` reads the live environment and can run inside the process, on a
schedule, or behind an admin route. `scan_source()` reads the repository and
can only run where a checkout exists. Merging them would make the runtime check
depend on files that are not deployed, and a check that cannot run in
production is not a production control.

Severity means what a person should do, not how bad it feels:

    CRITICAL   a guarantee this subsystem advertises is not in force
    WARNING    a control is weaker than its configuration implies
    INFO       true, worth knowing, nothing to do today
"""

from __future__ import annotations

import ast
import logging
import os
import re
from typing import Any

from services import undx_cost, undx_privacy

log = logging.getLogger(__name__)

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"
SEVERITIES: tuple[str, ...] = (CRITICAL, WARNING, INFO)


def _finding(severity: str, code: str, detail: str, fix: str,
             provider: str = "", where: str = "") -> dict[str, Any]:
    return {"severity": severity, "code": code, "provider": provider,
            "detail": detail, "fix": fix, "where": where}


def _router():
    import undx_router  # noqa: PLC0415

    return undx_router


# --------------------------------------------------------------- runtime checks

def _check_unsendable_keys(router) -> list[dict[str, Any]]:
    """A key that is set but cannot be sent, reported as if it were absent.

    `_api_key` returns empty for a value containing whitespace or newlines,
    which is correct — a header value with a newline makes the HTTP layer raise
    an exception quoting the offending value, which is how a credential reaches
    a log. But the consequence is that a fat-fingered paste and a deliberate
    decision not to configure a provider look identical everywhere downstream.
    """
    out = []
    for name in router.PROVIDERS:
        raw = router._raw_api_key(name)
        if raw and not router._api_key(name):
            out.append(_finding(
                CRITICAL, "unsendable_key",
                f"{name}: a credential is configured but cannot be sent — it "
                f"contains whitespace or a newline, so the provider is being "
                f"reported as unconfigured",
                f"Set {router.PROVIDERS[name].key_env} to the key alone, not to "
                f"a JSON document or a quoted, wrapped or padded value",
                provider=name, where=router.PROVIDERS[name].key_env))
    return out


#: Variables that a library or a copied snippet would reach for, mapped to the
#: vendor they conventionally mean. A value pointing somewhere else is not
#: merely untidy: it silently redirects anything that trusts the convention.
_CONVENTIONAL_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "ANTHROPIC_BASE_URL": ("anthropic.com",),
    "ANTHROPIC_API_URL": ("anthropic.com",),
    "OPENAI_BASE_URL": ("openai.com", "azure.com"),
    "OPENAI_API_BASE": ("openai.com", "azure.com"),
}


def _check_alien_credentials() -> list[dict[str, Any]]:
    out = []
    for var, expected in _CONVENTIONAL_ENDPOINTS.items():
        value = (os.getenv(var) or "").strip().lower()
        if value and not any(host in value for host in expected):
            out.append(_finding(
                CRITICAL, "alien_endpoint",
                f"{var} points at {value!r}, which is not "
                f"{' or '.join(expected)} — anything that trusts this variable "
                f"by convention will send credentials and content to a vendor "
                f"the privacy matrix has not classified for that purpose",
                f"Delete {var}, or set it to the vendor it names",
                where=var))

    model = (os.getenv("ANTHROPIC_MODEL") or "").strip()
    if model and undx_privacy.MODEL_CEILINGS.get(model.lower()) == undx_privacy.PRIVACY_SYNTHETIC:
        out.append(_finding(
            CRITICAL, "contributor_tier_credentials",
            f"ANTHROPIC_MODEL={model!r} names a contributor/training tier. The "
            f"router ignores these variables, but they are live credentials "
            f"aimed at a tier that may train on what it is sent",
            "Delete the ANTHROPIC_* variables; the router reads "
            "CLAUDE_AI_API and META_MODEL_API_KEY",
            where="ANTHROPIC_MODEL"))
    return out


def _check_privacy_ceiling_drift(router) -> list[dict[str, Any]]:
    """A model override that quietly lowers what its provider may be sent.

    `muse-spark-1.3-contributor` carries a lower ceiling than Meta itself, so
    setting `META_MUSE_MODEL` to it re-classifies every Meta route. The ceiling
    is enforced at refusal time, which is correct and also too late to be the
    only notice: by then someone has already configured a system that will
    refuse most of its own traffic and will look, from outside, like an outage.
    """
    out = []
    for name, config in router.PROVIDERS.items():
        model = router._model(name)
        base = undx_privacy.PROVIDER_CEILINGS.get(
            name, undx_privacy.UNDECLARED_PROVIDER_CEILING)
        effective = undx_privacy.provider_ceiling(name, model)
        if undx_privacy.rank(effective) < undx_privacy.rank(base):
            out.append(_finding(
                WARNING, "privacy_ceiling_lowered",
                f"{name}: model {model!r} lowers the privacy ceiling from "
                f"{base} to {effective}, so content this provider was cleared "
                f"for will now be refused",
                f"Set {config.model_env} back to {config.default_model!r}, or "
                f"accept the narrower ceiling deliberately",
                provider=name, where=config.model_env))
    return out


def _check_budget_coverage(router) -> list[dict[str, Any]]:
    """A dollar budget that can only see some of what it is supposed to limit."""
    out = []
    if not (undx_cost.global_cost_budget_usd() or undx_cost.provider_cost_budgets_usd()):
        return out
    models = router.configured_models()
    uncovered = undx_cost.uncovered_providers(models)
    if uncovered:
        severity = WARNING if undx_cost.strict_cost_budget() else CRITICAL
        out.append(_finding(
            severity, "budget_blind_spot",
            f"a dollar budget is configured, but {len(uncovered)} of "
            f"{len(models)} providers have no verified price: "
            f"{', '.join(uncovered)}. Spending on those is not counted, so the "
            f"budget restrains only the priced ones"
            + (" — strict mode refuses them instead, which means the budget "
               "silently removes them from routing"
               if undx_cost.strict_cost_budget() else ""),
            "Add prices to undx_cost.PRICE_PER_MILLION_USD, or set "
            "UNDX_MONTHLY_TOKEN_BUDGET, which every provider reports",
            where="UNDX_MONTHLY_COST_BUDGET_USD"))
    return out


def _check_shared_state(router) -> list[dict[str, Any]]:
    """Budgets and breakers that are being enforced once per process.

    The deployment runs four gunicorn workers plus five background workers. A
    limit enforced in each of them is nine limits, and the number in the
    configuration is not the number in force — which is the exact defect the
    shared ledger and shared breaker were built to remove.
    """
    from services import undx_health  # noqa: PLC0415

    out = []
    if undx_cost.budgets_configured() and not undx_cost.ledger_enabled():
        out.append(_finding(
            CRITICAL, "budget_without_ledger",
            "a budget is configured while UNDX_COST_LEDGER_ENABLED is off, so "
            "it is enforced against each process's own tally — nine processes "
            "against one limit is nine times the limit",
            "Set UNDX_COST_LEDGER_ENABLED=true, or remove the budget rather "
            "than leaving one that does not hold",
            where="UNDX_COST_LEDGER_ENABLED"))
    if not undx_health.shared_enabled():
        out.append(_finding(
            WARNING, "breaker_not_shared",
            f"UNDX_PROVIDER_HEALTH_SHARED is off, so the breaker threshold of "
            f"{undx_health.threshold()} applies per process and the half-open "
            f"probe is granted once per process rather than once per deployment",
            "Set UNDX_PROVIDER_HEALTH_SHARED=true, and raise "
            "UNDX_BREAKER_THRESHOLD if the shared count is too tight",
            where="UNDX_PROVIDER_HEALTH_SHARED"))
    return out


def _check_routing_reachability(router) -> list[dict[str, Any]]:
    """Providers that no request can reach, for reasons nothing else reports."""
    out = []
    reachable = [name for name in router.PROVIDERS
                 if router.provider_enabled(name) and router._api_key(name)]
    if not reachable:
        out.append(_finding(
            CRITICAL, "no_reachable_provider",
            "no provider is both enabled and holding a sendable key; every "
            "UNDX request will exhaust the chain",
            "Configure at least one provider key",
            where="*_API_KEY"))
    elif len(reachable) == 1:
        out.append(_finding(
            INFO, "single_provider",
            f"only {reachable[0]} is reachable, so failover has nowhere to go "
            f"and the breaker opening it is a full outage rather than a "
            f"degradation",
            "Configure a second provider",
            provider=reachable[0]))
    return out


def check() -> dict[str, Any]:
    """Every runtime finding, worst first. Never raises.

    A configuration audit that can fail is one more thing to page about, and
    the conditions it looks for are precisely the ones that make other code
    behave unexpectedly.
    """
    findings: list[dict[str, Any]] = []
    # Named here rather than read off the function, because `probe.__name__` is
    # evaluated inside the handler that exists to contain failures — and an
    # attribute lookup that raises there escapes to the outer handler, discards
    # every finding collected so far and returns a clean bill of health. A
    # failure inside the error path is the one place a safety check must not
    # have one.
    probes = (("unsendable_keys", _check_unsendable_keys),
              ("privacy_ceiling_drift", _check_privacy_ceiling_drift),
              ("budget_coverage", _check_budget_coverage),
              ("shared_state", _check_shared_state),
              ("routing_reachability", _check_routing_reachability),
              ("alien_credentials", lambda _router_arg: _check_alien_credentials()))
    try:
        router = _router()
    except Exception as exc:  # noqa: BLE001
        log.error("UNDX config drift audit could not load the router: %s",
                  type(exc).__name__)
        return _unusable(f"the router would not import ({type(exc).__name__})")

    for name, probe in probes:
        try:
            findings.extend(probe(router))
        except Exception as exc:  # noqa: BLE001
            log.warning("UNDX config drift check %s failed: %s",
                        name, type(exc).__name__)
            # Reported, not just logged. A check that did not run has not found
            # nothing — it has found nothing *yet*, and the difference is the
            # whole value of the audit. Leaving this in a log line would let the
            # summary say `ok` about a guarantee nobody verified.
            findings.append(_finding(
                WARNING, "check_did_not_run",
                f"the {name} check raised {type(exc).__name__}, so whatever it "
                f"would have found is unknown; this result is incomplete",
                "Fix the check — the conditions it looks for are the ones that "
                "make other code behave unexpectedly",
                where=f"services/undx_config_drift.py:_check_{name}"))

    return summarise(findings)


def summarise(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and an `ok` verdict over any list of findings.

    Shared by the runtime and source halves so the two cannot disagree
    about what severity makes a run fail.
    """
    order = {name: index for index, name in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["code"], f["provider"]))
    return {
        "ok": not any(f["severity"] == CRITICAL for f in findings),
        "findings": findings,
        "critical": sum(1 for f in findings if f["severity"] == CRITICAL),
        "warning": sum(1 for f in findings if f["severity"] == WARNING),
        "info": sum(1 for f in findings if f["severity"] == INFO),
    }


def _unusable(detail: str) -> dict[str, Any]:
    """The audit could not run at all.

    CRITICAL rather than an empty pass: an audit that cannot reach the thing it
    audits knows less than nothing about it, and `ok: True` from here would be
    the same fake green this whole module exists to remove.
    """
    return summarise([_finding(
        CRITICAL, "audit_unusable",
        f"the configuration audit could not run: {detail}. Nothing below was "
        f"checked",
        "Fix the import error; UNDX cannot route without the router either",
        where="services/undx_config_drift.py")])


# ---------------------------------------------------------------- source checks

#: Environment variables whose name says they select a model.
_MODEL_VAR_RE = re.compile(r"^[A-Z0-9_]*MODEL[A-Z0-9_]*$")

#: Files allowed to hold a model default. Exactly one, which is the point.
_CONFIG_AUTHORITY = ("undx_router.py",)

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "mobile",
              "mobile-native", "tests", "scripts", ".claude"}

_GETENV = {("os", "getenv"), ("environ", "get")}


def _model_defaults_in(tree: "ast.AST") -> list[tuple[int, str, str]]:
    """Model-selecting getenv calls with a literal default, as (line, var, default).

    The shape is a getenv on a name matching `_MODEL_VAR_RE` with a second
    argument — `OPENAI_MODEL` defaulting to a model id is the real instance, in
    `bot.py` and three other modules. Spelled out rather than shown, because the
    protection suite's environment-contract scanner is a regex over raw text and
    reads an example call as a real one; it then demands the invented variable
    be documented in `.env.example`. That scanner is deliberately over-broad —
    for its purpose a false positive costs a line of documentation and a false
    negative costs an operator an undebuggable silent feature — so the prose
    moves, not the scanner.

    Parsed rather than grepped. A regex over the raw text finds the pattern in
    docstrings, comments and in this module's own explanation of what it looks
    for — and a detector that reports its own documentation as a finding is one
    people learn to ignore, which is the same outcome as not having it.
    """
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        name = owner.id if isinstance(owner, ast.Name) else (
            owner.attr if isinstance(owner, ast.Attribute) else "")
        if (name, func.attr) not in _GETENV:
            continue
        var, default = node.args[0], node.args[1]
        if not (isinstance(var, ast.Constant) and isinstance(var.value, str)):
            continue
        if not (isinstance(default, ast.Constant) and isinstance(default.value, str)):
            continue
        if not default.value or not _MODEL_VAR_RE.match(var.value):
            continue
        found.append((node.lineno, var.value, default.value))
    return found


#: Vendor API hosts. A URL naming one of these, outside the config authority,
#: is a call that leaves this deployment without passing any of the controls.
_PROVIDER_HOSTS: tuple[str, ...] = (
    "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
    "api.deepseek.com", "api.groq.com", "api.perplexity.ai", "api.meta.ai",
)

#: Paths that do the same job the router does. Separated from the rest because
#: the distinction changes what a person should do about it: a second chat path
#: is a duplicate that should be routed, whereas embeddings and image
#: generation are capabilities the router does not offer at all, so there is
#: nothing to route them *to* yet.
_CHAT_PATHS: tuple[str, ...] = (
    "/chat/completions", "/v1/messages", ":generatecontent", "/v1/responses",
)


def _provider_urls_in(tree: "ast.AST") -> list[tuple[int, str]]:
    """String constants naming a vendor endpoint, as (line, url)."""
    found: list[tuple[int, str]] = []
    # An f-string's literal segments are Constant nodes *inside* the JoinedStr,
    # so walking naively reports the same URL twice — and reports the truncated
    # half at a lower severity, because the path that decides it is a chat call
    # lives on the other side of the interpolation. One call, one finding.
    inside_fstring = {id(part) for node in ast.walk(tree)
                      if isinstance(node, ast.JoinedStr)
                      for part in node.values}
    # A string that is evaluated and immediately discarded cannot be the target
    # of an HTTP call. That shape is a docstring — which is exactly where a
    # module explains the endpoint it calls, or where this detector explains
    # the hosts it looks for. Parsing instead of grepping removed the false
    # positives in comments; it does not remove these, because a docstring is a
    # real string node in the tree and only its *position* says it is prose.
    discarded = {id(node.value) for node in ast.walk(tree)
                 if isinstance(node, ast.Expr)}
    for node in ast.walk(tree):
        if id(node) in discarded:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in inside_fstring:
                continue
            value = node.value
        elif isinstance(node, ast.JoinedStr):
            # f-strings: the host is in a literal part even when the model is not.
            value = "".join(part.value for part in node.values
                            if isinstance(part, ast.Constant)
                            and isinstance(part.value, str))
        else:
            continue
        lowered = value.lower()
        if any(host in lowered for host in _PROVIDER_HOSTS) and "/" in value:
            found.append((node.lineno, value))
    return found


def scan_source(root: str) -> list[dict[str, Any]]:
    """Provider calls and model defaults that live outside the fabric.

    A second model default is a second answer to "which model does this
    deployment send", and the two agree only until one is edited. That is worth
    knowing on its own, but every instance found here turned out to be the
    visible symptom of something larger: the call it configures goes straight to
    the vendor. It is not metered by the ledger, so the budget is a budget over
    part of the spend. It does not consult the breaker, so it keeps paying full
    timeouts into a provider the router has already taken out. And it is not
    checked against a privacy ceiling, so whatever it sends — a user's question,
    a pasted message being screened for fraud — reaches a vendor without anyone
    having classified it.

    Reported by inspection rather than by import: these modules are not safe to
    import for a side-effect-free audit, and a check that had to import
    `bot.py` to run would not run.
    """
    out: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in sorted(filenames):
            if not filename.endswith(".py") or filename in _CONFIG_AUTHORITY:
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    tree = ast.parse(handle.read(), filename=path)
            except (OSError, SyntaxError):
                continue
            relative = os.path.relpath(path, root)
            if relative.startswith("services/undx_config_drift"):
                continue  # the detector names the hosts it looks for
            for number, var, default in _model_defaults_in(tree):
                out.append(_finding(
                    WARNING, "duplicate_model_default",
                    f"{relative}:{number} declares its own default for "
                    f"{var} ({default!r}); undx_router.PROVIDERS is the "
                    f"configuration authority and the two agree only until one "
                    f"is edited",
                    "Read the model through undx_router, or route the call "
                    "through undx_router.route_structured_request so it is "
                    "also metered and breaker-protected",
                    where=f"{relative}:{number}"))
            for number, url in _provider_urls_in(tree):
                chat = any(path_part in url.lower() for path_part in _CHAT_PATHS)
                out.append(_finding(
                    CRITICAL if chat else WARNING,
                    "unrouted_chat_call" if chat else "unmetered_provider_call",
                    f"{relative}:{number} calls {url} directly. It is outside "
                    f"the cost ledger, outside the circuit breaker and outside "
                    f"the privacy ceilings"
                    + (", and it duplicates the chat path undx_router already owns"
                       if chat else
                       " — the router has no equivalent capability, so this "
                       "cannot simply be routed"),
                    "Call undx_router.route_structured_request"
                    if chat else
                    "Meter it through undx_cost.record and check "
                    "undx_health.should_skip before calling",
                    where=f"{relative}:{number}"))
    return sorted(out, key=lambda f: (SEVERITIES.index(f["severity"]), f["where"]))


__all__ = [
    "CRITICAL", "WARNING", "INFO", "SEVERITIES", "check", "scan_source",
    "summarise",
]
