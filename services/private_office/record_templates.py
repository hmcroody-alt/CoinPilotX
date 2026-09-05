"""The record template contract: what a structured private record *is*.

Why a registry and not more fields on the bottom sheet
-----------------------------------------------------
The Private Facts sheet asks four questions — domain, fact type, value type,
value — and it asks them about everything. That is the correct shape for an
attribute store and the wrong shape for a passport. A passport is not a string
with a label on it; it is a document with a holder, an issuing country, a
nationality, a date of birth, an issue date, an expiry date, an issuing
authority, a status, evidence, and a number that must never appear in a
notification, a log line, a URL or an analytics payload.

Encoding that per-record structure as free text and hoping the reader
reconstructs it is how a store ends up unable to answer "what expires in the
next ninety days" — the one question the data exists to answer.

So structure is declared, once, server-side, in a versioned registry. This
module is the *contract*: what a template may say, what a field may say, how a
submitted payload is validated against it, and how a value is masked for
display. The templates themselves live in :mod:`record_template_catalog`, which
imports this module and never the other way round. The split is not cosmetic —
it means the contract can be tested with synthetic templates and a catalog
mistake cannot break the validator.

Server-controlled, and why the client still gets a copy
-------------------------------------------------------
Templates are owned by the server. The client receives a *manifest*: a typed,
renderable projection with no SQL, no column names, no Python types and no
storage detail. It carries :data:`CONTRACT_VERSION`, and a client that does not
recognise the version it is handed must degrade to the plain record view rather
than guess at a field kind it has never seen. That is the "fail safely on an
unsupported future schema" requirement, and it is why the version lives at the
top of the manifest rather than being implied by the fields present.

The client must not be *fully* dependent on the fetched manifest either: it
ships a small pinned copy of the templates it was built against so that an
offline first-run, or a manifest fetch that fails, still renders a passport form
rather than an error. The manifest wins when it is present and its version is
understood.

What a field declares, and why every attribute is load-bearing
---------------------------------------------------------------
``sensitivity`` and ``mask`` are separate on purpose. Sensitivity answers "how
much damage does disclosure do" and drives storage (encryption at rest for
RESTRICTED) and retrieval ceilings. Mask answers "what does the screen show
before anyone proves anything", and a field can be CONFIDENTIAL and masked (a
policy number) or RESTRICTED and unmasked-once-revealed. Collapsing them would
mean either masking everything confidential — unusable — or storing everything
maskable as restricted, which inflates the encryption surface until nobody
trusts it.

``searchable`` is the third distinct axis. A passport number is searchable *by
its last four* and must never enter a full-text index; the flag says whether the
projection may be indexed at all, and :func:`mask_value` decides what a
suggestion is permitted to show. A field that is masked and searchable is
indexed on its masked form, never its raw one.

Validation returns rather than raises
--------------------------------------
Following :mod:`model`, every normalizer here returns a canonical value or
``None``, and :func:`validate_payload` collects errors instead of aborting on
the first one. A member filling a fourteen-field passport form should be told
about all four mistakes at once, not made to discover them one round trip at a
time. The caller decides what a failure means; this module never decides to
store something it could not understand.

What this module deliberately does NOT do
------------------------------------------
* It does not touch the database. It produces a validated *projection* — one row
  per field, already typed and already carrying its sensitivity — and the
  storage layer writes it. Keeping the schema out of here is what lets the same
  validator run in a test with no connection.
* It does not verify anything about the world. A template may require an
  ``issuing_country`` and this module will check that the value is a well-formed
  ISO 3166-1 alpha-2 code. It will not claim the country issued the document,
  that the document exists, or that the number is genuine. Every value written
  through here begins at USER_ASSERTED or DOCUMENT_EXTRACTED provenance and is
  never promoted by validation succeeding.
* It does not decide access. Field sensitivity is *declared* here and *enforced*
  by the read path and the reveal endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field, replace
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from services.private_office import model

# ---------------------------------------------------------------------------
# Contract version
# ---------------------------------------------------------------------------
#: The version of the *manifest format* — the shape of the contract, not the
#: content of any template. A client reads this first and decides whether it
#: understands the document at all. Individual templates carry their own
#: ``version``, which changes when a template's fields change; this number
#: changes only when a new field ``kind``, a new mask strategy, or a new
#: structural concept is introduced that an older client could not render.
#:
#: Two numbers rather than one because they answer different questions. "Can you
#: draw this form?" is the contract version. "Is the record you are looking at
#: shaped the way this template is shaped today?" is the template version, and
#: that one has to be recorded on every stored record so a migration can find
#: the rows written before a field moved.
CONTRACT_VERSION = 1

MAX_TEXT_LENGTH = 4000
MAX_SHORT_TEXT_LENGTH = 256


# ---------------------------------------------------------------------------
# Field kinds
# ---------------------------------------------------------------------------
# A closed set. Each kind is a promise to the client about which control to
# render, and a promise to the storage layer about which typed column receives
# the value. Adding one is a CONTRACT_VERSION change, because an older client
# handed a kind it does not know cannot render the form correctly and must say
# so rather than fall back to a text box — a text box under a field the server
# will reject is worse than an honest "update the app to edit this record".
KIND_TEXT = "text"
KIND_LONG_TEXT = "long_text"
KIND_ENUM = "enum"
KIND_DATE = "date"
KIND_DATETIME = "datetime"
KIND_COUNTRY = "country"
KIND_NUMBER = "number"
KIND_MONEY = "money"
KIND_BOOLEAN = "boolean"
KIND_IDENTIFIER = "identifier"
KIND_EMAIL = "email"
KIND_PHONE = "phone"
KIND_PERSON_NAME = "person_name"
KIND_URL = "url"

FIELD_KINDS: tuple[str, ...] = (
    KIND_TEXT,
    KIND_LONG_TEXT,
    KIND_ENUM,
    KIND_DATE,
    KIND_DATETIME,
    KIND_COUNTRY,
    KIND_NUMBER,
    KIND_MONEY,
    KIND_BOOLEAN,
    KIND_IDENTIFIER,
    KIND_EMAIL,
    KIND_PHONE,
    KIND_PERSON_NAME,
    KIND_URL,
)

#: How each kind is stored. The storage layer has six typed columns
#: (:data:`model.VALUE_TYPES`) and fourteen kinds, because kind is a *rendering
#: and validation* concept while value type is a *comparison* concept. An email
#: and a passport number are both stored as STRING; nothing downstream needs to
#: compare them differently, but everything upstream needs to render and
#: validate them differently.
KIND_VALUE_TYPES: dict[str, str] = {
    KIND_TEXT: model.VALUE_STRING,
    KIND_LONG_TEXT: model.VALUE_STRING,
    KIND_ENUM: model.VALUE_STRING,
    KIND_DATE: model.VALUE_DATE,
    KIND_DATETIME: model.VALUE_DATE,
    KIND_COUNTRY: model.VALUE_STRING,
    KIND_NUMBER: model.VALUE_NUMBER,
    KIND_MONEY: model.VALUE_MONEY,
    KIND_BOOLEAN: model.VALUE_BOOLEAN,
    KIND_IDENTIFIER: model.VALUE_STRING,
    KIND_EMAIL: model.VALUE_STRING,
    KIND_PHONE: model.VALUE_STRING,
    KIND_PERSON_NAME: model.VALUE_STRING,
    KIND_URL: model.VALUE_STRING,
}

#: Kinds whose canonical form is a date, and which therefore may drive
#: expirations, reminders and calendar proposals.
DATE_KINDS: frozenset[str] = frozenset({KIND_DATE, KIND_DATETIME})


# ---------------------------------------------------------------------------
# Mask strategies
# ---------------------------------------------------------------------------
# What the screen shows before anyone proves who they are. The raw value never
# leaves the server on a masked field; :func:`mask_value` renders the
# substitute, and the reveal endpoint is the only path to the original.
MASK_NONE = "none"
MASK_LAST4 = "last4"
MASK_YEAR = "year"
MASK_INITIALS = "initials"
MASK_FULL = "full"

MASK_STRATEGIES: tuple[str, ...] = (
    MASK_NONE,
    MASK_LAST4,
    MASK_YEAR,
    MASK_INITIALS,
    MASK_FULL,
)

#: The character used to stand in for withheld content. A fixed count is used
#: rather than one dot per withheld character, because a mask whose length
#: tracks the secret leaks the secret's length — which for an identifier with a
#: known format can be most of what an attacker wanted.
_MASK_DOT = "•"
_MASK_RUN = _MASK_DOT * 4


def normalize_kind(value: object) -> str | None:
    """Canonical field kind, or ``None``."""
    text = str(value or "").strip().lower()
    return text if text in FIELD_KINDS else None


def normalize_mask(value: object) -> str | None:
    """Canonical mask strategy, or ``None``."""
    text = str(value or "").strip().lower()
    return text if text in MASK_STRATEGIES else None


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
#: ISO 3166-1 alpha-2. Present so a country field is *validated* rather than
#: merely trimmed, and so the client can offer a real picker instead of a text
#: box that accepts "Untied Sates".
#:
#: Codes only, deliberately. Display names are localised strings and belong in
#: the client's i18n catalogs or its platform region-name API; shipping 249
#: English names from the server would either force English on every locale or
#: duplicate a translation table the platform already has. The code is the
#: stable key; the name is presentation.
COUNTRY_CODES: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ
    BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR
    CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ
    MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF
    PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI
    SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR
    TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split()
)

#: ISO 4217 codes are not enumerated. The list changes with monetary policy, and
#: a stale allowlist that rejects a member's own currency is a worse failure than
#: accepting a well-formed code the platform has no rate for. Shape is checked;
#: existence is not claimed.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
#: E.164-ish. Deliberately permissive about separators on input — they are
#: stripped — and strict about what remains: an optional leading ``+`` and 6 to
#: 15 digits. Rejecting a valid international number because of a space is a
#: form that fights its user.
_PHONE_RE = re.compile(r"^\+?[0-9]{6,15}$")
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)
#: A dotted path segment. ``issuance.issue_date`` and ``holders[0].given_names``
#: are the two shapes; anything else is a template bug and is caught at
#: registration rather than at first write.
_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\[[0-9]+\])?(\.[a-z][a-z0-9_]*(\[[0-9]+\])?)*$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TemplateError(ValueError):
    """A template is internally inconsistent.

    Raised at registration, never at request time. A malformed template is a
    programming error the developer must see immediately; deferring it to the
    first member who opens the form turns a startup failure into a support
    ticket.
    """


@dataclass(frozen=True)
class FieldError:
    """One reason one field was not accepted.

    ``code`` is a stable token from :data:`ERROR_CODES` so the client can
    localise the message, and ``detail`` carries the bounded machine facts (a
    limit, an allowed set) without ever echoing the rejected value — echoing it
    would put a restricted identifier into an error body, and error bodies are
    logged.
    """

    path: str
    code: str
    detail: dict[str, Any] = _dc_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "code": self.code, "detail": dict(self.detail)}


ERR_REQUIRED = "required"
ERR_UNKNOWN_FIELD = "unknown_field"
ERR_TOO_LONG = "too_long"
ERR_NOT_A_NUMBER = "not_a_number"
ERR_OUT_OF_RANGE = "out_of_range"
ERR_BAD_DATE = "bad_date"
ERR_BAD_DATETIME = "bad_datetime"
ERR_BAD_ENUM = "bad_enum"
ERR_BAD_COUNTRY = "bad_country"
ERR_BAD_CURRENCY = "bad_currency"
ERR_BAD_EMAIL = "bad_email"
ERR_BAD_PHONE = "bad_phone"
ERR_BAD_URL = "bad_url"
ERR_BAD_PATTERN = "bad_pattern"
ERR_BAD_BOOLEAN = "bad_boolean"
ERR_TOO_MANY_ENTRIES = "too_many_entries"

ERROR_CODES: tuple[str, ...] = (
    ERR_REQUIRED,
    ERR_UNKNOWN_FIELD,
    ERR_TOO_LONG,
    ERR_NOT_A_NUMBER,
    ERR_OUT_OF_RANGE,
    ERR_BAD_DATE,
    ERR_BAD_DATETIME,
    ERR_BAD_ENUM,
    ERR_BAD_COUNTRY,
    ERR_BAD_CURRENCY,
    ERR_BAD_EMAIL,
    ERR_BAD_PHONE,
    ERR_BAD_URL,
    ERR_BAD_PATTERN,
    ERR_BAD_BOOLEAN,
    ERR_TOO_MANY_ENTRIES,
)


# ---------------------------------------------------------------------------
# Field, section, template
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Option:
    """One choice in an enum field.

    ``value`` is the stored token and never changes; ``label_key`` is what the
    client localises and ``label_fallback`` is what it shows when the catalog
    has no entry yet. Storing the label instead of the token is the mistake this
    shape exists to prevent — it makes a translation change a data migration.
    """

    value: str
    label_key: str
    label_fallback: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "label_key": self.label_key,
            "label_fallback": self.label_fallback,
        }


@dataclass(frozen=True)
class FieldSpec:
    """One field of one template version."""

    path: str
    kind: str
    label_key: str
    label_fallback: str

    required: bool = False
    sensitivity: str = model.SENSITIVITY_CONFIDENTIAL
    mask: str = MASK_NONE
    #: May the field's projection be indexed for search? A masked searchable
    #: field is indexed on its *masked* form only — see
    #: :func:`search_index_text`, which is the single place that decision is
    #: made so no future caller can index a raw restricted value by writing the
    #: obvious code.
    searchable: bool = False
    #: May UNDX read this field at all? Independent of sensitivity: a member may
    #: be perfectly willing to see a value themselves and unwilling to have it
    #: in a model prompt. Default False for RESTRICTED is enforced in
    #: :meth:`__post_init__`, so a template author cannot expose a restricted
    #: value to UNDX by omission.
    undx_readable: bool = True

    options: tuple[Option, ...] = ()
    max_length: int = MAX_SHORT_TEXT_LENGTH
    min_number: float | None = None
    max_number: float | None = None
    pattern: str = ""
    help_key: str = ""
    help_fallback: str = ""
    #: Does this field participate in duplicate detection? Two records of the
    #: same template whose identity fields all match are the same record entered
    #: twice.
    identity: bool = False
    #: Does this field drive the record's expiration lifecycle? At most one per
    #: template, checked at registration.
    expires_record: bool = False
    #: Evidence is *expected* for this field. Not enforced as a hard requirement
    #: at write time — refusing to let a member record their own passport
    #: because they have not yet photographed it makes the product useless on
    #: the day they need it — but surfaced as a review prompt and reflected in
    #: the record's verification state.
    evidence_expected: bool = False

    def __post_init__(self) -> None:
        if not _PATH_RE.match(self.path or ""):
            raise TemplateError(f"field path is not a valid dotted path: {self.path!r}")
        if normalize_kind(self.kind) is None:
            raise TemplateError(f"unknown field kind {self.kind!r} at {self.path}")
        if model.normalize_sensitivity(self.sensitivity) is None:
            raise TemplateError(f"unknown sensitivity {self.sensitivity!r} at {self.path}")
        if normalize_mask(self.mask) is None:
            raise TemplateError(f"unknown mask {self.mask!r} at {self.path}")
        if self.kind == KIND_ENUM and not self.options:
            raise TemplateError(f"enum field {self.path} declares no options")
        if self.kind != KIND_ENUM and self.options:
            raise TemplateError(f"non-enum field {self.path} declares options")
        if self.expires_record and self.kind not in DATE_KINDS:
            raise TemplateError(f"{self.path} drives expiration but is not a date")
        if self.max_length > MAX_TEXT_LENGTH:
            raise TemplateError(f"{self.path} max_length exceeds {MAX_TEXT_LENGTH}")
        # ``undx_readable`` defaults to True, which is the right default for the
        # ninety percent of fields that are a city or a job title. It is the
        # wrong default exactly once, and that once is the case that matters, so
        # a RESTRICTED field must state the exclusion rather than inherit the
        # default. Enforcing it at registration means the rule survives a
        # template author who copied a neighbouring field and changed the label.
        if self.sensitivity == model.SENSITIVITY_RESTRICTED and self.undx_readable:
            raise TemplateError(
                f"{self.path} is RESTRICTED and must set undx_readable=False"
            )
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:  # pragma: no cover - author error
                raise TemplateError(f"{self.path} pattern does not compile: {exc}") from exc

    @property
    def value_type(self) -> str:
        return KIND_VALUE_TYPES[self.kind]

    def as_manifest(self) -> dict[str, Any]:
        """The client-facing projection.

        Note what is absent: no column names, no Python types, no storage hints,
        no ``identity`` flag (duplicate detection is a server decision and
        telling the client which fields it keys on invites a client that tries
        to do it itself and disagrees).
        """
        out: dict[str, Any] = {
            "path": self.path,
            "kind": self.kind,
            "label_key": self.label_key,
            "label_fallback": self.label_fallback,
            "required": bool(self.required),
            "sensitivity": self.sensitivity,
            "masked": self.mask != MASK_NONE,
            "mask": self.mask,
            "evidence_expected": bool(self.evidence_expected),
        }
        if self.options:
            out["options"] = [option.as_dict() for option in self.options]
        if self.kind in (KIND_TEXT, KIND_LONG_TEXT, KIND_IDENTIFIER, KIND_PERSON_NAME):
            out["max_length"] = self.max_length
        if self.min_number is not None:
            out["min"] = self.min_number
        if self.max_number is not None:
            out["max"] = self.max_number
        if self.help_key:
            out["help_key"] = self.help_key
            out["help_fallback"] = self.help_fallback
        return out


@dataclass(frozen=True)
class SectionSpec:
    """A group of fields the form renders together.

    Sections exist for the member, not for the database. A passport form with
    fourteen fields in one column is a wall; the same fourteen split into
    *Identification*, *Issuance*, *Evidence* and *Machine-readable zone* is four
    short questions. The storage projection is flat regardless — a field's
    section is presentation, and moving a field between sections is not a
    migration.
    """

    key: str
    label_key: str
    label_fallback: str
    fields: tuple[FieldSpec, ...]
    #: A repeatable section renders an "add another" control and its field paths
    #: carry an index (``holders[0].given_names``). ``max_entries`` is a hard
    #: bound, because an unbounded repeatable is an unbounded row count per
    #: record and the storage layer must be able to state a maximum.
    repeatable: bool = False
    max_entries: int = 1
    collapsed_by_default: bool = False

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key or ""):
            raise TemplateError(f"section key is not a valid token: {self.key!r}")
        if not self.fields:
            raise TemplateError(f"section {self.key} has no fields")
        if self.repeatable and self.max_entries < 2:
            raise TemplateError(f"repeatable section {self.key} must allow 2+ entries")
        if not self.repeatable and self.max_entries != 1:
            raise TemplateError(f"non-repeatable section {self.key} must allow 1 entry")

    def as_manifest(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label_key": self.label_key,
            "label_fallback": self.label_fallback,
            "repeatable": bool(self.repeatable),
            "max_entries": self.max_entries,
            "collapsed_by_default": bool(self.collapsed_by_default),
            "fields": [f.as_manifest() for f in self.fields],
        }


@dataclass(frozen=True)
class ReminderRule:
    """One reminder a template proposes when it is saved.

    Proposes, not schedules. Every rule arrives at the form as a checkbox with a
    default, and the member's answer is what the reminder service stores. A
    system that silently subscribes a member to five notifications because they
    recorded a document is a system they turn off entirely.

    ``offset_days`` is *before* :attr:`FieldSpec.expires_record`. Positive
    numbers only; a reminder after the thing expired is a different feature
    (an overdue escalation) and conflating them makes the sign of a number
    load-bearing.
    """

    key: str
    offset_days: int
    label_key: str
    label_fallback: str
    default_enabled: bool = True

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key or ""):
            raise TemplateError(f"reminder key is not a valid token: {self.key!r}")
        if self.offset_days < 0:
            raise TemplateError(f"reminder {self.key} has a negative offset")

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "offset_days": self.offset_days,
            "label_key": self.label_key,
            "label_fallback": self.label_fallback,
            "default_enabled": bool(self.default_enabled),
        }


# --- automation hooks -------------------------------------------------------
# Named here so a template can only request an automation that exists. A free
# string would let a template ask for "renew_passport_automatically", which
# nothing implements and nobody notices is not running.
HOOK_EXPIRATION_TO_CALENDAR = "EXPIRATION_TO_CALENDAR"
HOOK_EXPIRATION_TO_TASK = "EXPIRATION_TO_TASK"
HOOK_EXPIRATION_TO_BRIEFING = "EXPIRATION_TO_BRIEFING"
HOOK_DOCUMENT_TO_RECORD = "DOCUMENT_TO_RECORD"
HOOK_RECORD_TO_GRAPH = "RECORD_TO_GRAPH"

AUTOMATION_HOOKS: tuple[str, ...] = (
    HOOK_EXPIRATION_TO_CALENDAR,
    HOOK_EXPIRATION_TO_TASK,
    HOOK_EXPIRATION_TO_BRIEFING,
    HOOK_DOCUMENT_TO_RECORD,
    HOOK_RECORD_TO_GRAPH,
)


@dataclass(frozen=True)
class Template:
    """One version of one record type.

    A template is immutable and versioned. Editing a shipped template in place
    would silently change the meaning of every record already stored against it;
    the supported change is a new :attr:`version` plus a migration rule, so a
    record can always say which shape it was written in.
    """

    key: str
    version: int
    domain: str
    ia_domain: str
    display_key: str
    display_fallback: str
    description_key: str
    description_fallback: str
    icon: str
    sections: tuple[SectionSpec, ...]
    statuses: tuple[str, ...]
    default_status: str

    #: Baseline sensitivity for the record envelope. Individual fields may sit
    #: above it — a passport record is CONFIDENTIAL while its number is
    #: RESTRICTED — but never below, checked at registration. A field more open
    #: than its record is how a "confidential" record leaks through one column.
    sensitivity: str = model.SENSITIVITY_CONFIDENTIAL

    reminders: tuple[ReminderRule, ...] = ()
    automations: tuple[str, ...] = ()
    #: Which graph node type, if any, a saved record projects into. Empty means
    #: the record stays out of the graph entirely — which is the correct and
    #: mandatory answer for every HEALTH template.
    graph_node_type: str = ""
    #: Relations this template's records may participate in, as a hint to the
    #: relationship editor. Enforcement remains :func:`model.relation_permits`.
    graph_relations: tuple[str, ...] = ()
    #: May UNDX read records of this template at all, subject to per-field
    #: ``undx_readable``? A False here is a whole-template exclusion and
    #: outranks any field that says otherwise.
    undx_readable: bool = True
    #: May UNDX *draft* a record of this template? Drafting is always
    #: needs-review; there is no configuration in which UNDX writes a verified
    #: record of any template.
    undx_draftable: bool = False
    #: Legacy ``private_facts.fact_type`` values that map deterministically onto
    #: this template's field paths. Used only by the migration, and only where
    #: the mapping is unambiguous — anything else becomes a legacy custom
    #: record rather than a guess at a passport's structure.
    legacy_fact_types: tuple[tuple[str, str], ...] = ()
    #: Superseded template versions this one can absorb, with a per-path rename
    #: map. Absent means records at the old version are readable but are not
    #: rewritten.
    migrates_from: tuple[tuple[int, tuple[tuple[str, str], ...]], ...] = ()

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key or ""):
            raise TemplateError(f"template key is not a valid token: {self.key!r}")
        if self.version < 1:
            raise TemplateError(f"{self.key} version must be >= 1")
        if model.normalize_domain(self.domain) is None:
            raise TemplateError(f"{self.key} names an unknown domain {self.domain!r}")
        if model.normalize_sensitivity(self.sensitivity) is None:
            raise TemplateError(f"{self.key} names an unknown sensitivity")
        if not self.sections:
            raise TemplateError(f"{self.key} has no sections")
        if not self.statuses:
            raise TemplateError(f"{self.key} has no statuses")
        if self.default_status not in self.statuses:
            raise TemplateError(f"{self.key} default status is not in its status set")
        if self.graph_node_type and model.normalize_node_type(self.graph_node_type) is None:
            raise TemplateError(f"{self.key} names an unknown graph node type")
        if self.domain == model.DOMAIN_HEALTH and self.graph_node_type:
            # The brief is explicit that health records never reach the Capital
            # Graph. Enforcing it at registration means the rule cannot be
            # broken by a future template author who was not in the room.
            raise TemplateError(f"{self.key} is a HEALTH template and must not project into the graph")
        for hook in self.automations:
            if hook not in AUTOMATION_HOOKS:
                raise TemplateError(f"{self.key} requests unknown automation {hook!r}")
        for relation in self.graph_relations:
            if model.normalize_relation(relation) is None:
                raise TemplateError(f"{self.key} names an unknown relation {relation!r}")

        seen: set[str] = set()
        expiring: list[str] = []
        floor = model.SENSITIVITY_RANK[self.sensitivity]
        for section in self.sections:
            for spec in section.fields:
                if spec.path in seen:
                    raise TemplateError(f"{self.key} declares {spec.path} twice")
                seen.add(spec.path)
                if model.SENSITIVITY_RANK[spec.sensitivity] < floor:
                    raise TemplateError(
                        f"{self.key}.{spec.path} is less sensitive than its record"
                    )
                if spec.expires_record:
                    expiring.append(spec.path)
                if section.repeatable and "[" not in spec.path:
                    raise TemplateError(
                        f"{self.key}.{spec.path} is in a repeatable section "
                        "but its path carries no index"
                    )
        if len(expiring) > 1:
            raise TemplateError(f"{self.key} declares {len(expiring)} expiration fields")
        if self.reminders and not expiring:
            raise TemplateError(f"{self.key} declares reminders but nothing that expires")
        for _version, renames in self.migrates_from:
            for old, new in renames:
                if new and new not in seen:
                    raise TemplateError(f"{self.key} migration targets unknown path {new!r}")

    # -- derived views ------------------------------------------------------
    @property
    def fields(self) -> tuple[FieldSpec, ...]:
        return tuple(f for section in self.sections for f in section.fields)

    @property
    def field_map(self) -> dict[str, FieldSpec]:
        return {f.path: f for f in self.fields}

    @property
    def expiration_path(self) -> str:
        for spec in self.fields:
            if spec.expires_record:
                return spec.path
        return ""

    @property
    def identity_paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.fields if f.identity)

    @property
    def schema_key(self) -> str:
        """The stored discriminator: ``passport@2``.

        Key and version travel together on every record because reading a record
        requires knowing both, and two columns that must always be read together
        are one fact stored twice.
        """
        return f"{self.key}@{self.version}"

    def as_manifest(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "version": self.version,
            "schema_key": self.schema_key,
            "domain": self.domain,
            "ia_domain": self.ia_domain,
            "display_key": self.display_key,
            "display_fallback": self.display_fallback,
            "description_key": self.description_key,
            "description_fallback": self.description_fallback,
            "icon": self.icon,
            "sensitivity": self.sensitivity,
            "statuses": list(self.statuses),
            "default_status": self.default_status,
            "sections": [s.as_manifest() for s in self.sections],
            "reminders": [r.as_dict() for r in self.reminders],
            "expiration_path": self.expiration_path,
            "undx_readable": bool(self.undx_readable),
        }
        return out


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldValue:
    """A validated field, ready for the storage layer.

    This is the projection the mission's "searchable indexed fields" requirement
    asks for and the reason there is no single JSON blob: every field arrives
    here already typed, already carrying its own sensitivity, and already
    knowing whether it may be indexed. A blob can carry none of that, which is
    why a blob cannot be searched, cannot be redacted per field, and cannot be
    encrypted for the one column that needs it.
    """

    path: str
    kind: str
    value_type: str
    value_text: str
    value_number: float | None
    sensitivity: str
    mask: str
    searchable: bool
    #: Set for date and datetime kinds: the canonical ``YYYY-MM-DD`` used by
    #: expiration queries, so "what expires in ninety days" is an index scan
    #: rather than a parse of every row.
    value_date: str = ""
    currency: str = ""


def _clean_text(value: object) -> str:
    """Trim and strip control characters.

    Control characters are removed rather than rejected: they arrive from
    copy-paste and OCR far more often than from an attacker, and rejecting a
    pasted passport number because it carried a zero-width space is a form that
    appears broken. Newlines survive only in long text, handled by the caller.
    """
    text = str(value if value is not None else "")
    return "".join(ch for ch in text if ch == "\n" or ch >= " ").strip()


def _normalize_number(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = _clean_text(raw).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_date(raw: object) -> str:
    """``YYYY-MM-DD`` or ``""``.

    Accepts a full ISO datetime and keeps only the date part, because a date
    picker on one platform sends midnight-local and on another sends a bare
    date, and a birth date that shifts a day at a timezone boundary is a defect
    members notice immediately.
    """
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw.isoformat()
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    text = _clean_text(raw)
    if not text:
        return ""
    candidate = text[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return ""


def _normalize_datetime(raw: object) -> str:
    """UTC ISO 8601 with a ``Z``, or ``""``.

    A naive input is read as UTC. That is a decision with consequences, and the
    honest place for it is here rather than three call sites: the calendar
    carries its own explicit timezone column precisely because a bare timestamp
    cannot represent "9am wherever I am", and a *field* value on a record is not
    that — it is a point in time, and a point in time is UTC.
    """
    if isinstance(raw, datetime):
        moment = raw
    else:
        text = _clean_text(raw)
        if not text:
            return ""
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_TRUE_TOKENS = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "n", "off", ""})


def _normalize_boolean(raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    text = _clean_text(raw).lower()
    if text in _TRUE_TOKENS:
        return True
    if text in _FALSE_TOKENS:
        return False
    return None


def normalize_field(spec: FieldSpec, raw: object) -> tuple[FieldValue | None, FieldError | None]:
    """Validate one submitted value against one field spec.

    Returns ``(value, None)`` on success, ``(None, error)`` on failure, and
    ``(None, None)`` when the field was left empty and is optional — three
    outcomes because "absent" and "invalid" must not be confused. Treating an
    empty optional field as an error makes a fourteen-field form unfillable;
    treating an invalid one as absent silently drops what the member typed.
    """
    kind = spec.kind

    if kind == KIND_BOOLEAN:
        # Booleans are checked first because "" is a *meaningful* false for a
        # checkbox, not an absent value, and the emptiness test below would
        # otherwise discard every unchecked box.
        flag = _normalize_boolean(raw)
        if flag is None:
            return None, FieldError(spec.path, ERR_BAD_BOOLEAN)
        return (
            FieldValue(
                path=spec.path,
                kind=kind,
                value_type=spec.value_type,
                value_text="true" if flag else "false",
                value_number=1.0 if flag else 0.0,
                sensitivity=spec.sensitivity,
                mask=spec.mask,
                searchable=spec.searchable,
            ),
            None,
        )

    if kind == KIND_MONEY:
        amount_raw: object
        currency_raw: object
        if isinstance(raw, Mapping):
            amount_raw = raw.get("amount")
            currency_raw = raw.get("currency")
        else:
            parts = _clean_text(raw).split()
            amount_raw = parts[0] if parts else ""
            currency_raw = parts[1] if len(parts) > 1 else ""
        amount = _normalize_number(amount_raw)
        currency = _clean_text(currency_raw).upper()
        if amount is None and not currency:
            if spec.required:
                return None, FieldError(spec.path, ERR_REQUIRED)
            return None, None
        if amount is None:
            return None, FieldError(spec.path, ERR_NOT_A_NUMBER)
        if currency and not _CURRENCY_RE.match(currency):
            return None, FieldError(spec.path, ERR_BAD_CURRENCY)
        bounds = _check_bounds(spec, amount)
        if bounds is not None:
            return None, bounds
        return (
            FieldValue(
                path=spec.path,
                kind=kind,
                value_type=spec.value_type,
                value_text=f"{amount:g} {currency}".strip(),
                value_number=amount,
                sensitivity=spec.sensitivity,
                mask=spec.mask,
                searchable=spec.searchable,
                currency=currency,
            ),
            None,
        )

    text = _clean_text(raw)
    if kind != KIND_LONG_TEXT:
        text = text.replace("\n", " ").strip()

    if not text:
        if spec.required:
            return None, FieldError(spec.path, ERR_REQUIRED)
        return None, None

    limit = MAX_TEXT_LENGTH if kind == KIND_LONG_TEXT else spec.max_length
    if len(text) > limit:
        return None, FieldError(spec.path, ERR_TOO_LONG, {"max_length": limit})

    number: float | None = None
    value_date = ""

    if kind == KIND_ENUM:
        allowed = {option.value: option.value for option in spec.options}
        folded = {option.value.casefold(): option.value for option in spec.options}
        canonical = allowed.get(text) or folded.get(text.casefold())
        if canonical is None:
            return None, FieldError(
                spec.path, ERR_BAD_ENUM, {"allowed": [o.value for o in spec.options]}
            )
        text = canonical

    elif kind == KIND_COUNTRY:
        text = text.upper()
        if text not in COUNTRY_CODES:
            return None, FieldError(spec.path, ERR_BAD_COUNTRY)

    elif kind == KIND_DATE:
        value_date = _normalize_date(text)
        if not value_date:
            return None, FieldError(spec.path, ERR_BAD_DATE)
        text = value_date

    elif kind == KIND_DATETIME:
        moment = _normalize_datetime(text)
        if not moment:
            return None, FieldError(spec.path, ERR_BAD_DATETIME)
        text = moment
        value_date = moment[:10]

    elif kind == KIND_NUMBER:
        number = _normalize_number(text)
        if number is None:
            return None, FieldError(spec.path, ERR_NOT_A_NUMBER)
        bounds = _check_bounds(spec, number)
        if bounds is not None:
            return None, bounds
        text = f"{number:g}"

    elif kind == KIND_EMAIL:
        text = text.lower()
        if not _EMAIL_RE.match(text):
            return None, FieldError(spec.path, ERR_BAD_EMAIL)

    elif kind == KIND_PHONE:
        compact = re.sub(r"[\s().-]", "", text)
        if not _PHONE_RE.match(compact):
            return None, FieldError(spec.path, ERR_BAD_PHONE)
        text = compact

    elif kind == KIND_URL:
        if not _URL_RE.match(text):
            return None, FieldError(spec.path, ERR_BAD_URL)

    elif kind == KIND_IDENTIFIER:
        # Identifiers are compared and masked, so internal whitespace is
        # normalised away. Case is *not* — some document numbers are
        # case-sensitive, and folding them would make two distinct documents
        # look like a duplicate.
        text = re.sub(r"\s+", "", text)

    if spec.pattern and not re.match(spec.pattern, text):
        return None, FieldError(spec.path, ERR_BAD_PATTERN)

    return (
        FieldValue(
            path=spec.path,
            kind=kind,
            value_type=spec.value_type,
            value_text=text,
            value_number=number,
            sensitivity=spec.sensitivity,
            mask=spec.mask,
            searchable=spec.searchable,
            value_date=value_date,
        ),
        None,
    )


def _check_bounds(spec: FieldSpec, number: float) -> FieldError | None:
    if spec.min_number is not None and number < spec.min_number:
        return FieldError(spec.path, ERR_OUT_OF_RANGE, {"min": spec.min_number})
    if spec.max_number is not None and number > spec.max_number:
        return FieldError(spec.path, ERR_OUT_OF_RANGE, {"max": spec.max_number})
    return None


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ValidationResult:
    """Everything the write path needs, or everything the form needs to fix."""

    ok: bool
    values: tuple[FieldValue, ...]
    errors: tuple[FieldError, ...]

    def errors_as_list(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.errors]

    def by_path(self) -> dict[str, FieldValue]:
        return {v.path: v for v in self.values}


def _index_of(path: str) -> int:
    match = re.search(r"\[([0-9]+)\]", path)
    return int(match.group(1)) if match else 0


def _template_path(path: str) -> str:
    """``holders[3].given_names`` -> ``holders[0].given_names``.

    Repeatable sections are declared once at index zero and submitted at any
    index. Collapsing the index is how a submitted path finds its spec, and
    doing it in one function is how the two places that need it cannot disagree.
    """
    return re.sub(r"\[[0-9]+\]", "[0]", path)


def validate_payload(
    template: Template,
    payload: Mapping[str, Any],
    *,
    partial: bool = False,
) -> ValidationResult:
    """Validate a whole submitted record against a template.

    ``partial`` is for drafts and patches: required fields that were not
    submitted at all are not errors, but anything present is validated exactly
    as strictly. A draft that accepts garbage is a draft that fails at save
    time, having let the member type for ten minutes first.

    Unknown paths are errors, not silently dropped. A client sending a field the
    server does not know is either an older server or a typo, and both are
    conditions the developer must see. Dropping them would mean data the member
    entered and watched disappear.
    """
    specs = template.field_map
    values: list[FieldValue] = []
    errors: list[FieldError] = []

    submitted: dict[str, Any] = {}
    for raw_path, raw_value in payload.items():
        path = str(raw_path or "").strip()
        if not path:
            continue
        submitted[path] = raw_value

    # Bound repeatable sections before validating, so a payload with a thousand
    # entries is rejected on the count rather than validated a thousand times.
    for section in template.sections:
        if not section.repeatable:
            continue
        prefix = section.fields[0].path.split("[")[0]
        indexes = {
            _index_of(path)
            for path in submitted
            if path.split("[")[0] == prefix and "[" in path
        }
        if len(indexes) > section.max_entries:
            errors.append(
                FieldError(prefix, ERR_TOO_MANY_ENTRIES, {"max": section.max_entries})
            )

    for path, raw_value in submitted.items():
        spec = specs.get(_template_path(path))
        if spec is None:
            errors.append(FieldError(path, ERR_UNKNOWN_FIELD))
            continue
        value, error = normalize_field(spec, raw_value)
        if error is not None:
            # Report the error at the *submitted* path, so a repeatable section
            # highlights the entry the member actually got wrong.
            errors.append(FieldError(path, error.code, error.detail))
            continue
        if value is not None:
            # Re-stamp the submitted path. ``normalize_field`` works from the
            # spec, whose path is the index-zero declaration, so an entry at
            # ``holders[2]`` would otherwise be stored as ``holders[0]`` and
            # silently overwrite the first one.
            values.append(value if value.path == path else replace(value, path=path))

    if not partial:
        present = {p for p in submitted}
        for spec in template.fields:
            if not spec.required:
                continue
            if "[" in spec.path:
                # A required field inside a repeatable section is required per
                # submitted entry, which the loop above already enforced for
                # every entry that appeared. A section with no entries at all is
                # a section-level question, not a field-level one.
                continue
            if spec.path not in present:
                errors.append(FieldError(spec.path, ERR_REQUIRED))

    return ValidationResult(ok=not errors, values=tuple(values), errors=tuple(errors))


# ---------------------------------------------------------------------------
# Masking and search projection
# ---------------------------------------------------------------------------
def mask_value(mask: str, kind: str, value_text: str) -> str:
    """What the screen shows for a masked field.

    The raw value is never the answer. Every branch either withholds entirely or
    withholds enough that the remainder is not the secret: last four of an
    identifier, the year of a date, the initials of a name. A caller that wants
    the original calls the reveal path, which requires a live Office grant and a
    fresh step-up, and writes an audit event.

    An unrecognised mask returns full concealment. Failing closed here matters
    more than usual: the failure mode of the other direction is printing a
    passport number because someone typoed a strategy name.
    """
    strategy = normalize_mask(mask) or MASK_FULL
    text = str(value_text or "")
    if not text:
        return ""
    if strategy == MASK_NONE:
        return text
    if strategy == MASK_LAST4:
        tail = text[-4:]
        return f"{_MASK_RUN} {tail}" if len(text) > 4 else _MASK_RUN
    if strategy == MASK_YEAR:
        if kind in DATE_KINDS and len(text) >= 4:
            return text[:4]
        return _MASK_RUN
    if strategy == MASK_INITIALS:
        initials = [part[0].upper() for part in re.split(r"[\s-]+", text) if part]
        return ". ".join(initials[:3]) + "." if initials else _MASK_RUN
    return _MASK_RUN


def display_value(value: FieldValue) -> dict[str, Any]:
    """The read-path projection of one field.

    Note the shape: a masked field carries ``value`` set to the *masked* string
    and ``masked: true``. There is no key holding the raw value alongside it,
    because a response with both is a response one careless client logs in full.
    """
    masked = value.mask != MASK_NONE
    return {
        "path": value.path,
        "kind": value.kind,
        "value": mask_value(value.mask, value.kind, value.value_text) if masked else value.value_text,
        "masked": masked,
        "sensitivity": value.sensitivity,
        "revealable": masked,
    }


def search_index_text(value: FieldValue) -> str:
    """What may enter the search index for this field.

    The single decision point for "is this safe to index". A masked field is
    indexed on its masked form only, which is what makes a suggestion line like
    ``Passport • United States • ending 1234`` possible without the index ever
    holding the number. A non-searchable field contributes nothing at all.
    """
    if not value.searchable:
        return ""
    if value.mask != MASK_NONE:
        return mask_value(value.mask, value.kind, value.value_text)
    return value.value_text


def undx_projection(template: Template, values: Iterable[FieldValue]) -> list[dict[str, Any]]:
    """The subset of a record UNDX is permitted to read.

    Two gates, both of which must pass: the template must be readable at all,
    and the field must be readable. Masked values are excluded outright rather
    than passed through masked — a model given ``•••• 1234`` will cheerfully
    reason about "the passport ending 1234" and put it in a summary, which is
    the leak the mask existed to prevent, laundered through an assistant.
    """
    if not template.undx_readable:
        return []
    specs = template.field_map
    out: list[dict[str, Any]] = []
    for value in values:
        spec = specs.get(_template_path(value.path))
        if spec is None or not spec.undx_readable or spec.mask != MASK_NONE:
            continue
        out.append({"path": value.path, "kind": value.kind, "value": value.value_text})
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, dict[int, Template]] = {}
_LOADED = False


def register(template: Template) -> Template:
    """Add a template to the registry.

    Registration is where a malformed template fails, which is why the dataclass
    validates in ``__post_init__`` and this function only guards uniqueness: by
    the time a Template object exists it is already internally consistent.
    """
    versions = _REGISTRY.setdefault(template.key, {})
    if template.version in versions:
        raise TemplateError(f"{template.schema_key} is registered twice")
    versions[template.version] = template
    return template


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    # Imported lazily and inside the guard: the catalog imports this module, so
    # a module-level import would close the ring at startup — the same reason
    # ``entitlements.owner`` imports ``premium`` inside its functions.
    from services.private_office import record_template_catalog  # noqa: F401

    _LOADED = True


def get_template(key: object, version: object = None) -> Template | None:
    """A template by key, at a version, or the latest.

    ``None`` for anything unknown. A caller handed ``None`` must refuse the
    write: a record whose shape nobody can name is a record nothing downstream
    can validate, search or migrate.
    """
    _load()
    versions = _REGISTRY.get(str(key or "").strip().lower())
    if not versions:
        return None
    if version is None or str(version).strip() == "":
        return versions[max(versions)]
    try:
        wanted = int(version)
    except (TypeError, ValueError):
        return None
    return versions.get(wanted)


def get_by_schema_key(schema_key: object) -> Template | None:
    """A template from a stored ``key@version`` discriminator."""
    text = str(schema_key or "").strip()
    if "@" not in text:
        return get_template(text)
    key, _, version = text.partition("@")
    return get_template(key, version)


def latest_templates() -> tuple[Template, ...]:
    """The current version of every registered template, ordered by key."""
    _load()
    return tuple(
        _REGISTRY[key][max(_REGISTRY[key])] for key in sorted(_REGISTRY)
    )


def templates_for_ia_domain(ia_domain: object) -> tuple[Template, ...]:
    wanted = str(ia_domain or "").strip().lower()
    return tuple(t for t in latest_templates() if t.ia_domain == wanted)


def manifest(*, ia_domain: object = None) -> dict[str, Any]:
    """The client-facing template document.

    :data:`CONTRACT_VERSION` is first and is the thing a client checks before
    reading anything else. ``reference_lists`` carries the bounded vocabularies
    a form needs to render a picker without inventing its own copy — today just
    country codes, which is exactly the kind of list that drifts when two layers
    each keep one.
    """
    templates = (
        templates_for_ia_domain(ia_domain) if ia_domain else latest_templates()
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "field_kinds": list(FIELD_KINDS),
        "mask_strategies": list(MASK_STRATEGIES),
        "reference_lists": {"country": sorted(COUNTRY_CODES)},
        "templates": [t.as_manifest() for t in templates],
    }


def legacy_fact_type_map() -> dict[str, tuple[str, str]]:
    """``fact_type`` -> ``(schema_key, field_path)`` for the migration.

    Only deterministic mappings appear here, and only because a template author
    stated them. Everything else migrates to a legacy custom record — the brief
    is explicit that a passport's structure must never be guessed from a fact
    named ``passport_info``, and the way to guarantee that is for this map to
    contain nothing nobody wrote down.
    """
    out: dict[str, tuple[str, str]] = {}
    for template in latest_templates():
        for fact_type, path in template.legacy_fact_types:
            key = str(fact_type or "").strip().lower()
            if key and key not in out:
                out[key] = (template.schema_key, path)
    return out


def registry_health() -> dict[str, Any]:
    """A cheap self-check for the status endpoint and the test suite."""
    _load()
    return {
        "contract_version": CONTRACT_VERSION,
        "template_count": len(_REGISTRY),
        "version_count": sum(len(v) for v in _REGISTRY.values()),
        "keys": sorted(_REGISTRY),
    }


def _reset_for_tests() -> None:
    """Clear the registry and forget the catalog import. Tests only.

    Dropping the catalog from ``sys.modules`` is the load-bearing half. Clearing
    the dict alone would leave the module cached, so the next :func:`_load`
    would import a module that had already run its ``register`` calls and
    register nothing — an empty registry that every subsequent lookup answers
    ``None`` from, which is a far more confusing failure than the one the reset
    was meant to produce.
    """
    import sys

    global _LOADED
    _REGISTRY.clear()
    _LOADED = False
    sys.modules.pop("services.private_office.record_template_catalog", None)


__all__ = [
    "CONTRACT_VERSION",
    "FIELD_KINDS",
    "MASK_STRATEGIES",
    "COUNTRY_CODES",
    "AUTOMATION_HOOKS",
    "TemplateError",
    "FieldError",
    "Option",
    "FieldSpec",
    "SectionSpec",
    "ReminderRule",
    "Template",
    "FieldValue",
    "ValidationResult",
    "normalize_field",
    "validate_payload",
    "mask_value",
    "display_value",
    "search_index_text",
    "undx_projection",
    "register",
    "get_template",
    "get_by_schema_key",
    "latest_templates",
    "templates_for_ia_domain",
    "manifest",
    "legacy_fact_type_map",
    "registry_health",
]
