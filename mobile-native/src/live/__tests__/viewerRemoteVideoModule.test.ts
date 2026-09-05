/**
 * Regression guard for the Live viewer P0: audience joins, hears nothing wrong
 * in telemetry ("channel_joined" fires), yet never reaches PLAYING — every
 * viewer surface falls back to Web with "native playback unavailable".
 *
 * Root cause: `engine.enableVideo()` lived inside the
 * `if (publishingVideoRef.current)` publisher gate. In the Agora 4.x SDK that
 * call enables the video MODULE — the thing that lets a client DECODE remote
 * video, not just capture its own. An audience client that skips it can join
 * and subscribe, but `onFirstRemoteVideoDecoded` can never fire, so
 * `hasVideo` stays false and the screen waits for host media forever.
 *
 * Why this is a source-level test: the hook resolves react-native-agora via
 * `await import(...)`, which this project's Jest config cannot evaluate (see
 * liveEchoControlWiring.test.ts). The decision layer is covered as pure
 * functions in liveStreamQuality.test.ts; this file pins the one wiring fact
 * the pure layer cannot see — where in connect() the module is switched on.
 * These assertions fail against the pre-fix source.
 */
import { readFileSync } from "fs";
import { join } from "path";

const source = readFileSync(join(__dirname, "..", "useAgoraLiveBroadcastRoom.ts"), "utf8");

/** The connect() body from engine creation to the publisher-only block. */
function connectSetupSegment(): { setup: string; publisherBlock: string } {
  const createAt = source.indexOf("createAgoraRtcEngine()");
  expect(createAt).toBeGreaterThan(-1);
  const gateAt = source.indexOf("if (publishingVideoRef.current) {", createAt);
  expect(gateAt).toBeGreaterThan(-1);
  const gateEnd = source.indexOf("}", source.indexOf("startPreview()", gateAt));
  expect(gateEnd).toBeGreaterThan(-1);
  return {
    setup: source.slice(createAt, gateAt),
    publisherBlock: source.slice(gateAt, gateEnd)
  };
}

describe("audience decode path", () => {
  it("enables the video module unconditionally, before the publisher gate", () => {
    const { setup } = connectSetupSegment();
    // Both module switches sit in the role-independent setup: audio has always
    // been there; video is the fix. If enableVideo() moves back behind the
    // publisher gate, this fails and the audience bug returns.
    expect(setup).toContain("engine.enableAudio()");
    expect(setup).toContain("engine.enableVideo()");
  });

  it("keeps capture behind the publisher gate — the module is not the camera", () => {
    const { setup, publisherBlock } = connectSetupSegment();
    // Stage 25 must survive the fix: an audience member initialises no capture
    // hardware. Preview (which starts the camera) stays publisher-only.
    expect(setup).not.toContain("engine.startPreview()");
    expect(publisherBlock).toContain("engine.startPreview()");
    // And the publisher gate no longer carries its own enableVideo() — a
    // duplicate there would invite someone to "clean up" the unconditional one.
    expect(publisherBlock).not.toContain("enableVideo()");
  });

  it("still joins the audience with capture publication pinned off", () => {
    expect(source).toContain("publishCameraTrack: Boolean(localTrack)");
    // Auto-subscribe is what makes the module fix sufficient: with both true,
    // the first remote frame decodes as soon as the host publishes.
    expect(source).toContain("autoSubscribeVideo: true");
    expect(source).toContain("autoSubscribeAudio: true");
  });
});
