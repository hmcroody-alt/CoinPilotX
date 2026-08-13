import { Audio } from "expo-av";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { PulseMusicTrack, recordPulseMusicEvent } from "../api/music";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { colors } from "../theme/colors";
import { CreatorMixSlider } from "./CreatorMixSlider";
import {
  CREATOR_MIXER_PRESETS,
  CreatorMixerPreset,
  CreatorMixerSettings,
  applyCreatorMixerPreset,
  creatorMicBusGainDb,
  creatorMixLevelToLinearGain,
  creatorMixLevelToPercent,
  creatorMixPercentToLevel,
  creatorMusicBusGainDb,
  loadCreatorMixerSettings,
  saveCreatorMixerSettings,
  withDucking,
  withMicLevel,
  withMusicLevel
} from "./creatorMixer";
import {
  CREATOR_MUSIC_LANES,
  CreatorMusicLane,
  creatorMusicGenresFrom,
  loadCreatorMusicLane,
  rememberCreatorMusicTrack
} from "./creatorMusicLibrary";
import {
  CREATOR_MUSIC_MIN_TAIL_SECONDS,
  CreatorMusicSelection,
  clampCreatorMusicStartOffset,
  createCreatorMusicSelection
} from "./creatorMusicSelection";

/**
 * Pick a song, hear it, choose where it starts, set the two levels.
 *
 * One sheet serves both surfaces the mission covers — the camera before a
 * recording, and the Live host mid-broadcast — because the thing being edited is
 * the same object in both cases. What differs is only what the caller does with
 * the result: the camera carries it to the upload, Live pushes it straight into
 * the running mix.
 *
 * What this component deliberately does **not** do is touch the audio session.
 * There is no `setAudioModeAsync` here and no microphone monitoring, because
 * this sheet opens on top of a live camera and, in the Live case, on top of an
 * active broadcast. A preview player that reconfigured the shared session would
 * be exactly the failure the real-time audio policy exists to prevent: the
 * broadcast stays green and goes silent. Preview goes through the media playback
 * coordinator instead, which pauses Radio and Reels the same way every other
 * player in the app does.
 */

type Stage = "browse" | "tune";

const PREVIEW_OWNER = "creator-music-preview";

export type CreatorMusicSheetProps = {
  visible: boolean;
  /** What the caller is currently using, so reopening the sheet resumes rather than restarts. */
  selection: CreatorMusicSelection | null;
  onClose: () => void;
  onSelect: (selection: CreatorMusicSelection) => void;
  onRemove: () => void;
  /**
   * Live pushes every change into the running mix; the camera only needs the
   * final answer. Supplying this makes the faders live.
   */
  onSelectionPreview?: (selection: CreatorMusicSelection) => void;
  title?: string;
  allowStartOffset?: boolean;
  /**
   * Whether the local preview player may run.
   *
   * Live turns this off, and the reason is the central constraint of the whole
   * feature. The preview plays out of the phone's speaker; on a broadcast the
   * microphone is open, so a preview would be captured acoustically and go out
   * to viewers as a muddy, room-smeared re-recording layered under the real mix.
   * Live auditions through `onSelectionPreview` instead, which pushes the track
   * into the SDK's own mixer — the clean digital path, and the only one the
   * viewer should ever hear.
   */
  allowPreview?: boolean;
  confirmLabel?: string;
};

