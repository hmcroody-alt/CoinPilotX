# UNDX Sovereign Super-Master Mission — Verification Matrix

Recorded 2026-07-25/26. Every number below was produced by a run in this session; nothing
here is inferred. Verdicts are limited to PASS / PARTIAL / BLOCKED / NOT TESTED.

Baseline commit for all attribution work: `9f190345`. Mission commits on top of it:
`6127e42d`, `79ec9964`, `d4c8ffe4`.

## 1. Reels preload contract (directive 1) — PASS

The failing assertion in `tests/protection/test_media_playback_contract.py` was a grep for
`"reelLightPreloaded'+(idx+1)"` inside a minified line of `bot.py`. Investigation found the
behavior existed but the grep was pinning a real defect in place: the flag it matched was an
*early return*, so a card warmed once and then torn down by `releaseFarReelMedia`
(`preload='none'`, buffer dropped) could never be re-armed. Scrolling back up left the Reel
about to become active holding no data at all.

Production fix in `bot.py` (committed at `d4c8ffe4`): the window policy is re-asserted on
every pass and the flag became a record of having been warmed rather than a gate on being
re-armed. The actual fetch stays gated on `readyState === 0`, which is naturally idempotent,
so re-asserting the window costs zero extra network.

The grep was replaced, not weakened, by a behavioral contract. `tests/protection/reels_preload_harness.js`
supplies only DOM primitives (elements, `getBoundingClientRect`, `video.load()`, `new Image()`)
and counts what production chooses to do with them; `tests/protection/reels_preload_runner.py`
lifts the six shipping functions (`reelCards`, `warmReelPoster`, `primaryReelVideo`,
`logReelAudioState`, `releaseFarReelMedia`, `preloadNextReel`) out of `bot.py` by brace matching
and executes them under Node. No preload policy lives in the harness, so every reported number
is a decision made by shipping code.

`load()` is counted in two separate senses, because conflating them would let a teardown
regression masquerade as a download regression: with `preload !== 'none'` it FETCHES bytes;
with `preload === 'none'` it DROPS a buffer and pulls nothing.

Scenarios covered (15): `window_shape`, `window_shape_autodetect`, `rapid_scroll_idempotent`,
`fling_skips_cards`, `sequential_walk`, `short_feed_one`, `short_feed_two`, `end_of_feed`,
`penultimate`, `empty_feed`, `release_then_replay`, `release_stops_and_frees`,
`load_failure_is_contained`, `release_failure_is_contained`, `poster_warm_once`. Together these
are the short feeds, feed edges, rapid scrolling, network-interruption containment, replay,
mute state, and cleanup the directive named.

Result: `rc=0`, 54 `ok -` lines, final line `media playback protection contract ok`, identical
across three consecutive runs plus a fourth run after the source-restore verification below.

### Second stale contract, found and repaired the same way

`scripts/pulse_reels_mobile_playback_audit.py` carried the same species of defect: it asserted
"Reels preload is single-shot per adjacent window" by matching `const flag='reelLightPreloaded'+mode`
and `card.dataset[flag]==='1'` — the literal text of the early return that *was* the bug. The
label is preserved verbatim and the guarantee is now measured through the shared runner. Result:
`rc=0`, 20 `PASS:` lines.

The extraction-and-run machinery was factored into `tests/protection/reels_preload_runner.py`
precisely because two callers need the same guarantee, and a guarantee stated twice eventually
disagrees with itself.

### Mutation evidence (non-vacuity) — 3/3 CAUGHT, 0 VACUOUS

| Mutation to `bot.py` | Caught by |
| --- | --- |
| `drop_readyState_fetch_gate` | "Reels preload is single-shot per adjacent window" |
| `poster_warm_every_pass` | "repeated window passes warm each poster once and re-download nothing" / "ten window passes warm each poster once and re-download nothing" |
| `rename_production_function` | `missing production function: preloadNextReel()` |

`bot.py` was restored byte-identically after every mutation, verified by sha256
(`71bcf5ba3e61d3e8fb0a35ffa6389f0bfcf4109b9706a3456851adea4a32833e`, 7,107,654 bytes).

## 2. Blocked werkzeug audit (directive 2) — resolved without vendoring

`scripts/pulsesoc_undx_pulsesoc_operator_v5_audit.py` was unblocked by a `sitecustomize.py`
shim that *appends* (never prepends) the repo `.venv` site-packages, so macOS-built `.so` files
cannot shadow working system packages, and backports `datetime.UTC` for Python 3.10. No
third-party code was vendored and no audit result was fabricated.

## 3. System-wide matrix (directive 3)

