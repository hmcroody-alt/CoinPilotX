/**
 * Stages 24-26 — what a device subscribes to, what it publishes, and what it
 * gives up first when it cannot keep up.
 *
 * The arithmetic that makes this stage necessary: a solo Live asks a viewer's
 * phone to decode one 720p stream. A six-publisher Live asks it to decode six.
 * Nothing in the join path changes between those two cases, so a device that
 * handled Live perfectly well for two years starts dropping frames, heating,
 * and eventually dying the first time a host puts five people on stage — and
 * the report that comes back is "the app broke", not "the stage got bigger".
 *
 * Three rules hold everything here together.
 *
 *   1. AUDIO IS NEVER THE THING THAT DEGRADES. A stage that sounds right and
 *      looks rough is a broadcast having a bad minute. A stage that looks right
 *      and sounds broken is a broadcast that has failed, because the content of
 *      a Live is people talking. Every degradation step below removes video.
 *
 *   2. A TILE GETS THE RESOLUTION ITS SIZE JUSTIFIES. Six tiles on a phone are
 *      each smaller than a business card; subscribing to 720p for each is
 *      decoding pixels that are thrown away before they reach a pixel of glass.
 *      Agora's low stream exists exactly for this.
 *
 *   3. AN AUDIENCE MEMBER INITIALISES NOTHING. Not the camera, not the
 *      microphone, not an encoder. This is Stage 25 and it is a privacy
 *      property before it is a performance one.
 *
 * Pure: no Agora, no React, no I/O. The hook applies these answers; it does not
 * form its own.
 */

import type { LiveRole } from "./liveParticipantRegistry";

// ---------------------------------------------------------------------------
// Stage 25 — an audience member initialises nothing
// ---------------------------------------------------------------------------

export type LivePublishPlan = {
  /**
   * Enable the Agora video MODULE.
   *
   * This is not the camera. In the 4.x SDK, `enableVideo()` is what allows the
   * client to DECODE remote video as well as encode its own — a client that
   * never calls it can join, subscribe, and hear audio, but will never get a
   * first remote video frame. That is precisely the audience viewer's bug
   * signature: joined, "waiting for host media" forever, Web fallback.
   *
   * Capture is a separate act: it begins only with `startPreview()` or a
   * publication, both of which stay false for the audience below. So this is
   * true for EVERY client, and Stage 25 keeps its meaning through
   * `planTouchesCaptureHardware`, which deliberately ignores this field.
   */
  enableVideoModule: boolean;
  /** Open the camera. */
  enableVideo: boolean;
  /** Open the microphone. */
  enableAudio: boolean;
  /** Start the local preview surface. */
  startPreview: boolean;
  /** Configure the video encoder. Meaningless without a capture. */
  configureEncoder: boolean;
  /** Publish a low stream alongside the high one. */
  enableDualStream: boolean;
  /** Agora client role. */
  clientRole: "broadcaster" | "audience";
};

const AUDIENCE_PLAN: LivePublishPlan = {
  // The one thing an audience member DOES initialise: the decode path.
  // Without it the viewer never reaches PLAYING. Not capture hardware.
  enableVideoModule: true,
  enableVideo: false,
  enableAudio: false,
  startPreview: false,
  configureEncoder: false,
  enableDualStream: false,
  clientRole: "audience"
};

/**
 * What a client sets up on join.
 *
 * `serverAuthorized` is not a convenience flag; it is the whole gate. A client
 * may believe it is a guest — an optimistic UI update, a stale prop, a screen
 * that was reused — and that belief must not be sufficient to open a camera.
 * Only credentials the server issued can produce a broadcaster plan, which is
 * why the role alone is never asked.
 *
 * Returned as an explicit object rather than a boolean because "should this
 * client publish" and "should this client start a preview" have drifted apart
 * before: a guest waiting to be let on stage wants a preview and no
 * publication, and collapsing the two produces either a black waiting screen or
 * a guest who is live before they agreed to be.
 */
