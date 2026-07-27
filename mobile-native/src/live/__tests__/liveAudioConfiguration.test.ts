import { resolveLiveAudioConfiguration } from "../useLiveBroadcastRoom";

/**
 * Regression guard for the production livestream-audio P0: native calls already
 * have working bidirectional audio, so Live must use the same call-grade iOS
 * session instead of a media-playback-only profile that can be interrupted by
 * Reels/Radio/media playback while LiveKit is rendering video.
 *
 * The contract these tests lock in:
 *   - host/co-host records, so it MUST use `playAndRecord`.
 *   - listen-only viewer also uses the call-compatible session so subscribed
 *     remote host/co-host audio has the same AVAudioSession path as calls.
 */
describe("resolveLiveAudioConfiguration", () => {
  it("gives a publisher a record-capable communication session", () => {
    const config = resolveLiveAudioConfiguration(true);
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    // defaultToSpeaker is only valid alongside playAndRecord.
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
  });

  it("gives a listen-only viewer the same call-compatible output route as calls", () => {
    const config = resolveLiveAudioConfiguration(false);
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
  });

  it("routes both roles to Bluetooth/AirPlay outputs", () => {
    for (const publish of [true, false]) {
      const config = resolveLiveAudioConfiguration(publish);
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"])
      );
    }
  });
});
