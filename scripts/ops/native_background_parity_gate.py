#!/usr/bin/env python3
"""Assert the web backdrop tokens still equal the native ones.

`web/src/theme/pulseBackground.ts` is a verbatim copy of
`mobile-native/src/theme/pulseBackground.ts`, plus a copy of
`galacticProfileFor` from native's `ThemeContext.tsx`. Two copies of one table
is a divergence waiting to happen, and this one diverges *quietly*: a node
moved three percent on one platform and not the other is invisible in review,
invisible in tests, and obvious only when someone opens the app and the site
side by side.

Unlike the palette gate, both sides here are TypeScript with the same shape, so
the claim is identity rather than agreement-between-representations. That makes
a single parser correct -- what would be wrong is a parser that quietly matches
nothing, because two empty extractions compare equal and the gate would pass
for the one reason it must never pass. Every table therefore has to come back
non-empty on both sides or the run is could-not-check, never a pass.

Exit codes:
  0  verified -- every table matches
  1  verified-broken -- a real divergence, named
  3  could-not-check -- the gate could not read one of the sides
"""

from __future__ import annotations

import pathlib
import re
import sys

EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_NO_DATA = 3

REPO = pathlib.Path(__file__).resolve().parents[2]
NATIVE_TOKENS = REPO / "mobile-native" / "src" / "theme" / "pulseBackground.ts"
NATIVE_CONTEXT = REPO / "mobile-native" / "src" / "theme" / "ThemeContext.tsx"
WEB_TOKENS = REPO / "web" / "src" / "theme" / "pulseBackground.ts"

#: The exported tables that must be identical on both sides.
TABLES = [
    "PULSE_BACKGROUND_COLORS",
    "PULSE_BACKGROUND_CEILINGS",
    "PULSE_BACKGROUND_CYCLES",
    "PULSE_BACKGROUND_GEOMETRY",
    "PULSE_BACKGROUND_NODES",
    "PULSE_BACKGROUND_LINES",
    "PULSE_BACKGROUND_SURFACES",
    "PULSE_BACKGROUND_VARIANTS",
    "PULSE_BACKGROUND_INTENSITY_SCALE",
]


class CouldNotCheck(Exception):
    """The gate could not read what it needs. Never the same as 'no problems'."""


# --------------------------------------------------------------------------
# A small reader for the subset of TypeScript these tables are written in.
# --------------------------------------------------------------------------


def _strip_comments(source: str) -> str:
    """Remove `//` and `/* */` comments without touching string literals.

    Written as a scanner rather than a regex because a regex that removes `//`
    to end-of-line also eats the `//` inside `"https://..."`, and the colours
    table is one refactor away from containing a URL in a comment.
    """
    out = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char in "\"'`":
            quote = char
            out.append(char)
            index += 1
            while index < length:
                if source[index] == "\\":
                    out.append(source[index : index + 2])
                    index += 2
                    continue
                out.append(source[index])
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "/":
            while index < length and source[index] != "\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "*":
            end = source.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


class _Reader:
    """Recursive-descent reader for object/array literals of plain values."""

    def __init__(self, text: str, position: int = 0):
        self.text = text
        self.position = position

    def skip(self) -> None:
        while self.position < len(self.text) and self.text[self.position] in " \t\r\n,":
            self.position += 1

    def peek(self) -> str:
        self.skip()
        return self.text[self.position] if self.position < len(self.text) else ""

    def value(self):
        self.skip()
        if self.position >= len(self.text):
            raise CouldNotCheck("unexpected end of literal")
        char = self.text[self.position]
        if char == "{":
            return self.obj()
        if char == "[":
            return self.array()
        if char in "\"'`":
            return self.string()
        return self.bare()

    def obj(self) -> dict:
        assert self.text[self.position] == "{"
        self.position += 1
        result: dict = {}
        while True:
            if self.peek() == "}":
                self.position += 1
                return result
            key = self.key()
            if self.peek() != ":":
                raise CouldNotCheck(f"expected `:` after key {key!r}")
            self.position += 1
            result[key] = self.value()

    def array(self) -> list:
        assert self.text[self.position] == "["
        self.position += 1
        result: list = []
        while True:
            if self.peek() == "]":
                self.position += 1
                return result
            result.append(self.value())

    def key(self) -> str:
        self.skip()
        char = self.text[self.position]
        if char in "\"'`":
            return self.string()
        start = self.position
        while self.position < len(self.text) and (
            self.text[self.position].isalnum() or self.text[self.position] in "_$"
        ):
            self.position += 1
        if start == self.position:
            raise CouldNotCheck(f"could not read an object key at offset {start}")
        return self.text[start:self.position]

    def string(self) -> str:
        quote = self.text[self.position]
        self.position += 1
        chunks = []
        while self.position < len(self.text):
            char = self.text[self.position]
            if char == "\\":
                chunks.append(self.text[self.position + 1])
                self.position += 2
                continue
            if char == quote:
                self.position += 1
                return "".join(chunks)
            chunks.append(char)
            self.position += 1
        raise CouldNotCheck("unterminated string literal")

    def bare(self):
        """A number, a boolean, or an identifier reference like `X.base`."""
        start = self.position
        while self.position < len(self.text) and (
            self.text[self.position].isalnum() or self.text[self.position] in "._-+$"
        ):
            self.position += 1
        raw = self.text[start:self.position].strip()
        if not raw:
            raise CouldNotCheck(f"could not read a value at offset {start}")
        if raw == "true":
            return True
        if raw == "false":
            return False
        try:
            return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
        except ValueError:
            # An identifier reference. Kept as a marker so it can be resolved
            # against the colours table rather than compared as text -- native
            # writing `COLORS.base` and web writing "#101A4A" is the same
            # colour, and a gate that called that a divergence would be noise.
            return _Ref(raw)