| Suite | Result |
| --- | --- |
| `tests/` tree | 73/73 suites, 745 cases, **0 failures**, 0 unparseable counts (re-run after the runner refactor) |
| `tests/protection/test_media_playback_contract.py` | rc=0, 54 assertions, 3x identical |
| `scripts/pulse_reels_mobile_playback_audit.py` | rc=0, 20 assertions |
| `python -m compileall -q services scripts tests bot.py` | rc=0 |
| `tsc --noEmit` in `mobile-native` | rc=0 |
| `tsc --noEmit` in `mobile/pulse-react-native` | rc=0 |
| `mobile/` TypeScript | NOT TESTED — no `node_modules` present |
| `jest --ci` in `mobile-native` | 56 suites / 487 tests passed / 0 failures / 22.1s |
| Native iOS build | **NOT TESTED** — target is `mobile-native/ios/PulseSocNative.xcworkspace`; no Xcode in a Linux sandbox |
| Simulator / device observations | **NOT TESTED** — same reason |
| Python lint | **NOT TESTED** — no ruff/flake8/pylint/mypy/pyright installed and no lint config in the repo |

### Historical audit corpus — 677 targets

Corrected totals after re-running the one signal-killed target serially:

- 435 rc=0
- 240 rc=1 (real assertion failures)
- 2 NOT COMPLETED (exceed the 45s per-command sandbox ceiling)

Failure classification of the 240, with the shim active: 130 REAL_assertion,
88 REAL_nonzero_exit_no_traceback, 16 REAL_other, 3 REAL_missing_file, 2 REAL_ValueError,
2 REAL_FileExistsError. **Zero ENV-class failures.**

`scripts/pulsesoc_native_activity_fixture_hardening_audit.py` reported rc=-6 (SIGABRT) in the
8-way parallel sweep. Run serially it is rc=0 and ends `pulsesoc native commerce activity fixture
audit ok` — that was a sweep-infrastructure flake under sqlite/virtiofs contention, not a
product failure.

The 2 NOT COMPLETED targets, and why:

- `scripts/pulsesoc_titan_performance_audit.py` — its `IGNORE_DIRS` contains `venv` but the
  repo virtualenv is `.venv`, so it rglobs and regex-scans it. Measured scan surface: 5,035
  files / 66.3 MB, of which **3,759 files / 47.9 MB (72.3%) are inside `.venv`**. The file was
  last changed at `c7576922` (2026-06-30), well before this mission's baseline, so the cost is
  pre-existing and untouched by this work.
- `scripts/pulsesoc_native_comms_safety_event_emission_audit.py` — last changed at `656d0627`
  (2026-07-06), likewise untouched by this mission.

Both were retried once more in a clean workspace with 2.1 GB free and both still exit 124
(timeout) at 43s, so they remain **NOT COMPLETED** rather than pass or fail. An attempt to work
around the ceiling by staging a copy of the tree outside the mount filled the sandbox disk and
wedged its shell supervisor; that was a self-inflicted detour, it required a workspace restart,
and it touched nothing in this repository.

### Attribution proof: did this mission's changes break any audit?

Coarse identifier matching was useless (230/241 flagged) because the diff contains English
prose comments, so words like "active", "failed" and "missing" match nearly every audit. It was
replaced by a **directional string-literal flip analysis**, which is exact for grep-style
audits: an audit's verdict can only change if it contains a literal `L` with
`(L in old) != (L in new)`, and only a **removed** literal can turn a passing grep into a
failing one.

Removal surface across all nine mission-changed files, measured against `9f190345`:

| File | Removed lines | Bytes |
| --- | --- | --- |
| `bot.py` | 1 | 1,016 |
| `scripts/pulsesoc_undx_bootstrap_v3_audit.py` | 2 | 297 |
| `services/business_os/undx_actions/engine.py` | 1 | 53 |
| `services/pulse_ai_service.py` | 0 | 0 |
| `services/undx_architecture.py` | 4 | 598 |
| `tests/business_os/test_confirmation_conformance.py` | 0 | 0 |
| `tests/business_os/test_undx_api.py` | 1 | 67 |
| `tests/business_os/test_undx_engine.py` | 2 | 124 |
| `tests/protection/test_media_playback_contract.py` | 3 | 254 |

Scanning all 677 audits for literals that were present in the baseline file and absent in the
current file yields **13 audits**, and every match is a generic literal (`status=`, ` status`)
or a literal in a file the audit does not read. Of those 13, four currently fail.

Those four were then measured, not argued: all nine mission-changed files were replaced with
their `9f190345` content, the four audits were re-run, and the source was restored.

| Audit | Current tree | At baseline `9f190345` |
| --- | --- | --- |
| `pulse_comm_v2_attachment_send_audit` | rc=1, `image attachment links uploaded media failed` | rc=1, identical |
| `pulse_communications_audit` | rc=1, `communications frontend logs endpoint diagnostics failed` | rc=1, identical |
| `pulse_communications_v2_desktop_layout_audit` | rc=1, `desktop CSS includes @media (min-width: 941px) failed` | rc=1, identical |
| `pulse_communications_v2_mobile_regression_audit` | rc=1, `ValueError: substring not found` | rc=1, identical |

