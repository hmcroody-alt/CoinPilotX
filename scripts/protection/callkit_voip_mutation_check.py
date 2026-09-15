#!/usr/bin/env python3
"""Mutation check for the PushKit/CallKit change (mission section 60).

Each entry breaks one invariant the new tests claim to protect. A mutation that leaves the
suite green means the test does not actually test what it says it does.

Safety: every target file is snapshotted before anything is written and restored from that
snapshot afterwards, and the script refuses to run unless the tree is clean of any leftover
mutation. A previous harness in this repo wrote the real checkout; this one operates only on
the worktree below and verifies byte-for-byte restoration at the end.
"""
import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path("/tmp/voip-callkit/mobile-native")
BRIDGE = ROOT / "src/calls/callKitBridge.ts"
CALLS = ROOT / "src/api/calls.ts"

BRIDGE_TEST = "src/calls/__tests__/callKitBridge.test.ts"
CALLS_TEST = "src/api/__tests__/voipToken.test.ts"

# (name, file, old, new, test file, the test that must break)
MUTATIONS = [
    (
        "M1 report a call the server gave no UUID for",
        BRIDGE,
        "if (!isNativeCallKitEnabled() || !provider || !incoming.callId || !incoming.callUuid) return;",
        "if (!isNativeCallKitEnabled() || !provider || !incoming.callId) return;",
        BRIDGE_TEST,
        "skips a call the server gave no UUID for",
    ),
    (
        "M2 drop the double-ring guard",
        BRIDGE,
        "  if (reportedUuids.has(uuid)) return;\n  reportedUuids.add(uuid);",
        "  reportedUuids.add(uuid);",
        BRIDGE_TEST,
        "does not ring twice when the poller re-reports",
    ),
    (
        "M3 mint a client UUID instead of using the server's",
        BRIDGE,
        "  const uuid = rememberCallKitCall(incoming.callId, incoming.callUuid);",
        "  const uuid = rememberCallKitCall(incoming.callId, `client-${incoming.callId}`);",
        BRIDGE_TEST,
        "reports the call under the server's UUID",
    ),
    (
        "M4 treat every CallKit end as a hang-up",
        BRIDGE,
        "      const answered = answeredUuids.has(uuid);",
        "      const answered = true;",
        BRIDGE_TEST,
        "declines when CallKit ends before the call was answered",
    ),
    (
        "M5 treat every CallKit end as a decline",
        BRIDGE,
        "      const answered = answeredUuids.has(uuid);",
        "      const answered = false;",
        BRIDGE_TEST,
        "hangs up when CallKit ends after the call was answered",
    ),
    (
        "M6 invent a CallKit call for an unknown call id",
        BRIDGE,
        "  const uuid = uuidByCallId.get(callId);\n  if (!uuid) return;\n  provider.setCallConnected(uuid);",
        "  const uuid = uuidByCallId.get(callId) || `minted-${callId}`;\n  provider.setCallConnected(uuid);",
        BRIDGE_TEST,
        "does not invent a CallKit call for an id it has never seen",
    ),
    (
        "M7 forget the reverse lookup the push path depends on",
        BRIDGE,
        "  callIdByUuid.set(callUuid, callId);",
        "  // callIdByUuid.set(callUuid, callId);",
        BRIDGE_TEST,
        "maps a push-reported call so it can later be connected and ended",
    ),
    (
        "M8 never revoke the VoIP token on sign-out",
        BRIDGE,
        "  await unregisterVoipPushToken({ token, reason }).catch(() => undefined);",
        "  return;",
        BRIDGE_TEST,
        "revokes the VoIP registration",
    ),
    (
        "M9 keep the token in memory after sign-out",
        BRIDGE,
        "  const token = lastVoipToken;\n  lastVoipToken = \"\";",
        "  const token = lastVoipToken;",
        BRIDGE_TEST,
        "forgets the token so it cannot be re-sent under the next account",
    ),
    (
        "M10 never record the token the register event delivered",
        BRIDGE,
        "      lastVoipToken = token;",
        "      // lastVoipToken = token;",
        BRIDGE_TEST,
        "revokes the VoIP registration, naming the token it saw",
    ),
    (
        "M11 omit the device id the backend requires to register",
        CALLS,
        "      device_id: deviceId,\n      installation_id: deviceId,\n      ...payload,",
        "      ...payload,",
        CALLS_TEST,
        "sends the installation id the backend requires",
    ),
    (
        "M12 file the VoIP token under its own device id",
        CALLS,
        "  const deviceId = await getPushInstallationId().catch(() => \"\");\n  return pulseApi<{ ok?: boolean; message?: string; voip_ready?: boolean; status?: string }>",
        "  const deviceId = `voip-${Date.now()}`;\n  return pulseApi<{ ok?: boolean; message?: string; voip_ready?: boolean; status?: string }>",
        CALLS_TEST,
        "files the VoIP token under the SAME id as the alert-push registration",
    ),
    (
        "M13 revoke only by token, never by device",
        CALLS,
        "      token: options.token || undefined,\n      device_id: deviceId,\n      installation_id: deviceId,\n      platform: \"ios\",",
        "      token: options.token || undefined,\n      platform: \"ios\",",
        CALLS_TEST,
        "identifies the device by installation id, not only by token",
    ),
]


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_jest(test_file: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["npx", "jest", test_file, "--silent"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return proc.returncode == 0, proc.stderr + proc.stdout


def main() -> int:
    originals = {p: p.read_text() for p in {BRIDGE, CALLS}}
    baseline = {p: sha(p) for p in originals}

    print("Baseline: confirming both suites are green before mutating.")
    for test_file in (BRIDGE_TEST, CALLS_TEST):
        ok, _ = run_jest(test_file)
        if not ok:
            print(f"  ABORT: {test_file} is already red.")
            return 2
        print(f"  green  {test_file}")

    survivors, killed = [], []
    try:
        for name, path, old, new, test_file, expect in MUTATIONS:
            source = originals[path]
            if source.count(old) != 1:
                print(f"\n{name}\n  ABORT: anchor matched {source.count(old)} times, expected 1.")
                return 2
            path.write_text(source.replace(old, new, 1))
            ok, output = run_jest(test_file)
            path.write_text(source)

            if ok:
                survivors.append((name, expect))
                print(f"\n{name}\n  SURVIVED — suite still green. The test does not cover this.")
            else:
                killed.append(name)
                hit = expect.lower() in output.lower()
                print(f"\n{name}\n  killed by {test_file}" + ("" if hit else f"  (NOTE: expected failure naming '{expect}' not found)"))
    finally:
        for path, text in originals.items():
            path.write_text(text)

    print("\n--- restoration check ---")
    clean = True
    for path in originals:
        now = sha(path)
        state = "restored" if now == baseline[path] else "MODIFIED"
        if now != baseline[path]:
            clean = False
        print(f"  {state}  {path.relative_to(ROOT)}  {now[:12]}")

    print(f"\n{len(killed)}/{len(MUTATIONS)} mutations killed, {len(survivors)} survived.")
    for name, expect in survivors:
        print(f"  SURVIVOR: {name}  (claimed cover: {expect})")
    return 0 if (clean and not survivors) else 1


if __name__ == "__main__":
    sys.exit(main())