export function CreatorMusicSheet({
  visible,
  selection,
  onClose,
  onSelect,
  onRemove,
  onSelectionPreview,
  title = "Music",
  allowStartOffset = true,
  allowPreview = true,
  confirmLabel = "Use this track"
}: CreatorMusicSheetProps) {
  const insets = useSafeAreaInsets();
  const [stage, setStage] = useState<Stage>("browse");
  const [lane, setLane] = useState<CreatorMusicLane>("trending");
  const [query, setQuery] = useState("");
  const [genre, setGenre] = useState("");
  const [tracks, setTracks] = useState<PulseMusicTrack[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [offline, setOffline] = useState(false);
  const [track, setTrack] = useState<PulseMusicTrack | null>(null);
  const [startOffset, setStartOffset] = useState(0);
  const [mixer, setMixer] = useState<CreatorMixerSettings | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const previewSound = useRef<Audio.Sound | null>(null);
  const previewOffsetRef = useRef(0);

  const stopPreview = useCallback(async () => {
    const sound = previewSound.current;
    previewSound.current = null;
    setPreviewing(false);
    if (sound) await sound.unloadAsync().catch(() => undefined);
    await releaseMediaPlayback(PREVIEW_OWNER).catch(() => undefined);
  }, []);

  // The mixer is loaded once per opening rather than held in module state: the
  // creator may have changed it from the Live sheet since this screen mounted.
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    loadCreatorMixerSettings()
      .then((stored) => {
        if (cancelled) return;
        setMixer(selection?.mixer || stored);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [visible, selection]);

  useEffect(() => {
    if (visible) {
      setStage(selection ? "tune" : "browse");
      setTrack(null);
      setStartOffset(selection?.startOffsetSeconds || 0);
      setMessage("");
      return;
    }
    stopPreview().catch(() => undefined);
  }, [visible, selection, stopPreview]);

  useEffect(() => {
    return () => {
      stopPreview().catch(() => undefined);
    };
  }, [stopPreview]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await loadCreatorMusicLane({ lane, query, genre });
      setTracks(result.tracks);
      setOffline(result.offline);
      setMessage(result.message);
    } finally {
      setLoading(false);
    }
  }, [genre, lane, query]);

  useEffect(() => {
    if (!visible || stage !== "browse") return;
    const timer = setTimeout(() => {
      load().catch(() => undefined);
    }, query ? 340 : 0);
    return () => clearTimeout(timer);
  }, [visible, stage, load, query]);

  const genres = useMemo(() => creatorMusicGenresFrom(tracks), [tracks]);

  /**
   * The track being tuned.
   *
   * Falls back to the selection the caller handed in so that reopening the sheet
   * on an existing choice lands on the faders rather than making the creator
   * find their song again.
   */
  const tuningTrack: PulseMusicTrack | null = useMemo(() => {
    if (track) return track;
    if (!selection) return null;
    return {
      ...emptyTrack,
      id: selection.track.trackId,
      title: selection.track.title,
      artist: selection.track.artist,
      artistUserId: selection.track.artistUserId,
      durationSeconds: selection.track.durationSeconds,
      audioUrl: selection.track.audioUrl,
      previewUrl: selection.track.audioUrl,
      coverArtUrl: selection.track.coverArtUrl,
      licenseLabel: selection.track.licenseLabel,
      moderationStatus: selection.track.moderationStatus
    };
  }, [track, selection]);

  const draft: CreatorMusicSelection | null = useMemo(() => {
    if (!tuningTrack || !mixer) return null;
    return createCreatorMusicSelection(tuningTrack, mixer, startOffset);
  }, [tuningTrack, mixer, startOffset]);

  // Live wants every fader move immediately; the camera wants nothing until the
  // creator commits. Firing on the draft rather than inside each handler means a
  // preset tap and a fader drag both reach Live without two code paths.
  useEffect(() => {
    if (!visible || !draft || !onSelectionPreview) return;
    onSelectionPreview(draft);
  }, [visible, draft, onSelectionPreview]);

  const maxStartOffset = Math.max(0, (tuningTrack?.durationSeconds || 0) - CREATOR_MUSIC_MIN_TAIL_SECONDS);

  async function openTrack(next: PulseMusicTrack) {
    await stopPreview();
    setTrack(next);
    setStartOffset(clampCreatorMusicStartOffset(0, next.durationSeconds));
    setStage("tune");
    setMessage("");
  }

  async function togglePreview() {
    if (previewing) {
      await stopPreview();
      return;
    }
    const url = tuningTrack?.audioUrl || tuningTrack?.previewUrl || "";
    if (!url) {
      setMessage("This track has no playable audio yet.");
      return;
    }
    const granted = await claimMediaPlayback({
      id: PREVIEW_OWNER,
      kind: "music_preview",
      pause: () => stopPreview(),
      stop: () => stopPreview()
    });
    if (!granted) {
      setMessage("Another audio surface is active. Stop it before previewing.");
      return;
    }
    try {
      previewOffsetRef.current = startOffset;
      const created = await Audio.Sound.createAsync(
        { uri: url },
        {
          shouldPlay: true,
          // Start where the take will start. Previewing from zero is the classic
          // way a creator sets a start point that sounds nothing like what they
          // heard.
          positionMillis: Math.round(startOffset * 1000),
          volume: previewVolumeFor(mixer)
        },
        (status) => {
          if (status.isLoaded && status.didJustFinish) stopPreview().catch(() => undefined);
        }
      );
      previewSound.current = created.sound;
      setPreviewing(true);
      await recordPulseMusicEvent(String(tuningTrack?.id || ""), "play", "native_creator_music").catch(() => undefined);
    } catch (error) {
      await releaseMediaPlayback(PREVIEW_OWNER).catch(() => undefined);
      setMessage(error instanceof Error ? error.message : "Preview could not play.");
    }
  }

  /** Keep what the creator hears matched to the music fader, live. */
  useEffect(() => {
    const sound = previewSound.current;
    if (!sound || !mixer) return;
    sound.setVolumeAsync(previewVolumeFor(mixer)).catch(() => undefined);
  }, [mixer]);

  /** Re-cue the preview when the start point moves, so the slider is audible. */
  useEffect(() => {
    const sound = previewSound.current;
    if (!sound || !previewing) return;
    if (Math.abs(previewOffsetRef.current - startOffset) < 0.25) return;
    previewOffsetRef.current = startOffset;
    sound.setPositionAsync(Math.round(startOffset * 1000)).catch(() => undefined);
  }, [startOffset, previewing]);

  function updateMixer(next: CreatorMixerSettings) {
    setMixer(next);
    saveCreatorMixerSettings(next).catch(() => undefined);
  }

  async function confirm() {
    if (!draft) return;
    await stopPreview();
    if (tuningTrack) await rememberCreatorMusicTrack(tuningTrack).catch(() => undefined);
    onSelect(draft);
  }

  async function remove() {
    await stopPreview();
    onRemove();
  }

  async function close() {
    await stopPreview();
    onClose();
  }

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={() => void close()}>
      <View style={styles.backdrop}>
        <Pressable style={styles.backdropFill} onPress={() => void close()} accessibilityLabel="Dismiss music picker" />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + 16 }]}>
          <View style={styles.handle} />
          <View style={styles.header}>
            {stage === "tune" ? (
              <Pressable
                accessibilityRole="button"
                style={styles.headerButton}
                onPress={() => {
                  void stopPreview();
                  setStage("browse");
                }}
              >
                <Text style={styles.headerButtonText}>Back</Text>
              </Pressable>
            ) : (
              <View style={styles.headerSpacer} />
            )}
            <Text style={styles.title}>{stage === "tune" ? "Mix" : title}</Text>
            <Pressable accessibilityRole="button" style={styles.headerButton} onPress={() => void close()}>
              <Text style={styles.headerButtonText}>Close</Text>
            </Pressable>
          </View>

          {stage === "browse" ? (
            <View style={styles.browse}>
              <TextInput
                style={styles.search}
                value={query}
                onChangeText={setQuery}
                placeholder="Search PulseSoc Music"
                placeholderTextColor={colors.muted}
                autoCorrect={false}
                returnKeyType="search"
                accessibilityLabel="Search PulseSoc Music"
              />

              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                {CREATOR_MUSIC_LANES.map((item) => (
                  <Pressable
                    key={item.key}
                    accessibilityRole="button"
                    accessibilityState={{ selected: lane === item.key }}
                    style={[styles.chip, lane === item.key && styles.chipActive]}
                    onPress={() => setLane(item.key)}
                  >
                    <Text style={[styles.chipText, lane === item.key && styles.chipTextActive]}>{item.label}</Text>
                  </Pressable>
                ))}
              </ScrollView>

              {genres.length ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityState={{ selected: !genre }}
                    style={[styles.chipSmall, !genre && styles.chipActive]}
                    onPress={() => setGenre("")}
                  >
                    <Text style={[styles.chipText, !genre && styles.chipTextActive]}>All genres</Text>
                  </Pressable>
                  {genres.map((item) => (
                    <Pressable
                      key={item}
                      accessibilityRole="button"
                      accessibilityState={{ selected: genre === item }}
                      style={[styles.chipSmall, genre === item && styles.chipActive]}
                      onPress={() => setGenre((current) => (current === item ? "" : item))}
                    >
                      <Text style={[styles.chipText, genre === item && styles.chipTextActive]}>{item}</Text>
                    </Pressable>
                  ))}
                </ScrollView>
              ) : null}

              {offline || message ? (
                <Text style={[styles.notice, offline && styles.noticeWarning]}>{message}</Text>
              ) : null}

              {loading ? (
                <View style={styles.loading}>
                  <ActivityIndicator color={colors.accent} />
                </View>
              ) : (
                <ScrollView style={styles.list} keyboardShouldPersistTaps="handled">
                  {tracks.map((item) => (
                    <Pressable
                      key={item.id}
                      accessibilityRole="button"
                      accessibilityLabel={`${item.title} by ${item.artist}`}
                      style={styles.row}
                      onPress={() => void openTrack(item)}
                    >
                      <View style={styles.rowBody}>
                        <Text style={styles.rowTitle} numberOfLines={1}>
                          {item.title}
                        </Text>
                        <Text style={styles.rowMeta} numberOfLines={1}>
                          {item.artist} · {formatSeconds(item.durationSeconds)} · {item.licenseLabel}
                        </Text>
                      </View>
                      <Text style={styles.rowAction}>Use</Text>
                    </Pressable>
                  ))}
                  {!tracks.length && !message ? <Text style={styles.notice}>No tracks to show.</Text> : null}
                </ScrollView>
              )}
            </View>
          ) : (
            <ScrollView style={styles.tune} contentContainerStyle={styles.tuneContent} keyboardShouldPersistTaps="handled">
              <Text style={styles.trackTitle} numberOfLines={1}>
                {tuningTrack?.title || "Track"}
              </Text>
              <Text style={styles.trackMeta} numberOfLines={1}>
                {tuningTrack?.artist || "PulseSoc Music"} · {formatSeconds(tuningTrack?.durationSeconds || 0)}
              </Text>

              {allowPreview ? (
                <Pressable accessibilityRole="button" style={styles.previewButton} onPress={() => void togglePreview()}>
                  <Text style={styles.previewText}>{previewing ? "Stop preview" : "Preview from start point"}</Text>
                </Pressable>
              ) : (
                <Text style={styles.notice}>You are live — this track is already playing to your viewers. Move the faders and you will hear what they hear.</Text>
              )}

              {message ? <Text style={styles.notice}>{message}</Text> : null}

              {allowStartOffset ? (
                maxStartOffset > 0 ? (
                  <CreatorMixSlider
                    label="Start point"
                    testID="creator-music-start-offset"
                    value={startOffset}
                    minimumValue={0}
                    maximumValue={maxStartOffset}
                    step={0.5}
                    tint={colors.accentStrong}
                    formatValue={(next) => formatSeconds(next)}
                    onChange={setStartOffset}
                  />
                ) : (
                  <Text style={styles.notice}>This track is too short to start anywhere but the beginning.</Text>
                )
              ) : null}

              <View style={styles.presetRow}>
                {[...CREATOR_MIXER_PRESETS, "custom" as CreatorMixerPreset].map((preset) => {
                  const active = mixer?.preset === preset;
                  const disabled = preset === "custom";
                  return (
                    <Pressable
                      key={preset}
                      accessibilityRole="button"
                      accessibilityState={{ selected: active, disabled }}
                      disabled={disabled}
                      style={[styles.preset, active && styles.presetActive, disabled && styles.presetReadonly]}
                      onPress={() => updateMixer(applyCreatorMixerPreset(preset))}
                    >
                      <Text style={[styles.presetText, active && styles.presetTextActive]}>{presetLabel(preset)}</Text>
                    </Pressable>
                  );
                })}
              </View>

              {mixer ? (
                <>
                  <CreatorMixSlider
                    label="Music"
                    testID="creator-music-level"
                    value={creatorMixLevelToPercent(mixer.musicLevel)}
                    minimumValue={0}
                    maximumValue={100}
                    step={1}
                    formatValue={(next) => `${Math.round(next)}%`}
                    onChange={(next) => updateMixer(withMusicLevel(mixer, creatorMixPercentToLevel(next)))}
                  />
                  <CreatorMixSlider
                    label="Microphone"
                    testID="creator-mic-level"
                    value={creatorMixLevelToPercent(mixer.micLevel)}
                    minimumValue={0}
                    maximumValue={100}
                    step={1}
                    tint={colors.creator}
                    formatValue={(next) => `${Math.round(next)}%`}
                    onChange={(next) => updateMixer(withMicLevel(mixer, creatorMixPercentToLevel(next)))}
                  />

                  <Pressable
                    accessibilityRole="switch"
                    accessibilityState={{ checked: mixer.ducking.enabled }}
                    style={styles.toggleRow}
                    onPress={() => updateMixer(withDucking(mixer, { enabled: !mixer.ducking.enabled }))}
                  >
                    <View style={styles.toggleBody}>
                      <Text style={styles.toggleTitle}>Duck music under my voice</Text>
                      <Text style={styles.toggleHint}>
                        Steps the music back while you speak and brings it up again when you stop.
                      </Text>
                    </View>
                    <View style={[styles.toggleTrack, mixer.ducking.enabled && styles.toggleTrackOn]}>
                      <View style={[styles.toggleKnob, mixer.ducking.enabled && styles.toggleKnobOn]} />
                    </View>
                  </Pressable>

                  {mixer.ducking.enabled ? (
                    <CreatorMixSlider
                      label="Duck depth"
                      testID="creator-duck-depth"
                      value={mixer.ducking.depthDb}
                      minimumValue={0}
                      maximumValue={18}
                      step={0.5}
                      tint={colors.warning}
                      formatValue={(next) => `${next.toFixed(1)} dB`}
                      onChange={(next) => updateMixer(withDucking(mixer, { depthDb: next }))}
                    />
                  ) : null}

                  <Text style={styles.gainLine}>
                    Music bus {formatDb(creatorMusicBusGainDb(mixer))} · Mic bus {formatDb(creatorMicBusGainDb(mixer))}
                  </Text>
                </>
              ) : (
                <View style={styles.loading}>
                  <ActivityIndicator color={colors.accent} />
                </View>
              )}

              <Pressable
                accessibilityRole="button"
                accessibilityState={{ disabled: !draft }}
                style={[styles.primaryButton, !draft && styles.primaryDisabled]}
                disabled={!draft}
                onPress={() => void confirm()}
              >
                <Text style={styles.primaryText}>{confirmLabel}</Text>
              </Pressable>

              {selection ? (
                <Pressable accessibilityRole="button" style={styles.removeButton} onPress={() => void remove()}>
                  <Text style={styles.removeText}>Remove music</Text>
                </Pressable>
              ) : null}
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
}

