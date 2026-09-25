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
import functools
import io
import pathlib
import re
import tokenize

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
    # `_env_float` was missing while two modules already used it, which hid
    # UNDX_EMBEDDING_MONTHLY_BUDGET_USD and every commerce-discovery ratio knob.
    "_env_float",
    "_env_int",
    "_env_text",
    "_flag",
    "_guard_enabled",
    "_truthy_env",
    # `services/business_os/suppliers/policy.enabled` - the only public name here.
    # It hid every CJ rollout gate except the two read through a bare os.getenv.
    "enabled",
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

    `followlinks=True` because the mutation harness builds its sandbox by making
    real directories only along the path to the mutated file and symlinking every
    sibling - so `services/` is a symlink there, and `os.walk` would list it as a
    directory and then decline to enter it. That silently cut the scan down to the
    root-level modules, which made three tests fail in the sandbox on an
    *unmutated* file: a broken baseline that reports the harness's own blind spot
    as a finding about the code. The repository itself contains no symlinks, so
    this changes nothing about a normal run, and the `EXCLUDED_PARTS` pruning plus
    the leading-dot filter still keep the walk off `.venv/` and `.git/`.
    """
    import os

    for directory, subdirectories, filenames in os.walk(ROOT, followlinks=True):
        subdirectories[:] = [d for d in subdirectories if d not in EXCLUDED_PARTS and not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".py"):
                yield pathlib.Path(directory) / filename


def _blank_span(lines, start, end):
    """Overwrite a token's characters with spaces, in place, keeping every offset.

    Spaces rather than deletion so that character offsets still line up with the
    original file, and line terminators are stepped over rather than blanked so
    that row numbers keep addressing the same rows. A multi-line token therefore
    stays multi-line and the lines it covered stay exactly as long as they were.
    """
    (start_row, start_column), (end_row, end_column) = start, end
    for row in range(start_row, end_row + 1):
        line = lines[row - 1]
        for terminator in ("\r\n", "\n", "\r"):
            if line.endswith(terminator):
                body, ending = line[: -len(terminator)], terminator
                break
        else:
            body, ending = line, ""
        first = min(start_column if row == start_row else 0, len(body))
        last = min(end_column if row == end_row else len(body), len(body))
        if last > first:
            body = body[:first] + " " * (last - first) + body[last:]
        lines[row - 1] = body + ending


def _statement_strings(tokens):
    """Yield the (start, end) of every string literal that is an entire statement.

    A string that occupies a statement by itself is a docstring or a block of
    prose left where a reader will find it. Either way the interpreter evaluates
    it to a value and immediately discards it, so no name inside one can reach
    `os.environ`. That is the same argument that licenses blanking comments, and
    it is the only argument used here: this does not touch strings that are
    arguments, elements, or operands, because `os.getenv("X")` is a string
    literal too and dropping those would blind the scanner completely.

    Detected through `tokenize` rather than `ast` on purpose. `ast` reports
    columns as UTF-8 byte offsets and splits lines on a different set of
    characters than `str.splitlines()` does - U+2028 among them, which `bot.py`
    has carried before - so resolving AST positions against split lines needs a
    second coordinate system that agrees with the first only by luck. `tokenize`
    is the one the comment stripper already uses.
    """
    # A statement can only begin after one of these, or at the start of a file.
    boundaries = {tokenize.ENCODING, tokenize.NEWLINE, tokenize.NL,
                  tokenize.INDENT, tokenize.DEDENT}
    significant = [t for t in tokens if t.type != tokenize.COMMENT]
    at_statement_start = True
    index = 0
    while index < len(significant):
        token = significant[index]
        if token.type == tokenize.STRING and at_statement_start:
            # Adjacent literals concatenate, so a docstring may be several
            # tokens. Take the whole run, then confirm the statement ends there.
            run = index
            while run < len(significant) and significant[run].type == tokenize.STRING:
                run += 1
            if run < len(significant) and significant[run].type == tokenize.NEWLINE:
                for string_token in significant[index:run]:
                    yield string_token.start, string_token.end
                index = run
                at_statement_start = False
                continue
        at_statement_start = token.type in boundaries
        index += 1


def _without_docstrings(text):
    """Blank out docstrings and bare prose strings, preserving every offset.

    The scanner is a set of regexes over source text, and prose about a read is
    not a read. `services/pulse_control_plane/drift.py` explains why its own
    audit deliberately matches loosely by contrasting it with the precise form,
    quoting a placeholder variable name to do so. Nothing evaluates that
    sentence, but the regex matched it and the suite demanded that `.env.example`
    document a variable called NAME. That was worked around by rewording the
    paragraph, which left the next such sentence free to fail the gate again.

    This deletes the false positive without weakening discovery: every indirect
    accessor call, alias list and dynamic declaration this suite relies on is
    real code, and none of it is a string standing alone as a statement.

    Falls back to the untouched text when a file will not tokenize, matching
    `_without_comments` - an unparseable file over-reports rather than silently
    contributing nothing, because over-reporting fails loudly here and
    under-reporting is invisible.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return text
    # Split the way the tokenizer read it, so a row number cannot address a
    # different line here than it did there. `str.splitlines()` breaks on
    # separators `readline` passes straight through.
    lines = io.StringIO(text).readlines()
    for start, end in _statement_strings(tokens):
        _blank_span(lines, start, end)
    return "".join(lines)


