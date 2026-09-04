# Media Device QA Checklist

`expo-media-library` and `expo-sharing` are native modules. They are **not** present in the
current dev client, so nothing below can be exercised until a new build is produced:

```
cd mobile-native
eas build --profile development --platform ios     # and/or android
```

Static analysis and the Jest suite cannot substitute for any item on this list. Per the
repo's own protection policy, uploads, checkout, push and livestream always require device
QA; media save/share now joins that list.

## iOS

- [ ] First save shows the system Photos prompt with the string from
      `NSPhotoLibraryAddUsageDescription` ("PulseSoc saves photos and videos you download to
      your photo library.").
- [ ] Grant **Limited** access: the save still succeeds and the app reports saved.
- [ ] Deny, then save again: the app directs the user to Settings and does not loop a prompt
      that iOS will no longer show.
- [ ] Save a photo, then a video; confirm both appear in Photos with correct type.
- [ ] Share a photo: the system share sheet opens with the actual file, and AirDrop /
      Messages receive the image, not a link.
- [ ] Share a post with `sourceUrl`: the canonical pulsesoc.com link is shared.
- [ ] Lock-screen / Now Playing controls still behave (`modules/pulse-now-playing`).

## Android

- [ ] Save on API 33+ (scoped storage, no legacy WRITE_EXTERNAL_STORAGE prompt).
- [ ] Save on API 29–32.
- [ ] Saved media appears in the Gallery/Photos app without a reboot or media rescan.
- [ ] Share sheet receives the file with a correct MIME type.

## Both platforms

- [ ] **Airplane mode**: opening uncached media shows an honest failure, no spinner that
      never resolves, no crash.
- [ ] **Network transition** (Wi-Fi → cellular mid-download): the transfer resumes or fails
      cleanly; it does not leave a file at the canonical path.
- [ ] **Kill the app mid-download**, relaunch, re-open the same media: it downloads again and
      succeeds. No truncated file is served to a decoder.
- [ ] **Fill the device** to under 128 MB free: the download is refused up front with a
      storage message and no network transfer starts.
- [ ] **Fast-scroll a media-heavy feed** for 60s: no duplicate downloads (watch the
      `MEDIA_DOWNLOAD_STARTED` telemetry), memory stays flat, no OOM kill.
- [ ] **Open a malformed / truncated file**: the screen shows a failure state and stays
      usable. The conversation does not crash.
- [ ] **Save a document (PDF)**: the "Save to Photos" button is absent; Share works.
- [ ] **Account switch**: sign in as A, cache media, sign out, sign in as B. B cannot see A's
      media offline and the cache directory for A is gone.
- [ ] **Regression — realtime audio**: join a live audio room, an audio call, and a video
      call; open the media viewer and save a photo during each. Audio must not drop, switch
      route, or go silent. This is the specific failure mode the audio protection policy
      exists for.

## Telemetry to watch during QA

Set a sink with `setMediaTelemetrySink` and confirm the field discipline holds: no event
should carry anything resembling a URL, and every failure should carry a code from the
closed vocabulary rather than free text.
