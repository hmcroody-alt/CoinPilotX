"""PulseSoc Private Office — canonical substrate.

This package is the single owner of the Private Office tier ladder, feature
matrix, private fact store, private graph, and the Private Office service
registry. It is deliberately thin: it does NOT reimplement entitlements,
UNDX capability registration, governed execution, verification, or Pulse
Briefings. Those foundations already exist and stay canonical.

Ownership boundaries are frozen in ``PRIVATE_OFFICE_OWNERSHIP_CONTRACT.md``
at the repo root. Read it before adding a module here. In particular:

* Entitlement grants remain owned by ``services/business_os/entitlements``.
  This package only *maps* canonical grants onto a four-rung tier ladder.
* Agent capabilities register in ``services/undx_capability_registry.py``.
  A second capability registry is forbidden.
* Governed execution stays ``services/undx_tool_gateway.execute()``.
* Overall user risk stays owned by ``services/user_trust_engine.py``. Nothing
  in this package may emit a score about a *person*.

Submodules are not imported eagerly: importing ``services.private_office``
must stay cheap and side-effect free so workers can import it at startup.
"""

__all__ = [
    # Entitlement (Stages 1-2)
    "tiers",
    "feature_matrix",
    "status",
    # Substrate (Stages 6-13). `model` is the shared vocabulary, `schema` owns
    # the DDL, and `facts` / `graph` are the only writers — feature code calls
    # those rather than issuing its own INSERT, which
    # `tests/private_office/test_private_write_boundary.py` enforces.
    "model",
    "schema",
    "audit",
    "facts",
    "graph",
    "contradictions",
    # Observability (Stages 34-38). `telemetry` declares the only six events
    # this package may emit and structurally forbids a fact value reaching one;
    # `health` is the read-only operator surface. Neither accepts a user
    # identifier, and `health` is the module to extend rather than adding a
    # second status endpoint elsewhere.
    "telemetry",
    "health",
    # Retrieval (Stages 15-17). The only sanctioned way for anything outside
    # this package — UNDX above all — to obtain private context. It applies
    # owner, authorization, sensitivity, domain and purpose before any row
    # leaves, which is why callers must not assemble context from `facts` and
    # `graph` directly.
    "retrieval",
    # Relationship Intelligence and Private Meetings, the two features the
    # Office still contains, plus the second lock that guards both.
    "relationships",
    "meetings",
    "security",
]


#: Engines whose feature was withdrawn from the product surface.
#:
#: The Private Office contains three things: Relationship Intelligence, Private
#: Meetings and Office Security. Document Intelligence, Private Briefings,
#: Private Shield, Human Concierge, Private Conversations, Private Operations,
#: the Capital Graph and Structured Records were withdrawn. Their routes are
#: deregistered, their screens are deleted and their clients are gone.
#:
#: Their engines are still here, and that is a decision rather than an
#: oversight. Each name below is classified, and the two classes get different
#: treatment:
#:
#: HISTORICAL DATA — the module owns `CREATE TABLE IF NOT EXISTS` DDL and is the
#: only reader and writer of tables that still hold member rows in production.
#: Deleting the module would not delete one row; the tables outlive it either
#: way. What deletion would destroy is the only description in this repository
#: of what those rows *mean* — which columns are encrypted, which are
#: provenance, how a claim relates to a document. A member exercising a data
#: request, or a decision to bring a feature back, both need that description,
#: and neither is served by a migration that drops the table.
#:
#:   briefings                private_office_briefings,
#:                            private_office_briefing_items
#:   concierge                private_concierge_messages
#:   conversations            private_office_conversations,
#:                            private_office_conversation_links
#:   documents                private_documents, private_document_claims
#:   jobs                     private_office_jobs
#:   shield                   private_shield_findings
#:   structured_records       private_structured_records, private_record_fields,
#:                            private_record_revisions
#:   record_templates         the field vocabulary the rows above are stored
#:   record_template_catalog  against; without it the revisions are opaque
#:
#: DEAD CODE — pure projection over rows owned elsewhere. These own no table and
#: hold no state, so nothing is lost by deleting them except the arithmetic
#: itself. They are kept with the cluster above rather than separately because
#: they are how the surviving Capital Graph rows were ever turned into a number,
#: and a half-removed cluster is harder to reason about than a whole retired one.
#:
#:   capital_graph, capital_overview, cash_flow, obligation_projection,
#:   integrity
#:
#: What is NOT here is as deliberate. `facts`, `graph`, `model`, `schema`,
#: `audit`, `evidence`, `records`, `retrieval`, `telemetry` and `field_crypto`
#: all lost callers when these features went, and every one of them is SHARED:
#: `facts` is read by `pulse_briefings` and both UNDX brain modules and is the
#: fact store behind a person's profile; `field_crypto` encrypts supplier
#: credentials for Business OS; `portfolio_projection` is imported by
#: `portfolio_service`; `schema` is imported by fourteen modules outside this
#: package. None of them is a Private Office feature engine and none is retired.
#:
#: The risk a retired engine carries is not that it runs — it cannot, nothing
#: reaches it — but that someone imports one back into a live path and gets a
#: withdrawn feature working again by accident.
#: `tests/private_office/test_retired_engines_stay_unreachable.py` computes the
#: import graph and fails if any name below becomes reachable from product code.
RETIRED_ENGINE_MODULES = (
    "briefings",
    "capital_graph",
    "capital_overview",
    "cash_flow",
    "concierge",
    "conversations",
    "documents",
    "integrity",
    "jobs",
    "obligation_projection",
    "record_template_catalog",
    "record_templates",
    "shield",
    "structured_records",
)
