"""Everything the PulseDrop ops page needs to render, assembled here.

## Why this is not in the route

The route's job is authentication and HTML. Reading five tables, deciding
whether a setting is overridden or inherited, and working out whether a stalled
render is stalled is domain logic, and it is the same logic a future JSON
endpoint or a CLI would need. It also means the interesting part is testable
without a request context or an admin session.

## What the page is actually for

Three questions, in the order an operator asks them.

*Is it on?* — the master switch and the two surface switches, with the value
that is **running** rather than the value in the environment, because the
override layer means those differ.

*Is it working?* — the run log. PulseDrop's normal healthy state is publishing
nothing: an empty catalog, everything on cooldown, and a crash all look identical
from outside, which is exactly why ``curator`` writes a row per tick with its
outcome and rejection histogram. This surface exists to make that legible.

*What did it do?* — recent publications, each naming the listing and the seller,
so "PulseDrop posted something odd" is one lookup rather than a feed scroll.

## Why nothing here can stop a page load

Every reader degrades to an empty section. A deployment that has never run
PulseDrop has no tables, and an ops page that 500s on a subsystem being switched
off is an ops page nobody can use to switch it back on.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from . import account, audio, config, curator, lease, schema

log = logging.getLogger(__name__)

#: Renders that have sat in one state this long are reported as stuck. Ten
#: minutes is comfortably past ``reel_render_timeout_seconds``' ceiling of 15,
#: so a render that is merely slow is not flagged as a casualty.
STALL_MINUTES = 10


def _ensure_schema() -> None:
    """Create the tables if they are missing, and shrug if that is impossible.

    Separate from ``schema.ensure_schema`` because that one raises, correctly:
    a worker that cannot create its tables should fail loudly rather than tick
    forever against nothing. This caller wants the opposite. The page whose
    reason for existing is the kill switch cannot be the page that 500s when
    PulseDrop's storage is the thing that is broken, and every reader below
    already degrades to an empty section on its own.
    """
    try:
        schema.ensure_schema()
    except Exception:
        log.warning("pulsedrop_ops_schema_unavailable", exc_info=True)


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    """Query, or an empty list. Never raises — see the module docstring."""
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall() or []]
    except Exception:
        log.warning("pulsedrop_ops_query_failed sql=%s", sql.split()[0:4], exc_info=True)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def settings_view() -> list[dict]:
    """Every tunable, with where its current value came from.

    ``source`` is the part that is worth having. A value shown without it is
    ambiguous in the one way that matters during an incident: an operator who
    sees ``PulseDrop enabled: false`` cannot tell whether someone hit the kill
    switch or whether the deployment never set the variable, and those call for
    opposite actions.
    """
    stored = config.overrides()
    view: list[dict] = []
    for key, spec in config.SETTINGS.items():
        resolved = config.resolve(key)
        override = stored.get(key)
        view.append({
            "key": key,
            "spec": spec,
            "label": spec.label or key,
            "group": spec.group or "Other",
            "help": spec.help,
            "kind": spec.kind,
            "value": resolved,
            "display": _display(spec, resolved),
            "overridden": override not in (None, ""),
            "override": override or "",
            "source": "override" if override not in (None, "") else "environment",
        })
    return view


def _display(spec, value) -> str:
    if spec.kind == config.FLAG:
        return "on" if value else "off"
    return str(value)


def groups(view: list[dict]) -> list[tuple[str, list[dict]]]:
    """``settings_view`` bucketed by heading, in declaration order."""
    ordered: dict[str, list[dict]] = {}
    for item in view:
        ordered.setdefault(item["group"], []).append(item)
    return list(ordered.items())


def account_state() -> dict:
    """Whether @pulsedrop exists yet, and what it looks like if so."""
    user_id = account.account_user_id()
    if not user_id:
        # Not an error. The account is provisioned by the worker's first cycle,
        # so "absent" is the correct state of a deployment that has never run.
        return {"provisioned": False, "user_id": 0}
    rows = _rows(
        "SELECT username, display_name, avatar_url, cover_url, account_status, login_enabled"
        " FROM users WHERE user_id=? LIMIT 1",
        (user_id,),
    )
    row = rows[0] if rows else {}
    followers = _rows(
        "SELECT COUNT(*) AS total FROM pulse_follows WHERE followed_user_id=?", (user_id,)
    )
    return {
        "provisioned": True,
        "user_id": user_id,
        "username": row.get("username") or account.USERNAME,
        "display_name": row.get("display_name") or account.DISPLAY_NAME,
        "avatar_url": row.get("avatar_url") or "",
        "cover_url": row.get("cover_url") or "",
        "status": row.get("account_status") or "",
        # Asserted on the page because it is the security property, and a row
        # edited by hand could quietly turn it back on.
        "login_disabled": not bool(row.get("login_enabled") or 0),
        "follower_count": int((followers[0] if followers else {}).get("total") or 0),
    }


def runs(limit: int = 20) -> list[dict]:
    """Recent ticks, newest first, with the rejection histogram decoded."""
    try:
        recent = curator.recent_runs(limit)
    except Exception:
        # ``recent_runs`` degrades on a failed *read* but calls
        # ``ensure_schema`` outside its own guard, so a database that will not
        # take the DDL still reaches here as an exception.
        log.warning("pulsedrop_ops_runs_unavailable", exc_info=True)
        return []
    decoded = []
    for row in recent:
        item = dict(row)
        try:
            item["rejected"] = json.loads(item.get("rejected_json") or "{}")
        except Exception:
            item["rejected"] = {}
        decoded.append(item)
    return decoded


def publications(limit: int = 20) -> list[dict]:
    """What PulseDrop published, newest first, with the product's title.

    LEFT JOINed so a listing the seller has since deleted still shows as a row
    rather than vanishing from the audit trail — the post it produced is still
    in the feed, and "what is that" is the question this table answers.
    """
    return _rows(
        """
        SELECT p.id, p.surface, p.state, p.listing_id, p.seller_user_id, p.category,
               p.post_id, p.published_at, p.claimed_by,
               l.title AS listing_title, l.status AS listing_status,
               u.username AS seller_username
        FROM pulsedrop_publications p
        LEFT JOIN marketplace_listings l ON l.id = p.listing_id
        LEFT JOIN users u ON u.user_id = p.seller_user_id
        ORDER BY p.id DESC LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def renders(limit: int = 20) -> list[dict]:
    return _rows(
        "SELECT id, listing_id, source_kind, state, attempts, max_attempts, failure_reason,"
        " duration_seconds, video_url, claimed_by, updated_at"
        " FROM pulsedrop_renders ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
    )


