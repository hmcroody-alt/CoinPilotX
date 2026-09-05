"""The shipped record templates.

This module is data. Every decision about *what a template may say* lives in
:mod:`record_templates`; this file says what the shipped ones do say. The split
means a mistake here fails at import with a :class:`~record_templates.TemplateError`
naming the field, and the validator can be tested against synthetic templates
that no product decision can invalidate.

Reading order
-------------
Templates are grouped by the information architecture domain the member sees
(``ia_domain``), which is a different and coarser axis than
:data:`model.DOMAINS` — the storage/retrieval domain that governs cross-domain
join policy. A driver's licence is ``ia_domain="identity_government"`` for
navigation and ``domain=IDENTITY`` for policy; a vehicle is
``ia_domain="property_vehicles"`` and ``domain=FINANCIAL``, because what the
retrieval layer must know about a car is that it is an asset.

Two conventions that are load-bearing
--------------------------------------
**Legacy mappings are stated, never inferred.** ``legacy_fact_types`` is empty
on every template whose structure could be guessed wrongly — passport, medical,
financial, legal. A stored fact named ``passport_info`` might be a number, an
expiry date, a country or a note to self, and a migration that decides it is an
expiry date produces a record that looks authoritative and is fiction. Those
facts migrate to ``legacy_record`` with their original text intact and a review
prompt, which is recoverable. Only unambiguous, single-meaning fact types appear
in a mapping.

**Nothing here asks for a credential.** No template has a field for a seed
phrase, a private key, a banking password, an account password, a 2FA seed, a
CVV, a PIN, or a full government identifier. This is not an oversight to be
corrected later by a helpful contributor: those values have no read path that
justifies storing them, and a field that exists is a field that gets filled. The
security templates carry a ``vault_reference`` instead — a pointer to material
held where it belongs — and the identity templates carry a last-four fragment.
"""

from __future__ import annotations

from services.private_office import model
from services.private_office.record_templates import (
    HOOK_EXPIRATION_TO_BRIEFING,
    HOOK_EXPIRATION_TO_CALENDAR,
    HOOK_EXPIRATION_TO_TASK,
    HOOK_RECORD_TO_GRAPH,
    KIND_BOOLEAN,
    KIND_COUNTRY,
    KIND_DATE,
    KIND_EMAIL,
    KIND_ENUM,
    KIND_IDENTIFIER,
    KIND_LONG_TEXT,
    KIND_MONEY,
    KIND_NUMBER,
    KIND_PERSON_NAME,
    KIND_PHONE,
    KIND_TEXT,
    KIND_URL,
    MASK_FULL,
    MASK_LAST4,
    MASK_NONE,
    MASK_YEAR,
    FieldSpec,
    Option,
    ReminderRule,
    SectionSpec,
    Template,
    register,
)

# ---------------------------------------------------------------------------
# IA domains — the member-facing navigation
# ---------------------------------------------------------------------------
IA_IDENTITY = "identity_government"
IA_CONTACT = "contact_residence"
IA_FAMILY = "family_emergency"
IA_EDUCATION = "education_credentials"
IA_EMPLOYMENT = "employment_professional"
IA_FINANCIAL = "financial_assets"
IA_LEGAL = "legal_contracts"
IA_HEALTH = "health_medical"
IA_SECURITY = "security_digital"
IA_TRAVEL = "travel_immigration"
IA_PROPERTY = "property_vehicles"
IA_PREFERENCES = "preferences_instructions"
IA_CUSTOM = "custom"

IA_DOMAINS: tuple[tuple[str, str, str], ...] = (
    (IA_IDENTITY, "privateOffice.ia.identityGovernment", "Identity and Government"),
    (IA_CONTACT, "privateOffice.ia.contactResidence", "Contact and Residence"),
    (IA_FAMILY, "privateOffice.ia.familyEmergency", "Family and Emergency"),
    (IA_EDUCATION, "privateOffice.ia.educationCredentials", "Education and Credentials"),
    (IA_EMPLOYMENT, "privateOffice.ia.employmentProfessional", "Employment and Professional"),
    (IA_FINANCIAL, "privateOffice.ia.financialAssets", "Financial and Assets"),
    (IA_LEGAL, "privateOffice.ia.legalContracts", "Legal and Contracts"),
    (IA_HEALTH, "privateOffice.ia.healthMedical", "Health and Medical"),
    (IA_SECURITY, "privateOffice.ia.securityDigital", "Security and Digital Identity"),
    (IA_TRAVEL, "privateOffice.ia.travelImmigration", "Travel and Immigration"),
    (IA_PROPERTY, "privateOffice.ia.propertyVehicles", "Property and Vehicles"),
    (IA_PREFERENCES, "privateOffice.ia.preferencesInstructions", "Preferences and Instructions"),
    (IA_CUSTOM, "privateOffice.ia.custom", "Custom Records"),
)

#: Just the keys, for the places that validate a submitted domain rather than
#: render the list. Derived from :data:`IA_DOMAINS` rather than written out
#: again, because a hand-maintained second copy is a fourteenth domain waiting
#: to be accepted by a route and unknown to everything downstream.
IA_DOMAIN_KEYS: tuple[str, ...] = tuple(key for key, _label, _fallback in IA_DOMAINS)


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------
def _opts(group: str, *values: str) -> tuple[Option, ...]:
    """Enum options with derived i18n keys.

    The fallback is generated from the token (``NOT_STARTED`` -> ``Not
    started``) so a new option is one string, not three. The generated fallback
    is English and deliberately plain: it is what a client shows only when its
    catalog has no entry, and a plain word is a better placeholder than a
    polished one that discourages translating it.
    """
    out = []
    for value in values:
        out.append(
            Option(
                value=value,
                label_key=f"privateOffice.options.{group}.{value.lower()}",
                label_fallback=value.replace("_", " ").capitalize(),
            )
        )
    return tuple(out)


def _notes(prefix: str, *, sensitivity: str = model.SENSITIVITY_CONFIDENTIAL) -> FieldSpec:
    """The free-text field every template ends with.

    Every structured template needs somewhere for the thing the template did not
    anticipate. Without it members put the exception in the nearest field that
    accepts text, which is how a passport's ``issuing_authority`` ends up
    containing a sentence about a lost-document report.
    """
    return FieldSpec(
        path=f"{prefix}.notes",
        kind=KIND_LONG_TEXT,
        label_key="privateOffice.fields.notes",
        label_fallback="Notes",
        sensitivity=sensitivity,
    )


# Status vocabularies shared by more than one template. Written once because a
# second copy is a second thing to keep in step with the state machine.
DOCUMENT_STATUSES = (
    "DRAFT",
    "ACTIVE",
    "EXPIRING_SOON",
    "EXPIRED",
    "LOST",
    "STOLEN",
    "REPLACED",
    "REVOKED",
    "ARCHIVED",
)
SIMPLE_STATUSES = ("DRAFT", "ACTIVE", "INACTIVE", "ARCHIVED")