export function resolvePublishPlan(input: {
  role: LiveRole;
  serverAuthorized: boolean;
  videoRequested?: boolean;
}): LivePublishPlan {
  if (!input.serverAuthorized) return { ...AUDIENCE_PLAN };
  if (input.role === "audience") return { ...AUDIENCE_PLAN };

  const video = input.videoRequested !== false;
  return {
    // Every client decodes remote video; publishers additionally capture.
    enableVideoModule: true,
    enableVideo: video,
    enableAudio: true,
    startPreview: video,
    configureEncoder: video,
    // Only publishers produce a low stream, and only when there is video to
    // produce it from. A simulcast layer nobody subscribes to is uploaded
    // anyway, so this costs the guest's data plan for nothing.
    enableDualStream: video,
    clientRole: "broadcaster"
  };
}

/**
 * Whether a plan would open any capture device.
 *
 * Stage 25 as a single assertion, so a regression in `resolvePublishPlan` fails
 * loudly instead of quietly opening a viewer's microphone.
 */
export function planTouchesCaptureHardware(plan: LivePublishPlan): boolean {
  return plan.enableVideo || plan.enableAudio || plan.startPreview;
}

// ---------------------------------------------------------------------------
// Stage 24 — which stream each tile subscribes to
// ---------------------------------------------------------------------------

export type RemoteStreamType = "high" | "low";

/**
 * The stream quality a tile should subscribe to.
 *
 * The focus tile always gets the high stream: on a host-priority layout it is
 * most of the screen, and a low stream stretched to fill it is visibly soft in
 * a way an audience reads as a bad broadcast.
 *
 * Everything else is a judgement about tile size. Below three publishers the
 * tiles are large enough that the high stream is worth its cost. At three and
 * above the stage is a grid of small cells, and the low stream is both
 * sufficient and the difference between a phone that copes and one that does
 * not.
 *
 * A degraded device drops to low everywhere, focus included. That is a visible
 * concession, and it is the correct one: a soft picture is a worse broadcast, a
 * dropped connection is no broadcast.
 */
export function remoteStreamTypeFor(input: {
  publisherCount: number;
  isFocus: boolean;
  degraded?: boolean;
}): RemoteStreamType {
  if (input.degraded) return "low";
  if (input.isFocus) return "high";
  const count = Math.max(0, Math.floor(Number(input.publisherCount) || 0));
  return count >= 3 ? "low" : "high";
}

/**
 * The encoder configuration a publisher should use for a stage of this size.
 *
 * Publishing 720p into a six-way grid is uploading a resolution nobody will
 * subscribe to, on a mobile connection, from a device that is also decoding
 * five other streams. The ladder steps down with the stage and stops at 360p,
 * which is still legible at the tile sizes involved.
 *
 * The frame rate falls more slowly than the resolution because motion judder
 * reads as "broken" to a viewer far more readily than softness does.
 */
export function publisherVideoProfile(publisherCount: number): {
  width: number;
  height: number;
  frameRate: number;
} {
  const count = Math.max(1, Math.floor(Number(publisherCount) || 1));
  if (count <= 1) return { width: 720, height: 1280, frameRate: 30 };
  if (count <= 2) return { width: 540, height: 960, frameRate: 30 };
  if (count <= 4) return { width: 480, height: 854, frameRate: 24 };
  return { width: 360, height: 640, frameRate: 24 };
}

// ---------------------------------------------------------------------------
// Stage 26 — measuring a guest device, and what to give up
// ---------------------------------------------------------------------------

export type DeviceSample = {
  /** Frames per second the local encoder is actually achieving. */
  encodeFps: number;
  /** Target frame rate the encoder was configured for. */
  targetFps: number;
  /** Application CPU as a percentage, 0-100. */
  cpuPercent: number;
  /** Round-trip time to the Agora edge, in milliseconds. */
  rttMs: number;
  /** Fraction of packets lost upstream, 0-1. */
  uplinkLoss: number;
};

export type DegradationStep =
  /** Everything is fine. */
  | "none"
  /** Subscribe to low streams for non-focus tiles. */
  | "subscribeLow"
  /** Also reduce what this device publishes. */
  | "reducePublish"
  /** Stop rendering remote video; keep every audio stream. */
  | "audioOnlyRemote"
  /** Stop publishing video. This device is still heard and still hears. */
  | "stopPublishVideo";

