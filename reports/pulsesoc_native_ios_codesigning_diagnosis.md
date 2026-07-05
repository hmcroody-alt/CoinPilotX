# PulseSoc Native iOS Code-Signing Diagnosis

Date: 2026-07-05

## Current Status

Status: resolved after WWDR G3 installation.

`security find-identity -v -p codesigning` now returns two valid Apple Development identities, and `npx expo run:ios --device 00008140-000E2D9A2EE8801C` can build, sign, and install `com.pulsesoc.nativeapp` on the connected iPhone 16 Pro.

This report preserves the original diagnosis evidence because it explains why the earlier physical-device install failed.

## Original Result

Root cause: the local macOS signing chain is incomplete for the current Apple Development certificates. The Apple Development certs in the login keychain are issued by `Apple Worldwide Developer Relations Certification Authority, OU=G3`, but the locally installed WWDR certificates are the expired legacy WWDR and WWDR G6. WWDR G3 is not installed in the searched keychains, so `codesign` cannot build the chain to a trusted root and `security find-identity -v -p codesigning` returns `0 valid identities found`.

Do not reset the keychain. The login keychain is visible, unlocked/accessible, the Apple Development certs exist, matching private keys exist, and the iPhone is Developer Mode-ready. The safest fix is to install the missing Apple WWDR G3 intermediate certificate, then re-run identity and build checks.

## Evidence

### Keychain Search List

User keychain search list:

```text
/Users/hmcherie/Library/Keychains/login.keychain-db
```

Default user keychain:

```text
/Users/hmcherie/Library/Keychains/login.keychain-db
```

Login keychain state:

```text
Keychain "/Users/hmcherie/Library/Keychains/login.keychain-db" no-timeout
```

This rules out a wrong default keychain or locked login keychain as the primary cause.

### Identity Lookup

```bash
security find-identity -v -p codesigning
security find-identity -v -p codesigning ~/Library/Keychains/login.keychain-db
```

Both return:

```text
0 valid identities found
```

### Apple Development Certificates

Two Apple Development certificates are present in the login keychain:

```text
Apple Development: ROODY CHERIE (HB5FV6P922)
SHA-1: 9AA0603693FED4F7038C1A975B3D3B4595FC4647
notBefore: Jul 5 17:29:15 2026 GMT
notAfter:  Jul 5 17:29:14 2027 GMT
issuer: Apple Worldwide Developer Relations Certification Authority, OU=G3
```

```text
Apple Development: ROODY CHERIE (HB5FV6P922)
SHA-1: 6E0B7551E4E8509D779AFE96AA1F96E5D3DEAE6F
notBefore: Jul 5 18:18:58 2026 GMT
notAfter:  Jul 5 18:18:57 2027 GMT
issuer: Apple Worldwide Developer Relations Certification Authority, OU=G3
```

The current system time during diagnosis was:

```text
Sun Jul 5 18:31:21 UTC 2026
```

So the cert validity window is not the problem.

### Private Key Association

Private keys are present in the login keychain. Their application labels match the certificate public-key hashes:

```text
Private key label: Apple Development: ROODY CHERIE (ROODY CHERIE)
Application label: 42B28B4420BE03DE97C5DCFF743C719FD0C19A14
Matches cert/key hash: 42B28B4420BE03DE97C5DCFF743C719FD0C19A14
```

```text
Private key label: Apple Development: ROODY CHERIE (ROODY CHERIE)
Application label: 2ABC0E94B2825F38EDDB1B2243A88EFDFF8B7257
Matches cert/key hash: 2ABC0E94B2825F38EDDB1B2243A88EFDFF8B7257
```

This rules out a missing private key as the primary cause.

### WWDR / Root Certificates

Installed WWDR certificates:

```text
Apple Worldwide Developer Relations Certification Authority
SHA-1: FF6797793A3CD798DC5B2ABEF56F73EDC9F83A64
notAfter: Feb 7 21:48:47 2023 GMT
status: expired legacy WWDR
```

```text
Apple Worldwide Developer Relations Certification Authority, OU=G6
SHA-1: 0BE38BFE21FD434D8CC51CBE0E2BC7758DDBF97B
notAfter: Mar 19 00:00:00 2036 GMT
status: valid, but wrong intermediate for these G3-issued development certs
```

Fetched but not installed WWDR G3 certificate metadata:

