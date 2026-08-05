""".env.example is the deployment contract; it must describe the code that exists.

A variable that production code reads but `.env.example` never mentions is not a
documentation gap - it is a silent feature-off switch. PulseSoc registers its
optional route packs inside `except Exception` blocks so that one broken
subsystem cannot block boot. The cost of that design is that an unset variable
does not crash: the surface simply 404s, returns empty, or renders a confident
zero. An operator provisioning a fresh Railway environment has no way to
discover the key they were supposed to set.

When this suite was written, `.env.example` documented 129 keys while production
code read 379. The 295 undocumented keys included every credential for
Cloudflare R2 (all media, uploads and replays), Mux (live streaming),
TELEGRAM_BOT_TOKEN, REDIS_URL, the Twilio SMS sender, and the Stripe price IDs
that decide what a subscriber is actually charged for.

The suite also pins the storage alias contract. `services/media_storage.py`
resolves the bucket as `R2_BUCKET or S3_BUCKET` and the credentials as
`R2_* or AWS_*`. Three other call sites read only the R2_ names, so an
S3-configured deployment uploaded successfully while `media_service` built CDN
URLs with the bucket doubled into the path and `media_worker`'s readiness probe
reported "storage not configured" about storage that was working.

Scans source rather than importing: importing bot.py boots a 111k-line Flask
monolith with ~1,538 routes and live integrations.
"""

import collections
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"

# Directories that are not this application's production runtime.
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "mobile",          # legacy Expo 51 app
    "mobile-native",   # JS/TS, uses its own config, not os.getenv
    "scripts",         # ~200 one-off audit scripts; not deployed
    "tests",
}

# Helpers that wrap os.getenv and therefore hide the variable name from a plain
# grep for `getenv`. Each was confirmed by reading its body: every one of these
# passes its string argument(s) through to os.getenv or os.environ.get. Scanning
# for `getenv` alone under-reported production reads by 60 variables, including
# LIVESTREAM_AUDIO_TRACE_ENABLED - the observability switch for the exact live
# audio failure this suite was written alongside.
#
# If a new wrapper is added, add it here. A wrapper that is not listed makes this
# suite quietly incomplete in precisely the way it exists to prevent.
INDIRECT_ACCESSORS = (
    "_env_value",
    "_clean_env",
    "_configured",
    "_csv",
    "_enabled",
    "_env",
    "_env_bool",
    "_env_enabled",
    "_env_int",
    "_env_text",
    "_flag",
    "_guard_enabled",
    "_truthy_env",
    "env_text",
    "pulse_live_audio_v2_env_flag",
    "subflag_enabled",
)