/**
 * Preview loudness follows the music fader but not the headroom trim.
 *
 * The trim exists so two sources can sum without clipping; applying it to a
 * solo preview would just make every track sound quiet and push creators to
 * over-set the fader to compensate — which is precisely the mistake the trim was
 * there to prevent.
 */
function previewVolumeFor(mixer: CreatorMixerSettings | null) {
  if (!mixer) return 0.8;
  return Math.max(0, Math.min(creatorMixLevelToLinearGain(mixer.musicLevel), 1));
}

function presetLabel(preset: CreatorMixerPreset) {
  if (preset === "voice_focus") return "Voice";
  if (preset === "music_focus") return "Music";
  if (preset === "custom") return "Custom";
  return "Balanced";
}

function formatSeconds(value: number) {
  const total = Math.max(0, Math.round(Number(value) || 0));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatDb(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)} dB`;
}

const emptyTrack: PulseMusicTrack = {
  id: "",
  title: "",
  artist: "",
  artistUserId: 0,
  durationSeconds: 0,
  previewUrl: "",
  audioUrl: "",
  coverArtUrl: "",
  waveform: [],
  genre: "",
  language: "",
  mood: "",
  licenseLabel: "",
  moderationStatus: "",
  approvedByAdmin: false,
  active: true,
  playCount: 0,
  usageCount: 0,
  trendScore: 0,
  saveCount: 0,
  shareCount: 0
};

const styles = StyleSheet.create({
  backdrop: {
    backgroundColor: "rgba(2, 6, 12, 0.72)",
    flex: 1,
    justifyContent: "flex-end"
  },
  backdropFill: {
    flex: 1
  },
  browse: {
    maxHeight: 460
  },
  chip: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 18,
    borderWidth: 1,
    marginRight: 8,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  chipActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  chipRow: {
    paddingBottom: 10,
    paddingRight: 8
  },
  chipSmall: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 14,
    borderWidth: 1,
    marginRight: 8,
    paddingHorizontal: 11,
    paddingVertical: 6
  },
  chipText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600"
  },
  chipTextActive: {
    color: colors.text
  },
  gainLine: {
    color: colors.muted,
    fontSize: 12,
    fontVariant: ["tabular-nums"],
    marginTop: 12
  },
  handle: {
    alignSelf: "center",
    backgroundColor: colors.border,
    borderRadius: 3,
    height: 5,
    marginBottom: 12,
    width: 44
  },
  header: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 14
  },
  headerButton: {
    minWidth: 56,
    paddingVertical: 4
  },
  headerButtonText: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "600"
  },
  headerSpacer: {
    minWidth: 56
  },
  list: {
    maxHeight: 320
  },
  loading: {
    paddingVertical: 28
  },
  notice: {
    color: colors.muted,
    fontSize: 13,
    paddingVertical: 10
  },
  noticeWarning: {
    color: colors.warning
  },
  preset: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    flex: 1,
    marginRight: 8,
    paddingVertical: 10
  },
  presetActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  presetReadonly: {
    opacity: 0.75
  },
  presetRow: {
    flexDirection: "row",
    marginBottom: 6,
    marginTop: 14
  },
  presetText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600",
    textAlign: "center"
  },
  presetTextActive: {
    color: colors.text
  },
  previewButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 14,
    paddingVertical: 12
  },
  previewText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600"
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 14,
    marginTop: 20,
    paddingVertical: 14
  },
  primaryDisabled: {
    backgroundColor: colors.disabled
  },
  primaryText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "700"
  },
  removeButton: {
    alignItems: "center",
    paddingVertical: 14
  },
  removeText: {
    color: colors.danger,
    fontSize: 14,
    fontWeight: "600"
  },
  row: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    paddingVertical: 12
  },
  rowAction: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "700",
    marginLeft: 12
  },
  rowBody: {
    flex: 1
  },
  rowMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 3
  },
  rowTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "600"
  },
  search: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    color: colors.text,
    marginBottom: 12,
    paddingHorizontal: 14,
    paddingVertical: 11
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    paddingHorizontal: 18,
    paddingTop: 10
  },
  title: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "700"
  },
  toggleBody: {
    flex: 1,
    paddingRight: 12
  },
  toggleHint: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 3
  },
  toggleKnob: {
    backgroundColor: colors.muted,
    borderRadius: 11,
    height: 22,
    width: 22
  },
  toggleKnobOn: {
    backgroundColor: colors.background,
    transform: [{ translateX: 20 }]
  },
  toggleRow: {
    alignItems: "center",
    flexDirection: "row",
    marginTop: 16
  },
  toggleTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600"
  },
  toggleTrack: {
    backgroundColor: colors.border,
    borderRadius: 14,
    height: 28,
    justifyContent: "center",
    paddingHorizontal: 3,
    width: 48
  },
  toggleTrackOn: {
    backgroundColor: colors.accent
  },
  trackMeta: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 4
  },
  trackTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "700"
  },
  tune: {
    maxHeight: 500
  },
  tuneContent: {
    paddingBottom: 12
  }
});
