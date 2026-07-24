import { resolveLiveAudioConfiguration } from "../useLiveBroadcastRoom";

/**
 * Regression guard for the production livestream-audio P0: viewers could see the
 * host but never hear them. Root cause was that a listen-only viewer reused the
 * publisher's iOS `playAndRecord`/`videoChat` audio session — which requires a
 * microphone grant and can fail to activate for a viewer who never granted mic
 * permission, leaving subscribed host audio with no output route.
 *
 * The contract these tests lock in:
 *   - a publisher (host/co-host) records, so it MUST use `playAndRecord`.
 *   - a listen-only viewer MUST use `playback` and MUST NOT request record
 *     capability, so host audio always plays with no mic dependency.
 */
describe("resolveLiveAudioConfiguration", () => {
  it("gives a publisher a record-capable communication session", () => {
    const config = resolveLiveAudioConfiguration(true);
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    // defaultToSpeaker is only valid alongside playAndRecord.
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
  });

  it("gives a listen-only viewer a playback session that never touches the mic", () => {
    const config = resolveLiveAudioConfiguration(false);
    expect(config.audioCategory).toBe("playback");
    expect(config.audioCategory).not.toBe("playAndRecord");
    expect(config.audioMode).toBe("moviePlayback");
    // defaultToSpeaker is invalid without playAndRecord and must be omitted.
    expect(config.audioCategoryOptions).not.toContain("defaultToSpeaker");
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
