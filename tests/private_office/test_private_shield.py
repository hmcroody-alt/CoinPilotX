"""Private Shield — internal monitoring with honest edges.

Run either way::

    python -m pytest tests/private_office/test_private_shield.py
    python tests/private_office/test_private_shield.py

What these tests defend
-----------------------
* **The gate order holds over HTTP.** No session is 401, no tier is 403, the
  kill switch is 404, a locked Office is 423 — before any posture is read.
* **Safety is never fabricated.** An empty office scans to zero findings said
  truthfully, and every payload — posture and scan alike — states that no
  external breach provider exists and nothing external has been checked. The
  matrix row ``private_shield.breach_monitoring`` stays PROVIDER_REQUIRED.
* **Detection is deterministic and cited.** Overdue obligations, fact
  contradictions, unreviewed claims, unreadable documents and expired facts
  each surface as a finding with evidence refs that parse and resolve to the
  rows behind them.
* **Rescans deduplicate, never multiply.** A persisting condition refreshes
  its open finding; a dismissed finding stays dismissed; a condition that is
  gone resolves its finding with a note that says exactly that.
* **The lifecycle is the member's.** OPEN → ACKNOWLEDGED → RESOLVED /
  DISMISSED, terminal states final, no path back to OPEN.
* **Owner isolation everywhere.** Another member's findings are invisible and
  untouchable; their posture is their own.
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_shield_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route packs can import it -----------------
_stub = types.ModuleType("bot")
_stub._test_user = None


def _api_account_user():
    return _stub._test_user


def _require_admin_api(permission):
    return (None, ("DENIED", 403))


_stub.api_account_user = _api_account_user
_stub.require_admin_api = _require_admin_api
sys.modules["bot"] = _stub

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services import private_office_shield_routes as sh_routes  # noqa: E402
from services.private_office import documents as documents_mod  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts as facts_mod  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import jobs as jobs_mod  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import records as records_mod  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import shield as shield_mod  # noqa: E402
from services.private_office import tiers  # noqa: E402

USER_A = 9931
USER_B = 9932
USER_C = 9933  # a real session with no Private tier — the 403 case

# Seeded content that must never surface in audit rows.
_SECRETS = ("Renew umbrella policy", "ownership_share", "PX-9313")

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


_GRANTS: dict[int, str] = {}
PASSCODE = "638194"


class _GrantClient(FlaskClient):
    def open(self, *args, **kwargs):
        user = _stub._test_user or {}
        token = _GRANTS.get(int(user.get("user_id") or 0), "")
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(routes.GRANT_HEADER, token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _app():
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    routes.register(app)
    sh_routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active", "access_enabled": 1}


def _unlock(user_id):
    app = Flask(__name__)
    routes.register(app)
    client = app.test_client()
    _as(user_id)
    client.post("/api/private-office/security/setup",
                json={"passcode": PASSCODE, "confirm_passcode": PASSCODE})
    resp = client.post("/api/private-office/security/unlock",
                       json={"passcode": PASSCODE})
    token = (resp.get_json() or {}).get("grant_token") or ""
    if token:
        _GRANTS[int(user_id)] = token
    return token


def setup_environment():
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B, USER_C):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        records_mod.ensure_records_schema(cur, force=True)
        documents_mod.ensure_documents_schema(cur, force=True)
        shield_mod.ensure_shield_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


def _query_all(sql, params=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _service(work):
    """Run a service-level write with commit-on-success, like a route would."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        out = work(cur)
        conn.commit()
        return out
    finally:
        conn.close()


_STATE = {}  # ids threaded between stages


# ---------------------------------------------------------------------------
# Gate order
# ---------------------------------------------------------------------------

