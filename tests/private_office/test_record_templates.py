"""The record template contract — validation, masking, and the UNDX boundary.

Run either way::

    python -m pytest tests/private_office/test_record_templates.py
    python tests/private_office/test_record_templates.py

This module needs no database. The template contract is deliberately pure — it
turns a submitted payload into a typed projection and never touches storage —
and that purity is what lets these checks run in milliseconds and be trusted as
the definition of the boundary rather than a description of one implementation
of it.

What these tests defend
-----------------------
* **A masked value has no read path that returns it.** Not the display
  projection, not the search index, not the UNDX projection. Three separate
  assertions because they are three separate functions, and the leak this
  guards against historically arrives through whichever one nobody checked.
* **RESTRICTED is structurally excluded from UNDX.** Not by a call-site check
  that a future caller might forget — by a registration-time refusal, so a
  template declaring a restricted field readable cannot be imported at all.
* **Health records cannot reach the graph.** The Template validator raises on a
  HEALTH template that names a node type, so the rule is enforced against
  template authors who were not in the room when it was decided.
* **No template asks for a credential.** Asserted over every field path in the
  catalog, so a future template that adds a password field fails here rather
  than in a breach.
* **Errors never echo the value they rejected.** Error bodies are logged; an
  error that quotes a passport number puts it in the log.
* **A malformed template fails loudly at import, not quietly at first write.**
  Nine separate structural mistakes are each asserted to raise.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.private_office import model  # noqa: E402
from services.private_office import record_template_catalog as catalog  # noqa: E402
from services.private_office import record_templates as rt  # noqa: E402


_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  ok   {label}")
        return True
    print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    return False


def raises_template_error(label: str, build) -> None:
    try:
        build()
    except rt.TemplateError as exc:
        check(label, True)
        print(f"       ({exc})")
        return
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"raised {type(exc).__name__} instead of TemplateError: {exc}")
        return
    check(label, False, "did not raise")


# A well-formed passport payload, reused by several stages. Written once so a
# stage that needs one bad field can copy it and change exactly that field —
# which is what makes those stages readable as "this and only this is wrong".
GOOD_PASSPORT: dict[str, object] = {
    "identification.surname": "Okonkwo",
    "identification.given_names": "Amara Chidinma",
    "identification.date_of_birth": "1987-04-02",
    "identification.nationality": "US",
    "identification.sex": "F",
    "issuance.document_number": "547991234",
    "issuance.issuing_country": "US",
    "issuance.expiry_date": "2030-12-11",
    "issuance.document_type": "ORDINARY",
}

#: Every substring that names a secret this product must never hold. Checked
#: against field paths *and* fallback labels, because a field named
#: ``account.detail`` labelled "Password" is the same mistake wearing a
#: different hat.
FORBIDDEN_TOKENS = (
    "password", "passphrase", "seed_phrase", "seed phrase", "private_key",
    "private key", "secret_key", "mnemonic", "cvv", "cvc", "security code",
    "pin_code", "api_key", "api key", "access_token", "recovery_code",
    "recovery code", "2fa_seed", "totp_secret", "withdrawal",
)


# ---------------------------------------------------------------------------
def stage_registry_loads() -> None:
    print("\n[1] the registry loads and every shipped template is well formed")
    health = rt.registry_health()
    check("contract version is exported", health["contract_version"] == rt.CONTRACT_VERSION)
    check("templates are registered", health["template_count"] >= 16,
          f"got {health['template_count']}")
    check("passport is present", "passport" in health["keys"])

    templates = rt.latest_templates()
    check("latest_templates returns one per key",
          len(templates) == health["template_count"])
    check("every template exposes a schema key",
          all("@" in t.schema_key for t in templates))

    # Every IA domain the navigation offers must have somewhere to land. A
    # domain chip that opens an empty list is a promise the catalog did not
    # keep, and it is the kind of gap that survives review because nothing
    # errors.
    for ia_key, _label_key, label in catalog.IA_DOMAINS:
        found = rt.templates_for_ia_domain(ia_key)
        check(f"IA domain {label!r} has at least one template", bool(found))

    check("an unknown key resolves to None", rt.get_template("no_such_template") is None)
    check("an unknown version resolves to None", rt.get_template("passport", 99) is None)
    check("a non-numeric version resolves to None", rt.get_template("passport", "x") is None)
    check("schema keys round-trip",
          rt.get_by_schema_key("passport@1") is rt.get_template("passport", 1))
    check("a bare key round-trips as latest",
          rt.get_by_schema_key("passport") is rt.get_template("passport"))


def stage_structural_invariants() -> None:
    print("\n[2] invariants that hold across every shipped template")
    for template in rt.latest_templates():
        name = template.key
        floor = model.SENSITIVITY_RANK[template.sensitivity]

        for spec in template.fields:
            check(
                f"{name}.{spec.path}: kind maps to a storage type",
                spec.kind in rt.KIND_VALUE_TYPES,
            )
            check(
                f"{name}.{spec.path}: sensitivity is at or above its record",
                model.SENSITIVITY_RANK[spec.sensitivity] >= floor,
            )
            # The structural exclusion. A RESTRICTED field readable by UNDX
            # cannot exist, because `FieldSpec.__post_init__` refuses to build
            # one — this asserts the refusal actually held for the catalog.
            if spec.sensitivity == model.SENSITIVITY_RESTRICTED:
                check(
                    f"{name}.{spec.path}: RESTRICTED implies not UNDX-readable",
                    not spec.undx_readable,
                )

        if template.reminders:
            check(
                f"{name}: reminders require something that expires",
                bool(template.expiration_path),
            )
            offsets = [r.offset_days for r in template.reminders]
            check(f"{name}: reminder offsets are non-negative", all(o >= 0 for o in offsets))
            check(f"{name}: reminder keys are unique",
                  len({r.key for r in template.reminders}) == len(offsets))

        check(f"{name}: default status is a member of its status set",
              template.default_status in template.statuses)
        check(f"{name}: at most one expiration field",
              sum(1 for f in template.fields if f.expires_record) <= 1)


def stage_no_credential_fields() -> None:
    print("\n[3] nothing in the catalog asks for a credential")
    for template in rt.latest_templates():
        for spec in template.fields:
            haystack = f"{spec.path} {spec.label_fallback} {spec.help_fallback}".lower()
            hits = [token for token in FORBIDDEN_TOKENS if token in haystack]
            # The help text on the financial account and the digital account
            # deliberately *names* these words in order to tell the member not
            # to record them. That is the one legitimate appearance, and it is
            # only legitimate in help text.
            in_help_only = all(token in spec.help_fallback.lower() for token in hits)
            check(
                f"{template.key}.{spec.path} does not solicit a secret",
                not hits or in_help_only,
                f"matched {hits}",
            )


def stage_health_isolation() -> None:
    print("\n[4] health records are isolated structurally, not by convention")
    health_templates = [
        t for t in rt.latest_templates() if t.domain == model.DOMAIN_HEALTH
    ]
    check("the catalog ships at least one health template", bool(health_templates))
    for template in health_templates:
        check(f"{template.key}: no graph projection", template.graph_node_type == "")
        check(f"{template.key}: not UNDX-readable at all", not template.undx_readable)
        check(f"{template.key}: not UNDX-draftable", not template.undx_draftable)
        check(
            f"{template.key}: no briefing automation",
            rt.HOOK_EXPIRATION_TO_BRIEFING not in template.automations,
        )
        check(
            f"{template.key}: record floor is at least HIGHLY_SENSITIVE",
            model.SENSITIVITY_RANK[template.sensitivity]
            >= model.SENSITIVITY_RANK[model.SENSITIVITY_HIGHLY_SENSITIVE],
        )

    # And the rule is enforced against future authors, not just observed on the
    # current ones.
    raises_template_error(
        "a HEALTH template naming a graph node type is refused",
        lambda: rt.Template(
            key="bad_health", version=1, domain=model.DOMAIN_HEALTH,
            ia_domain="health_medical", display_key="k", display_fallback="k",
            description_key="d", description_fallback="d", icon="i",
            sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
            statuses=("ACTIVE",), default_status="ACTIVE",
            graph_node_type=model.NODE_PERSON,
            sections=(
                rt.SectionSpec(
                    key="s", label_key="k", label_fallback="s",
                    fields=(rt.FieldSpec(
                        path="s.a", kind=rt.KIND_TEXT, label_key="k",
                        label_fallback="a",
                        sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    ),),
                ),
            ),
        ),
    )


def stage_passport_validation() -> None:
    print("\n[5] the passport form validates what it claims to")
    passport = rt.get_template("passport")

    result = rt.validate_payload(passport, GOOD_PASSPORT)
    check("a complete passport validates", result.ok, str(result.errors_as_list()))
    values = result.by_path()
    check("nine submitted fields produce nine values", len(result.values) == 9)

    # Required fields.
    for missing in ("issuance.document_number", "issuance.expiry_date",
                    "identification.surname", "identification.nationality"):
        payload = {k: v for k, v in GOOD_PASSPORT.items() if k != missing}
        outcome = rt.validate_payload(passport, payload)
        codes = {(e.path, e.code) for e in outcome.errors}
        check(f"{missing} is required", (missing, rt.ERR_REQUIRED) in codes,
              str(outcome.errors_as_list()))

    # Type validation.
    bad_cases = (
        ("identification.nationality", "Untied Sates", rt.ERR_BAD_COUNTRY),
        ("issuance.issuing_country", "USA", rt.ERR_BAD_COUNTRY),
        ("issuance.expiry_date", "December 2030", rt.ERR_BAD_DATE),
        ("issuance.expiry_date", "2030-13-45", rt.ERR_BAD_DATE),
        ("identification.sex", "ANDROID", rt.ERR_BAD_ENUM),
        ("issuance.document_type", "SUPER_DIPLOMATIC", rt.ERR_BAD_ENUM),
        ("mrz.line1", "P" * 45, rt.ERR_TOO_LONG),
    )
    for path, value, expected in bad_cases:
        payload = dict(GOOD_PASSPORT)
        payload[path] = value
        outcome = rt.validate_payload(passport, payload)
        codes = {(e.path, e.code) for e in outcome.errors}
        check(f"{path}={value!r} is rejected as {expected}",
              (path, expected) in codes, str(outcome.errors_as_list()))

    # Unknown fields are reported, not silently dropped. Silently dropping is
    # how a member watches data they typed disappear on save.
    outcome = rt.validate_payload(passport, {**GOOD_PASSPORT, "issuance.blood_type": "O"})
    codes = {(e.path, e.code) for e in outcome.errors}
    check("an unknown field is an error", ("issuance.blood_type", rt.ERR_UNKNOWN_FIELD) in codes)

    # Drafts accept incompleteness and still reject nonsense.
    draft = rt.validate_payload(passport, {"identification.surname": "Okonkwo"}, partial=True)
    check("a draft with one field validates", draft.ok, str(draft.errors_as_list()))
    bad_draft = rt.validate_payload(
        passport, {"issuance.issuing_country": "ZZZ"}, partial=True
    )
    check("a draft still rejects a bad country", not bad_draft.ok)

    # Normalisation. An identifier typed with spaces and a country typed in
    # lower case must land on the same stored value as the clean form, or
    # duplicate detection sees two documents where there is one.
    messy = dict(GOOD_PASSPORT)
    messy["issuance.document_number"] = " 547 991 234 "
    messy["issuance.issuing_country"] = "us"
    messy["identification.sex"] = "f"
    normalised = rt.validate_payload(passport, messy)
    check("a messy payload validates", normalised.ok, str(normalised.errors_as_list()))
    got = normalised.by_path()
    check("the identifier normalises to the clean form",
          got["issuance.document_number"].value_text == "547991234",
          got["issuance.document_number"].value_text)
    check("the country normalises to upper case",
          got["issuance.issuing_country"].value_text == "US")
    check("the enum normalises to its canonical token",
          got["identification.sex"].value_text == "F")

    # A datetime submitted for a date field keeps the date the member picked.
    dated = dict(GOOD_PASSPORT)
    dated["identification.date_of_birth"] = "1987-04-02T23:30:00+02:00"
    outcome = rt.validate_payload(passport, dated)
    check("a datetime on a date field keeps its calendar date",
          outcome.by_path()["identification.date_of_birth"].value_text == "1987-04-02")

    # Expiration is discoverable without parsing the form.
    check("the passport's expiration field is its expiry date",
          passport.expiration_path == "issuance.expiry_date")
    check("the expiry value carries an indexable date",
          values["issuance.expiry_date"].value_date == "2030-12-11")


def stage_passport_secrecy() -> None:
    print("\n[6] the passport number has no read path that returns it")
    passport = rt.get_template("passport")
    payload = dict(GOOD_PASSPORT)
    payload["mrz.line1"] = "P<USAOKONKWO<<AMARA<CHIDINMA<<<<<<<<<<<<<<<<"
    payload["mrz.line2"] = "5479912344USA8704022F3012113<<<<<<<<<<<<<<06"
    result = rt.validate_payload(passport, payload)
    check("the payload with an MRZ validates", result.ok, str(result.errors_as_list()))
    values = result.by_path()

    number = values["issuance.document_number"]
    raw = "547991234"

    check("the number is stored RESTRICTED",
          number.sensitivity == model.SENSITIVITY_RESTRICTED)
    check("the number's mask is last-four", number.mask == rt.MASK_LAST4)

    shown = rt.display_value(number)
    check("the display projection shows only the last four",
          shown["value"] == "•••• 1234", shown["value"])
    check("the display projection is flagged masked", shown["masked"] is True)
    check("the display projection is flagged revealable", shown["revealable"] is True)
    check("the display projection carries no key holding the raw value",
          raw not in repr(shown), repr(shown))

    indexed = rt.search_index_text(number)
    check("the search index holds only the masked form", indexed == "•••• 1234", indexed)
    check("the search index does not contain the number", raw not in indexed)

    # The MRZ contains the number, the birth date and the expiry in one string,
    # so it is masked completely rather than partially.
    for path in ("mrz.line1", "mrz.line2"):
        mrz = values[path]
        rendered = rt.display_value(mrz)
        check(f"{path} is fully masked", rendered["value"] == "••••", rendered["value"])
        check(f"{path} contributes nothing to the index",
              rt.search_index_text(mrz) == "")
        check(f"{path} does not leak the number",
              raw not in rendered["value"])

    # The birth date shows its year and nothing finer.
    dob = rt.display_value(values["identification.date_of_birth"])
    check("the birth date shows only its year", dob["value"] == "1987", dob["value"])
    check("the birth date does not show its month or day", "04" not in dob["value"])

    # UNDX.
    visible = {item["path"] for item in rt.undx_projection(passport, result.values)}
    for hidden in ("issuance.document_number", "identification.date_of_birth",
                   "identification.place_of_birth", "identification.sex",
                   "mrz.line1", "mrz.line2"):
        check(f"UNDX cannot see {hidden}", hidden not in visible)
    for allowed in ("issuance.expiry_date", "issuance.issuing_country",
                    "identification.surname"):
        check(f"UNDX can see {allowed}", allowed in visible)
    check("no UNDX-visible value contains the number",
          all(raw not in str(item["value"]) for item in rt.undx_projection(passport, result.values)))

    # And a whole-template exclusion outranks any field that says otherwise.
    condition = rt.get_template("medical_condition")
    health_result = rt.validate_payload(condition, {
        "condition.name": "Annual review",
        "condition.category": "CONDITION",
    })
    check("the health payload validates", health_result.ok,
          str(health_result.errors_as_list()))
    check("UNDX sees nothing at all on a health record",
          rt.undx_projection(condition, health_result.values) == [])


def stage_error_bodies_are_safe() -> None:
    print("\n[7] an error never echoes the value it rejected")
    passport = rt.get_template("passport")
    secret = "SUPERSECRET99887766"
    payload = dict(GOOD_PASSPORT)
    payload["issuance.document_number"] = secret * 5  # over max_length
    payload["issuance.issuing_country"] = secret
    payload["identification.sex"] = secret
    outcome = rt.validate_payload(passport, payload)
    check("the bad payload is rejected", not outcome.ok)
    body = repr(outcome.errors_as_list())
    check("no error detail echoes the rejected value", secret not in body, body)
    check("every error code is from the closed set",
          all(e.code in rt.ERROR_CODES for e in outcome.errors))


def stage_manifest_shape() -> None:
    print("\n[8] the client manifest carries a contract version and no server internals")
    doc = rt.manifest()
    check("the manifest states its contract version",
          doc["contract_version"] == rt.CONTRACT_VERSION)
    check("the manifest enumerates the field kinds it may use",
          set(doc["field_kinds"]) == set(rt.FIELD_KINDS))
    check("the manifest ships the country reference list",
          len(doc["reference_lists"]["country"]) == len(rt.COUNTRY_CODES))
    check("US is a valid country code", "US" in rt.COUNTRY_CODES)
    check("USA is not (alpha-2 only)", "USA" not in rt.COUNTRY_CODES)

    passport_doc = next(t for t in doc["templates"] if t["key"] == "passport")
    check("the manifest names the expiration path",
          passport_doc["expiration_path"] == "issuance.expiry_date")
    check("the manifest carries the nine document statuses",
          tuple(passport_doc["statuses"]) == catalog.DOCUMENT_STATUSES,
          str(passport_doc["statuses"]))
    check("the manifest carries the renewal ladder",
          [r["offset_days"] for r in passport_doc["reminders"]] == [365, 274, 183, 91, 30],
          str(passport_doc["reminders"]))

    fields = {
        f["path"]: f
        for section in passport_doc["sections"]
        for f in section["fields"]
    }
    number = fields["issuance.document_number"]
    check("the manifest tells the client the number is masked", number["masked"] is True)
    check("the manifest does not expose the duplicate-detection flag",
          "identity" not in number)
    check("the manifest does not expose per-field UNDX policy",
          "undx_readable" not in number)
    check("the manifest does not expose legacy migration mappings",
          "legacy_fact_types" not in passport_doc)
    check("the manifest carries no storage vocabulary",
          "value_type" not in number and "table" not in passport_doc)

    # A filtered manifest is a strict subset. The Add Record flow fetches one
    # domain at a time, and a filter that silently returned everything would
    # ship the whole catalog to a client that asked for one chip.
    identity_doc = rt.manifest(ia_domain=catalog.IA_IDENTITY)
    keys = {t["key"] for t in identity_doc["templates"]}
    check("a filtered manifest contains the domain's templates",
          {"passport", "drivers_licence", "national_id"} <= keys)
    check("a filtered manifest excludes other domains", "vehicle" not in keys)


def stage_legacy_mapping_is_stated_not_guessed() -> None:
    print("\n[9] legacy migration maps only what a template author wrote down")
    mapping = rt.legacy_fact_type_map()
    check("some legacy mappings exist", bool(mapping))
    for fact_type, (schema_key, path) in mapping.items():
        template = rt.get_by_schema_key(schema_key)
        check(f"{fact_type!r} targets a real template", template is not None)
        check(f"{fact_type!r} targets a real field",
              template is not None and path in template.field_map)

    # The rule the brief states outright: never guess a passport, medical,
    # financial or legal structure from an ambiguous legacy name. The way that
    # rule is kept is that those templates claim no legacy fact types at all,
    # so every such fact lands in `legacy_record` with its text intact.
    for key in ("passport", "drivers_licence", "national_id", "medical_condition",
                "financial_account", "contract", "visa"):
        template = rt.get_template(key)
        check(f"{key} claims no legacy fact types", template.legacy_fact_types == ())

    legacy = rt.get_template("legacy_record")
    check("the legacy landing template exists", legacy is not None)
    check("legacy records land needing review", legacy.default_status == "NEEDS_REVIEW")
    check("the legacy template preserves the original fact type",
          "legacy.fact_type" in legacy.field_map)
    check("the legacy template preserves the original value",
          "legacy.value" in legacy.field_map)


def stage_repeatable_sections() -> None:
    print("\n[10] repeatable sections index correctly and are bounded")
    template = rt.Template(
        key="synthetic_repeatable", version=1, domain=model.DOMAIN_GENERAL,
        ia_domain="custom", display_key="k", display_fallback="k",
        description_key="d", description_fallback="d", icon="i",
        statuses=("ACTIVE",), default_status="ACTIVE",
        sections=(
            rt.SectionSpec(
                key="holders", label_key="k", label_fallback="Holders",
                repeatable=True, max_entries=3,
                fields=(
                    rt.FieldSpec(
                        path="holders[0].name", kind=rt.KIND_PERSON_NAME,
                        label_key="k", label_fallback="Name", searchable=True,
                    ),
                ),
            ),
        ),
    )
    result = rt.validate_payload(template, {
        "holders[0].name": "Ada",
        "holders[2].name": "Grace",
    })
    check("two entries at non-contiguous indexes validate", result.ok,
          str(result.errors_as_list()))
    paths = {v.path for v in result.values}
    check("each entry keeps its own index", paths == {"holders[0].name", "holders[2].name"},
          str(paths))
    check("entries do not collapse onto index zero", len(result.values) == 2)

    over = rt.validate_payload(template, {
        f"holders[{i}].name": f"P{i}" for i in range(5)
    })
    codes = {(e.path, e.code) for e in over.errors}
    check("exceeding max_entries is rejected",
          ("holders", rt.ERR_TOO_MANY_ENTRIES) in codes, str(over.errors_as_list()))


def stage_malformed_templates_are_refused() -> None:
    print("\n[11] a malformed template fails at import, not at first write")

    def field(**kwargs):
        base = dict(path="s.a", kind=rt.KIND_TEXT, label_key="k", label_fallback="a")
        base.update(kwargs)
        return rt.FieldSpec(**base)

    def template(**kwargs):
        base = dict(
            key="synthetic", version=1, domain=model.DOMAIN_GENERAL,
            ia_domain="custom", display_key="k", display_fallback="k",
            description_key="d", description_fallback="d", icon="i",
            statuses=("ACTIVE",), default_status="ACTIVE",
            sections=(rt.SectionSpec(
                key="s", label_key="k", label_fallback="s", fields=(field(),),
            ),),
        )
        base.update(kwargs)
        return rt.Template(**base)

    raises_template_error("a field path with a capital letter",
                          lambda: field(path="s.Amount"))
    raises_template_error("an unknown field kind",
                          lambda: field(kind="colour_picker"))
    raises_template_error("an unknown sensitivity",
                          lambda: field(sensitivity="SECRET"))
    raises_template_error("an unknown mask strategy",
                          lambda: field(mask="redact"))
    raises_template_error("an enum with no options",
                          lambda: field(kind=rt.KIND_ENUM))
    raises_template_error("options on a non-enum field",
                          lambda: field(options=(rt.Option("A", "k", "A"),)))
    raises_template_error("expiration driven by a text field",
                          lambda: field(expires_record=True))
    raises_template_error("a RESTRICTED field left UNDX-readable",
                          lambda: field(sensitivity=model.SENSITIVITY_RESTRICTED,
                                        mask=rt.MASK_FULL, undx_readable=True))
    raises_template_error("a pattern that does not compile",
                          lambda: field(pattern="[unclosed"))
    raises_template_error("a max_length beyond the hard ceiling",
                          lambda: field(max_length=rt.MAX_TEXT_LENGTH + 1))

    raises_template_error("a section with no fields",
                          lambda: rt.SectionSpec(key="s", label_key="k",
                                                 label_fallback="s", fields=()))
    raises_template_error("a repeatable section allowing one entry",
                          lambda: rt.SectionSpec(key="s", label_key="k",
                                                 label_fallback="s", repeatable=True,
                                                 max_entries=1, fields=(field(),)))
    raises_template_error(
        "a repeatable section whose field path has no index",
        lambda: template(sections=(rt.SectionSpec(
            key="s", label_key="k", label_fallback="s",
            repeatable=True, max_entries=2, fields=(field(),),
        ),)),
    )
    raises_template_error("a default status outside the status set",
                          lambda: template(default_status="ARCHIVED"))
    raises_template_error("an unknown domain",
                          lambda: template(domain="SPORTS"))
    raises_template_error("an unknown automation hook",
                          lambda: template(automations=("SEND_EMAIL",)))
    raises_template_error("an unknown graph relation",
                          lambda: template(graph_relations=("LIKES",)))
    raises_template_error(
        "a field less sensitive than its own record",
        lambda: template(
            sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
            sections=(rt.SectionSpec(
                key="s", label_key="k", label_fallback="s",
                fields=(field(sensitivity=model.SENSITIVITY_INTERNAL),),
            ),),
        ),
    )
    raises_template_error(
        "reminders on a template with nothing that expires",
        lambda: template(reminders=(rt.ReminderRule("d7", 7, "k", "7 days"),)),
    )
    raises_template_error(
        "two fields driving expiration",
        lambda: template(sections=(rt.SectionSpec(
            key="s", label_key="k", label_fallback="s",
            fields=(
                field(path="s.a", kind=rt.KIND_DATE, expires_record=True),
                field(path="s.b", kind=rt.KIND_DATE, expires_record=True),
            ),
        ),)),
    )
    raises_template_error("a negative reminder offset",
                          lambda: rt.ReminderRule("d7", -7, "k", "late"))


def stage_masking_fails_closed() -> None:
    print("\n[12] masking fails closed on anything it does not recognise")
    check("an unknown strategy conceals entirely",
          rt.mask_value("shred", rt.KIND_TEXT, "547991234") == "••••")
    check("an empty strategy conceals entirely",
          rt.mask_value("", rt.KIND_TEXT, "547991234") == "••••")
    check("None conceals entirely",
          rt.mask_value(None, rt.KIND_TEXT, "547991234") == "••••")

    # A short value must not become its own mask. "12" masked to last-four is
    # "12", which is the whole secret.
    check("a value shorter than the mask window is concealed entirely",
          rt.mask_value(rt.MASK_LAST4, rt.KIND_IDENTIFIER, "12") == "••••")
    check("a value exactly the mask window is concealed entirely",
          rt.mask_value(rt.MASK_LAST4, rt.KIND_IDENTIFIER, "1234") == "••••")
    check("a longer value shows its last four",
          rt.mask_value(rt.MASK_LAST4, rt.KIND_IDENTIFIER, "12345") == "•••• 2345")

    # The mask length must not track the secret length, or it leaks the format.
    short = rt.mask_value(rt.MASK_FULL, rt.KIND_TEXT, "ab")
    long = rt.mask_value(rt.MASK_FULL, rt.KIND_TEXT, "a" * 200)
    check("full concealment is a fixed width regardless of input", short == long)

    check("year masking on a non-date conceals entirely",
          rt.mask_value(rt.MASK_YEAR, rt.KIND_TEXT, "547991234") == "••••")
    check("initials masking keeps only initials",
          rt.mask_value(rt.MASK_INITIALS, rt.KIND_PERSON_NAME, "Amara Chidinma Okonkwo")
          == "A. C. O.")
    check("an empty value masks to empty",
          rt.mask_value(rt.MASK_LAST4, rt.KIND_IDENTIFIER, "") == "")


def stage_registry_rejects_duplicates() -> None:
    print("\n[13] a key registered twice at one version is refused")
    spec = rt.Template(
        key="synthetic_dup", version=1, domain=model.DOMAIN_GENERAL,
        ia_domain="custom", display_key="k", display_fallback="k",
        description_key="d", description_fallback="d", icon="i",
        statuses=("ACTIVE",), default_status="ACTIVE",
        sections=(rt.SectionSpec(
            key="s", label_key="k", label_fallback="s",
            fields=(rt.FieldSpec(path="s.a", kind=rt.KIND_TEXT,
                                 label_key="k", label_fallback="a"),),
        ),),
    )
    try:
        rt.register(spec)
        raises_template_error("registering the same key and version twice",
                              lambda: rt.register(spec))
    finally:
        # The registry is process-global. Leaving a synthetic template in it
        # would make a later stage's `latest_templates()` assertions depend on
        # the order the stages ran in, which is exactly the class of bug this
        # directory's conftest exists to prevent.
        rt._REGISTRY.pop("synthetic_dup", None)
    check("the synthetic template is gone again",
          rt.get_template("synthetic_dup") is None)


def test_everything() -> None:
    stage_registry_loads()
    stage_structural_invariants()
    stage_no_credential_fields()
    stage_health_isolation()
    stage_passport_validation()
    stage_passport_secrecy()
    stage_error_bodies_are_safe()
    stage_manifest_shape()
    stage_legacy_mapping_is_stated_not_guessed()
    stage_repeatable_sections()
    stage_malformed_templates_are_refused()
    stage_masking_fails_closed()
    stage_registry_rejects_duplicates()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    print("PRIVATE OFFICE RECORD TEMPLATES — contract, masking and the UNDX boundary")
    try:
        test_everything()
    except AssertionError:
        pass
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