#: The renewal ladder the brief specifies: twelve, nine, six and three months,
#: then thirty days. Expressed in days because the reminder scheduler works in
#: days and a "months before" that means 30/31/28 depending on the month is a
#: reminder that arrives on a different day each year.
RENEWAL_REMINDERS: tuple[ReminderRule, ...] = (
    ReminderRule("m12", 365, "privateOffice.reminders.m12", "12 months before", True),
    ReminderRule("m9", 274, "privateOffice.reminders.m9", "9 months before", False),
    ReminderRule("m6", 183, "privateOffice.reminders.m6", "6 months before", True),
    ReminderRule("m3", 91, "privateOffice.reminders.m3", "3 months before", True),
    ReminderRule("d30", 30, "privateOffice.reminders.d30", "30 days before", True),
)

EXPIRY_REMINDERS: tuple[ReminderRule, ...] = (
    ReminderRule("m3", 91, "privateOffice.reminders.m3", "3 months before", True),
    ReminderRule("d30", 30, "privateOffice.reminders.d30", "30 days before", True),
    ReminderRule("d7", 7, "privateOffice.reminders.d7", "7 days before", True),
)


# ===========================================================================
# 1. Identity and Government
# ===========================================================================
PASSPORT = register(Template(
    key="passport",
    version=1,
    domain=model.DOMAIN_IDENTITY,
    ia_domain=IA_IDENTITY,
    display_key="privateOffice.templates.passport.title",
    display_fallback="Passport",
    description_key="privateOffice.templates.passport.description",
    description_fallback="A travel document, its holder, its validity and its evidence.",
    icon="passport",
    sensitivity=model.SENSITIVITY_CONFIDENTIAL,
    statuses=DOCUMENT_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="identification",
            label_key="privateOffice.sections.identification",
            label_fallback="Identification",
            fields=(
                FieldSpec(
                    path="identification.surname",
                    kind=KIND_PERSON_NAME,
                    label_key="privateOffice.fields.surname",
                    label_fallback="Surname",
                    required=True,
                    searchable=True,
                    identity=True,
                ),
                FieldSpec(
                    path="identification.given_names",
                    kind=KIND_PERSON_NAME,
                    label_key="privateOffice.fields.givenNames",
                    label_fallback="Given names",
                    required=True,
                    searchable=True,
                ),
                FieldSpec(
                    path="identification.date_of_birth",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.dateOfBirth",
                    label_fallback="Date of birth",
                    # Masked to the year. A birth date is one of the two or three
                    # values that reset a bank password, so it is not something a
                    # shoulder-surfer should read off an unlocked screen — but the
                    # year alone is what a member needs to confirm they are
                    # looking at the right record, so full concealment would make
                    # the field useless without making it safer.
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    mask=MASK_YEAR,
                    undx_readable=False,
                ),
                FieldSpec(
                    path="identification.place_of_birth",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.placeOfBirth",
                    label_fallback="Place of birth",
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                ),
                FieldSpec(
                    path="identification.sex",
                    kind=KIND_ENUM,
                    label_key="privateOffice.fields.sex",
                    label_fallback="Sex as printed",
                    help_key="privateOffice.help.sexAsPrinted",
                    help_fallback="Record what the document shows, not how you identify.",
                    options=_opts("sex", "F", "M", "X", "UNSPECIFIED"),
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                ),
                FieldSpec(
                    path="identification.nationality",
                    kind=KIND_COUNTRY,
                    label_key="privateOffice.fields.nationality",
                    label_fallback="Nationality",
                    required=True,
                    searchable=True,
                ),
            ),
        ),
        SectionSpec(
            key="issuance",
            label_key="privateOffice.sections.issuance",
            label_fallback="Issuance and validity",
            fields=(
                FieldSpec(
                    path="issuance.document_number",
                    kind=KIND_IDENTIFIER,
                    label_key="privateOffice.fields.passportNumber",
                    label_fallback="Passport number",
                    required=True,
                    # The field the whole security design exists for. RESTRICTED
                    # means encrypted at rest; MASK_LAST4 means the read path
                    # returns "•••• 1234" and the raw value only ever leaves the
                    # server through the reveal endpoint, behind a live Office
                    # grant, a fresh step-up and an audit event. `searchable` is
                    # True and safe precisely because search indexes the masked
                    # form — see ``record_templates.search_index_text``.
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_LAST4,
                    searchable=True,
                    undx_readable=False,
                    identity=True,
                    evidence_expected=True,
                    max_length=24,
                ),
                FieldSpec(
                    path="issuance.issuing_country",
                    kind=KIND_COUNTRY,
                    label_key="privateOffice.fields.issuingCountry",
                    label_fallback="Issuing country",
                    required=True,
                    searchable=True,
                    identity=True,
                ),
                FieldSpec(
                    path="issuance.issuing_authority",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.issuingAuthority",
                    label_fallback="Issuing authority",
                ),
                FieldSpec(
                    path="issuance.document_type",
                    kind=KIND_ENUM,
                    label_key="privateOffice.fields.documentType",
                    label_fallback="Document type",
                    options=_opts(
                        "passport_type",
                        "ORDINARY", "DIPLOMATIC", "SERVICE", "EMERGENCY", "OTHER",
                    ),
                    searchable=True,
                ),
                FieldSpec(
                    path="issuance.issue_date",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.issueDate",
                    label_fallback="Issue date",
                ),
                FieldSpec(
                    path="issuance.expiry_date",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.expiryDate",
                    label_fallback="Expiry date",
                    required=True,
                    searchable=True,
                    expires_record=True,
                    evidence_expected=True,
                ),
                FieldSpec(
                    path="issuance.endorsements",
                    kind=KIND_LONG_TEXT,
                    label_key="privateOffice.fields.endorsements",
                    label_fallback="Endorsements and observations",
                ),
            ),
        ),
        SectionSpec(
            key="evidence",
            label_key="privateOffice.sections.evidence",
            label_fallback="Evidence",
            collapsed_by_default=True,
            fields=(
                FieldSpec(
                    path="evidence.scan_in_vault",
                    kind=KIND_BOOLEAN,
                    label_key="privateOffice.fields.scanInVault",
                    label_fallback="A scan is held in the Secure Vault",
                    help_key="privateOffice.help.scanInVault",
                    help_fallback="Scans belong in the Secure Vault, never in the camera roll.",
                ),
                FieldSpec(
                    path="evidence.certified_copy_location",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.certifiedCopyLocation",
                    label_fallback="Where a certified copy is kept",
                ),
                _notes("evidence"),
            ),
        ),
        SectionSpec(
            key="mrz",
            label_key="privateOffice.sections.mrz",
            label_fallback="Machine-readable zone",
            collapsed_by_default=True,
            fields=(
                # The MRZ contains the document number, the date of birth and the
                # expiry date in one string. It is therefore exactly as sensitive
                # as the most sensitive thing in it and is masked completely —
                # there is no partial view of an MRZ that is not the number.
                FieldSpec(
                    path="mrz.line1",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.mrzLine1",
                    label_fallback="MRZ line 1",
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_FULL,
                    undx_readable=False,
                    max_length=44,
                ),
                FieldSpec(
                    path="mrz.line2",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.mrzLine2",
                    label_fallback="MRZ line 2",
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_FULL,
                    undx_readable=False,
                    max_length=44,
                ),
            ),
        ),
    ),
    reminders=RENEWAL_REMINDERS,
    automations=(
        HOOK_EXPIRATION_TO_CALENDAR,
        HOOK_EXPIRATION_TO_TASK,
        HOOK_EXPIRATION_TO_BRIEFING,
        HOOK_RECORD_TO_GRAPH,
    ),
    graph_node_type=model.NODE_DOCUMENT,
    graph_relations=(model.RELATION_DESCRIBES,),
    undx_readable=True,
    undx_draftable=True,
    # Deliberately empty. See the module docstring: a legacy fact named
    # ``passport`` or ``passport_info`` could be a number, a date, a country or a
    # note, and a mapping that picks one produces a record that reads as
    # authoritative and is a guess.
    legacy_fact_types=(),
))