def stage_gates():
    print("\n[shield: gates]")
    client = _app().test_client()

    _stub._test_user = None
    resp = client.get("/api/private-office/shield")
    check("no session is 401", resp.status_code == 401, str(resp.status_code))

    _as(USER_C)
    resp = client.get("/api/private-office/shield")
    body = resp.get_json() or {}
    check("no Private tier is 403 with a minimum tier",
          resp.status_code == 403 and body.get("minimum_tier"),
          f"{resp.status_code} {body}")

    previous = os.environ.get("PRIVATE_SHIELD_ENABLED")
    os.environ["PRIVATE_SHIELD_ENABLED"] = "false"
    try:
        _as(USER_A)
        resp = client.get("/api/private-office/shield")
        check("kill switch off is 404 for an entitled member",
              resp.status_code == 404, str(resp.status_code))
        state = matrix.availability(sh_routes.SHIELD_FEATURE_ID, tiers.TIER_PRIVATE)
        check("flag off reads as FEATURE_DISABLED, implementation still IMPLEMENTED",
              state["availability"] == matrix.AVAIL_FEATURE_DISABLED
              and state["implementation"] == matrix.IMPL_IMPLEMENTED, str(state))
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_SHIELD_ENABLED", None)
        else:
            os.environ["PRIVATE_SHIELD_ENABLED"] = previous

    _as(USER_A)
    resp = client.get("/api/private-office/shield",
                      headers={routes.GRANT_HEADER: ""})
    check("no unlock grant is 423 Locked", resp.status_code == 423, str(resp.status_code))

    resp = client.get("/api/private-office/shield")
    body = resp.get_json() or {}
    check("unlocked member reaches the posture",
          resp.status_code == 200 and body.get("ok"), str(resp.status_code))
    check("no-store on the posture",
          "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))


# ---------------------------------------------------------------------------
# Truthful zero
# ---------------------------------------------------------------------------

def stage_truthful_zero():
    print("\n[shield: an empty office is a truthful zero]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.get("/api/private-office/shield")
    body = resp.get_json() or {}
    posture = body.get("posture") or {}
    check("before any scan: zero open findings, no last scan",
          posture.get("open_findings") == 0 and posture.get("last_scan") is None,
          str(posture))
    external = (posture.get("external") or {}).get("breach_monitoring") or {}
    check("external coverage is stated as unmonitored, PROVIDER_REQUIRED",
          external.get("monitored") is False
          and external.get("state") == "PROVIDER_REQUIRED",
          str(external))
    check("the payload never claims external monitoring",
          (body.get("provider_status") or {}).get("external_monitoring") == "none",
          str(body.get("provider_status")))

    resp = client.post("/api/private-office/shield/scan")
    body = resp.get_json() or {}
    scan = body.get("scan") or {}
    check("scanning an empty office is 201 with zero findings — not an error, not a fabrication",
          resp.status_code == 201 and scan.get("new") == 0
          and scan.get("open_findings") == [], f"{resp.status_code} {scan}")


# ---------------------------------------------------------------------------
# Seeding conditions
# ---------------------------------------------------------------------------

def stage_seed():
    print("\n[shield: seeding conditions]")

    def work(cur):
        overdue = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Renew umbrella policy", obligation_type="INSURANCE",
            due_at="2026-01-15T00:00:00+00:00", actor_user_id=USER_A)
        # 35% vs 40% for one period, two sources — the canonical contradiction.
        for value, source in (("35", model.PROVENANCE_USER_ASSERTED),
                              ("40", model.PROVENANCE_PROVIDER_ASSERTED)):
            facts_mod.record_fact(
                cur, owner_user_id=USER_A, subject_type="NODE", subject_id="77",
                fact_type="ownership_share", value=value,
                value_type=model.VALUE_PERCENT, provenance_type=source,
                observed_at="2026-01-01T00:00:00+00:00",
                valid_from="2026-01-01T00:00:00+00:00",
                valid_to="2026-12-31T00:00:00+00:00", actor_user_id=USER_A)
        expired = facts_mod.record_fact(
            cur, owner_user_id=USER_A, subject_type="NODE", subject_id="78",
            fact_type="passport_valid", value="true",
            value_type=model.VALUE_BOOLEAN,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
            observed_at="2025-01-01T00:00:00+00:00",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_to="2025-12-31T00:00:00+00:00", actor_user_id=USER_A)
        txt = documents_mod.store_document(
            cur, owner_user_id=USER_A, filename="policy.txt",
            content=b"insurance_policy_number: PX-9313\n",
            title="Umbrella policy", actor_user_id=USER_A)
        documents_mod.process_document(
            cur, owner_user_id=USER_A, document_id=int(txt["id"]),
            content=b"insurance_policy_number: PX-9313\n", actor_user_id=USER_A)
        pdf = documents_mod.store_document(
            cur, owner_user_id=USER_A, filename="deed.pdf",
            content=b"%PDF-1.4 minimal", title="Property deed",
            actor_user_id=USER_A)
        outcome = documents_mod.process_document(
            cur, owner_user_id=USER_A, document_id=int(pdf["id"]),
            content=b"%PDF-1.4 minimal", actor_user_id=USER_A)
        return overdue, expired, int(txt["id"]), int(pdf["id"]), outcome

    overdue, expired, txt_id, pdf_id, pdf_outcome = _service(work)
    _STATE.update({
        "overdue": int(overdue["record_id"]),
        "expired_fact": int(expired.get("fact_id") or 0),
        "txt": txt_id, "pdf": pdf_id,
    })
    check("conditions seed through the canonical writers",
          overdue["status"] in ("created", "existing") and txt_id and pdf_id,
          f"{overdue} {txt_id} {pdf_id}")
    check("the PDF lands PROVIDER_REQUIRED — the honest unreadable state",
          str(pdf_outcome.get("extraction_state") or "")
          == documents_mod.EXTRACTION_PROVIDER_REQUIRED, str(pdf_outcome))


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def stage_scan():
    print("\n[shield: the scan]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post("/api/private-office/shield/scan")
    body = resp.get_json() or {}
    scan = body.get("scan") or {}
    findings = scan.get("open_findings") or []
    check("the scan is 201 and finds the seeded conditions",
          resp.status_code == 201 and scan.get("new") == len(findings) > 0,
          f"{resp.status_code} new={scan.get('new')} open={len(findings)}")

    by_kind = {}
    for finding in findings:
        by_kind.setdefault(finding["kind"], []).append(finding)
    check("all five internal checks fire",
          set(by_kind) == {"OVERDUE_OBLIGATION", "FACT_CONTRADICTION",
                           "UNREVIEWED_CLAIMS", "EXTRACTION_GAP", "EXPIRED_FACT"},
          str(sorted(by_kind)))

    overdue = (by_kind.get("OVERDUE_OBLIGATION") or [{}])[0]
    check("the overdue obligation is HIGH and cites its record",
          overdue.get("severity") == "HIGH"
          and overdue.get("evidence") == [evidence.format_ref("obligation", _STATE["overdue"])],
          str(overdue))
    contradiction = (by_kind.get("FACT_CONTRADICTION") or [{}])[0]
    check("the contradiction cites both competing facts",
          contradiction.get("severity") == "HIGH"
          and len(contradiction.get("evidence") or []) == 2,
          str(contradiction))
    gap = (by_kind.get("EXTRACTION_GAP") or [{}])[0]
    check("the unreadable PDF surfaces with its document ref",
          gap.get("evidence") == [evidence.format_ref("document", _STATE["pdf"])],
          str(gap))
    check("every finding is cited and every ref parses",
          all(f.get("evidence") for f in findings)
          and all(evidence.parse_ref(ref)
                  for f in findings for ref in f["evidence"]),
          str(findings[:2]))
    _STATE["findings"] = {f["kind"]: f for f in findings}
    _STATE["finding_count"] = len(findings)

    resp = client.get("/api/private-office/shield")
    posture = (resp.get_json() or {}).get("posture") or {}
    check("the posture now counts the open findings by severity",
          posture.get("open_findings") == len(findings)
          and (posture.get("by_severity") or {}).get("HIGH", 0) >= 2,
          str(posture.get("by_severity")))
    check("the last scan is a SUCCEEDED job",
          (posture.get("last_scan") or {}).get("status") == jobs_mod.STATUS_SUCCEEDED,
          str(posture.get("last_scan")))


def stage_dedupe():
    print("\n[shield: rescans deduplicate]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post("/api/private-office/shield/scan")
    scan = (resp.get_json() or {}).get("scan") or {}
    check("a rescan of unchanged conditions opens nothing new",
          scan.get("new") == 0 and scan.get("refreshed") == _STATE["finding_count"],
          f"new={scan.get('new')} refreshed={scan.get('refreshed')}")
    check("the findings list did not multiply",
          len(scan.get("open_findings") or []) == _STATE["finding_count"],
          str(len(scan.get("open_findings") or [])))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def stage_lifecycle():
    print("\n[shield: the lifecycle is the member's]")
    client = _app().test_client()
    _as(USER_A)

    expired = _STATE["findings"]["EXPIRED_FACT"]
    resp = client.post(f"/api/private-office/shield/findings/{expired['id']}",
                       json={"status": "ACKNOWLEDGED"})
    body = resp.get_json() or {}
    check("acknowledging is 200 and audited",
          resp.status_code == 200
          and (body.get("finding") or {}).get("status") == "ACKNOWLEDGED",
          f"{resp.status_code} {body}")

    resp = client.post(f"/api/private-office/shield/findings/{expired['id']}",
                       json={"status": "RESOLVED", "note": "renewed the passport"})
    check("an acknowledged finding resolves",
          resp.status_code == 200
          and ((resp.get_json() or {}).get("finding") or {}).get("status") == "RESOLVED",
          str(resp.status_code))

    resp = client.post(f"/api/private-office/shield/findings/{expired['id']}",
                       json={"status": "OPEN"})
    check("terminal states are final — 400", resp.status_code == 400,
          str(resp.status_code))

    gap = _STATE["findings"]["EXTRACTION_GAP"]
    resp = client.post(f"/api/private-office/shield/findings/{gap['id']}",
                       json={"status": "DISMISSED", "note": "will scan it at the notary"})
    check("dismissing is a member's right", resp.status_code == 200,
          str(resp.status_code))

    resp = client.post(f"/api/private-office/shield/findings/{gap['id']}",
                       json={"status": "SHOUTING"})
    check("an unknown status is refused 400", resp.status_code == 400,
          str(resp.status_code))

    def resolve_obligation(cur):
        return records_mod.update_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            record_id=_STATE["overdue"], status="RESOLVED", actor_user_id=USER_A)

    _service(resolve_obligation)
    resp = client.post("/api/private-office/shield/scan")
    scan = (resp.get_json() or {}).get("scan") or {}
    check("the dismissed finding stays dismissed — suppressed, not re-opened",
          scan.get("suppressed", 0) >= 1, str(scan))
    check("the resolved obligation's finding clears on rescan",
          scan.get("cleared", 0) >= 1, str(scan))
    kinds_open = {f["kind"] for f in scan.get("open_findings") or []}
    check("neither cleared nor dismissed conditions remain open",
          "OVERDUE_OBLIGATION" not in kinds_open
          and "EXTRACTION_GAP" not in kinds_open, str(kinds_open))

    resp = client.get("/api/private-office/shield/findings",
                      query_string={"status": "RESOLVED"})
    rows = (resp.get_json() or {}).get("findings") or []
    auto = [r for r in rows if r["resolution_note"] == shield_mod.CLEARED_NOTE]
    check("the auto-cleared finding says exactly why it closed",
          any(r["kind"] == "OVERDUE_OBLIGATION" for r in auto), str(auto))


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def stage_isolation():
    print("\n[shield: isolation]")
    client = _app().test_client()
    _as(USER_B)

    resp = client.get("/api/private-office/shield")
    posture = (resp.get_json() or {}).get("posture") or {}
    check("B's posture is untouched by A's findings",
          posture.get("open_findings") == 0, str(posture.get("open_findings")))

    resp = client.get("/api/private-office/shield/findings")
    check("B's findings list is empty",
          (resp.get_json() or {}).get("findings") == [], str(resp.get_json()))

    a_finding = _STATE["findings"]["FACT_CONTRADICTION"]
    resp = client.post(f"/api/private-office/shield/findings/{a_finding['id']}",
                       json={"status": "DISMISSED"})
    check("B cannot touch A's finding — 404", resp.status_code == 404,
          str(resp.status_code))

    def read(cur):
        return shield_mod.get_finding(
            cur, owner_user_id=USER_B, finding_id=a_finding["id"])

    check("service-level reads answer None across the owner boundary",
          _service(read) is None)


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------

def stage_bookkeeping():
    print("\n[shield: bookkeeping]")

    def read(cur):
        return jobs_mod.list_jobs(cur, owner_user_id=USER_A,
                                  job_type=jobs_mod.JOB_SHIELD_SCAN)

    runs = _service(read)
    check("every scan ran under a job that succeeded",
          len(runs) >= 3
          and all(j.get("status") == jobs_mod.STATUS_SUCCEEDED for j in runs),
          str([(j.get("status")) for j in runs]))

    audits = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE owner_user_id=? "
        f"AND action IN (?, ?, ?)",
        (USER_A, "PRIVATE_SHIELD_SCAN", "PRIVATE_SHIELD_READ",
         "PRIVATE_SHIELD_FINDING_UPDATE"))
    actions = {row["action"] for row in audits}
    check("scans, reads and finding updates are all audited",
          {"PRIVATE_SHIELD_SCAN", "PRIVATE_SHIELD_READ",
           "PRIVATE_SHIELD_FINDING_UPDATE"} <= actions, str(actions))

    flat = " ".join(str(v) for row in audits for v in row.values())
    check("audit rows are metadata only — no member content",
          not any(secret in flat for secret in _SECRETS), flat[:200])


# ---------------------------------------------------------------------------
# The feature matrix tells the truth
# ---------------------------------------------------------------------------

def stage_feature_matrix():
    print("\n[shield: feature matrix]")
    got = matrix.availability("private_shield", tiers.TIER_PRIVATE)
    check("private_shield is IMPLEMENTED and ENTITLED at PRIVATE",
          got["implementation"] == matrix.IMPL_IMPLEMENTED
          and got["availability"] == matrix.AVAIL_ENTITLED, str(got))
    spec = matrix.get("private_shield")
    check("the kill switch is the documented flag",
          spec.flag_env == "PRIVATE_SHIELD_ENABLED", str(spec.flag_env))
    breach = matrix.availability("private_shield.breach_monitoring",
                                 tiers.TIER_PRIVATE_OFFICE)
    check("breach monitoring stays PROVIDER_REQUIRED — flipping Shield never implied it",
          breach["implementation"] == matrix.IMPL_PROVIDER_REQUIRED
          and breach["availability"] == matrix.AVAIL_NOT_IMPLEMENTED,
          str(breach))


STAGES = (
    stage_gates,
    stage_truthful_zero,
    stage_seed,
    stage_scan,
    stage_dedupe,
    stage_lifecycle,
    stage_isolation,
    stage_bookkeeping,
    stage_feature_matrix,
)


def test_everything():
    setup_environment()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    setup_environment()
    for stage in STAGES:
        stage()
    print()
    if _FAILURES:
        print(f"FAILURES ({len(_FAILURES)}):")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("All private shield checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
