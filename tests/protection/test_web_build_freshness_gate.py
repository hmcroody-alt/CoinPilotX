"""Locks for the gate that says the committed SPA was built from the committed source.

Why this gate needs its own tests
---------------------------------
The gate exists because a committed build artifact goes stale *silently*: edit
`web/src/`, forget to rebuild, commit, and every signal stays green while the
deployed bundle is last week's. The gate is the only thing that looks.

Which makes the gate itself the same kind of risk, one level up. Every way it
can break -- a source list that stops matching, a record that fails to load, a
tree that resolves to no files -- makes it *greener*, not louder. Running it on
a healthy tree proves nothing; it would pass just as cheerfully with its
collector returning `{}`. So these drive `main()` against synthesised trees and
assert it goes red where it must.

The one property NOT locked here is that the Python hashing agrees with
`web/scripts/fingerprint.mjs`. That needs Node, which the protection suite does
not have. It is verified structurally instead, by the ordering in
`.github/workflows/web-build.yml`: the JS writes the record, then this gate
recomputes it, so any divergence between the two implementations fails CI on
the very next build rather than sitting latent.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "ops" / "web_build_freshness_gate.py"
FINGERPRINT_JS = REPO / "web" / "scripts" / "fingerprint.mjs"


def _load():
    spec = importlib.util.spec_from_file_location("web_build_freshness_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GATE = _load()

SOURCES = {
    "src/App.tsx": "export const App = () => null;\n",
    "src/styles/tokens.css": ":root { --space-1: 8px; }\n",
    "index.html": "<!doctype html><div id=root></div>\n",
    "vite.config.ts": "export default {};\n",
    "tsconfig.json": "{}\n",
    "package.json": '{"name":"x"}\n',
    "package-lock.json": '{"lockfileVersion":3}\n',
    "scripts/fingerprint.mjs": "// fingerprint\n",
}


class _Tree:
    """A synthetic repo with a web/ tree and a build record, wired into the gate."""

    def __init__(self, sources=None, record="auto"):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.web = self.dir / "web"
        self.record_path = self.dir / "static" / "app" / "build-source-hash.json"
        # Created unconditionally, including for the empty-source case. An
        # earlier version only created it as a side effect of writing a file,
        # so `sources={}` produced a tree with no `web/` at all -- and the
        # empty-tree test then exited via the missing-directory guard without
        # ever reaching the branch it was named for. It passed against a gate
        # whose empty-tree guard had been deleted.
        self.web.mkdir(parents=True, exist_ok=True)
        self.written = {}
        for rel, text in (SOURCES if sources is None else sources).items():
            self.write(rel, text)
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        if record == "auto":
            self.write_record_from_tree()
        elif record is not None:
            self.record_path.write_text(record, encoding="utf-8")

    def write_record_from_tree(self):
        """Build the record independently of the gate's own collector.

        Using `GATE.collect_sources()` here would make the comparison
        tautological: a collector that stops seeing a file drops it from both
        the record and the check, so the two still agree and the gate reports
        fresh. That is exactly how a `rglob` -> `glob` regression survived an
        earlier version of these tests. The record is therefore assembled from
        the files this harness knows it wrote, hashed with plain `hashlib`.
        """
        files = {
            f"web/{rel}": hashlib.sha256(text.encode("utf-8")).hexdigest()
            for rel, text in sorted(self.written.items())
        }
        self.record_path.write_text(json.dumps({
            "version": 1, "algorithm": "sha256",
            "fingerprint": GATE.fingerprint_of(files),
            "file_count": len(files), "files": files,
        }), encoding="utf-8")

    @contextlib.contextmanager
    def applied(self):
        saved = (GATE.REPO, GATE.WEB, GATE.RECORD)
        GATE.REPO, GATE.WEB, GATE.RECORD = self.dir, self.web, self.record_path
        try:
            yield
        finally:
            GATE.REPO, GATE.WEB, GATE.RECORD = saved

    def run(self):
        """(exit_code, combined output)."""
        out, err = io.StringIO(), io.StringIO()
        saved_argv = sys.argv
        sys.argv = ["web_build_freshness_gate.py"]
        try:
            with self.applied(), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = GATE.main()
        finally:
            sys.argv = saved_argv
        return code, out.getvalue() + err.getvalue()

    def write(self, rel, text):
        path = self.web / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.written[rel] = text

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def test_a_tree_built_from_its_own_source_passes():
    tree = _Tree()
    try:
        code, out = tree.run()
        assert code == GATE.EXIT_OK, out
    finally:
        tree.close()


def test_an_edited_source_file_fails_and_is_named():
    """The failure the gate exists for, in its most ordinary form."""
    tree = _Tree()
    try:
        tree.write("src/App.tsx", "export const App = () => 'changed';\n")
        code, out = tree.run()
        assert code == GATE.EXIT_STALE, out
        assert "src/App.tsx" in out, (
            "a stale artifact was reported without naming the file that went "
            f"stale; the message has to be actionable:\n{out}")
    finally:
        tree.close()


def test_a_brand_new_source_file_fails():
    """A file added after the last build is stale-by-omission.

    It has no recorded hash to differ from, so a gate that only compared hashes
    of files it already knew would pass -- while the bundle contains none of the
    new code.
    """
    tree = _Tree()
    try:
        tree.write("src/NewScreen.tsx", "export const NewScreen = () => null;\n")
        code, out = tree.run()
        assert code == GATE.EXIT_STALE, out
        assert "NewScreen" in out, out
    finally:
        tree.close()


def test_a_deleted_source_file_fails():
    tree = _Tree()
    try:
        (tree.web / "src" / "App.tsx").unlink()
        code, out = tree.run()
        assert code == GATE.EXIT_STALE, out
    finally:
        tree.close()


def test_a_dependency_bump_alone_fails():
    """`package-lock.json` changes the bundle with no diff anywhere in src/.

    This is the stale case a source-only fingerprint misses: upgrade React,
    rebuild nothing, and every hash under `src/` still matches.
    """
    tree = _Tree()
    try:
        tree.write("package-lock.json", '{"lockfileVersion":3,"bumped":true}\n')
        code, out = tree.run()
        assert code == GATE.EXIT_STALE, out
        assert "package-lock.json" in out, out
    finally:
        tree.close()


def test_an_empty_source_tree_is_could_not_check_and_never_a_pass():
    """The most dangerous branch in the gate.

    An empty source map compares equal to an empty record, so a collector that
    silently stops matching files does not fail -- it reports a clean tree. The
    gate must refuse to answer instead. If this test ever goes green on exit 0,
    the gate has stopped checking anything and is still saying yes.
    """
    tree = _Tree(sources={}, record=json.dumps({"files": {}}))
    try:
        assert tree.web.is_dir(), (
            "the harness must present an existing but empty web/ here, or this "
            "exits via the missing-directory guard and never reaches the "
            "branch under test")
        code, out = tree.run()
        assert code == GATE.EXIT_NO_DATA, (
            f"an empty source tree returned {code}; could-not-check must never "
            f"be reported as freshness:\n{out}")
    finally:
        tree.close()


def test_a_missing_record_is_could_not_check():
    tree = _Tree(record=None)
    try:
        code, out = tree.run()
        assert code == GATE.EXIT_NO_DATA, out
    finally:
        tree.close()


def test_an_unreadable_record_is_could_not_check_not_stale():
    """Corrupt input is a broken check, and reporting it as `stale` would send
    someone to rebuild when the real problem is the record itself."""
    tree = _Tree(record="{not json at all")
    try:
        code, out = tree.run()
        assert code == GATE.EXIT_NO_DATA, out
    finally:
        tree.close()


def test_a_record_without_a_files_map_is_could_not_check():
    tree = _Tree(record=json.dumps({"version": 1, "fingerprint": "abc"}))
    try:
        code, out = tree.run()
        assert code == GATE.EXIT_NO_DATA, out
    finally:
        tree.close()


def test_a_missing_web_directory_is_could_not_check():
    """A missing tree and an empty tree both exit 3, so the code alone does not
    distinguish this guard from the one after it. The message is what makes it
    worth having -- "there is no web/" sends someone somewhere different from
    "web/ matched no files" -- so the message is what is asserted."""
    tree = _Tree()
    try:
        shutil.rmtree(tree.web)
        code, out = tree.run()
        assert code == GATE.EXIT_NO_DATA, out
        assert "no web/ directory" in out, (
            f"a missing web/ was reported with the generic empty-tree message:\n{out}")
    finally:
        tree.close()


def test_the_watched_source_list_matches_the_build_script():
    """Two implementations, one list. Drift here narrows the gate silently.

    If `fingerprint.mjs` starts recording a file the gate does not recompute,
    that file stops being watched: it can change freely and the gate still says
    fresh. The reverse spelling -- the gate watching something the build never
    records -- is permanently red instead. Both are bad, and neither announces
    itself, so the lists are compared directly.
    """
    js = FINGERPRINT_JS.read_text(encoding="utf-8")

    def array(name):
        match = re.search(rf"const {name} = \[(.*?)\];", js, re.S)
        assert match, f"{name} not found in fingerprint.mjs"
        return sorted(re.findall(r'"([^"]+)"', match.group(1)))

    assert array("SOURCE_DIRS") == sorted(GATE.SOURCE_DIRS), (
        "the build script and the gate disagree about which directories are "
        "source")
    assert array("SOURCE_FILES") == sorted(GATE.SOURCE_FILES), (
        "the build script and the gate disagree about which files are source")


def test_the_lockfile_is_watched_by_both():
    """Named explicitly because it is the easy one to drop.

    It is not source, it lives outside `src/`, and removing it from the list
    breaks nothing visible -- until a dependency bump ships an unrebuilt bundle.
    """
    assert "package-lock.json" in GATE.SOURCE_FILES
    assert "package-lock.json" in FINGERPRINT_JS.read_text(encoding="utf-8")


def test_hashing_is_over_raw_bytes():
    """The Python and JS sides must agree byte-for-byte, so neither may
    normalise. A newline-only difference has to register as a change."""
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "f.txt"
        path.write_bytes(b"a\nb\n")
        assert GATE.sha256(path) == hashlib.sha256(b"a\nb\n").hexdigest()
        path.write_bytes(b"a\r\nb\r\n")
        assert GATE.sha256(path) == hashlib.sha256(b"a\r\nb\r\n").hexdigest(), (
            "line endings were normalised; the JS side does not normalise and "
            "the two would silently disagree")


def test_the_fingerprint_depends_on_names_as_well_as_contents():
    """A rename with identical contents must change the fingerprint.

    Stated this way on purpose. The obvious phrasing -- two files swapping
    contents -- does not actually test anything: the swapped pair still hashes
    to a different sequence even if names are dropped entirely, so it passes
    against a fingerprint computed over contents alone. Same contents under
    different names is the case that collides.
    """
    a = GATE.fingerprint_of({"web/src/Feed.tsx": "aaa", "web/src/b.css": "bbb"})
    b = GATE.fingerprint_of({"web/src/Reels.tsx": "aaa", "web/src/b.css": "bbb"})
    assert a != b, "the fingerprint ignores which file a hash belongs to"


def test_the_exit_codes_stay_distinct():
    assert GATE.EXIT_OK == 0
    assert GATE.EXIT_STALE == 1
    assert GATE.EXIT_NO_DATA == 3


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