DRIVERS_LICENCE = register(Template(
    key="drivers_licence",
    version=1,
    domain=model.DOMAIN_IDENTITY,
    ia_domain=IA_IDENTITY,
    display_key="privateOffice.templates.driversLicence.title",
    display_fallback="Driver's licence",
    description_key="privateOffice.templates.driversLicence.description",
    description_fallback="A driving entitlement, its classes, restrictions and expiry.",
    icon="licence",
    statuses=DOCUMENT_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="identification",
            label_key="privateOffice.sections.identification",
            label_fallback="Identification",
            fields=(
                FieldSpec(
                    path="identification.full_name",
                    kind=KIND_PERSON_NAME,
                    label_key="privateOffice.fields.fullName",
                    label_fallback="Name as printed",
                    required=True,
                    searchable=True,
                ),
                FieldSpec(
                    path="identification.date_of_birth",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.dateOfBirth",
                    label_fallback="Date of birth",
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    mask=MASK_YEAR,
                    undx_readable=False,
                ),
            ),
        ),
        SectionSpec(
            key="issuance",
            label_key="privateOffice.sections.issuance",
            label_fallback="Issuance and validity",
            fields=(
                FieldSpec(
                    path="issuance.licence_number",
                    kind=KIND_IDENTIFIER,
                    label_key="privateOffice.fields.licenceNumber",
                    label_fallback="Licence number",
                    required=True,
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_LAST4,
                    searchable=True,
                    undx_readable=False,
                    identity=True,
                    evidence_expected=True,
                    max_length=32,
                ),
                FieldSpec(
                    path="issuance.issuing_country",
                    kind=KIND_COUNTRY,
                    label_key="privateOffice.fields.issuingCountry",
                    label_fallback="Issuing country",
                    required=True,
                    searchable=True,
                    identity=True,
                ),
                FieldSpec(
                    path="issuance.issuing_region",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.issuingRegion",
                    label_fallback="State, province or region",
                    searchable=True,
                    identity=True,
                ),
                FieldSpec(
                    path="issuance.issue_date",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.issueDate",
                    label_fallback="Issue date",
                ),
                FieldSpec(
                    path="issuance.expiry_date",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.expiryDate",
                    label_fallback="Expiry date",
                    required=True,
                    searchable=True,
                    expires_record=True,
                ),
            ),
        ),
        SectionSpec(
            key="entitlements",
            label_key="privateOffice.sections.entitlements",
            label_fallback="Entitlements",
            fields=(
                FieldSpec(
                    path="entitlements.classes",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.licenceClasses",
                    label_fallback="Classes",
                    searchable=True,
                ),
                FieldSpec(
                    path="entitlements.restrictions",
                    kind=KIND_LONG_TEXT,
                    label_key="privateOffice.fields.restrictions",
                    label_fallback="Restrictions",
                ),
                FieldSpec(
                    path="entitlements.organ_donor",
                    kind=KIND_BOOLEAN,
                    label_key="privateOffice.fields.organDonor",
                    label_fallback="Organ donor designation",
                    # A donor designation is health information printed on an
                    # identity document. It stays out of UNDX and out of the
                    # graph regardless of the record it happens to live on.
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                ),
                _notes("entitlements"),
            ),
        ),
    ),
    reminders=EXPIRY_REMINDERS,
    automations=(HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK, HOOK_RECORD_TO_GRAPH),
    graph_node_type=model.NODE_DOCUMENT,
    graph_relations=(model.RELATION_DESCRIBES,),
    undx_draftable=True,
))


NATIONAL_ID = register(Template(
    key="national_id",
    version=1,
    domain=model.DOMAIN_IDENTITY,
    ia_domain=IA_IDENTITY,
    display_key="privateOffice.templates.nationalId.title",
    display_fallback="National identifier",
    description_key="privateOffice.templates.nationalId.description",
    description_fallback=(
        "A reference to a government identifier. The full number is never stored here."
    ),
    icon="id-card",
    statuses=DOCUMENT_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="identifier",
            label_key="privateOffice.sections.identifier",
            label_fallback="Identifier",
            fields=(
                FieldSpec(
                    path="identifier.kind",
                    kind=KIND_ENUM,
                    label_key="privateOffice.fields.identifierKind",
                    label_fallback="Identifier type",
                    required=True,
                    options=_opts(
                        "national_id_kind",
                        "SOCIAL_SECURITY", "TAX_ID", "NATIONAL_REGISTRY",
                        "RESIDENCE_PERMIT", "HEALTH_SERVICE", "OTHER",
                    ),
                    searchable=True,
                    identity=True,
                ),
                FieldSpec(
                    path="identifier.issuing_country",
                    kind=KIND_COUNTRY,
                    label_key="privateOffice.fields.issuingCountry",
                    label_fallback="Issuing country",
                    required=True,
                    searchable=True,
                    identity=True,
                ),
                # There is no field for the complete number, and that absence is
                # the design. A full government identifier stored in a product
                # database has one legitimate read — proving to a third party you
                # know it — and that read is not something this app performs. The
                # last four is enough for a member to recognise which of two
                # identifiers a record refers to, which is the actual need.
                FieldSpec(
                    path="identifier.last_four",
                    kind=KIND_IDENTIFIER,
                    label_key="privateOffice.fields.lastFour",
                    label_fallback="Last four characters",
                    help_key="privateOffice.help.lastFourOnly",
                    help_fallback=(
                        "Record only the last four. Keep the full identifier in the "
                        "Secure Vault or where it was issued."
                    ),
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    max_length=4,
                    pattern=r"^[A-Za-z0-9]{1,4}$",
                ),
                FieldSpec(
                    path="identifier.vault_reference",
                    kind=KIND_TEXT,
                    label_key="privateOffice.fields.vaultReference",
                    label_fallback="Secure Vault reference",
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                ),
                FieldSpec(
                    path="identifier.expiry_date",
                    kind=KIND_DATE,
                    label_key="privateOffice.fields.expiryDate",
                    label_fallback="Expiry date",
                    searchable=True,
                    expires_record=True,
                ),
                _notes("identifier"),
            ),
        ),
    ),
    reminders=EXPIRY_REMINDERS,
    automations=(HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK),
    # No graph projection. An identifier record is a pointer to a number, and
    # nothing a traversal answers is improved by reaching it.
    undx_draftable=False,
))