```text
subject: Apple Worldwide Developer Relations Certification Authority, OU=G3
issuer:  Apple Root CA
SHA-1:   06EC06599F4ED0027CC58956B4D3AC1255114F35
notAfter: Feb 20 00:00:00 2030 GMT
source: http://certs.apple.com/wwdrg3.der
```

The Apple Development certificates include this authority information access URL:

```text
CA Issuers - URI:http://certs.apple.com/wwdrg3.der
```

### Direct codesign Probe

Signing by the common name is ambiguous because two duplicate certs exist:

```text
Apple Development: ROODY CHERIE (HB5FV6P922): ambiguous
```

Signing by either SHA-1 fingerprint fails with chain construction:

```text
Warning: unable to build chain to self-signed root for signer "Apple Development: ROODY CHERIE (HB5FV6P922)"
/tmp/pulsesoc-codesign-probe: errSecInternalComponent
```

That is the decisive evidence: `codesign` can see the cert/private-key pair, but cannot build the trust chain.

### Xcode / Provisioning State

The native iOS project uses:

```text
PRODUCT_BUNDLE_IDENTIFIER = com.pulsesoc.nativeapp
CODE_SIGN_IDENTITY = iPhone Developer
PROVISIONING_PROFILE_REQUIRED = YES
```

No `DEVELOPMENT_TEAM` is configured in the project file.

Provisioning profile inventory:

```text
~/Library/MobileDevice/Provisioning Profiles: 0 profiles
```

This is a second blocker after the identity becomes valid: Xcode will still need a development team and provisioning profile for `com.pulsesoc.nativeapp`.

### Device State

Connected device:

```text
P3r7or
iPhone 16 Pro
iOS 18.7.3
UDID: 00008140-000E2D9A2EE8801C
developerModeStatus: enabled
ddiServicesAvailable: true
```

The iPhone is no longer the blocker.

## Root Cause

Primary root cause:

- Missing local Apple WWDR G3 intermediate certificate for the current Apple Development certificates.

Secondary signing blockers:

- Duplicate Apple Development certificates with the same common name make name-based signing ambiguous. Fingerprint-based signing avoids ambiguity, but still fails until the chain is fixed.
- No provisioning profiles are installed.
- The Xcode project does not currently declare a `DEVELOPMENT_TEAM`.

Not the root cause:

- Not a locked login keychain.
- Not a missing login keychain from the search list.
- Not an expired Apple Development certificate.
- Not a missing private key.
- Not missing WWDR entirely; WWDR exists, but not the needed G3 intermediate.
- Not the iPhone Developer Mode state; Developer Mode is now enabled.

## Safest Fix

Do not reset the keychain.

Recommended sequence:

1. Install Apple WWDR G3 into the login or System keychain:

```bash
curl -L -o /tmp/wwdrg3.der http://certs.apple.com/wwdrg3.der
security add-certificates -k ~/Library/Keychains/login.keychain-db /tmp/wwdrg3.der
```

2. Re-run:

```bash
security find-identity -v -p codesigning
```

Expected result:

```text
Apple Development: ROODY CHERIE (HB5FV6P922)
```

3. If two valid identities appear with the same name, remove or ignore the older duplicate certificate. Prefer using the newest SHA-1 fingerprint or let Xcode regenerate a single fresh Apple Development certificate.

4. Configure Xcode signing:

- Sign into Xcode with the Apple developer account.
- Select the team for `PulseSocNative`.
- Ensure bundle ID remains `com.pulsesoc.nativeapp`.
- Let Xcode create/download a development provisioning profile for `com.pulsesoc.nativeapp` and device `00008140-000E2D9A2EE8801C`.

5. Re-run:

```bash
cd mobile-native
npx expo run:ios --device 00008140-000E2D9A2EE8801C
```

## Keychain Reset Recommendation

Do not reset the keychain.

The keychain is not corrupt based on current evidence. It contains the certs and matching private keys, is the default searched keychain, and is accessible. Resetting would risk deleting useful credentials while not directly addressing the missing WWDR G3/provisioning/team setup.

## Next PulseSoc QA Step

Signing now succeeds for `com.pulsesoc.nativeapp` on `P3r7or`. Continue with physical Camera Studio interaction QA:

- Photo capture.
- Video capture.
- Front/back camera.
- Microphone permission and recording.
- Gallery picker.
- Large video upload.
- Weak-network retry/cancel.
- Upload progress.
- Feed/Status/Reels publish.
- Foreground/background recovery.
- Native visual quality on the physical iPhone.

Do not move to Native LiveKit calls until this physical Camera Studio pass has real evidence.
