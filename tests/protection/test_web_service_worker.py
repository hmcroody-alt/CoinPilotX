"""One service worker, and push subscribed against it.

There used to be two. `static/service-worker.js` began as a copy of
`static/sw.js`, and at aa70aa7e ("Enable locked-screen Intelligence alert
delivery") the copy was hardened and the original was not. From then on the two
files disagreed about how to build a push notification, and nothing anywhere
said so -- both were valid JavaScript, both were served, both were registered,
and the site worked. The only symptom was that which notification you got
depended on which worker happened to own your subscription.

That is the failure this file is built around, so it is worth naming precisely.
A duplicated file is not a bug. A duplicated file becomes a bug the moment
someone fixes one copy, and there is no moment at which anyone is told that is
what they just did. Reviewing the fix shows a correct fix. The suite stays
green. The second copy simply keeps doing the old thing, in production, for the
subset of users who happen to be routed to it.

So the claims here are singularity claims -- exactly one worker takes fetch,
exactly one takes push, nothing registers the retired one -- plus one
regression claim: the hardening that only ever lived in the fork must still be
present in the survivor. Consolidating two files by keeping the wrong one is
the obvious way to do this badly, and it would look like a tidy-up in review.

The retired worker is *not* deleted, and that is deliberate enough to test.
A registered service worker runs from the browser's stored copy; removing the
file from the server unregisters nothing. Per spec a script fetch that 404s
makes the update job fail and leaves the old worker installed -- so deleting it
would strand the fork permanently on exactly the devices that have it. It stays,
as a worker whose only job is to unregister itself.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LIVE_WORKER = os.path.join("static", "sw.js")
RETIRED_WORKER = os.path.join("static", "service-worker.js")
PUSH_CLIENT = os.path.join("static", "notifications.js")

# The URL the single worker is served from, and the scope it claims.
LIVE_WORKER_URL = "/sw.js"
RETIRED_WORKER_URL = "/static/service-worker.js"

# Roots that ship to a browser. Same reasoning as the promotion gate: the
# question is "does a user receive this string", not "is this file source".
# Listed as an inclusion list so a new directory reads as uncovered rather than
# as covered-by-accident.
SHIPPED_ROOTS = ("templates", "static", os.path.join("web", "src"), "bot.py")

_HANDLER_RE = re.compile(
    r"""self\s*\.\s*addEventListener\s*\(\s*["'](\w+)["']""", re.VERBOSE
)


def _read(relative):
    with open(os.path.join(ROOT, relative), "r", encoding="utf-8") as handle:
        return handle.read()


def _strip_comments(source):
    """Drop // and /* */ so a comment mentioning a handler is not a handler.

    This file's whole job is distinguishing a live worker from a tombstone, and
    the tombstone's explanation of why it has no fetch handler necessarily
    contains the words "fetch handler". Without this, the tombstone would be
    read as live by the very test that exists to tell them apart -- the gate
    would fail on correct code, which is the failure mode that gets a gate
    deleted rather than fixed.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def _handlers(relative):
    """The set of service-worker events a file actually subscribes to."""
    return set(_HANDLER_RE.findall(_strip_comments(_read(relative))))


def _shipped_files():
    """Every file under SHIPPED_ROOTS that a browser could receive."""
    found = []
    for root in SHIPPED_ROOTS:
        absolute = os.path.join(ROOT, root)
        if os.path.isfile(absolute):
            found.append(root)
            continue
        for directory, _dirs, names in os.walk(absolute):
            if "node_modules" in directory or os.sep + "app" + os.sep in directory:
                continue
            for name in names:
                if name.endswith((".js", ".html", ".py", ".ts", ".tsx", ".jsx")):
                    found.append(
                        os.path.relpath(os.path.join(directory, name), ROOT)
                    )
    return found


# ---------------------------------------------------------------------------
# Singularity: exactly one worker is live.
# ---------------------------------------------------------------------------


def test_exactly_one_service_worker_intercepts_fetches():
    """Two workers with fetch handlers means two caching policies on one origin."""
    live = _handlers(LIVE_WORKER)
    retired = _handlers(RETIRED_WORKER)
    assert "fetch" in live, (
        f"{LIVE_WORKER} has no fetch handler. It is supposed to be the one "
        f"worker serving this origin."
    )
    assert "fetch" not in retired, (
        f"{RETIRED_WORKER} has a fetch handler again. It is a tombstone; a "
        f"tombstone that intercepts requests keeps serving the retired fork's "
        f"caching behaviour for as long as it takes to activate."
    )


def test_exactly_one_service_worker_handles_push():
    """The divergence that made this necessary was in the push path specifically.

    A second push handler does not produce an error. It produces a different
    notification for whichever users the subscription happens to belong to,
    which is indistinguishable from the design working.
    """
    assert "push" in _handlers(LIVE_WORKER), (
        f"{LIVE_WORKER} no longer handles push. Web push is delivered to a "
        f"registration, so this is silent: notifications simply stop."
    )
    assert "push" not in _handlers(RETIRED_WORKER), (
        f"{RETIRED_WORKER} handles push again -- the exact split this "
        f"consolidation removed."
    )


def test_the_retired_worker_unregisters_itself():
    """The tombstone has to actively remove itself; existing ones do not expire."""
    source = _strip_comments(_read(RETIRED_WORKER))
    assert "registration.unregister()" in source, (
        f"{RETIRED_WORKER} no longer calls registration.unregister(). Without "
        f"it the retired worker stays installed on every device that has it, "
        f"forever, because nothing else ever removes a service worker."
    )
    assert "activate" in _handlers(RETIRED_WORKER), (
        f"{RETIRED_WORKER} must unregister from its activate handler; that is "
        f"the only event guaranteed to run after it takes over from the fork."
    )


def test_the_retired_worker_is_still_served():
    """Deleting the file would strand the fork rather than retire it.

    This is the counter-intuitive one, and the reason it is written down: the
    instinct on finding a duplicate file is to delete it, and here deleting it
    is strictly worse than leaving it. A 404 on the update check makes the
    update *fail*, and a failed update leaves the previous worker in place.
    """
    path = os.path.join(ROOT, RETIRED_WORKER)
    assert os.path.isfile(path), (
        f"{RETIRED_WORKER} is gone. Deleting it does not unregister it -- the "
        f"browser's update fetch 404s, the update job fails, and the old fork "
        f"stays installed and in control. It has to remain and say "
        f"'unregister me'."
    )
    assert os.path.getsize(path) > 0, f"{RETIRED_WORKER} is empty."


def test_nothing_registers_the_retired_worker():
    """A registration anywhere would resurrect the second worker."""
    offenders = []
    for relative in _shipped_files():
        if relative.replace("/", os.sep) == RETIRED_WORKER:
            continue
        source = _strip_comments(_read(relative))
        for match in re.finditer(r"\.register\(\s*[\"']([^\"']+)[\"']", source):
            if match.group(1) == RETIRED_WORKER_URL:
                offenders.append(relative)
    assert not offenders, (
        f"{RETIRED_WORKER_URL} is registered as a service worker by: "
        f"{sorted(set(offenders))}. Registering it re-creates the second "
        f"worker this file exists to prevent."
    )


def test_push_subscribes_against_the_single_worker():
    """Push must attach to the surviving registration, not the retired one."""
    source = _strip_comments(_read(PUSH_CLIENT))
    registered = re.findall(r"\.register\(\s*([A-Z_]+|[\"'][^\"']+[\"'])", source)
    assert registered, (
        f"{PUSH_CLIENT} registers no service worker at all. Web push cannot "
        f"be subscribed without a registration."
    )
    resolved = set()
    for token in registered:
        if token.startswith(('"', "'")):
            resolved.add(token.strip("\"'"))
        else:
            constant = re.search(
                rf"const\s+{re.escape(token)}\s*=\s*[\"']([^\"']+)[\"']", source
            )
            assert constant, f"{PUSH_CLIENT} registers {token}, which is not a literal const"
            resolved.add(constant.group(1))
    assert resolved == {LIVE_WORKER_URL}, (
        f"{PUSH_CLIENT} registers {sorted(resolved)}; expected only "
        f"{LIVE_WORKER_URL}."
    )


def test_the_push_migration_checks_the_scope_before_unregistering():
    """getRegistration() resolves a document URL, so it outlives its target.

    `getRegistration("/static/service-worker.js")` returns the *most specific*
    registration whose scope covers that path. Once the legacy registration is
    gone, that same call returns the new scope-"/" registration instead -- so a
    migration that unregisters whatever comes back would delete the worker it is
    migrating to, and take the user's push subscription with it. The scope check
    is what makes the call safe, and nothing about the code looks wrong without
    it.
    """
    source = _strip_comments(_read(PUSH_CLIENT))
    migration = re.search(
        r"async function migrateLegacyPushRegistration\(\)[\s\S]*?\n  \}", source
    )
    assert migration, f"{PUSH_CLIENT} has no migrateLegacyPushRegistration()"
    body = migration.group(0)
    assert "scopePath" in body and "LEGACY_SW_SCOPE_PATH" in body, (
        "migrateLegacyPushRegistration() no longer compares the registration "
        "scope before unregistering. Without that comparison it will "
        "eventually unregister the surviving worker."
    )
    unregister = body.index(".unregister(")
    guard = body.index("if (scopePath !== LEGACY_SW_SCOPE_PATH) return false;")
    assert guard < unregister, (
        "The scope guard runs after the unregister call, so it guards nothing."
    )


# ---------------------------------------------------------------------------
# Regression: the fork's hardening survived the merge.
# ---------------------------------------------------------------------------


def test_the_surviving_worker_kept_the_forks_badge_validation():
    """Consolidating onto the un-hardened copy is the plausible way to do this wrong.

    aa70aa7e added this to service-worker.js only. Android drops a notification
    whose badge fails to load, so an unvalidated badge does not degrade the
    notification -- it deletes it. Keeping `sw.js` as the survivor without
    porting this forward would have looked like a clean de-duplication and
    would have silently reinstated the bug on the worker that serves everyone.
    """
    source = _strip_comments(_read(LIVE_WORKER))
    assert "badgeAsset" in source, (
        f"{LIVE_WORKER} lost the badge validation that came from the retired "
        f"fork. This is a regression the consolidation was specifically at "
        f"risk of causing."
    )
    assert re.search(r"badge\s*:\s*badgeAsset", source), (
        f"{LIVE_WORKER} computes badgeAsset but does not use it for the badge."
    )
    assert re.search(r"startsWith\(\s*[\"']/[\"']\s*\)", source), (
        f"{LIVE_WORKER}'s badge validation no longer requires a same-origin "
        f"absolute path."
    )


def test_the_surviving_worker_kept_the_forks_sound_key_normalisation():
    """The fork's other improvement, for the same reason."""
    source = _strip_comments(_read(LIVE_WORKER))
    assert "soundKey" in source, (
        f"{LIVE_WORKER} lost the sound-key normalisation from the retired fork."
    )
    assert re.search(r"sound_key\s*:\s*soundKey", source), (
        f"{LIVE_WORKER} computes soundKey but does not use it."
    )


# ---------------------------------------------------------------------------
# Behaviour: safeNotificationUrl actually refuses hostile targets.
# ---------------------------------------------------------------------------


def _eval_safe_notification_url(cases):
    """Run the real safeNotificationUrl from sw.js under node.

    Asserting the guard clauses exist textually would pass on a function whose
    guards had been rewritten into no-ops. This is the security-relevant
    function in the file -- it decides what URL a notification click navigates
    to, from server-supplied data -- so it is worth executing rather than
    reading.

    A verifier that cannot run is a failure to verify, not a pass and not a
    skip. That is the same rule tests/protection/reels_preload_runner.py
    follows, and the reason is that "node was missing so we said nothing"
    reaches CI as a green tick.
    """
    node = shutil.which("node") or shutil.which("nodejs")
    assert node, (
        "node is required to verify safeNotificationUrl behaviourally. "
        "Refusing rather than passing: an unverified guard is not a verified one."
    )
    source = _read(LIVE_WORKER)
    match = re.search(r"(?m)^function safeNotificationUrl[\s\S]*?^\}$", source)
    assert match, f"{LIVE_WORKER} has no safeNotificationUrl() to verify"

    harness = (
        match.group(0)
        + "\nconst self = { location: { origin: 'https://pulsesoc.com' } };\n"
        # argv[0] is the node binary and argv[1] is this script, so the cases
        # arrive at argv[2].
        + "const out = JSON.parse(process.argv[2]).map(safeNotificationUrl);\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "probe.js")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(harness)
        proc = subprocess.run(
            [node, script, json.dumps(cases)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, f"node failed: {proc.stderr[:400]}"
    return json.loads(proc.stdout)


def test_safe_notification_url_refuses_to_leave_the_origin():
    """A notification click is a navigation, and the payload comes off the wire."""
    fallback = "/pulse/notifications"
    hostile = [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "//evil.example.com/steal",
        "https://evil.example.com/steal",
        "blob:https://pulsesoc.com/abc",
        "file:///etc/passwd",
        "/api/push/subscribe",
        "/admin/",
        "/static/anything.js",
    ]
    results = _eval_safe_notification_url(hostile)
    for target, result in zip(hostile, results):
        assert result == fallback, (
            f"safeNotificationUrl({target!r}) returned {result!r}; a hostile or "
            f"off-limits target must fall back to {fallback}."
        )


def test_safe_notification_url_still_allows_real_destinations():
    """The negative control: a guard that rejects everything is not a guard.

    Without this, hardcoding `return "/pulse/notifications"` would pass the
    test above perfectly while breaking every notification on the site.
    """
    allowed = {
        "/pulse/messages/42": "/pulse/messages/42",
        "/pulse/alerts?x=1": "/pulse/alerts?x=1",
        "https://pulsesoc.com/pulse/videos": "/pulse/videos",
        "https://www.pulsesoc.com/pulse": "/pulse",
    }
    results = _eval_safe_notification_url(list(allowed))
    for target, result in zip(allowed, results):
        assert result == allowed[target], (
            f"safeNotificationUrl({target!r}) returned {result!r}, expected "
            f"{allowed[target]!r}. Legitimate destinations must survive."
        )


# ---------------------------------------------------------------------------
# The manifest duplicate.
# ---------------------------------------------------------------------------


def test_the_manifest_is_one_file():
    """static/site.webmanifest was a byte-identical second copy of manifest.json."""
    duplicate = os.path.join(ROOT, "static", "site.webmanifest")
    assert not os.path.exists(duplicate), (
        "static/site.webmanifest is back. It was a byte-for-byte copy of "
        "static/manifest.json with nothing keeping the two in step -- a "
        "divergence waiting to happen rather than a redundancy."
    )


def test_the_legacy_manifest_url_still_answers_with_the_same_bytes():
    """The file went; the URL did not, and must not change what it serves.

    No template links /site.webmanifest, but an installed PWA can hold it in a
    stored manifest and crawlers have indexed it. Retiring the URL is a separate
    decision that wants route-hit evidence; until then it answers exactly as
    before.
    """
    sys.path.insert(0, ROOT)
    import bot  # noqa: E402

    client = bot.webhook_app.test_client()
    canonical = client.get("/manifest.json")
    legacy = client.get("/site.webmanifest")
    assert canonical.status_code == 200, f"/manifest.json returned {canonical.status_code}"
    assert legacy.status_code == 200, (
        f"/site.webmanifest returned {legacy.status_code}. The URL has been "
        f"served for a long time; removing it is a route retirement, not a "
        f"file cleanup."
    )
    canonical_hash = hashlib.sha256(canonical.get_data()).hexdigest()
    legacy_hash = hashlib.sha256(legacy.get_data()).hexdigest()
    assert canonical_hash == legacy_hash, (
        "/site.webmanifest and /manifest.json no longer serve identical bytes. "
        "The whole point of pointing both at one file was that they cannot "
        "drift apart again."
    )


def _unsubscribe_calls(relative):
    """Every POST to /api/push/unsubscribe, with its enclosing function.

    Returns [(owner, preserves_preferences)]. The owner matters because the
    same endpoint means two different things depending on who is calling it.
    """
    source = _strip_comments(_read(relative))
    definitions = [
        (match.start(), match.group(1))
        for match in re.finditer(r"(?m)^\s*(?:async\s+)?function\s+(\w+)", source)
    ]
    calls = []
    for match in re.finditer(r"""fetch\(\s*["']/api/push/unsubscribe["']""", source):
        owner = "(top level)"
        for position, name in definitions:
            if position < match.start():
                owner = name
        window = source[match.start() : match.start() + 400]
        calls.append((owner, "preserve_preferences" in window))
    return calls


def test_retiring_an_endpoint_does_not_switch_push_off_for_the_account():
    """Moving a subscription must not read as the user turning push off.

    /api/push/unsubscribe does two things, and only one of them is obvious.
    It forgets the endpoint, and -- unless the caller passes
    preserve_preferences -- it also sets enable_push_notifications = false on
    the account, because that is what the settings toggle needs.

    The tombstone and the migration are not the settings toggle. They are
    housekeeping the user did not ask for. Calling the endpoint bare from
    either of them disables web push for every account the cleanup reaches,
    and the tombstone case does not recover: that worker unregisters itself
    immediately afterwards, so there is no second pass to undo it. The symptom
    is a user who never touched anything and simply stops getting
    notifications, with a preferences row that says they asked for it.

    The native client already draws this line -- every non-user-initiated
    unregister in mobile-native/src/api/push.ts passes preservePreferences --
    so this is the web catching up to the existing contract, not a new one.
    """
    for relative in (RETIRED_WORKER, PUSH_CLIENT):
        for owner, preserves in _unsubscribe_calls(relative):
            if owner == "unsubscribePush":
                assert not preserves, (
                    f"{relative}:{owner}() passes preserve_preferences. That is "
                    f"the user-initiated 'turn push off' path, and it is the one "
                    f"caller that genuinely means the preference. Preserving it "
                    f"there means the user switches push off and the server "
                    f"carries on believing they want it."
                )
            else:
                assert preserves, (
                    f"{relative}:{owner}() calls /api/push/unsubscribe without "
                    f"preserve_preferences. This is a migration, not a "
                    f"withdrawal -- it will switch web push off on the account "
                    f"as a side effect of a cleanup the user never requested."
                )


def test_the_unsubscribe_call_scan_found_both_kinds_of_caller():
    """Anti-vacuity: the test above is two 'for' loops over a possibly empty list.

    If the scan found nothing, or found only housekeeping callers, or only the
    user-initiated one, every assertion above passes and the distinction it
    exists to protect is unguarded.
    """
    calls = _unsubscribe_calls(RETIRED_WORKER) + _unsubscribe_calls(PUSH_CLIENT)
    assert calls, (
        "Found no calls to /api/push/unsubscribe at all. Either the scan is "
        "broken or nothing reports a dead endpoint to the server, which would "
        "leave it pushing into the void."
    )
    owners = {owner for owner, _ in calls}
    assert "unsubscribePush" in owners, (
        f"The scan attributed no call to unsubscribePush(); it found {owners}. "
        f"Without the user-initiated caller in the set, the 'must not preserve' "
        f"half of the check above never runs."
    )
    assert owners - {"unsubscribePush"}, (
        f"The scan found only unsubscribePush(). The housekeeping callers (the "
        f"tombstone's activate handler and migrateLegacyPushRegistration) are "
        f"the ones the preference side effect actually harms."
    )


# ---------------------------------------------------------------------------
# Delivery: the migration is only worth writing if it reaches anyone.
# ---------------------------------------------------------------------------
#
# Everything above is about the code being right. This section is about the
# code arriving, which is a separate question with its own silent failure.
#
# `/static/*` is served `public, max-age=31536000, immutable`. A browser holding
# last week's notifications.js will not revalidate it for a year, and immutable
# means it will not even ask. So the migration in notifications.js does not
# reach an existing user until the *URL* changes.
#
# The tombstone, meanwhile, reaches everyone quickly, because delivering a push
# soft-updates the registration and /static/service-worker.js is exempted from
# the immutable header. That asymmetry is the danger: the half that unsubscribes
# a user ships immediately, and the half that re-subscribes them ships when the
# cache happens to expire. The result is a user silently losing web push, with
# no error on either side.


_LOADER_RE = re.compile(r"/static/notifications\.js(\?[^\"'\s>]*)?")


def _push_client_loader_tokens():
    """Every query string any shipped file appends to notifications.js."""
    seen = {}
    for relative in _shipped_files():
        # The client cannot load itself; the tombstone only mentions it in
        # prose; and sw.js names it to *precache* it, which is checked
        # separately against this result rather than folded into it.
        if relative.replace("/", os.sep) in (PUSH_CLIENT, RETIRED_WORKER, LIVE_WORKER):
            continue
        for match in _LOADER_RE.finditer(_strip_comments(_read(relative))):
            seen.setdefault(match.group(1) or "", []).append(relative)
    return seen


def test_every_loader_of_the_push_client_uses_one_cache_busting_token():
    """An unversioned loader pins a user to a year-old copy of the migration."""
    tokens = _push_client_loader_tokens()
    assert tokens, (
        "No file loads /static/notifications.js. Either the scan broke or the "
        "push client is dead code; both make the rest of this file meaningless."
    )
    assert "" not in tokens, (
        f"These reference /static/notifications.js with no version query: "
        f"{sorted(set(tokens['']))}. That URL is served with "
        f"`max-age=31536000, immutable`, so those users keep whatever copy they "
        f"already have -- for a year, without revalidating. They would receive "
        f"the tombstone that drops their push subscription and not the "
        f"migration that moves it."
    )
    assert len(tokens) == 1, (
        f"/static/notifications.js is loaded under {len(tokens)} different "
        f"version queries: "
        + "; ".join(f"{q or '(none)'} <- {sorted(set(f))}" for q, f in tokens.items())
        + ". Each distinct URL is a separate immutable cache entry, so this is "
        "not a cosmetic inconsistency: a user can hold two different versions "
        "of the push client at once, and which one runs depends on which page "
        "they opened."
    )


def test_the_boot_profile_strip_matches_a_real_loader():
    """bot.py removes the script tag by exact string, so the token must move with it.

    `notifications_off` and friends strip the loader out of the rendered shell
    with a literal `.replace(...)`. Bump the version query on the loader and
    not on the strip, and the strip matches nothing: the boot profile silently
    stops working and every page loads the script it was meant to omit. Nothing
    raises, nothing logs, and the page still renders.
    """
    source = _read("bot.py")
    targets = re.findall(
        r"\.replace\(\s*'(<script src=\"/static/notifications\.js[^']*?</script>)'",
        source,
    )
    assert targets, (
        "bot.py no longer strips the notifications.js tag for any boot profile. "
        "If that was deliberate the strip should be gone from the profile list "
        "too; if it was not, a boot profile has quietly stopped applying."
    )
    for target in set(targets):
        # Once for the strip itself, at least once more for a tag that emits it.
        assert source.count(target) >= 2, (
            f"bot.py strips {target!r} but never emits that exact string. The "
            f"strip is a no-op, so the boot profile that relies on it is not "
            f"doing anything -- most likely a version token was bumped on the "
            f"loader and not here."
        )


def test_the_worker_precaches_the_url_that_pages_actually_request():
    """Cache-first keyed on the full URL: a bare precache entry is never hit.

    sw.js serves static assets cache-first via `caches.match(request)`, which
    does not ignore the query string. Precaching the unversioned URL therefore
    warms an entry nothing asks for, and -- if any page ever did ask for it --
    hands back install-time bytes indefinitely.
    """
    tokens = _push_client_loader_tokens()
    (token,) = tokens.keys()
    source = _strip_comments(_read(LIVE_WORKER))
    constant = re.search(
        r"const\s+NOTIFICATIONS_JS\s*=\s*[\"']([^\"']+)[\"']", source
    )
    assert constant, (
        f"{LIVE_WORKER} has no NOTIFICATIONS_JS constant. The precache list "
        f"must name the same URL pages request, and a literal in the array "
        f"cannot be checked against the loaders."
    )
    assert constant.group(1) == "/static/notifications.js" + token, (
        f"{LIVE_WORKER} precaches {constant.group(1)!r} but pages request "
        f"{'/static/notifications.js' + token!r}. The precached entry will "
        f"never be hit, and the version bump this pins is the only "
        f"invalidation mechanism the file has."
    )
    assert "NOTIFICATIONS_JS," in source, (
        f"{LIVE_WORKER} defines NOTIFICATIONS_JS but does not put it in "
        f"STATIC_ASSETS."
    )


def _cache_control(path):
    sys.path.insert(0, ROOT)
    import bot  # noqa: E402

    response = bot.webhook_app.test_client().get(path)
    return response.status_code, response.headers.get("Cache-Control")


def test_both_worker_urls_escape_the_immutable_static_cache():
    """A worker under `immutable` cannot be replaced, which defeats both halves.

    bot.py exempts /sw.js and /static/service-worker.js from the one-year
    immutable header that the rest of /static/ gets. That exemption is
    load-bearing in a way that is easy to delete as redundant: a service worker
    is fetched by the browser's own update job, and if that fetch is answered
    from an immutable cache the update never happens. The surviving worker could
    not ship a fix, and -- the reason this consolidation would fail outright --
    the tombstone would never reach the devices that still run the fork.
    """
    for url in (LIVE_WORKER_URL, RETIRED_WORKER_URL):
        status, cache_control = _cache_control(url)
        assert status == 200, f"{url} returned {status}; it must be served."
        assert cache_control and "no-store" in cache_control, (
            f"{url} is served with Cache-Control={cache_control!r}. A service "
            f"worker script must not be cached: the browser's update check is "
            f"an ordinary fetch, so a cached response means the worker on the "
            f"device can never be replaced."
        )


def test_the_cache_header_check_is_reading_a_real_header():
    """Negative control for the test above.

    `"no-store" in None` is a TypeError, but `"no-store" in ""` is False and a
    route that stopped setting the header entirely would fail loudly -- so that
    direction is safe. The direction that is not safe is a test client that
    returns no-store for everything, which would make the exemption above
    unfalsifiable. So: prove some other /static/ URL really is immutable.
    """
    status, cache_control = _cache_control("/static/notifications.js")
    assert status == 200, f"/static/notifications.js returned {status}"
    assert cache_control and "immutable" in cache_control, (
        f"/static/notifications.js is served with Cache-Control="
        f"{cache_control!r}, not the expected long-lived immutable header. "
        f"Either the static caching policy changed -- in which case the "
        f"version-token requirement above may no longer be necessary -- or "
        f"this test client is not seeing real response headers, in which case "
        f"the service-worker exemption test proves nothing."
    )


# ---------------------------------------------------------------------------
# Anti-vacuity: prove the checks above are looking at something.
# ---------------------------------------------------------------------------


def test_the_handler_extractor_can_tell_a_worker_from_a_tombstone():
    """If _handlers() returned nothing, every singularity claim above passes.

    Three of the assertions in this file are of the form "this file does NOT
    have handler X", and all three are satisfied by an extractor that finds
    nothing at all -- a renamed API, a changed quote style, a regex that stopped
    matching. So the extractor is pinned against both known files.
    """
    live = _handlers(LIVE_WORKER)
    assert {"install", "activate", "fetch", "push", "notificationclick"} <= live, (
        f"The handler extractor found only {sorted(live)} in {LIVE_WORKER}. "
        f"It is supposed to find at least install/activate/fetch/push/"
        f"notificationclick, so the 'no fetch handler' assertions elsewhere in "
        f"this file are not currently evidence of anything."
    )
    retired = _handlers(RETIRED_WORKER)
    assert retired, (
        f"The handler extractor found nothing in {RETIRED_WORKER}. The "
        f"tombstone still has install and activate handlers, so finding zero "
        f"means the extractor is broken, not that the file is clean."
    )


def test_the_shipped_file_scan_is_not_empty():
    """test_nothing_registers_the_retired_worker passes vacuously over no files."""
    files = _shipped_files()
    # 40 is a floor under a measured population, not a guess: the scan covers 55
    # files today (20 templates, 29 static scripts, 5 web/src modules, bot.py).
    # The number exists to catch a scan that collapsed to nothing, so it is set
    # well below the real count -- tight enough that 0 or 1 fails loudly, loose
    # enough that deleting a handful of templates does not.
    assert len(files) >= 40, (
        f"The shipped-file scan found only {len(files)} files, against ~55 "
        f"expected. The registration check walks this list, so a mis-pointed "
        f"scan reports zero violations and looks like success."
    )
    normalised = {f.replace("/", os.sep) for f in files}
    for required in (PUSH_CLIENT, LIVE_WORKER, RETIRED_WORKER):
        assert required in normalised, (
            f"{required} is not in the scanned set. A scan that misses the "
            f"service worker files cannot police service worker registration."
        )


def test_the_two_workers_are_no_longer_near_duplicates():
    """The condition that made the divergence possible is gone, not just patched.

    Both files being ~330 lines of the same code is what let one be fixed
    without the other. If the tombstone grows back toward the size of a real
    worker, the setup for the original bug is back.
    """
    live = len(_read(LIVE_WORKER).splitlines())
    retired = len(_read(RETIRED_WORKER).splitlines())
    assert retired < live / 2, (
        f"{RETIRED_WORKER} is {retired} lines against {LIVE_WORKER}'s {live}. "
        f"A tombstone should be a fraction of a worker; this one is large "
        f"enough to be a second implementation again."
    )


if __name__ == "__main__":
    # The suite runner executes this file as a script and fails it for
    # reporting zero checks, so it has to be runnable both ways. Zero-argument
    # tests, no fixtures -- see the note in tests/protection/test_route_auth.py.
    import pathlib as _pathlib

    sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