# ===========================================================================
# 2. Contact and Residence
# ===========================================================================
RESIDENCE = register(Template(
    key="residence",
    version=1,
    domain=model.DOMAIN_GENERAL,
    ia_domain=IA_CONTACT,
    display_key="privateOffice.templates.residence.title",
    display_fallback="Residence",
    description_key="privateOffice.templates.residence.description",
    description_fallback="An address, the period you lived there and how it is held.",
    icon="home",
    statuses=("DRAFT", "CURRENT", "PREVIOUS", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="address",
            label_key="privateOffice.sections.address",
            label_fallback="Address",
            fields=(
                FieldSpec(
                    path="address.line1", kind=KIND_TEXT, required=True, searchable=True,
                    identity=True,
                    label_key="privateOffice.fields.addressLine1",
                    label_fallback="Address line 1",
                ),
                FieldSpec(
                    path="address.line2", kind=KIND_TEXT,
                    label_key="privateOffice.fields.addressLine2",
                    label_fallback="Address line 2",
                ),
                FieldSpec(
                    path="address.city", kind=KIND_TEXT, searchable=True,
                    label_key="privateOffice.fields.city", label_fallback="City",
                ),
                FieldSpec(
                    path="address.region", kind=KIND_TEXT, searchable=True,
                    label_key="privateOffice.fields.region",
                    label_fallback="State, province or region",
                ),
                FieldSpec(
                    path="address.postal_code", kind=KIND_TEXT,
                    label_key="privateOffice.fields.postalCode",
                    label_fallback="Postal code", max_length=32,
                ),
                FieldSpec(
                    path="address.country", kind=KIND_COUNTRY, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.country", label_fallback="Country",
                ),
            ),
        ),
        SectionSpec(
            key="occupancy",
            label_key="privateOffice.sections.occupancy",
            label_fallback="Occupancy",
            fields=(
                FieldSpec(
                    path="occupancy.tenure", kind=KIND_ENUM,
                    options=_opts("tenure", "OWNED", "RENTED", "FAMILY", "COMPANY", "OTHER"),
                    searchable=True,
                    label_key="privateOffice.fields.tenure", label_fallback="Tenure",
                ),
                FieldSpec(
                    path="occupancy.moved_in", kind=KIND_DATE,
                    label_key="privateOffice.fields.movedIn", label_fallback="Moved in",
                ),
                FieldSpec(
                    path="occupancy.moved_out", kind=KIND_DATE,
                    label_key="privateOffice.fields.movedOut", label_fallback="Moved out",
                ),
                _notes("occupancy"),
            ),
        ),
    ),
    automations=(HOOK_RECORD_TO_GRAPH,),
    graph_node_type=model.NODE_PROPERTY,
    graph_relations=(model.RELATION_OWNS, model.RELATION_COVERED_BY, model.RELATION_GOVERNED_BY),
    undx_draftable=True,
    legacy_fact_types=(
        # Unambiguous single-meaning fact types only.
        ("home_city", "address.city"),
        ("home_country", "address.country"),
        ("postal_code", "address.postal_code"),
    ),
))


# ===========================================================================
# 3. Family and Emergency
# ===========================================================================
EMERGENCY_CONTACT = register(Template(
    key="emergency_contact",
    version=1,
    domain=model.DOMAIN_FAMILY,
    ia_domain=IA_FAMILY,
    display_key="privateOffice.templates.emergencyContact.title",
    display_fallback="Emergency contact",
    description_key="privateOffice.templates.emergencyContact.description",
    description_fallback="Who to reach, how, and what they are authorised to do.",
    icon="contact",
    statuses=SIMPLE_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="person",
            label_key="privateOffice.sections.person",
            label_fallback="Person",
            fields=(
                FieldSpec(
                    path="person.full_name", kind=KIND_PERSON_NAME, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.fullName", label_fallback="Full name",
                ),
                FieldSpec(
                    path="person.relationship", kind=KIND_TEXT, searchable=True,
                    label_key="privateOffice.fields.relationship",
                    label_fallback="Relationship",
                ),
                FieldSpec(
                    path="person.phone", kind=KIND_PHONE, required=True, identity=True,
                    label_key="privateOffice.fields.phone", label_fallback="Phone",
                ),
                FieldSpec(
                    path="person.email", kind=KIND_EMAIL,
                    label_key="privateOffice.fields.email", label_fallback="Email",
                ),
                FieldSpec(
                    path="person.priority", kind=KIND_NUMBER, min_number=1, max_number=99,
                    label_key="privateOffice.fields.priority",
                    label_fallback="Call order",
                ),
            ),
        ),
        SectionSpec(
            key="authority",
            label_key="privateOffice.sections.authority",
            label_fallback="Authority",
            fields=(
                FieldSpec(
                    path="authority.may_make_medical_decisions", kind=KIND_BOOLEAN,
                    # A statement about medical decision authority is a legal
                    # claim about a person other than the member. It is recorded
                    # as the member's own note and is never treated as proof that
                    # such authority exists.
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.mayMakeMedicalDecisions",
                    label_fallback="Named for medical decisions",
                    help_key="privateOffice.help.notLegalProof",
                    help_fallback="This is your note, not legal authority on its own.",
                ),
                FieldSpec(
                    path="authority.holds_spare_keys", kind=KIND_BOOLEAN,
                    label_key="privateOffice.fields.holdsSpareKeys",
                    label_fallback="Holds spare keys",
                ),
                _notes("authority"),
            ),
        ),
    ),
    automations=(HOOK_RECORD_TO_GRAPH,),
    graph_node_type=model.NODE_PERSON,
    graph_relations=(model.RELATION_ADVISED_BY,),
    undx_draftable=True,
))


