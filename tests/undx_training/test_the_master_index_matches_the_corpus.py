"""``12_MASTER_KNOWLEDGE_CORPUS.yaml`` records a hash of every file it indexes, and
nothing has ever checked one.

The master index is not a summary. For each of the other eleven teaching files it records
a ``sha256_16`` of that file's bytes, a ``record_count``, and — across all eleven — a
308-entry index of every record id with its ``name``, ``domain``, ``status`` and
``surface``. All of it is generated in one pass by
``scripts/build_undx_training_corpus.py::build_master``, which reads each file back off
disk after writing it (line 2095).

Every one of those claims is true right now. 11 of 11 hashes match, the record index is an
exact bijection with the 308 records the eleven files carry, and of the 959 per-record
fields the files actually supply, 959 agree with the index. That is the whole reason this
file can exist: the corpus was generated in a single run (``97c617162``, 2026-08-26) and
no one has edited one of the eleven since. So these assertions are green today and cost
nothing, and the moment somebody edits a corpus file without regenerating the index they
go red and name the file.

That is not hypothetical. It is the next thing that will happen. #39 has to rebuild
``03_CAPABILITIES.yaml`` — the corpus teaches 87 capabilities against a registry of 125
(see :mod:`tests.undx_training.test_corpus_covers_the_registry`). A rebuild of that one
file leaves the index holding ``c0f0179f9ae1e981`` for a file that no longer hashes to it,
and holding 87 record ids for a file that now carries more. Nothing in the repo would
notice. The generator only writes a correct index when it runs over the *whole* corpus,
and nothing makes it.

So the two files are halves of one loop. ``test_corpus_covers_the_registry`` catches
"the registry grew and the corpus did not". This catches "a corpus file changed and the
index did not". Neither decides what the corpus should teach; #39 still owns that.

The index's own bytes are unhashed, necessarily — it is the root of trust here. A
rewrite of the index alone is the one drift these tests cannot see.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest
import yaml

_CORPUS_DIR = pathlib.Path(__file__).resolve().parents[2] / "UNDX_TRAINING"
_INDEX_NAME = "12_MASTER_KNOWLEDGE_CORPUS.yaml"

#: ``sources`` is a list of provenance strings, not of records. Every other top-level list
#: in a teaching file holds records, and a record is a mapping with an ``id``.
_NOT_A_RECORD_LIST = frozenset({"sources"})

#: The fields the index copies from each record. It defaults a missing one (to ``""``, or
#: to the id for ``name``), so these are only compared where the file actually supplies
#: the field — see
#: :func:`test_the_index_never_disagrees_with_a_field_a_record_actually_carries`.
_COPIED_FIELDS = ("name", "domain", "status", "surface")


def _load(name: str) -> dict:
    return yaml.safe_load((_CORPUS_DIR / name).read_text(encoding="utf-8"))


def _records_of(name: str) -> dict[str, dict]:
    """Every record a teaching file carries, by id.

    Files disagree about what they call their records — ``capabilities``, ``features``,
    ``journeys``, ``issues``, ``examples``, and two files split theirs across two keys
    (``endpoints`` + ``concepts``, ``registries`` + ``surfaces``). Keying off "a list whose
    entries have an id" rather than a list of expected key names means a rename does not
    silently reduce a file to zero records, which would make the comparisons below vacuous
    rather than failing.
    """
    found: dict[str, dict] = {}
    for key, value in _load(name).items():
        if key in _NOT_A_RECORD_LIST or not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict) and entry.get("id"):
                assert entry["id"] not in found, (
                    f"{name} carries the record id {entry['id']!r} twice"
                )
                found[entry["id"]] = entry
    assert found, (
        f"no records could be read out of {name}; the record lists have been renamed and "
        "these comparisons would pass by being empty"
    )
    return found


@pytest.fixture(scope="module")
def index() -> dict:
    return _load(_INDEX_NAME)


@pytest.fixture(scope="module")
def indexed_files(index) -> list[dict]:
    entries = index["files"]
    assert entries, "the master index lists no files"
    return entries


@pytest.fixture(scope="module")
def records_by_file(indexed_files) -> dict[str, dict[str, dict]]:
    return {entry["file"]: _records_of(entry["file"]) for entry in indexed_files}


def test_the_index_accounts_for_every_teaching_file_on_disk(indexed_files):
    """A twelfth teaching file would otherwise be indexed by nothing.

    The index covers eleven files and is the twelfth. Adding a corpus file without
    extending ``FILE_ORDER`` in the generator gives it no hash, no record count and no
    entry in the record index — every check below would still pass, because they all
    iterate the index rather than the directory.
    """
    on_disk = sorted(path.name for path in _CORPUS_DIR.glob("*.yaml"))
    accounted = sorted([entry["file"] for entry in indexed_files] + [_INDEX_NAME])
    assert accounted == on_disk, (
        "the set of teaching files and the set the master index accounts for have "
        f"diverged: only on disk {sorted(set(on_disk) - set(accounted))}, only in the "
        f"index {sorted(set(accounted) - set(on_disk))}"
    )


def test_every_indexed_file_still_hashes_to_what_the_index_recorded(indexed_files):
    """The one that will catch #39's rebuild.

    ``build_master`` hashes each file's full text off disk. Any edit at all — a rebuild,
    a typo fix, a line ending — changes the hash, and the index does not follow unless the
    whole corpus is regenerated.
    """
    stale = {}
    for entry in indexed_files:
        text = (_CORPUS_DIR / entry["file"]).read_text(encoding="utf-8")
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if actual != entry["sha256_16"]:
            stale[entry["file"]] = {"recorded": entry["sha256_16"], "actual": actual}
    assert stale == {}, (
        "these teaching files have changed since the master index was built, so the index "
        "now describes content that is not there. Regenerate the whole corpus with "
        "scripts/build_undx_training_corpus.py rather than editing the index by hand — but "
        f"read #39 first, the generator hardcodes counts a bare rebuild leaves wrong: {stale}"
    )


def test_the_recorded_record_counts_match_the_records_each_file_carries(
    indexed_files, records_by_file
):
    recorded = {entry["file"]: entry["record_count"] for entry in indexed_files}
    actual = {name: len(records) for name, records in records_by_file.items()}
    assert recorded == actual, (
        f"record_count disagrees with the records in the file: recorded {recorded}, "
        f"actual {actual}"
    )


def test_the_record_index_is_a_bijection_with_the_records_in_the_files(
    index, records_by_file
):
    """Per file, so a finding names where the drift is rather than only that there is some."""
    indexed: dict[str, set[str]] = {name: set() for name in records_by_file}
    unknown_file = {}
    for record in index["records"]:
        if record["file"] not in indexed:
            unknown_file[record["id"]] = record["file"]
            continue
        indexed[record["file"]].add(record["id"])
    assert unknown_file == {}, (
        f"the record index attributes records to files it does not list: {unknown_file}"
    )

    drift = {}
    for name, records in records_by_file.items():
        only_in_file = sorted(set(records) - indexed[name])
        only_in_index = sorted(indexed[name] - set(records))
        if only_in_file or only_in_index:
            drift[name] = {"unindexed": only_in_file, "indexed_but_absent": only_in_index}
    assert drift == {}, (
        "the master record index and the records in the teaching files have diverged. "
        "'unindexed' records are ones the corpus teaches that the index cannot find; "
        "'indexed_but_absent' are ones the index promises and the file no longer has: "
        f"{drift}"
    )


def test_the_declared_totals_match_the_structures_they_count(
    index, indexed_files, records_by_file
):
    """Three generated numbers, each derived from a different structure at build time.

    They agree now. They are asserted together because a partial hand-edit tends to move
    one and not the others, and which one moved says what was edited.
    """
    assert index["corpus_file_count"] == len(indexed_files)
    assert index["record_index_count"] == len(index["records"])
    assert index["total_records"] == sum(
        entry["record_count"] for entry in indexed_files
    ), "total_records is not the sum of the per-file record_count values"
    assert index["total_records"] == sum(
        len(records) for records in records_by_file.values()
    ), "total_records is not the number of records the files actually carry"

    ids = [record["id"] for record in index["records"]]
    assert len(set(ids)) == len(ids), "the record index lists an id more than once"


def test_the_index_never_disagrees_with_a_field_a_record_actually_carries(
    index, records_by_file
):
    """The narrow form, and why it has to be narrow.

    The index writes a ``name``, ``domain``, ``status`` and ``surface`` for all 308
    records, but most records do not carry all four — the generator substitutes ``""``, or
    the id for ``name``. Asserting equality outright would be asserting the generator's
    defaulting rules, which is testing the wrong thing and would report 273 differences
    that are not drift.

    Where a file does supply the field, though, the index has no licence to differ. That
    is 959 comparisons today, and it catches a record's ``status`` being corrected in a
    teaching file while the index keeps telling the model the old one.
    """
    flat = {rid: record for records in records_by_file.values() for rid, record in records.items()}
    compared = 0
    disagreements = {}
    for entry in index["records"]:
        source = flat.get(entry["id"])
        if source is None:
            continue  # the bijection test above owns this case
        for field in _COPIED_FIELDS:
            if field not in source:
                continue
            compared += 1
            if entry.get(field) != source[field]:
                disagreements[f"{entry['id']}.{field}"] = {
                    "index": entry.get(field), "file": source[field],
                }
    assert compared, (
        "no fields were compared, so this test proved nothing; the records no longer "
        f"carry any of {_COPIED_FIELDS}"
    )
    assert disagreements == {}, (
        "the master index contradicts a field the teaching file itself states, so the "
        f"index is describing the corpus as it used to be: {disagreements}"
    )


def test_each_indexed_file_entry_is_complete(indexed_files):
    """Shape, and the one comparison deliberately not made.

    A file entry carries ``file``, ``title``, ``record_count`` and ``sha256_16``. Note
    what is not asserted anywhere above: that ``title`` equals the file's own ``title``.
    For seven of the eleven it does not, and that is by design — ``FILE_ORDER`` in the
    generator (line 2047) carries its own short labels for the index, so
    ``05_DATABASE_CONCEPTS.yaml`` titles itself "Data model concepts, not table dumps"
    while the index calls it "Data model concepts". Reading those as drift and
    "correcting" the teaching files would be editing training content to fix a
    non-problem.
    """
    expected = {"file", "title", "record_count", "sha256_16"}
    for entry in indexed_files:
        assert set(entry) == expected, (
            f"index entry for {entry.get('file')!r} has fields {sorted(entry)}, "
            f"expected {sorted(expected)}"
        )
        assert entry["title"].strip(), f"{entry['file']} is indexed with an empty title"
        assert len(entry["sha256_16"]) == 16, (
            f"{entry['file']} has a {len(entry['sha256_16'])}-character hash, expected 16"
        )