def health(run_log: list[dict], render_log: list[dict], *, now: datetime | None = None) -> dict:
    """The few facts worth putting above the fold, derived from the logs above.

    ``last_publication_at`` is reported separately from ``last_run_at`` because
    the gap between them is the diagnosis. A recent run and an old publication is
    PulseDrop working as designed — most ticks should publish nothing. A stale
    run is the worker not running at all, which no amount of catalog is going to
    fix and which the page should not leave anyone to infer.
    """
    now = now or datetime.utcnow()
    published = [row for row in run_log if row.get("outcome") == curator.PUBLISHED]
    failures = [row for row in run_log if row.get("outcome") in (curator.FAILED, curator.ERROR)]
    stuck = [
        row for row in render_log
        if row.get("state") in ("pending", "rendering")
        and _age_minutes(row.get("updated_at"), now) > STALL_MINUTES
    ]
    return {
        "last_run_at": (run_log[0].get("started_at") if run_log else ""),
        "last_run_outcome": (run_log[0].get("outcome") if run_log else ""),
        "last_publication_at": (published[0].get("finished_at") if published else ""),
        "runs_shown": len(run_log),
        "failures_shown": len(failures),
        "renders_failed": len([r for r in render_log if r.get("state") == "failed"]),
        "renders_stuck": len(stuck),
    }


def _age_minutes(stamp, now: datetime) -> float:
    """Minutes since ``stamp``. An unparseable stamp is treated as brand new.

    Deliberately not "infinitely old": these timestamps are written by two
    engines in two formats, and a parse failure that reported every render as
    stalled would put a permanent red count on the page that no action clears.
    """
    text = str(stamp or "").strip()
    if not text:
        return 0.0
    for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return max(0.0, (now - datetime.strptime(text[:19], shape)).total_seconds() / 60.0)
        except ValueError:
            continue
    return 0.0


def schedule_view(*, now: datetime | None = None) -> dict:
    """The lease row, read as "when will something happen, and is it happening".

    Read from the lease rather than inferred from the run log because the lease
    *is* the schedule — it is the row the next tick's conditional UPDATE tests
    against — and because it is the only thing that distinguishes the two
    silences an operator has to tell apart. A curator that is mid-tick has an
    owner and an unexpired lease; one whose worker is not running at all is due
    and unheld, which the run log cannot show, since both look like "no new
    rows".
    """
    now = now or datetime.utcnow()
    row = lease.status()
    next_run_at = str(row.get("next_run_at") or "")
    holder = str(row.get("owner") or "")
    expires_at = str(row.get("expires_at") or "")
    # A lease whose expiry has passed is not held, whatever the owner column
    # says: expiry, not release, is the recovery path after a hard crash, so a
    # stale owner name is the *expected* residue of one.
    held = bool(holder) and expires_at > _iso(now)
    return {
        "known": bool(row),
        "held_by": holder if held else "",
        "held": held,
        "expires_at": expires_at,
        "next_run_at": next_run_at,
        "due": (not next_run_at) or next_run_at <= _iso(now),
        "due_in": _due_in(next_run_at, now),
    }


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _due_in(next_run_at: str, now: datetime) -> str:
    """"in ~N min", or "due now". Rough on purpose.

    A precise countdown would be a lie in two directions: the worker polls on
    its own cadence, and another instance may hold the lease when the moment
    arrives. This answers "should I have seen something by now", which is the
    only thing the number is used for.
    """
    if not next_run_at:
        return "due now"
    for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            due = datetime.strptime(next_run_at[:19], shape)
        except ValueError:
            continue
        seconds = (due - now).total_seconds()
        return "due now" if seconds <= 0 else f"in ~{max(1, int(seconds // 60))} min"
    return ""