# ===========================================================================
# 4. Education and Credentials
# ===========================================================================
CERTIFICATION = register(Template(
    key="certification",
    version=1,
    domain=model.DOMAIN_GENERAL,
    ia_domain=IA_EDUCATION,
    display_key="privateOffice.templates.certification.title",
    display_fallback="Certification or licence",
    description_key="privateOffice.templates.certification.description",
    description_fallback="A professional credential, its issuer, validity and renewal duties.",
    icon="certificate",
    statuses=DOCUMENT_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="credential",
            label_key="privateOffice.sections.credential",
            label_fallback="Credential",
            fields=(
                FieldSpec(
                    path="credential.name", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.credentialName",
                    label_fallback="Credential",
                ),
                FieldSpec(
                    path="credential.issuer", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.issuer", label_fallback="Issuer",
                ),
                FieldSpec(
                    path="credential.reference", kind=KIND_IDENTIFIER,
                    mask=MASK_LAST4, searchable=True,
                    label_key="privateOffice.fields.credentialReference",
                    label_fallback="Certificate number",
                ),
                FieldSpec(
                    path="credential.level", kind=KIND_TEXT,
                    label_key="privateOffice.fields.level", label_fallback="Level or grade",
                ),
                FieldSpec(
                    path="credential.awarded_on", kind=KIND_DATE,
                    label_key="privateOffice.fields.awardedOn", label_fallback="Awarded",
                ),
                FieldSpec(
                    path="credential.expires_on", kind=KIND_DATE, searchable=True,
                    expires_record=True,
                    label_key="privateOffice.fields.expiresOn", label_fallback="Expires",
                ),
                FieldSpec(
                    path="credential.verification_url", kind=KIND_URL,
                    label_key="privateOffice.fields.verificationUrl",
                    label_fallback="Verification link",
                ),
            ),
        ),
        SectionSpec(
            key="renewal",
            label_key="privateOffice.sections.renewal",
            label_fallback="Renewal",
            fields=(
                FieldSpec(
                    path="renewal.continuing_education_hours", kind=KIND_NUMBER,
                    min_number=0, max_number=10000,
                    label_key="privateOffice.fields.ceHours",
                    label_fallback="Continuing education hours required",
                ),
                FieldSpec(
                    path="renewal.hours_completed", kind=KIND_NUMBER,
                    min_number=0, max_number=10000,
                    label_key="privateOffice.fields.ceHoursCompleted",
                    label_fallback="Hours completed",
                ),
                FieldSpec(
                    path="renewal.fee", kind=KIND_MONEY,
                    label_key="privateOffice.fields.renewalFee",
                    label_fallback="Renewal fee",
                ),
                _notes("renewal"),
            ),
        ),
    ),
    reminders=EXPIRY_REMINDERS,
    automations=(
        HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK,
        HOOK_EXPIRATION_TO_BRIEFING, HOOK_RECORD_TO_GRAPH,
    ),
    graph_node_type=model.NODE_DOCUMENT,
    graph_relations=(model.RELATION_DESCRIBES,),
    undx_draftable=True,
))


# ===========================================================================
# 5. Employment and Professional
# ===========================================================================
EMPLOYMENT = register(Template(
    key="employment",
    version=1,
    domain=model.DOMAIN_FINANCIAL,
    ia_domain=IA_EMPLOYMENT,
    display_key="privateOffice.templates.employment.title",
    display_fallback="Employment",
    description_key="privateOffice.templates.employment.description",
    description_fallback="A role, its employer, its terms and its dates.",
    icon="briefcase",
    statuses=("DRAFT", "ACTIVE", "ON_LEAVE", "ENDED", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="role",
            label_key="privateOffice.sections.role",
            label_fallback="Role",
            fields=(
                FieldSpec(
                    path="role.employer", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.employer", label_fallback="Employer",
                ),
                FieldSpec(
                    path="role.title", kind=KIND_TEXT, required=True, searchable=True,
                    identity=True,
                    label_key="privateOffice.fields.jobTitle", label_fallback="Job title",
                ),
                FieldSpec(
                    path="role.employment_type", kind=KIND_ENUM,
                    options=_opts(
                        "employment_type", "FULL_TIME", "PART_TIME", "CONTRACT",
                        "FREELANCE", "INTERNSHIP", "FOUNDER", "OTHER",
                    ),
                    searchable=True,
                    label_key="privateOffice.fields.employmentType",
                    label_fallback="Employment type",
                ),
                FieldSpec(
                    path="role.started_on", kind=KIND_DATE,
                    label_key="privateOffice.fields.startedOn", label_fallback="Started",
                ),
                FieldSpec(
                    path="role.ended_on", kind=KIND_DATE,
                    label_key="privateOffice.fields.endedOn", label_fallback="Ended",
                ),
            ),
        ),
        SectionSpec(
            key="terms",
            label_key="privateOffice.sections.terms",
            label_fallback="Terms",
            fields=(
                FieldSpec(
                    path="terms.base_compensation", kind=KIND_MONEY,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.baseCompensation",
                    label_fallback="Base compensation",
                ),
                FieldSpec(
                    path="terms.notice_period_days", kind=KIND_NUMBER,
                    min_number=0, max_number=730,
                    label_key="privateOffice.fields.noticePeriod",
                    label_fallback="Notice period (days)",
                ),
                FieldSpec(
                    path="terms.non_compete", kind=KIND_BOOLEAN,
                    label_key="privateOffice.fields.nonCompete",
                    label_fallback="Subject to a non-compete",
                ),
                _notes("terms"),
            ),
        ),
    ),
    automations=(HOOK_RECORD_TO_GRAPH,),
    graph_node_type=model.NODE_BUSINESS,
    graph_relations=(model.RELATION_GOVERNED_BY, model.RELATION_ADVISED_BY),
    undx_draftable=True,
    legacy_fact_types=(("employer", "role.employer"), ("job_title", "role.title")),
))


# ===========================================================================
# 6. Financial and Assets
# ===========================================================================
FINANCIAL_ACCOUNT = register(Template(
    key="financial_account",
    version=1,
    domain=model.DOMAIN_FINANCIAL,
    ia_domain=IA_FINANCIAL,
    display_key="privateOffice.templates.financialAccount.title",
    display_fallback="Financial account",
    description_key="privateOffice.templates.financialAccount.description",
    description_fallback=(
        "An account and where it is held. Credentials are never recorded here."
    ),
    icon="bank",
    statuses=("DRAFT", "ACTIVE", "DORMANT", "CLOSED", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="account",
            label_key="privateOffice.sections.account",
            label_fallback="Account",
            fields=(
                FieldSpec(
                    path="account.institution", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.institution",
                    label_fallback="Institution",
                ),
                FieldSpec(
                    path="account.nickname", kind=KIND_TEXT, searchable=True,
                    label_key="privateOffice.fields.nickname", label_fallback="Nickname",
                ),
                FieldSpec(
                    path="account.account_type", kind=KIND_ENUM,
                    options=_opts(
                        "account_type", "CHECKING", "SAVINGS", "BROKERAGE",
                        "RETIREMENT", "CREDIT", "LOAN", "MORTGAGE", "OTHER",
                    ),
                    required=True, searchable=True, identity=True,
                    label_key="privateOffice.fields.accountType",
                    label_fallback="Account type",
                ),
                # Last four only, and masked even so. There is no field here for
                # a full account number, a routing number, a password, a PIN, a
                # seed phrase, a private key or an API key with trading or
                # withdrawal rights. This product never needs them, so it never
                # asks — a field that exists is a field that gets filled.
                FieldSpec(
                    path="account.number_last_four", kind=KIND_IDENTIFIER,
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_LAST4, searchable=True, undx_readable=False,
                    identity=True, max_length=4, pattern=r"^[A-Za-z0-9]{1,4}$",
                    label_key="privateOffice.fields.lastFour",
                    label_fallback="Last four digits",
                    help_key="privateOffice.help.neverCredentials",
                    help_fallback=(
                        "Never record passwords, PINs, seed phrases or private keys."
                    ),
                ),
                FieldSpec(
                    path="account.currency", kind=KIND_TEXT, max_length=3,
                    pattern=r"^[A-Za-z]{3}$", searchable=True,
                    label_key="privateOffice.fields.currency", label_fallback="Currency",
                ),
                FieldSpec(
                    path="account.opened_on", kind=KIND_DATE,
                    label_key="privateOffice.fields.openedOn", label_fallback="Opened",
                ),
                _notes("account", sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE),
            ),
        ),
    ),
    automations=(HOOK_RECORD_TO_GRAPH,),
    graph_node_type=model.NODE_ASSET,
    graph_relations=(model.RELATION_OWNS, model.RELATION_GOVERNED_BY),
    undx_readable=True,
    undx_draftable=False,
))


