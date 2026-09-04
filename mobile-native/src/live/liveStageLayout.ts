/**
 * How a Live stage arranges itself as publishers come and go.
 *
 * The governing constraint is editorial, not technical: a PulseSoc Live with six
 * people on it is still a broadcast with six people on stage, not a six-way video
 * call that an audience happens to be watching. A conference grid says "we are
 * all equal participants in a meeting". A broadcast stage says "this is someone's
 * show, and these people are on it". The difference is visible in three rules,
 * all implemented here:
 *
 *  1. The host is never just another cell. At every population from one to the
 *     maximum, the host's tile is the largest one and it is first.
 *  2. Tiles never move because someone spoke. Position is derived from the
 *     server's stage order and nothing else, so a tile cannot slide out from
 *     under a viewer's thumb mid-sentence.
 *  3. The stage grows by promoting a layout, not by adding cells to a fixed
 *     grid. One publisher is full-bleed; two is a split; more is a featured host
 *     above a strip of guests.
 *
 * Active speaker is surfaced as a *highlight* — a ring on an existing tile — and
 * never as a reordering. The tracker below exists to make that highlight stable
 * enough to be useful, because the raw signal from Agora's volume indication
 * flickers between people several times a second during normal conversation.
 *
 * Pure: no React, no Agora, no dimensions API. The caller supplies the viewport.
 */

import type { LiveStageParticipant } from "./liveParticipantRegistry";
import { publishingRoster, shouldRenderVideoTile } from "./liveParticipantRegistry";

export type StageLayoutMode =
  /** One publisher. Full bleed, no chrome competing with them. */
  | "solo"
  /** Two publishers. Split stage, host on the larger half. */
  | "split"
  /** Three to four. Host featured above a row of guests. */
  | "featured"
  /** Five or more. Host featured above a scrolling strip. */
  | "featured-strip";

export type StageTile = {
  participant: LiveStageParticipant;
  key: string;
  /** Column index, 0-based. */
  column: number;
  /** Row index, 0-based. Row 0 is the featured row. */
  row: number;
  /** How many columns this tile spans. The host's span is what makes it big. */
  columnSpan: number;
  /** Fraction of the stage height this tile's row occupies, 0..1. */
  heightRatio: number;
  /** Whether this tile is the visually dominant one. */
  featured: boolean;
  /** Whether a video surface should be mounted, versus an avatar placeholder. */
  showsVideo: boolean;
};

export type StageLayout = {
  mode: StageLayoutMode;
  columns: number;
  rows: number;
  tiles: StageTile[];
  /** Publishers beyond what the stage can show at once. Always 0 at or below the cap. */
  overflow: number;
};

/**
 * The largest stage the layout is designed for.
 *
 * Matches the server's guest ceiling: `LIVE_MAX_GUESTS` guests plus the host.
 * Kept as a layout-side sanity bound only — the server owns the real limit, and
 * this must never be used to decide whether someone may join.
 */
export const STAGE_LAYOUT_CAPACITY = 13;

function emptyLayout(mode: StageLayoutMode = "solo"): StageLayout {
  return { mode, columns: 1, rows: 0, tiles: [], overflow: 0 };
}

/**
 * Arrange the stage.
 *
 * Takes the full participant list — not just the publishing ones — because a
 * guest in `joining` still occupies their slot and shows an avatar. Dropping
 * them until their first frame arrives would make the stage jump the moment they
 * connect, which is precisely the reshuffle rule 2 forbids.
 */