def _without_comments(text):
    """Blank out `#` comments in place, preserving every other character offset.

    The scanner is a set of regexes over source text, and a regex cannot tell code
    from prose *about* code. `services/command_center_client.py` documents a read it
    deliberately removed:

        # `ai_configured()` used to live here as
        # `ai_enabled() and bool(_env_text("PULSE_AI_PROVIDER"))`.

    Nothing evaluates that line, but `_env_text` is an indirect accessor and the
    regex matched it, so the suite demanded that `.env.example` document a variable
    whose entire purpose was to no longer be read. Adding it would have instructed
    operators to set a dead key; deleting the comment would have deleted the
    explanation. Neither is a fix, because the defect is in the scanner.

    Comments only. String literals are left alone deliberately - `os.getenv("X")`
    *is* a string literal, so dropping strings would blind the scanner completely.
    That distinction is the whole point: a comment cannot be evaluated, a string
    constant can be. Offsets are preserved rather than the comment excised so that
    any future line/column reporting keeps pointing at the right place.

    Falls back to the untouched text when a file will not tokenize. That direction
    is chosen on purpose: an unparseable file then over-reports rather than silently
    contributing nothing, and over-reporting fails loudly here while under-reporting
    is invisible.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return text
    lines = text.splitlines(keepends=True)
    for token in tokens:
        if token.type == tokenize.COMMENT:
            _blank_span(lines, token.start, token.end)
    return "".join(lines)


def _variables_read_by_production_code():
    """Discovered variables, mapped to the files that read them.

    Cached, and copied on the way out. Three tests in this file need the whole scan,
    and it now tokenizes every production file twice over - once to blank comments
    and once to blank whole-statement strings - so repeating it per test cost more
    than the original scan did. The copy is what makes the cache safe to share: a
    caller that mutated the result would otherwise change what a later test sees,
    and the tests that consume this are looking for an empty set, which is also what
    a poisoned cache would produce.
    """
    return {name: set(sites) for name, sites in _scan_production_sources().items()}


@functools.lru_cache(maxsize=1)
def _scan_production_sources():
    read = collections.defaultdict(set)
    for path in _production_sources():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _without_docstrings(_without_comments(text))
        relative = str(path.relative_to(ROOT))
        for match in READ_PATTERN.finditer(text):
            name = match.group(1) or match.group(2) or match.group(3)
            read[name].add(relative)
        for match in INDIRECT_CALL_PATTERN.finditer(text):
            for name in ARGUMENT_NAME_PATTERN.findall(match.group(1)):
                read[name].add(relative)
    for name, sites in _dynamically_read_variables().items():
        read[name] |= sites
    for name, sites in _names_held_in_constants().items():
        read[name] |= sites
    return read


# Some variables are never named at a getenv() call site. `undx_router` reads
# `os.getenv(config.key_env)` where `key_env` came from a ProviderConfig literal,
# and `pulsesoc_intelligence_engine` declares `required_env` lists that a generic
# loop resolves. No regex over call sites can see those names, so the tables that
# declare them are treated as read sites in their own right.
# Inside a matched declaration, only these argument shapes are variable names.
DYNAMIC_NAME_PATTERN = re.compile(r"""["']([A-Za-z][A-Za-z0-9_]*_(?:API|KEY|MODEL|URL|TOKEN|SECRET|ID))["']""")
# A declaration table whose entries are *named* by their first argument rather than
# suffixed by role. `undx_brain.config.CATALOG` is the case: every entry is a
# `Flag("NAME", kind, default, purpose, ...)`, and no name in it ends in _KEY or
# _URL, so the suffix pattern above sees none of them.
DECLARED_FLAG_NAME_PATTERN = re.compile(r"""["']([A-Z][A-Z0-9_]{2,})["']""")

DYNAMIC_DECLARATION_SITES = (
    # (path, pattern over the whole file, name pattern within a match, why it is a read)
    (
        "undx_router.py",
        re.compile(r"""ProviderConfig\(([^)]*)\)"""),
        DYNAMIC_NAME_PATTERN,
        "os.getenv(config.key_env) / os.getenv(config.model_env)",
    ),
    (
        "services/pulsesoc_intelligence_engine.py",
        re.compile(r""""required_env":\s*\[([^\]]*)\]"""),
        DYNAMIC_NAME_PATTERN,
        "the registry loop calls os.getenv() on each declared name",
    ),
    (
        # `resolve()` iterates CATALOG and calls `source.get(flag.name)` with
        # `source = os.environ`. Every entry is therefore a read, and the name appears
        # only as `Flag`'s first argument - never at a getenv() call site. 82 flags were
        # invisible here, including UNDX_BRAIN_ENABLED, the master switch for the whole
        # layer, and every fail-closed authorisation flag beneath it. Matching `Flag(`
        # with its first argument rather than the whole table keeps the purpose and
        # default strings that follow from contributing names of their own.
        "services/undx_brain/config.py",
        re.compile(r"""\bFlag\(\s*(["'][A-Z][A-Z0-9_]{2,}["'])"""),
        DECLARED_FLAG_NAME_PATTERN,
        "config.resolve() reads os.environ.get(flag.name) for every CATALOG entry",
    ),
)


def _dynamically_read_variables():
    found = collections.defaultdict(set)
    for relative, pattern, name_pattern, _why in DYNAMIC_DECLARATION_SITES:
        path = ROOT / relative
        if not path.exists():
            continue
        source = _without_docstrings(_without_comments(path.read_text(encoding="utf-8")))
        for match in pattern.finditer(source):
            for name in name_pattern.findall(match.group(1)):
                found[name].add(relative)
    return found


# A variable whose name is held in a constant is read without its name ever appearing
# at the call site:
#
#     AGENT_ENABLED_ENV = "UNDX_AGENT_ENABLED"
#     ...
#     _truthy(os.getenv(AGENT_ENABLED_ENV))
#
# Every regex above looks for a string literal *inside* the accessor call, so it sees
# nothing here. That hid 71 reads, and the modules using the idiom are the ones where
# a silently-unset variable costs the most: `services/undx_agent_policy.py` (every
# UNDX write-authorisation flag and kill switch), `marketplace_payout_worker.py`
# (MARKETPLACE_PAYOUT_WORKER_OWNER_AUTHORIZED, which is what stops money moving
# without the owner's say-so), and the subject-salt and token-secret pairs behind ad
# and commerce-discovery privacy.
#
# The join is deliberately two-sided: a constant is only a read site once it is
# actually handed to an accessor. Collecting every `NAME = "UPPER_STRING"` assignment
# instead would pull in error codes, table names, column names and event types - the
# repo is full of uppercase string constants that have nothing to do with the
# environment, and each one would become a phantom `.env.example` key.
CONSTANT_ASSIGNMENT_PATTERN = re.compile(
    r"""^[ \t]*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["']([A-Z][A-Z0-9_]{2,})["']\s*(?:#.*)?$""",
    re.M,
)
_ACCESSOR_ALTERNATION = "|".join(("getenv", r"environ\.get") + INDIRECT_ACCESSORS)


def _names_held_in_constants(sources=None):
    """Resolve `accessor(CONSTANT)` back to the string literal the constant holds.

    `sources` is an iterable of `(name, text)` pairs, defaulting to every production
    file. The seam exists so the join can be tested on a fixture in both directions -
    a constant that reaches an accessor, and an uppercase constant that does not.

    Two passes, because the idiom crosses files. `services/undx_agent_policy.py`
    defines the constant and reads it in the same module, but a constant that is
    imported elsewhere and read there would be missed by a purely per-file join. So
    per-file mappings are applied first, then a repo-wide mapping is consulted for
    identifiers that resolve to exactly one literal everywhere they are assigned.

    An identifier assigned two different literals in two different files is left to
    its per-file meaning only. Guessing between them would attribute a read to
    whichever file was walked last, which is worse than the gap: the name would still
    be demanded of `.env.example`, but the reported read site would be a file that
    does not read it, and an operator chasing it down would find nothing there.
    """
    per_file = {}
    global_map = collections.defaultdict(set)
    for relative, raw in sources if sources is not None else _production_texts():
        text = _without_docstrings(_without_comments(raw))
        pairs = dict(CONSTANT_ASSIGNMENT_PATTERN.findall(text))
        per_file[relative] = (text, pairs)
        for identifier, name in pairs.items():
            global_map[identifier].add(name)
    unambiguous = {i: next(iter(n)) for i, n in global_map.items() if len(n) == 1}

    found = collections.defaultdict(set)
    for relative, (text, pairs) in per_file.items():
        known = {**unambiguous, **pairs}
        if not known:
            continue
        identifiers = "|".join(re.escape(i) for i in sorted(known, key=len, reverse=True))
        uses = re.compile(
            r"""(?:%s)\(\s*(%s)\s*[,)]""" % (_ACCESSOR_ALTERNATION, identifiers)
            + r"""|environ\[\s*(%s)\s*\]""" % identifiers
        )
        for match in uses.finditer(text):
            identifier = match.group(1) or match.group(2)
            found[known[identifier]].add(relative)
    return found


def _production_texts():
    for path in _production_sources():
        try:
            yield str(path.relative_to(ROOT)), path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def _declared_variables():
    return set(DECLARED_PATTERN.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


# --- 1. The contract must be complete ----------------------------------------

def test_a_name_held_in_a_constant_is_read_and_a_bare_constant_is_not():
    """The two-sided join, asserted in both directions on one fixture.

    Collecting every `NAME = "UPPER_STRING"` assignment would be the easy version and
    would fill `.env.example` with error codes and table names. Requiring the accessor
    call is what makes the collector specific, so the test that it *does not* fire for
    an unused constant matters as much as the test that it fires for a used one.
    """
    source = (
        'import os\n'
        'KILL_SWITCH_ENV = "REAL_KILL_SWITCH"\n'
        'TTL_ENV = "REAL_TTL_SECONDS"\n'
        'ERROR_CODE = "NOT_AN_ENVIRONMENT_VARIABLE"\n'
        'STATE = "PENDING_REVIEW"\n'
        'def go():\n'
        '    if os.getenv(KILL_SWITCH_ENV):\n'
        '        return None\n'
        '    return os.environ[TTL_ENV], ERROR_CODE, STATE\n'
    )
    found = _names_held_in_constants([("fixture.py", source)])
    assert set(found) == {"REAL_KILL_SWITCH", "REAL_TTL_SECONDS"}, (
        "The constant-name collector is not joining assignments to accessor calls. "
        f"Expected the two names that reach os.getenv/os.environ, got {sorted(found)}. "
        "Extra names mean every uppercase string constant in the repo is about to be "
        "demanded of .env.example; missing names mean a kill switch can be deleted "
        "from the deployment without this suite noticing."
    )
    # The fixture above proves the collector works; it says nothing about whether the
    # scanner still calls it. Unwiring it would lower the discovered count, and a lower
    # count cannot fail the completeness test - fewer reads to document is indis-
    # tinguishable from a complete .env.example. So the wiring is pinned separately,
    # against the reads that would go quiet first.
    read = _variables_read_by_production_code()
    unwired = [name for name in ("UNDX_WRITE_KILL_SWITCH", "UNDX_AGENT_WRITES_ENABLED",
                                 "MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN")
               if name not in read]
    assert not unwired, (
        f"{unwired} are read through a name-holding constant but are not in the scan, "
        "so _names_held_in_constants is no longer wired into "
        "_variables_read_by_production_code. Every UNDX kill switch is discovered that "
        "way and none of them would be missed by the completeness test."
    )


def test_every_undx_brain_catalog_flag_is_discovered():
    """The catalog is the read site, so the scanner must see all of it, not most of it.

    `config.resolve()` calls `os.environ.get(flag.name)` for every entry, so the count
    discovered here has to equal the number of entries declared. Pinning the count
    rather than a sample is deliberate: the failure this closed was 82 flags reduced to
    9, and a sample of three would have passed throughout.
    """
    source = (ROOT / "services" / "undx_brain" / "config.py").read_text(encoding="utf-8")
    declared = set(re.findall(r"""\bFlag\(\s*["']([A-Z][A-Z0-9_]{2,})["']""", source))
    assert len(declared) > 70, (
        f"Only {len(declared)} Flag declarations found in undx_brain/config.py - the "
        "table has been reshaped and the scanner's pattern no longer matches it."
    )
    read = _variables_read_by_production_code()
    missing = sorted(name for name in declared if name not in read)
    assert not missing, (
        f"{len(missing)} declared Brain flags are not discovered as reads, so they can "
        f"be dropped from .env.example without failing this suite: {missing[:15]}"
    )
    assert "UNDX_BRAIN_ENABLED" in read, (
        "UNDX_BRAIN_ENABLED is the master switch for the whole Brain layer and is not "
        "being discovered. It was previously seen only because an example in the "
        "module's own docstring happened to match the call-site regex."
    )


def test_the_comment_stripper_hides_prose_without_hiding_code():
    """The stripper sits upstream of every count in this file, so it gets its own test.

    A stripper that removed too much would make `undocumented` empty for the wrong
    reason, and empty is exactly what this suite reports on success. The failure mode
    is not hypothetical: the first version of this helper rebuilt the source by
    joining tokens with spaces, which turned `getenv("X")` into `getenv ("X")`. The
    regex requires `getenv(` with no gap, so 561 of 582 real reads vanished and the
    suite went green having measured almost nothing.

    So both directions are asserted on one fixture: the commented read must
    disappear, and the live read beside it must survive byte-for-byte.
    """
    source = (
        'import os\n'
        'TIMEOUT = os.getenv("LIVE_REAL_VARIABLE")\n'
        '# removed: os.getenv("COMMENTED_OUT_VARIABLE") is no longer read\n'
        'OTHER = os.environ["SECOND_REAL_VARIABLE"]  # os.getenv("TRAILING_COMMENT_VAR")\n'
    )
    cleaned = _without_comments(source)
    found = {m.group(1) or m.group(2) or m.group(3)
             for m in READ_PATTERN.finditer(cleaned)}
    assert found == {"LIVE_REAL_VARIABLE", "SECOND_REAL_VARIABLE"}, (
        "The comment stripper is not separating evaluated reads from prose about "
        f"reads. Expected the two live reads and neither commented one, got {found}. "
        "If real names are missing the stripper is corrupting code and every count "
        "in this file is understated."
    )
    assert len(cleaned) == len(source), (
        "The stripper changed the length of the source, so character offsets no "
        "longer line up with the original file."
    )


def test_the_docstring_stripper_hides_prose_without_hiding_code():
    """Same two directions as the comment stripper, for the same reason.

    The fixture is the shape that actually failed: a module docstring that
    explains a mechanism by quoting the call it is *not* making. The placeholder
    must disappear, and every real read around it - including the ones whose
    names only ever appear as arguments to an indirect accessor - must survive.
    """
    source = (
        '"""Substring matching, not os.getenv("NAME") matching.\n'
        '\n'
        'Mentions _env_bool("PROSE_ONLY_VARIABLE") too, and evaluates neither.\n'
        '"""\n'
        'import os\n'
        'TIMEOUT = os.getenv("LIVE_REAL_VARIABLE")\n'
        '\n'
        'def handler():\n'
        '    "A one-line docstring naming os.environ[\'DOCSTRING_SUBSCRIPT\']."\n'
        '    return _env_bool("INDIRECT_REAL_VARIABLE")\n'
    )
    cleaned = _without_docstrings(_without_comments(source))
    found = {m.group(1) or m.group(2) or m.group(3)
             for m in READ_PATTERN.finditer(cleaned)}
    assert found == {"LIVE_REAL_VARIABLE", "INDIRECT_REAL_VARIABLE"}, (
        "The docstring stripper is not separating evaluated reads from prose "
        f"about reads. Expected the two live reads and no prose name, got {found}."
    )
    assert len(cleaned) == len(source), (
        "The stripper changed the length of the source, so character offsets no "
        "longer line up with the original file."
    )
    assert cleaned.count("\n") == source.count("\n"), (
        "The stripper removed a line break, so row numbers no longer address the "
        "rows they did in the original file."
    )


def test_the_docstring_stripper_leaves_declared_name_tables_alone():
    """Blanking whole string *statements* must not blank strings that are data.

    This suite discovers a large share of its names from declaration tables -
    alias lists handed to a wrapper, and the `required_env` style registries
    resolved by a generic loop. Those are string literals sitting on lines of
    their own inside brackets, which is what a docstring looks like to anything
    cruder than a tokenizer. If they were blanked the gate would go quiet about
    precisely the indirect reads it was extended to catch.

    The constant on the last line is the sharpest case, and the reason this test
    is not just the inverse of the one above: `GATE_FLAG = "..."` is a string
    that ends its own statement, so a stripper that asked only "does a NEWLINE
    follow?" would blank it. That shape is how several modules here hold a
    variable name away from its call site, which makes it the exact indirection
    a stricter scan is most likely to lose.
    """
    source = (
        'REQUIRED = [\n'
        '    "TABLE_ELEMENT_VARIABLE",\n'
        '    "SECOND_TABLE_ELEMENT",\n'
        ']\n'
        'BUCKET = _env_value(\n'
        '    "R2_BUCKET_ALIAS",\n'
        '    "S3_BUCKET_ALIAS",\n'
        ')\n'
        'GATE_FLAG = "MODULE_CONSTANT_VARIABLE"\n'
    )
    cleaned = _without_docstrings(source)
    assert cleaned == source, (
        "String literals used as data were blanked. Only a string that is an "
        "entire statement may be treated as prose."
    )
    aliases = {name for match in INDIRECT_CALL_PATTERN.finditer(cleaned)
               for name in ARGUMENT_NAME_PATTERN.findall(match.group(1))}
    assert aliases == {"R2_BUCKET_ALIAS", "S3_BUCKET_ALIAS"}


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