def dashboard(*, limit: int = 20, now: datetime | None = None) -> dict:
    """One call, everything the page draws."""
    _ensure_schema()
    run_log = runs(limit)
    render_log = renders(limit)
    view = settings_view()
    return {
        "account": account_state(),
        "settings": view,
        "groups": groups(view),
        "runs": run_log,
        "renders": render_log,
        "publications": publications(limit),
        "health": health(run_log, render_log, now=now),
        "schedule": schedule_view(now=now),
        "beds": audio.bed_view(),
        "bed_candidates": audio.candidates(),
    }


#: What an operator may ask the page to do, and what each one is called in the
#: audit log. Declared as a set so the route can reject anything else without
#: knowing what any of them mean.
#:
#: ``clear`` and ``clear_bed`` are unrelated despite the word: the first clears
#: a setting *override*, the second grants a music clearance. The second is
#: spelled out rather than shortened for exactly that reason.
ACTIONS = frozenset({"set", "clear", "run_now", "clear_bed", "revoke_bed"})


def apply_action(
    action: str,
    key: str,
    value,
    *,
    admin_user_id: int = 0,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Perform one operator action. Returns ``(changed, message)``.

    The message is written for the person who pressed the button, so a refusal
    says what was refused rather than raising: a settings page that 500s on a
    mistyped number is a settings page an operator stops trusting mid-incident.
    ``changed`` is what the caller should audit on — a rejected write is not an
    event worth a row, and a successful one always is.

    Deliberately not a lock. Two admins saving the same key race, and the second
    write wins, which is the same outcome as saving twice in sequence and the
    same thing every other admin form in this codebase does.

    ``now`` exists for the same reason :func:`schedule_view` has one, and the
    two must be given the *same* instant or neither answer means anything.
    ``run_now`` writes a timestamp and ``schedule_view`` reads it; a caller that
    pins one clock and lets the other run free is not asking a question about
    the schedule, it is asking what time it is. Production passes neither and
    both read the wall clock, which is the same thing.
    """
    if action not in ACTIONS:
        return False, "Unknown action."
    if action == "run_now":
        # Clears the interval gate rather than running a tick inside the
        # request. The worker owns publication — it holds the lease, it drains
        # renders, it is the path that has been exercised — and a web request
        # that published would be a second, less-tested publisher whose
        # worst-case duration is a video encode. What this does is say "you are
        # due", and the next worker cycle does the rest. If nothing happens
        # afterwards, the worker is not running, which is a fact worth learning.
        lease.schedule_next(0, now=now)
        log.info("pulsedrop_run_now admin=%s", admin_user_id)
        return True, "PulseDrop is due now. The next worker cycle will evaluate."
    if action in ("clear_bed", "revoke_bed"):
        # ``key`` is a track id here rather than a setting name, which is why
        # this returns before the settings lookup below. The value carries the
        # operator's note on where the rights come from — the whole point of the
        # clearance is that a person wrote down why, so it is stored verbatim.
        try:
            track_id = int(str(key or "").strip() or 0)
        except (TypeError, ValueError):
            return False, "That is not a track id."
        if action == "revoke_bed":
            return audio.revoke(track_id, admin_user_id=admin_user_id, now=now)
        return audio.clear(
            track_id, admin_user_id=admin_user_id, note=str(value or ""), now=now,
        )
    if key not in config.SETTINGS:
        return False, "Unknown setting."
    label = config.SETTINGS[key].label or key
    try:
        if action == "clear":
            config.clear_override(key, updated_by=admin_user_id)
            return True, f"{label} cleared — it now follows the environment."
        stored = config.set_override(key, value, updated_by=admin_user_id)
    except ValueError as exc:
        # The operator typed something the setting cannot hold. Their problem to
        # fix, so they are told exactly what was wrong with it.
        return False, str(exc)
    except Exception:
        # The storage did not take the write. Reported as a failure rather than
        # raised, because the page still has to render: an operator whose save
        # silently 500s does not know whether the switch moved.
        log.exception("pulsedrop_setting_write_failed key=%s", key)
        return False, f"{label} could not be saved. The settings table is not writable."
    return True, f"{label} set to {stored}."