export function planStageLayout(participants: LiveStageParticipant[]): StageLayout {
  // `stageRoster` order is already host-first and stable; this must not re-sort.
  const onStage = (participants || []).filter((participant) => participant.phase !== "left");
  if (onStage.length === 0) return emptyLayout();

  const visible = onStage.slice(0, STAGE_LAYOUT_CAPACITY);
  const overflow = onStage.length - visible.length;
  const [featured, ...rest] = visible;

  const tile = (
    participant: LiveStageParticipant,
    column: number,
    row: number,
    columnSpan: number,
    heightRatio: number,
    isFeatured: boolean
  ): StageTile => ({
    participant,
    key: participant.key,
    column,
    row,
    columnSpan,
    heightRatio,
    featured: isFeatured,
    showsVideo: shouldRenderVideoTile(participant)
  });

  if (visible.length === 1) {
    return { mode: "solo", columns: 1, rows: 1, tiles: [tile(featured, 0, 0, 1, 1, true)], overflow };
  }

  if (visible.length === 2) {
    // A 60/40 split rather than an even one. Two equal halves is the visual
    // grammar of a video call; an uneven split still reads as someone's show
    // with a guest on it.
    return {
      mode: "split",
      columns: 1,
      rows: 2,
      tiles: [tile(featured, 0, 0, 1, 0.6, true), tile(rest[0], 0, 1, 1, 0.4, false)],
      overflow
    };
  }

  // Three or more: the host takes a full-width featured row, guests fill the
  // rows beneath it. The host's row is always taller than a guest row, so the
  // hierarchy survives at every population.
  const columns = rest.length <= 4 ? 2 : 3;
  const guestRows = Math.ceil(rest.length / columns);
  const mode: StageLayoutMode = rest.length <= 3 ? "featured" : "featured-strip";
  // The featured row keeps at least half the stage no matter how many guests
  // arrive, and never takes so much that a guest row becomes unreadably short.
  const featuredRatio = rest.length <= 3 ? 0.6 : 0.5;
  const guestRatio = (1 - featuredRatio) / guestRows;

  const tiles: StageTile[] = [tile(featured, 0, 0, columns, featuredRatio, true)];
  rest.forEach((participant, index) => {
    tiles.push(tile(participant, index % columns, 1 + Math.floor(index / columns), 1, guestRatio, false));
  });

  return { mode, columns, rows: 1 + guestRows, tiles, overflow };
}

/**
 * Whether the layout changed in a way that requires remounting video surfaces.
 *
 * Remounting an Agora video view black-flashes it, so a layout pass that only
 * moved a tile must not be treated as a structural change. Identity is what
 * matters: the same people in the same order is the same stage, whatever their
 * volume or mute state is doing.
 */
export function layoutIdentity(layout: StageLayout): string {
  return `${layout.mode}:${layout.columns}:${layout.tiles.map((item) => item.key).join(",")}`;
}

// ---------------------------------------------------------------------------
// Active speaker
// ---------------------------------------------------------------------------

/**
 * Agora reports speaker volumes several times a second. Rendering that signal
 * directly makes the highlight strobe: in an ordinary two-person conversation
 * the loudest speaker changes on nearly every report, because the listener's
 * microphone picks up breath, backchannel and room noise.
 *
 * So a change of speaker has to earn its way in. A challenger must be
 * meaningfully louder than the incumbent, stay louder across consecutive
 * reports, and the incumbent must have held the highlight long enough that
 * moving it is not just noise. The result is a highlight that follows the
 * conversation rather than the waveform.
 */
export type ActiveSpeakerConfig = {
  /** Below this (0..255, Agora's scale) nobody is considered speaking. */
  threshold: number;
  /** A challenger must beat the incumbent by this much to take over. */
  margin: number;
  /** Consecutive reports the challenger must lead for. */
  sustainedReports: number;
  /** Minimum time the incumbent holds before it can be displaced. */
  holdMs: number;
  /** Silence after which the highlight clears entirely. */
  silenceMs: number;
};

export const DEFAULT_ACTIVE_SPEAKER_CONFIG: ActiveSpeakerConfig = {
  threshold: 15,
  margin: 8,
  sustainedReports: 2,
  holdMs: 1200,
  silenceMs: 2500
};

export type ActiveSpeakerState = {
  /** The uid currently highlighted, or 0 for nobody. */
  activeUid: number;
  /** When the current holder took over. */
  sinceMs: number;
  /** When we last heard anyone above threshold. */
  lastVoiceMs: number;
  /** The uid currently building a case to take over. */
  challengerUid: number;
  challengerReports: number;
};