READ_PATTERN = re.compile(
    r"""(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""
    r"""|environ\[\s*["']([A-Z][A-Z0-9_]{2,})["']"""
    r"""|\b(?:""" + "|".join(INDIRECT_ACCESSORS) + r""")\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""
)
# A wrapper may be handed several aliases: `_env_value("R2_BUCKET", "S3_BUCKET")`.
# The pattern above captures only the first, so trailing arguments are collected
# separately rather than silently dropped.
INDIRECT_CALL_PATTERN = re.compile(
    r"""\b(?:""" + "|".join(INDIRECT_ACCESSORS) + r""")\(([^)]{0,300})\)"""
)
ARGUMENT_NAME_PATTERN = re.compile(r"""["']([A-Z][A-Z0-9_]{2,})["']""")
# Mixed case is deliberate. `undx_router.PROVIDERS` reads the Gemini key from
# `Gemini_AI_API` - the variable really is spelled that way in the deployed
# environment, and an all-caps declaration pattern silently failed to see it.
DECLARED_PATTERN = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*=", re.M)


def _production_sources():
    """Walk with pruning rather than rglob.

    `.venv/` alone holds tens of thousands of files; rglob would descend into it
    and filter afterwards, which makes this suite too slow to run on every push.
    """
    import os

    for directory, subdirectories, filenames in os.walk(ROOT):
        subdirectories[:] = [d for d in subdirectories if d not in EXCLUDED_PARTS and not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".py"):
                yield pathlib.Path(directory) / filename


def _variables_read_by_production_code():
    read = collections.defaultdict(set)
    for path in _production_sources():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = str(path.relative_to(ROOT))
        for match in READ_PATTERN.finditer(text):
            name = match.group(1) or match.group(2) or match.group(3)
            read[name].add(relative)
        for match in INDIRECT_CALL_PATTERN.finditer(text):
            for name in ARGUMENT_NAME_PATTERN.findall(match.group(1)):
                read[name].add(relative)
    for name, sites in _dynamically_read_variables().items():
        read[name] |= sites
    return read


# Some variables are never named at a getenv() call site. `undx_router` reads
# `os.getenv(config.key_env)` where `key_env` came from a ProviderConfig literal,
# and `pulsesoc_intelligence_engine` declares `required_env` lists that a generic
# loop resolves. No regex over call sites can see those names, so the tables that
# declare them are treated as read sites in their own right.
DYNAMIC_DECLARATION_SITES = (
    # (path, pattern over the whole file, why this is a real read site)
    (
        "undx_router.py",
        re.compile(r"""ProviderConfig\(([^)]*)\)"""),
        "os.getenv(config.key_env) / os.getenv(config.model_env)",
    ),
    (
        "services/pulsesoc_intelligence_engine.py",
        re.compile(r""""required_env":\s*\[([^\]]*)\]"""),
        "the registry loop calls os.getenv() on each declared name",
    ),
)
# Inside a matched declaration, only these argument shapes are variable names.
DYNAMIC_NAME_PATTERN = re.compile(r"""["']([A-Za-z][A-Za-z0-9_]*_(?:API|KEY|MODEL|URL|TOKEN|SECRET|ID))["']""")


def _dynamically_read_variables():
    found = collections.defaultdict(set)
    for relative, pattern, _why in DYNAMIC_DECLARATION_SITES:
        path = ROOT / relative
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            for name in DYNAMIC_NAME_PATTERN.findall(match.group(1)):
                found[name].add(relative)
    return found


def _declared_variables():
    return set(DECLARED_PATTERN.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


# --- 1. The contract must be complete ----------------------------------------

def test_every_variable_production_code_reads_is_documented():
    read = _variables_read_by_production_code()
    assert len(read) > 300, (
        f"Only {len(read)} environment reads discovered - the scanner has stopped "
        "matching and this test is no longer measuring anything."
    )
    declared = _declared_variables()
    undocumented = sorted(name for name in read if name not in declared)
    assert not undocumented, (
        f"{len(undocumented)} variables are read at runtime but absent from "
        ".env.example, so an operator cannot know to set them and the affected "
        "feature fails silently rather than loudly:\n"
        + "\n".join(f"  {name}  (read in {', '.join(sorted(read[name])[:3])})" for name in undocumented[:40])
    )


def test_env_example_has_no_duplicate_keys():
    """A duplicate silently overrides the earlier value when the file is sourced."""
    names = DECLARED_PATTERN.findall(ENV_EXAMPLE.read_text(encoding="utf-8"))
    duplicates = sorted({name for name, count in collections.Counter(names).items() if count > 1})
    assert not duplicates, f"Duplicate keys in .env.example: {duplicates}"


def test_no_real_secret_values_are_committed():
    """The example file must stay an example."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    leaks = []
    for pattern, label in (
        (r"sk_live_[A-Za-z0-9]{10,}", "Stripe live secret key"),
        (r"sk_test_[A-Za-z0-9]{20,}", "Stripe test secret key"),
        (r"xkeysib-[A-Za-z0-9]{20,}", "Brevo API key"),
        (r"\b\d{9,10}:[A-Za-z0-9_-]{35}\b", "Telegram bot token"),
        (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    ):
        if re.search(pattern, text):
            leaks.append(label)
    assert not leaks, f".env.example contains what look like real credentials: {leaks}"


# --- 2. The storage alias contract must be honoured everywhere ----------------

BUCKET_READERS = (
    "services/media_storage.py",
    "services/media_service.py",
    "services/messenger_media_foundation.py",
    "media_worker.py",
)


def test_every_bucket_lookup_accepts_the_same_aliases():
    """`R2_BUCKET` alone is a half-configured deployment waiting to happen.

    media_storage resolves `R2_BUCKET or S3_BUCKET`. Any other module that reads
    only one of the two disagrees with where the object was actually written.
    """
    offenders = []
    for relative in BUCKET_READERS:
        path = ROOT / relative
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "R2_BUCKET" not in line:
                continue
            if "S3_BUCKET" in line:
                continue
            offenders.append(f"{relative}:{number}: {line.strip()[:110]}")
    assert not offenders, (
        "Bucket lookups that ignore the S3_BUCKET alias accepted by "
        "services/media_storage.py:\n  " + "\n  ".join(offenders)
    )


def test_media_worker_readiness_matches_the_credentials_media_storage_accepts():
    """A readiness probe that disagrees with the code it describes is worse than none."""
    source = (ROOT / "media_worker.py").read_text(encoding="utf-8")
    start = source.index("def dependency_snapshot(")
    snapshot = source[start : start + 2000]
    for alias in ("S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL"):
        assert alias in snapshot, (
            f"media_worker.dependency_snapshot() ignores {alias}, which "
            "services/media_storage.py accepts. A working S3-configured "
            "deployment would be reported as unconfigured."
        )


# --- 3. Readiness tables may only name variables that exist -------------------
#
# Two modules publish provider-readiness rows to the admin surfaces:
# `services/backend_management_registry.py` (EXTERNAL_SERVICE_CHECKS, rendered on
# the Command Center) and `services/pulsesoc_reliability.py`
# (PROVIDER_REQUIREMENTS, served by the deep health endpoint). Both declare, per
# provider, the variables that must be set.
#
# A name in those tables that no code reads is not a typo with cosmetic
# consequences: the provider can never reach "configured", so the row is a
# permanent red light that no environment change can clear. Cloudflare R2 sat in
# exactly that state, in both tables, because both named `R2_BUCKET_NAME` while
# the runtime reads `R2_BUCKET or S3_BUCKET`. An operator looking at a red R2 row
# on a working deployment learns to distrust the whole page.


def _readiness_table_names(relative, marker, stop):
    """Every quoted uppercase name inside a declared readiness table."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    start = source.index(marker)
    block = source[start : source.index(stop, start)]
    return block


def test_runtime_readiness_tables_only_name_variables_that_are_read():
    read = _variables_read_by_production_code()
    block = _readiness_table_names(
        "services/backend_management_registry.py", "EXTERNAL_SERVICE_CHECKS", "def all_features"
    )
    phantoms = []
    for line in block.splitlines():
        if '"env"' not in line and not line.strip().startswith(("(", '"R2_', '"S3_', '"AWS_')):
            continue
        # Build credentials are read by the release pipeline, not by this service.
        if '"scope": "build"' in line:
            continue
        for name in re.findall(r'"([A-Z][A-Z0-9_]{2,})"', line):
            if name not in read:
                phantoms.append(name)
    assert not phantoms, (
        "EXTERNAL_SERVICE_CHECKS requires runtime variables that no production code "
        f"reads, so those providers can never report configured: {sorted(set(phantoms))}"
    )


def test_provider_requirements_only_name_variables_that_are_read():
    read = _variables_read_by_production_code()
    block = _readiness_table_names(
        "services/pulsesoc_reliability.py", "PROVIDER_REQUIREMENTS", "def _requirement_met"
    )
    phantoms = sorted(
        {name for name in re.findall(r'"([A-Z][A-Z0-9_]{2,})"', block) if name not in read}
    )
    assert not phantoms, (
        "pulsesoc_reliability.PROVIDER_REQUIREMENTS names variables no production "
        f"code reads; those providers report config_missing forever: {phantoms}"
    )


def test_r2_readiness_accepts_the_same_aliases_media_storage_accepts():
    """Both readiness tables must agree with services/media_storage.py, not with each other."""
    for relative, marker, stop in (
        ("services/backend_management_registry.py", "EXTERNAL_SERVICE_CHECKS", "def all_features"),
        ("services/pulsesoc_reliability.py", "PROVIDER_REQUIREMENTS", "def _requirement_met"),
    ):
        block = _readiness_table_names(relative, marker, stop)
        assert "R2_BUCKET_NAME" not in block, (
            f"{relative} still requires R2_BUCKET_NAME, which no code reads."
        )
        for alias in ("S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            assert alias in block, (
                f"{relative} does not accept {alias}, which services/media_storage.py "
                "accepts. An S3-configured deployment would be reported unconfigured."
            )


# --- 4. Gates that silently disable a headline signal stay documented ---------

SILENT_FEATURE_GATES = (
    # Default-off, and the surface it feeds reports a plausible zero when unset.
    "PULSESOC_VISITOR_LOGGING_ENABLED",
)


def test_silent_feature_gates_are_documented_with_their_consequence():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for gate in SILENT_FEATURE_GATES:
        assert gate in text
        index = text.index(gate)
        # The surrounding comment block must state what goes wrong when it is off.
        context = text[max(0, index - 700) : index]
        assert "#" in context, f"{gate} is declared with no explanatory comment."


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