# ===========================================================================
# 7. Legal and Contracts
# ===========================================================================
CONTRACT = register(Template(
    key="contract",
    version=1,
    domain=model.DOMAIN_LEGAL,
    ia_domain=IA_LEGAL,
    display_key="privateOffice.templates.contract.title",
    display_fallback="Contract or agreement",
    description_key="privateOffice.templates.contract.description",
    description_fallback="An agreement, its parties, its term and its renewal behaviour.",
    icon="contract",
    statuses=("DRAFT", "ACTIVE", "EXPIRING_SOON", "EXPIRED", "TERMINATED", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="agreement",
            label_key="privateOffice.sections.agreement",
            label_fallback="Agreement",
            fields=(
                FieldSpec(
                    path="agreement.name", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.agreementName",
                    label_fallback="Agreement",
                ),
                FieldSpec(
                    path="agreement.counterparty", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.counterparty",
                    label_fallback="Other party",
                ),
                FieldSpec(
                    path="agreement.agreement_type", kind=KIND_ENUM,
                    options=_opts(
                        "agreement_type", "SERVICE", "LEASE", "EMPLOYMENT", "NDA",
                        "LOAN", "INSURANCE", "SUBSCRIPTION", "OTHER",
                    ),
                    searchable=True,
                    label_key="privateOffice.fields.agreementType",
                    label_fallback="Type",
                ),
                FieldSpec(
                    path="agreement.effective_from", kind=KIND_DATE,
                    label_key="privateOffice.fields.effectiveFrom",
                    label_fallback="Effective from",
                ),
                FieldSpec(
                    path="agreement.ends_on", kind=KIND_DATE, searchable=True,
                    expires_record=True,
                    label_key="privateOffice.fields.endsOn", label_fallback="Ends",
                ),
            ),
        ),
        SectionSpec(
            key="obligations",
            label_key="privateOffice.sections.obligations",
            label_fallback="Obligations",
            fields=(
                FieldSpec(
                    path="obligations.recurring_amount", kind=KIND_MONEY,
                    label_key="privateOffice.fields.recurringAmount",
                    label_fallback="Recurring amount",
                ),
                FieldSpec(
                    path="obligations.auto_renews", kind=KIND_BOOLEAN,
                    label_key="privateOffice.fields.autoRenews",
                    label_fallback="Renews automatically",
                ),
                FieldSpec(
                    path="obligations.notice_period_days", kind=KIND_NUMBER,
                    min_number=0, max_number=730,
                    help_key="privateOffice.help.noticeBeforeRenewal",
                    help_fallback=(
                        "Reminders are offset from the end date, not from the notice window."
                    ),
                    label_key="privateOffice.fields.noticePeriod",
                    label_fallback="Notice period (days)",
                ),
                _notes("obligations"),
            ),
        ),
    ),
    reminders=EXPIRY_REMINDERS,
    automations=(
        HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK,
        HOOK_EXPIRATION_TO_BRIEFING, HOOK_RECORD_TO_GRAPH,
    ),
    graph_node_type=model.NODE_CONTRACT,
    graph_relations=(model.RELATION_GOVERNED_BY, model.RELATION_DESCRIBES),
    undx_draftable=True,
))


# ===========================================================================
# 8. Health and Medical
# ===========================================================================
# HEALTH templates are the strictest case in the catalog and the constraints are
# enforced structurally rather than remembered: the record sensitivity floor is
# HIGHLY_SENSITIVE, `graph_node_type` is empty (the Template validator *refuses*
# a health template that names one), and `undx_readable` is False for the whole
# template, so no field-level oversight can expose a diagnosis to a model.
MEDICAL_CONDITION = register(Template(
    key="medical_condition",
    version=1,
    domain=model.DOMAIN_HEALTH,
    ia_domain=IA_HEALTH,
    display_key="privateOffice.templates.medicalCondition.title",
    display_fallback="Health record",
    description_key="privateOffice.templates.medicalCondition.description",
    description_fallback="A condition, treatment or care detail you want kept to hand.",
    icon="health",
    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
    statuses=("DRAFT", "ACTIVE", "RESOLVED", "MONITORING", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="condition",
            label_key="privateOffice.sections.condition",
            label_fallback="Details",
            fields=(
                FieldSpec(
                    path="condition.name", kind=KIND_TEXT, required=True,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False, identity=True,
                    label_key="privateOffice.fields.conditionName",
                    label_fallback="What this is about",
                ),
                FieldSpec(
                    path="condition.category", kind=KIND_ENUM,
                    options=_opts(
                        "health_category", "CONDITION", "MEDICATION", "ALLERGY",
                        "PROCEDURE", "IMMUNISATION", "DEVICE", "OTHER",
                    ),
                    required=True, identity=True,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.healthCategory",
                    label_fallback="Category",
                ),
                FieldSpec(
                    path="condition.recorded_on", kind=KIND_DATE,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.recordedOn",
                    label_fallback="First recorded",
                ),
                FieldSpec(
                    path="condition.review_on", kind=KIND_DATE,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False, expires_record=True,
                    label_key="privateOffice.fields.reviewOn",
                    label_fallback="Review on",
                ),
                FieldSpec(
                    path="condition.detail", kind=KIND_LONG_TEXT,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    help_key="privateOffice.help.notMedicalAdvice",
                    help_fallback=(
                        "This is your own record. It is not clinical advice and is "
                        "not shared with anyone."
                    ),
                    label_key="privateOffice.fields.detail", label_fallback="Detail",
                ),
            ),
        ),
        SectionSpec(
            key="care",
            label_key="privateOffice.sections.care",
            label_fallback="Care",
            collapsed_by_default=True,
            fields=(
                FieldSpec(
                    path="care.provider", kind=KIND_TEXT,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.provider",
                    label_fallback="Provider or practice",
                ),
                FieldSpec(
                    path="care.provider_phone", kind=KIND_PHONE,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.providerPhone",
                    label_fallback="Provider phone",
                ),
                _notes("care", sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE),
            ),
        ),
    ),
    reminders=(
        ReminderRule("d30", 30, "privateOffice.reminders.d30", "30 days before", True),
        ReminderRule("d7", 7, "privateOffice.reminders.d7", "7 days before", True),
    ),
    # Calendar and task only. No briefing hook: a Private Briefing is a summary
    # surface, and a health item summarised onto a home screen is a diagnosis on
    # a lock screen one notification setting later.
    automations=(HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK),
    graph_node_type="",
    undx_readable=False,
    undx_draftable=False,
))


