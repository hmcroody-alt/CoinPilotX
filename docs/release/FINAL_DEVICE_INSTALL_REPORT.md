# Final device install report — 2026-09-17

Both targets run a Release build of `7dfeb7ac`.

## P3r7or — iPhone 16 Pro (physical)

| | |
|---|---|
| Identifier | `F45E640F-6D02-514E-877C-B764E8D6818F` |
| Device state | connected |
| Build | `mobile-native/ios/build_device.sh`, Release, `** BUILD SUCCEEDED **` |
| Installed | `xcrun devicectl`, bundle `com.pulsesoc.app` |
| Bundle UUID | `B2532591-360B-4D19-B636-B3EB3FA9E1E2` |
| Binary mtime | 15:41:48 |
| Running PID | 28276, from `…/B2532591-…/PulseSoc.app/PulseSoc` |
| Crash reports | none |

Entitlements as installed:

```
application-identifier        87ZC69AGSR.com.pulsesoc.app
aps-environment               development
associated-domains            applinks:pulsesoc.com
team-identifier               87ZC69AGSR
get-task-allow                true
```

`aps-environment: development` means this build mints **sandbox** APNs tokens.
Production leaves `APNS_USE_SANDBOX` unset, so raw-APNs pushes from the live
deployment will not reach this build, and the resulting `BadDeviceToken` is
indistinguishable from a dead token. That is a property of dev signing, not a
regression from this release, and Expo-routed alerts are unaffected because Expo
picks the host itself. Noted so it is not rediscovered as a bug.

The running process path contains the same bundle UUID that the install
reported, which is what proves the live process is the newly installed copy and
not a leftover.

## iPhone 17 Pro Max — simulator

| | |
|---|---|
| Identifier | `E859950D-B187-4897-B389-05447C5AD796` |
| Device state | Booted |
| Build | `mobile-native/ios/build_sim.sh`, Release, `** BUILD SUCCEEDED **` |
| Frameworks re-signed | 25 of 25, all verified |
| Keychain entitlements | embedded and verified |
| Binary mtime | 15:48:13 |
| Running PID | 72836 |

The re-sign loop and the entitlement assertion are load-bearing and were left
alone: dyld rejects the unsigned prebuilt Agora frameworks, and disabling signing
strips the linker-embedded `__TEXT,__entitlements` section, after which
SecureStore never persists the session envelope and every authenticated write
403s forever while reads keep working.

A screenshot of the running app shows it signed in against production — feed
loaded, Pulse Network "Connected", unread badge populated, Status rail and
navigation rendering.

## Lineage proof

`7dfeb7ac` was authored at 15:38:07. The device binary is 15:41:48 and the
simulator binary 15:48:13 — both later, so neither is a stale artifact.

The stronger evidence is the symbol count:

| Artifact | exported `PulseAppleTranslation` symbols |
|---|---|
| device `PulseSoc` | 86 |
| simulator `PulseSoc` | 86 |

This marker *inverts* between lineages. Before `6ba609b9` regenerated
`Podfile.lock`, the pod was absent from the Pods project, so a build made from
any earlier commit exports zero such symbols. Its presence cannot be explained by
a stale build, which is exactly the property a lineage marker needs and which a
string that is merely always-present does not have.

The JS bundles carry `native_bridge_unavailable` and `PulseAppleTranslation`, and
the control string `pulsesoc.com` appears 17 times in both — the control matters
because `grep` finds nothing in a Hermes bundle without `strings -a`, and a
zero-match result would otherwise read as a stale bundle.

So: the Apple on-device translation module is genuinely linked on both targets.
The feature that was inert is no longer inert. That is the single most important
outcome of this mission and it is now verified on hardware rather than inferred
from a passing test.

## What was not verified

**Physical audible validation of a live call was not performed.** It requires a
human to hear audio on a handset, and no amount of tooling substitutes for that.

What can be said instead: no protected audio source file changed in this release.
The three files that tripped the audio gate are `Podfile.lock`, `package.json`
and `package-lock.json` — `dependency_watch` entries, not audio code. No screen
gained an `AVAudioSession` call, no second microphone track or publication path
was introduced, no global audio singleton was added, and ownership arbitration is
untouched. The audio batteries pass (191 + 377 + 22 jest, 19 + 13 backend) and
the change gate accepts the declaration.

That is a strong argument that audio is unaffected. It is not the same thing as
having heard it, and the two are not being conflated here.