export const INITIAL_ACTIVE_SPEAKER_STATE: ActiveSpeakerState = {
  activeUid: 0,
  sinceMs: 0,
  lastVoiceMs: 0,
  challengerUid: 0,
  challengerReports: 0
};

export type SpeakerVolume = { rtcUid: number; volume: number };

/**
 * Fold one volume report into the tracker.
 *
 * Pure, and takes `nowMs` explicitly rather than reading the clock, so the hold
 * and silence windows can be tested without waiting for them.
 */
export function reduceActiveSpeaker(
  state: ActiveSpeakerState,
  volumes: SpeakerVolume[],
  nowMs: number,
  config: ActiveSpeakerConfig = DEFAULT_ACTIVE_SPEAKER_CONFIG
): ActiveSpeakerState {
  let loudestUid = 0;
  let loudestVolume = 0;
  let incumbentVolume = 0;

  for (const entry of volumes || []) {
    const uid = Number(entry?.rtcUid) || 0;
    const volume = Number(entry?.volume) || 0;
    // uid 0 is the local speaker in Agora's report; it is resolved to a real uid
    // by the caller, which knows its own seat. An unresolved 0 is discarded
    // rather than highlighted as a phantom participant.
    if (uid <= 0 || volume < config.threshold) continue;
    if (uid === state.activeUid) incumbentVolume = volume;
    if (volume > loudestVolume) {
      loudestVolume = volume;
      loudestUid = uid;
    }
  }

  if (loudestUid === 0) {
    // Nobody above threshold. Hold the highlight through short pauses — speech
    // has gaps — and clear it only once the silence is longer than a breath.
    if (state.activeUid && nowMs - state.lastVoiceMs > config.silenceMs) {
      return { ...INITIAL_ACTIVE_SPEAKER_STATE };
    }
    return { ...state, challengerUid: 0, challengerReports: 0 };
  }

  if (loudestUid === state.activeUid) {
    return { ...state, lastVoiceMs: nowMs, challengerUid: 0, challengerReports: 0 };
  }

  if (state.activeUid === 0) {
    // An empty stage needs no persuading; the first voice takes the highlight.
    return { activeUid: loudestUid, sinceMs: nowMs, lastVoiceMs: nowMs, challengerUid: 0, challengerReports: 0 };
  }

  const heldLongEnough = nowMs - state.sinceMs >= config.holdMs;
  const clearlyLouder = loudestVolume >= incumbentVolume + config.margin;
  if (!heldLongEnough || !clearlyLouder) {
    return { ...state, lastVoiceMs: nowMs, challengerUid: loudestUid, challengerReports: 0 };
  }

  const reports = state.challengerUid === loudestUid ? state.challengerReports + 1 : 1;
  if (reports < config.sustainedReports) {
    return { ...state, lastVoiceMs: nowMs, challengerUid: loudestUid, challengerReports: reports };
  }
  return { activeUid: loudestUid, sinceMs: nowMs, lastVoiceMs: nowMs, challengerUid: 0, challengerReports: 0 };
}

/**
 * Apply the highlight to the roster.
 *
 * Returns a new list with `speaking` set on at most one participant. Note what
 * it does not do: it does not re-sort. The highlight is a property of a tile,
 * never a reason for that tile to move.
 */
export function applyActiveSpeaker(
  participants: LiveStageParticipant[],
  state: ActiveSpeakerState
): LiveStageParticipant[] {
  return (participants || []).map((participant) => {
    const speaking = participant.rtcUid === state.activeUid && !participant.audioMuted;
    return participant.speaking === speaking ? participant : { ...participant, speaking };
  });
}

/**
 * The participant the highlight is on, if they are actually publishing.
 *
 * A muted or departed speaker cannot hold the highlight, so this returns null
 * rather than pointing at a tile that has gone quiet.
 */
export function activeSpeakerParticipant(
  participants: LiveStageParticipant[],
  state: ActiveSpeakerState
): LiveStageParticipant | null {
  if (!state.activeUid) return null;
  return (
    publishingRoster(participants).find(
      (participant) => participant.rtcUid === state.activeUid && !participant.audioMuted
    ) || null
  );
}