class _Ref:
    """An unresolved `IDENTIFIER.path` reference found in a literal."""

    __slots__ = ("path",)

    def __init__(self, path: str):
        self.path = path

    def __eq__(self, other):
        return isinstance(other, _Ref) and other.path == self.path

    def __repr__(self):
        return f"<ref {self.path}>"


def read_table(source: str, name: str):
    """The literal assigned to `export const <name>`, as Python data.

    Raises CouldNotCheck rather than returning None or {} when the declaration
    is absent. An empty table is indistinguishable from a matching one once it
    reaches the comparison, so it must not be allowed to get there.
    """
    match = re.search(
        rf"export\s+const\s+{re.escape(name)}\b[^=]*=\s*", source
    )
    if not match:
        raise CouldNotCheck(f"could not find `export const {name}` in the source")
    reader = _Reader(source, match.end())
    char = reader.peek()
    if char not in "{[":
        raise CouldNotCheck(f"`{name}` is not an object or array literal")
    value = reader.value()
    if not value:
        raise CouldNotCheck(f"`{name}` parsed as empty, which cannot be compared")
    return value


def resolve_refs(value, colours: dict):
    """Replace `PULSE_BACKGROUND_COLORS.x` markers with the colour itself."""
    if isinstance(value, _Ref):
        head, _, tail = value.path.partition(".")
        if head == "PULSE_BACKGROUND_COLORS" and tail in colours:
            return colours[tail]
        return value
    if isinstance(value, dict):
        return {key: resolve_refs(item, colours) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_refs(item, colours) for item in value]
    return value


# --------------------------------------------------------------------------
# The galactic profile table, which lives in a function rather than a literal.
# --------------------------------------------------------------------------

_PROFILE_ARM = re.compile(
    r"""mode\s*===\s*"(?P<mode>[a-z_]+)"\s*\)\s*return\s*(?P<body>\{[^}]*\})""",
    re.VERBOSE,
)
_PROFILE_FALLBACK = re.compile(
    r"""scheme\s*===\s*"light"\s*\?\s*(?P<light>\{[^}]*\})\s*:\s*(?P<dark>\{[^}]*\})""",
    re.VERBOSE,
)


def _match_forward(source: str, start: int, opener: str, closer: str) -> int:
    """Index just past the `closer` matching the `opener` at `start`."""
    depth = 0
    for index in range(start, len(source)):
        if source[index] == opener:
            depth += 1
        elif source[index] == closer:
            depth -= 1
            if depth == 0:
                return index + 1
    raise CouldNotCheck(f"unbalanced `{opener}` starting at offset {start}")


def _function_body(source: str, name: str) -> str:
    """The text between a function's own braces, and nothing after them.

    Brace-counted rather than cut at the first `\\n}`. The line-anchored form
    only terminates a function declared at column zero: indent the function by
    one level and the body runs on into whatever follows, so a *later*
    function's `return { ... }` gets read as another arm of this table. Both
    real files happen to declare it at column zero today, which means that bug
    would have sat here passing until the day someone wrapped it in a module.
    """
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", source)
    if not match:
        raise CouldNotCheck(f"could not find `{name}`")
    after_params = _match_forward(source, match.end() - 1, "(", ")")
    brace = source.find("{", after_params)
    if brace == -1:
        raise CouldNotCheck(f"`{name}` has no body")
    end = _match_forward(source, brace, "{", "}")
    return source[brace + 1 : end - 1]