# ===========================================================================
# 9. Security and Digital Identity
# ===========================================================================
DIGITAL_ACCOUNT = register(Template(
    key="digital_account",
    version=1,
    domain=model.DOMAIN_SECURITY,
    ia_domain=IA_SECURITY,
    display_key="privateOffice.templates.digitalAccount.title",
    display_fallback="Digital account",
    description_key="privateOffice.templates.digitalAccount.description",
    description_fallback=(
        "Where an account lives and how it recovers. No passwords are stored here."
    ),
    icon="shield",
    statuses=("DRAFT", "ACTIVE", "COMPROMISED", "CLOSED", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="account",
            label_key="privateOffice.sections.account",
            label_fallback="Account",
            fields=(
                FieldSpec(
                    path="account.service", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.service", label_fallback="Service",
                ),
                FieldSpec(
                    path="account.login_hint", kind=KIND_TEXT, identity=True,
                    help_key="privateOffice.help.loginHint",
                    help_fallback="A hint that reminds you which login, not the login itself.",
                    label_key="privateOffice.fields.loginHint",
                    label_fallback="Which login",
                ),
                FieldSpec(
                    path="account.url", kind=KIND_URL,
                    label_key="privateOffice.fields.url", label_fallback="Website",
                ),
                # No password field. No recovery code field. No 2FA seed field.
                # A password manager is the correct home for those and this is
                # not one; recording them here would create a second, weaker copy
                # of every secret the member already stores properly.
                FieldSpec(
                    path="account.mfa_method", kind=KIND_ENUM,
                    options=_opts(
                        "mfa_method", "NONE", "APP", "HARDWARE_KEY", "SMS",
                        "EMAIL", "PASSKEY", "UNKNOWN",
                    ),
                    searchable=True,
                    label_key="privateOffice.fields.mfaMethod",
                    label_fallback="Second factor",
                ),
                FieldSpec(
                    path="account.recovery_contact", kind=KIND_TEXT,
                    sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
                    undx_readable=False,
                    label_key="privateOffice.fields.recoveryContact",
                    label_fallback="Recovery contact",
                ),
                FieldSpec(
                    path="account.last_reviewed", kind=KIND_DATE, searchable=True,
                    label_key="privateOffice.fields.lastReviewed",
                    label_fallback="Last reviewed",
                ),
                _notes("account"),
            ),
        ),
    ),
    automations=(),
    undx_readable=True,
    undx_draftable=False,
))


# ===========================================================================
# 10. Travel and Immigration
# ===========================================================================
VISA = register(Template(
    key="visa",
    version=1,
    domain=model.DOMAIN_IDENTITY,
    ia_domain=IA_TRAVEL,
    display_key="privateOffice.templates.visa.title",
    display_fallback="Visa or permit",
    description_key="privateOffice.templates.visa.description",
    description_fallback="A permission to enter or remain, and the dates that bound it.",
    icon="stamp",
    statuses=DOCUMENT_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="permission",
            label_key="privateOffice.sections.permission",
            label_fallback="Permission",
            fields=(
                FieldSpec(
                    path="permission.country", kind=KIND_COUNTRY, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.country", label_fallback="Country",
                ),
                FieldSpec(
                    path="permission.category", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    help_key="privateOffice.help.visaCategory",
                    help_fallback="Record the category exactly as it appears on the document.",
                    label_key="privateOffice.fields.visaCategory",
                    label_fallback="Category",
                ),
                FieldSpec(
                    path="permission.reference", kind=KIND_IDENTIFIER,
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_LAST4, searchable=True, undx_readable=False,
                    max_length=32,
                    label_key="privateOffice.fields.visaReference",
                    label_fallback="Reference number",
                ),
                FieldSpec(
                    path="permission.valid_from", kind=KIND_DATE,
                    label_key="privateOffice.fields.validFrom",
                    label_fallback="Valid from",
                ),
                FieldSpec(
                    path="permission.valid_until", kind=KIND_DATE, required=True,
                    searchable=True, expires_record=True,
                    label_key="privateOffice.fields.validUntil",
                    label_fallback="Valid until",
                ),
                FieldSpec(
                    path="permission.entries", kind=KIND_ENUM,
                    options=_opts("visa_entries", "SINGLE", "DOUBLE", "MULTIPLE", "UNKNOWN"),
                    label_key="privateOffice.fields.entries", label_fallback="Entries",
                ),
                FieldSpec(
                    path="permission.max_stay_days", kind=KIND_NUMBER,
                    min_number=0, max_number=3650,
                    label_key="privateOffice.fields.maxStayDays",
                    label_fallback="Maximum stay (days)",
                ),
                # The brief is explicit that no destination-specific legal
                # conclusion may be produced without an authoritative source.
                # The template therefore records what the member read on their
                # own document and offers nowhere to store a derived "you may
                # enter" verdict.
                _notes("permission"),
            ),
        ),
    ),
    reminders=EXPIRY_REMINDERS,
    automations=(
        HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK,
        HOOK_EXPIRATION_TO_BRIEFING, HOOK_RECORD_TO_GRAPH,
    ),
    graph_node_type=model.NODE_DOCUMENT,
    graph_relations=(model.RELATION_DESCRIBES,),
    undx_draftable=True,
))


# ===========================================================================
# 11. Property and Vehicles
# ===========================================================================
VEHICLE = register(Template(
    key="vehicle",
    version=1,
    domain=model.DOMAIN_FINANCIAL,
    ia_domain=IA_PROPERTY,
    display_key="privateOffice.templates.vehicle.title",
    display_fallback="Vehicle",
    description_key="privateOffice.templates.vehicle.description",
    description_fallback="A vehicle, how it is identified, and when its paperwork falls due.",
    icon="car",
    statuses=("DRAFT", "OWNED", "LEASED", "SOLD", "ARCHIVED"),
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="vehicle",
            label_key="privateOffice.sections.vehicle",
            label_fallback="Vehicle",
            fields=(
                FieldSpec(
                    path="vehicle.make", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.make", label_fallback="Make",
                ),
                FieldSpec(
                    path="vehicle.model", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.model", label_fallback="Model",
                ),
                FieldSpec(
                    path="vehicle.year", kind=KIND_NUMBER, min_number=1885, max_number=2100,
                    searchable=True,
                    label_key="privateOffice.fields.year", label_fallback="Year",
                ),
                FieldSpec(
                    path="vehicle.registration", kind=KIND_IDENTIFIER,
                    mask=MASK_LAST4, searchable=True, identity=True, max_length=16,
                    label_key="privateOffice.fields.registration",
                    label_fallback="Registration plate",
                ),
                FieldSpec(
                    path="vehicle.vin", kind=KIND_IDENTIFIER,
                    sensitivity=model.SENSITIVITY_RESTRICTED,
                    mask=MASK_LAST4, undx_readable=False, max_length=17,
                    label_key="privateOffice.fields.vin",
                    label_fallback="VIN",
                ),
            ),
        ),
        SectionSpec(
            key="paperwork",
            label_key="privateOffice.sections.paperwork",
            label_fallback="Paperwork",
            fields=(
                FieldSpec(
                    path="paperwork.registration_expires", kind=KIND_DATE,
                    searchable=True, expires_record=True,
                    label_key="privateOffice.fields.registrationExpires",
                    label_fallback="Registration expires",
                ),
                FieldSpec(
                    path="paperwork.insurer", kind=KIND_TEXT, searchable=True,
                    label_key="privateOffice.fields.insurer", label_fallback="Insurer",
                ),
                FieldSpec(
                    path="paperwork.next_service", kind=KIND_DATE,
                    label_key="privateOffice.fields.nextService",
                    label_fallback="Next service",
                ),
                _notes("paperwork"),
            ),
        ),
    ),
    reminders=EXPIRY_REMINDERS,
    automations=(HOOK_EXPIRATION_TO_CALENDAR, HOOK_EXPIRATION_TO_TASK, HOOK_RECORD_TO_GRAPH),
    graph_node_type=model.NODE_ASSET,
    graph_relations=(model.RELATION_OWNS, model.RELATION_COVERED_BY, model.RELATION_SECURED_BY),
    undx_draftable=True,
))