All nine files were restored and verified byte-identical (9/9 sha256 match against the
pre-experiment manifest). The protection test and the mobile audit were re-run afterwards and
are still rc=0 / 54 assertions and rc=0 / 20 assertions.

**Conclusion: the 240 audit failures are pre-existing. None is attributable to this mission's
changes.** Separately, `scripts/pulse_music_review_audit.py` was proven pre-existing by direct
substitution: with `bot.py` replaced by `git show 9f190345:bot.py` it fails identically with
`AssertionError: Admin dashboard does not show Music Review link.`

### Canonical gate

`scripts/full_platform_audit.py` is **RED and pre-existing**. It fails at plan step 3
(`pulse_feed_layout_audit.py`, "composer uses unified media picker trigger"). The asserted
tokens `data-pulse-media-trigger` and `data-expand-composer="pulseComposer"` occur **0 times**
in both the working tree and `git show HEAD:bot.py`.

So directive 3's required end state — `failures: 0, errors: 0, unexpected_skips: 0,
unresolved_release_critical_defects: 0` — is **NOT met**, and the shortfall is entirely
pre-existing repository debt rather than regression from this mission.

## 4. Commit and push (directive 5) — committed; push BLOCKED by network policy

Branch: `release/undx-nexus-core-v4`. Commits, newest first:

| SHA | Author | Subject |
| --- | --- | --- |
| `6928378e` | PulseSoc Engineer | Share one behavioral Reels preload verifier and repair the stale mobile audit |
| `16767a57` | HM Cherie | Restore reproducible native dependency installation *(user's own commit — preserved, not touched)* |
| `d4c8ffe4` | PulseSoc Engineer | Re-arm Reels preload on scroll-back + behavioral playback harness |
| `79ec9964` | PulseSoc Engineer | Refuse caller-supplied confirmed status on recorded confirmations |
| `6127e42d` | HM Cherie | Bind UNDX operation audits to redeemed grants |

`6928378e` contains exactly four files and nothing else:

```
reports/undx_sovereign_mission_verification_matrix.md  | 212 +++
scripts/pulse_reels_mobile_playback_audit.py           |  58 +-
tests/protection/reels_preload_runner.py               | 137 +++
tests/protection/test_media_playback_contract.py       |  93 +--
4 files changed, 415 insertions(+), 85 deletions(-)
```

The staged set was scanned for credential-shaped strings before committing; none were found.
`mobile-native/package-lock.json` needed no revert — the user committed it themselves at
`16767a57`, so that change is preserved rather than reverted.

Deliberately left unstaged, all generated: `coinpilotx.db-wal`, 21 regenerated `reports/*`
artifacts, `coinpilotx.log.1/2/3/5`, `.undx_brain_layer_audit_workspace/`, and 744 undeletable
`.fuse_hidden*` artifacts. No uncommitted source changes remain.

**Push is BLOCKED and cannot be unblocked from here.** Both transports are refused by the
sandbox network policy:

- SSH: `CONNECT github.com:22: Forbidden`
- HTTPS: `Received HTTP code 403 from proxy after CONNECT`

There is no credential helper configured, and supplying credentials is not something this
session will do. Local `HEAD` is `6928378e`; the last known remote tip is `16767a57`, so the
branch is **1 ahead / 0 behind** and the remote SHA cannot be verified until someone with
network access runs:

```
git push origin release/undx-nexus-core-v4
```

Directive 5's requirement that local `HEAD` and the remote SHA match is therefore **NOT MET**,
for an environmental reason that is disclosed rather than worked around.

### Postscript on the earlier frozen index

An earlier stale, undeletable empty `.git/index.lock` — left by a git process killed at the 45s
command ceiling — had frozen the index at a pre-commit state, so `git status` reported staged
*reversions* of work that was in fact already committed. Restarting the workspace cleared the
FUSE state and the lock; index operations no longer need a `GIT_INDEX_FILE` override.

## Environment constraints (disclosed, not worked around)

Sandboxed Linux VM. Package index and the python-build-standalone proxy are blocked. Python
3.10 with a shim for 3.11+ `datetime.UTC`. macOS-built native wheels are unusable. No Xcode, so
no native build and no simulator or device observation. No background processes survive a
command (`bwrap --die-with-parent --unshare-pid`), so long sweeps had to be chunked into <=45s
foreground calls with atomic per-result checkpointing. virtiofs concurrency is non-monotonic: 8
workers beat 24-32, which made no progress at all. `.git/index.lock` and the `.fuse_hidden*`
artifacts cannot be deleted through the FUSE mount.

Carried-forward disclosure: the `drop_status_check` and `confirmed_at_written_from_caller`
mutations remain VACUOUS by design and are annotated as unobservable defense-in-depth in
`services/business_os/undx_actions/engine.py`.