def read_profiles(source: str) -> dict:
    """`galacticProfileFor`'s decision table, keyed by theme mode.

    Native writes it as a chain of early returns and the web copy mirrors that
    shape, so it is read as a table rather than executed. The `system` fallback
    is recorded under two synthetic keys because it is genuinely two answers.
    """
    body = _function_body(source, "galacticProfileFor")

    table = {}
    for arm in _PROFILE_ARM.finditer(body):
        table[arm.group("mode")] = _Reader(arm.group("body")).value()

    fallback = _PROFILE_FALLBACK.search(body)
    if fallback:
        table["system:light"] = _Reader(fallback.group("light")).value()
        table["system:dark"] = _Reader(fallback.group("dark")).value()

    if not table:
        raise CouldNotCheck(
            "`galacticProfileFor` parsed as an empty table -- the gate cannot "
            "tell a mirrored profile from an unread one"
        )
    return table


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _is_number(value) -> bool:
    """A leaf the float tolerance may be applied to.

    `bool` is a subclass of `int`, so the obvious `isinstance(value, (int,
    float))` sweeps `true` and `false` into the numeric branch and compares them
    with a 1e-9 tolerance. That is not merely untidy: `pulse: true` and
    `enabled: false` are the flags that decide whether a node breathes and
    whether White gets a backdrop at all, and routing them through the numeric
    arm means the branch that claims to check non-numeric leaves never sees
    them. Mutation testing found this -- killing the numeric comparison turned
    the two boolean tests red, and killing the equality comparison did not.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _describe(path: str, native, web) -> list[str]:
    """Every leaf where the two sides differ, named by its full path."""
    if isinstance(native, dict) and isinstance(web, dict):
        problems = []
        for key in sorted(set(native) | set(web)):
            if key not in native:
                problems.append(f"{path}.{key}: absent in native, present in web")
            elif key not in web:
                problems.append(f"{path}.{key}: present in native, absent in web")
            else:
                problems.extend(_describe(f"{path}.{key}", native[key], web[key]))
        return problems
    if isinstance(native, list) and isinstance(web, list):
        if len(native) != len(web):
            return [f"{path}: native has {len(native)} entries, web has {len(web)}"]
        problems = []
        for index, (left, right) in enumerate(zip(native, web)):
            problems.extend(_describe(f"{path}[{index}]", left, right))
        return problems
    if _is_number(native) and _is_number(web):
        # 0.2 and 0.20 are the same ceiling; int 1 and float 1.0 are the same
        # scale. Comparing the source text instead would make a reformat a
        # divergence, and a gate that cries over formatting gets switched off.
        if abs(float(native) - float(web)) > 1e-9:
            return [f"{path}: native {native}, web {web}"]
        return []
    if native != web:
        return [f"{path}: native {native!r}, web {web!r}"]
    return []


def compare(native_source: str, web_source: str, context_source: str) -> tuple[list[str], int]:
    """Problems found, and how many leaf values were actually compared."""
    native_colours = read_table(native_source, "PULSE_BACKGROUND_COLORS")
    web_colours = read_table(web_source, "PULSE_BACKGROUND_COLORS")

    problems: list[str] = []
    compared = 0

    for name in TABLES:
        native_table = resolve_refs(read_table(native_source, name), native_colours)
        web_table = resolve_refs(read_table(web_source, name), web_colours)
        compared += _count_leaves(native_table)
        problems.extend(_describe(name, native_table, web_table))

    native_profiles = read_profiles(context_source)
    web_profiles = read_profiles(web_source)
    compared += _count_leaves(native_profiles)
    problems.extend(_describe("galacticProfileFor", native_profiles, web_profiles))

    return problems, compared


def _count_leaves(value) -> int:
    if isinstance(value, dict):
        return sum(_count_leaves(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_leaves(item) for item in value)
    return 1


def main() -> int:
    try:
        for path in (NATIVE_TOKENS, NATIVE_CONTEXT, WEB_TOKENS):
            if not path.exists():
                raise CouldNotCheck(f"missing source file: {path.relative_to(REPO)}")
        native_source = _strip_comments(NATIVE_TOKENS.read_text(encoding="utf-8"))
        web_source = _strip_comments(WEB_TOKENS.read_text(encoding="utf-8"))
        context_source = _strip_comments(NATIVE_CONTEXT.read_text(encoding="utf-8"))
        problems, compared = compare(native_source, web_source, context_source)
    except CouldNotCheck as error:
        print(f"native-background-parity: COULD NOT CHECK -- {error}", file=sys.stderr)
        print(
            "This is not a pass. The backdrop may or may not match; the gate "
            "could not tell, which is the state it exists to make visible.",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    if not compared:
        print(
            "native-background-parity: COULD NOT CHECK -- zero values compared",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    if problems:
        print("native-background-parity: the web backdrop no longer matches native.")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nThe app and the site would render different fields. Update "
            f"{WEB_TOKENS.relative_to(REPO)} to match native, or -- if native "
            "changed on purpose -- carry the change across deliberately."
        )
        return EXIT_DIVERGED

    print(
        f"native-background-parity: verified {compared} values across "
        f"{len(TABLES)} tables and the galactic profile."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