# ===========================================================================
# 12. Preferences and Instructions
# ===========================================================================
INSTRUCTION = register(Template(
    key="instruction",
    version=1,
    domain=model.DOMAIN_GENERAL,
    ia_domain=IA_PREFERENCES,
    display_key="privateOffice.templates.instruction.title",
    display_fallback="Preference or instruction",
    description_key="privateOffice.templates.instruction.description",
    description_fallback="A standing preference, and who it applies to.",
    icon="note",
    statuses=SIMPLE_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="instruction",
            label_key="privateOffice.sections.instruction",
            label_fallback="Instruction",
            fields=(
                FieldSpec(
                    path="instruction.subject", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.subject", label_fallback="Applies to",
                ),
                FieldSpec(
                    path="instruction.body", kind=KIND_LONG_TEXT, required=True,
                    label_key="privateOffice.fields.instructionBody",
                    label_fallback="Instruction",
                ),
                FieldSpec(
                    path="instruction.audience", kind=KIND_ENUM,
                    options=_opts(
                        "instruction_audience", "SELF", "FAMILY", "ASSISTANT",
                        "PROVIDER", "ADVISOR", "OTHER",
                    ),
                    searchable=True,
                    label_key="privateOffice.fields.audience", label_fallback="Audience",
                ),
                FieldSpec(
                    path="instruction.review_on", kind=KIND_DATE, searchable=True,
                    expires_record=True,
                    label_key="privateOffice.fields.reviewOn", label_fallback="Review on",
                ),
            ),
        ),
    ),
    reminders=(
        ReminderRule("d7", 7, "privateOffice.reminders.d7", "7 days before", True),
    ),
    automations=(HOOK_EXPIRATION_TO_TASK,),
    undx_draftable=True,
))


# ===========================================================================
# 13. Custom
# ===========================================================================
CUSTOM_RECORD = register(Template(
    key="custom_record",
    version=1,
    domain=model.DOMAIN_GENERAL,
    ia_domain=IA_CUSTOM,
    display_key="privateOffice.templates.customRecord.title",
    display_fallback="Custom record",
    description_key="privateOffice.templates.customRecord.description",
    description_fallback="Something the structured templates do not cover yet.",
    icon="custom",
    statuses=SIMPLE_STATUSES,
    default_status="DRAFT",
    sections=(
        SectionSpec(
            key="custom",
            label_key="privateOffice.sections.custom",
            label_fallback="Record",
            fields=(
                FieldSpec(
                    path="custom.label", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.label", label_fallback="Label",
                ),
                FieldSpec(
                    path="custom.value", kind=KIND_LONG_TEXT,
                    label_key="privateOffice.fields.value", label_fallback="Value",
                ),
                FieldSpec(
                    path="custom.review_on", kind=KIND_DATE, searchable=True,
                    expires_record=True,
                    label_key="privateOffice.fields.reviewOn", label_fallback="Review on",
                ),
            ),
        ),
    ),
    reminders=(
        ReminderRule("d7", 7, "privateOffice.reminders.d7", "7 days before", True),
    ),
    automations=(HOOK_EXPIRATION_TO_TASK,),
    undx_draftable=False,
))


LEGACY_RECORD = register(Template(
    key="legacy_record",
    version=1,
    domain=model.DOMAIN_GENERAL,
    ia_domain=IA_CUSTOM,
    display_key="privateOffice.templates.legacyRecord.title",
    display_fallback="Custom record — legacy",
    description_key="privateOffice.templates.legacyRecord.description",
    description_fallback=(
        "A fact recorded before structured templates existed. Nothing has been "
        "changed; open it to file it properly."
    ),
    icon="archive",
    statuses=("NEEDS_REVIEW", "ACTIVE", "ARCHIVED"),
    default_status="NEEDS_REVIEW",
    sections=(
        SectionSpec(
            key="legacy",
            label_key="privateOffice.sections.legacy",
            label_fallback="Original record",
            fields=(
                # These four fields are the legacy fact's own columns, carried
                # across verbatim. The migration copies; it does not interpret.
                # A member who opens one of these sees exactly what they typed,
                # which is the only honest thing to show them.
                FieldSpec(
                    path="legacy.fact_type", kind=KIND_TEXT, required=True,
                    searchable=True, identity=True,
                    label_key="privateOffice.fields.factType",
                    label_fallback="Original fact type",
                ),
                FieldSpec(
                    path="legacy.value", kind=KIND_LONG_TEXT,
                    label_key="privateOffice.fields.originalValue",
                    label_fallback="Original value",
                ),
                FieldSpec(
                    path="legacy.value_type", kind=KIND_TEXT, max_length=32,
                    label_key="privateOffice.fields.originalValueType",
                    label_fallback="Original value type",
                ),
                FieldSpec(
                    path="legacy.fact_key", kind=KIND_TEXT, identity=True,
                    label_key="privateOffice.fields.originalKey",
                    label_fallback="Original key",
                ),
            ),
        ),
    ),
    automations=(),
    undx_readable=True,
    undx_draftable=False,
))


__all__ = [
    "IA_DOMAINS",
    "IA_DOMAIN_KEYS",
    "PASSPORT",
    "DRIVERS_LICENCE",
    "NATIONAL_ID",
    "RESIDENCE",
    "EMERGENCY_CONTACT",
    "CERTIFICATION",
    "EMPLOYMENT",
    "FINANCIAL_ACCOUNT",
    "CONTRACT",
    "MEDICAL_CONDITION",
    "DIGITAL_ACCOUNT",
    "VISA",
    "VEHICLE",
    "INSTRUCTION",
    "CUSTOM_RECORD",
    "LEGACY_RECORD",
]