export type PerformanceAssessment = {
  step: DegradationStep;
  /** Health band, for telemetry and for an honest banner. */
  health: "good" | "strained" | "critical";
  /** True when audio would be affected. Must never be true. */
  sacrificesAudio: false;
  /** Why, in a form safe to log. Never contains user content. */
  reason: string;
};

const HEALTHY: PerformanceAssessment = {
  step: "none",
  health: "good",
  sacrificesAudio: false,
  reason: "within_budget"
};

/**
 * What this device should give up, if anything.
 *
 * The ladder is ordered by what the user loses. Subscribing to low streams
 * costs sharpness on tiles the size of a stamp and is close to free. Reducing
 * what this device publishes costs the audience some sharpness of this person.
 * Dropping remote video turns the Live into a radio broadcast for this viewer,
 * which is a real loss but leaves the thing they came for intact. Only at the
 * end does this device stop publishing video — and even then it keeps its
 * microphone, because a panellist who is heard but not seen is still on the
 * panel.
 *
 * Note what is absent from every branch: audio. `sacrificesAudio` is typed as
 * the literal `false` so that a future edit which tries to degrade audio does
 * not compile.
 *
 * Thresholds are deliberately loose. A device is judged on sustained trouble,
 * not on a single bad sample, and the caller is expected to feed this a rolling
 * measurement — reacting to one frame of jitter would flap the layout.
 */
export function assessDevicePerformance(sample: DeviceSample, publisherCount = 1): PerformanceAssessment {
  const target = Math.max(1, Number(sample?.targetFps) || 1);
  const fps = Math.max(0, Number(sample?.encodeFps) || 0);
  const cpu = Math.min(100, Math.max(0, Number(sample?.cpuPercent) || 0));
  const rtt = Math.max(0, Number(sample?.rttMs) || 0);
  const loss = Math.min(1, Math.max(0, Number(sample?.uplinkLoss) || 0));
  const fpsRatio = fps / target;
  const count = Math.max(1, Math.floor(Number(publisherCount) || 1));

  // Nothing to give up on a solo Live: there are no extra streams, so the
  // ladder's first two rungs do not exist and a struggling device has already
  // been handled by Agora's own adaptation.
  if (count <= 1 && cpu < 90 && fpsRatio > 0.4) return HEALTHY;

  if (cpu >= 92 || fpsRatio < 0.35 || loss >= 0.3) {
    return {
      step: "stopPublishVideo",
      health: "critical",
      sacrificesAudio: false,
      reason: "device_cannot_sustain_capture"
    };
  }

  if (cpu >= 85 || fpsRatio < 0.5 || loss >= 0.2 || rtt >= 800) {
    return {
      step: "audioOnlyRemote",
      health: "critical",
      sacrificesAudio: false,
      reason: "decode_budget_exhausted"
    };
  }

  if (cpu >= 75 || fpsRatio < 0.7 || loss >= 0.1 || rtt >= 500) {
    return {
      step: "reducePublish",
      health: "strained",
      sacrificesAudio: false,
      reason: "uplink_or_encode_pressure"
    };
  }

  if (count >= 3 && (cpu >= 60 || fpsRatio < 0.85)) {
    return {
      step: "subscribeLow",
      health: "strained",
      sacrificesAudio: false,
      reason: "stage_size_decode_pressure"
    };
  }

  return HEALTHY;
}

/**
 * Whether a degradation step would take a participant off the air entirely.
 *
 * The invariant Stage 24 and Stage 26 share: however bad a device gets, the
 * person stays audible. Exposed so it can be asserted over every step rather
 * than only the ones a test author thought of.
 */
export function stepSilencesParticipant(step: DegradationStep): boolean {
  // Deliberately exhaustive rather than `return false`: adding a step to the
  // union without deciding this question becomes a type error.
  switch (step) {
    case "none":
    case "subscribeLow":
    case "reducePublish":
    case "audioOnlyRemote":
    case "stopPublishVideo":
      return false;
    default: {
      const exhaustive: never = step;
      return Boolean(exhaustive);
    }
  }
}
