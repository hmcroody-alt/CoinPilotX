import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { Audio } from "expo-av";
import * as DocumentPicker from "expo-document-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View
} from "react-native";
import {
  getPulseMusicTrack,
  loadCachedPulseMusicSnapshot,
  PulseMusicLane,
  PulseMusicTrack,
  PulseMusicUploadAsset,
  pulseMusicWebUrl,
  recordPulseMusicEvent,
  reportPulseMusic,
  searchPulseMusic,
  selectPulseMusicForSurface,
  uploadPulseMusic
} from "../api/music";
import { getMyProfile, PulseProfile } from "../api/profile";
import {
  cyclePulseRadioRepeatMode,
  getPulseRadioState,
  playNextTrack,
  playPreviousTrack,
  PulseRadioState,
  seekPulseRadioBy,
  subscribePulseRadio,
  togglePulseRadio,
  togglePulseRadioShuffle
} from "../core/pulseRadio";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type Props = NativeStackScreenProps<RootStackParamList, "Music">;

type UploadDraft = {
  audio: PulseMusicUploadAsset | null;
  cover: PulseMusicUploadAsset | null;
  title: string;
  artist: string;
  genre: string;
  language: string;
  mood: string;
  description: string;
  tags: string;
  rightsConfirmed: boolean;
};

/**
 * `label` holds a catalog key, not display text — it is resolved with `t` at
 * render time so the lane chips re-label themselves the moment the language
 * changes, instead of freezing whatever language was active at module load.
 */
const lanes: Array<{ key: PulseMusicLane; label: string }> = [
  { key: "", label: "discovery:music.laneBestMatch" },
  { key: "trending", label: "discovery:music.laneTrending" },
  { key: "new", label: "discovery:music.laneNewReleases" }
];

const emptyDraft: UploadDraft = {
  audio: null,
  cover: null,
  title: "",
  artist: "",
  genre: "",
  language: "",
  mood: "",
  description: "",
  tags: "",
  rightsConfirmed: false
};

const PREVIEW_OWNER = "native-music-preview";

export function MusicScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const { authState } = useAuth();
  // Wrong-subject guard: this screen is viewer-scoped (uploads, "your" radio,
  // getMyProfile). Reached with another profile's route params it must refuse
  // rather than render the signed-in viewer's data under that person's name.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const initialTrackId = String(route.params?.trackId || route.params?.track || "");
  const [tracks, setTracks] = useState<PulseMusicTrack[]>([]);
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [query, setQuery] = useState("");
  const [genre, setGenre] = useState("");
  const [language, setLanguage] = useState("");
  const [mood, setMood] = useState("");
  const [lane, setLane] = useState<PulseMusicLane>(initialTrackId ? "" : "trending");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [message, setMessage] = useState("");
  const [busyTrackId, setBusyTrackId] = useState("");
  const [previewingTrackId, setPreviewingTrackId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [draft, setDraft] = useState<UploadDraft>(emptyDraft);
  const [radio, setRadio] = useState<PulseRadioState>(getPulseRadioState());
  const [deepLinkedTrack, setDeepLinkedTrack] = useState<PulseMusicTrack | null>(null);
  const [deepLinkFailure, setDeepLinkFailure] = useState<"" | "missing" | "unreachable">("");
  const previewSound = useRef<Audio.Sound | null>(null);
  const deepLinkAttemptedFor = useRef("");

  const focusedTracks = useMemo(() => {
    if (!initialTrackId) return tracks;
    const focusIndex = tracks.findIndex((track) => track.id === initialTrackId);
    // Absent from the ranked pool: show the track that was actually tapped,
    // fetched by id, rather than an unrelated browse list under its name.
    if (focusIndex < 0) return deepLinkedTrack ? [deepLinkedTrack, ...tracks] : tracks;
    if (focusIndex === 0) return tracks;
    return [tracks[focusIndex], ...tracks.slice(0, focusIndex), ...tracks.slice(focusIndex + 1)];
  }, [deepLinkedTrack, initialTrackId, tracks]);

  // Derived, not stored: a late lookup result can never leave "we couldn't find
  // it" on screen next to the track it is talking about.
  const deepLinkNotice = useMemo(() => {
    if (!initialTrackId || !deepLinkFailure) return "";
    if (focusedTracks.some((track) => track.id === initialTrackId)) return "";
    return deepLinkFailure === "missing"
      ? "That song is no longer available in PulseSoc Music."
      : "That song could not be loaded. Browse the library below.";
  }, [deepLinkFailure, focusedTracks, initialTrackId]);

  const uploaderName = profile?.display_name || profile?.username || "";
  const uploadReadyHint = Boolean(
    profile?.verified_badge ||
      String(profile?.premium_status || "").toLowerCase().includes("premium") ||
      String(profile?.premium_status || "").toLowerCase().includes("founder") ||
      Number((profile as Record<string, unknown> | null)?.email_verified || 0)
  );

  const load = useCallback(
    async (mode: "initial" | "refresh" | "search" = "initial") => {
      setMessage("");
      setOffline(false);
      if (mode === "initial") setLoading(true);
      if (mode === "refresh") setRefreshing(true);
      try {
        const result = await searchPulseMusic({ query, genre, language, mood, lane, limit: 40 });
        setTracks(result.tracks);
        if (!result.tracks.length) setMessage(t("discovery:music.noMatches"));
      } catch (error) {
        const cached = await loadCachedPulseMusicSnapshot();
        if (cached.length) {
          setTracks(cached);
          setOffline(true);
          setMessage(t("discovery:music.offlineNotice"));
        } else {
          setMessage(error instanceof Error ? error.message : t("discovery:music.loadError"));
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [genre, language, lane, mood, query, t]
  );

  useEffect(() => {
    // Owner-only: never call the viewer's /api/me profile while the route says
    // the subject is someone else (skip entirely — no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    getMyProfile()
      .then((next) => {
        setProfile(next);
        setDraft((current) => (current.artist ? current : { ...current, artist: next.display_name || next.username || "" }));
      })
      .catch(() => undefined);
  }, [routeContext.isOwnProfile]);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => load("search").catch(() => undefined), 360);
    return () => clearTimeout(timer);
  }, [query, genre, language, mood, lane]);

  // A deep link names one song; the search pool is a ranked slice that may not
  // contain it. Resolve it by id so the tap always lands on what was tapped,
  // and say so plainly when the catalog no longer has it.
  useEffect(() => {
    if (!initialTrackId || loading) return;
    if (tracks.some((track) => track.id === initialTrackId)) return;
    if (deepLinkAttemptedFor.current === initialTrackId) return;
    deepLinkAttemptedFor.current = initialTrackId;
    let cancelled = false;
    getPulseMusicTrack(initialTrackId)
      .then((track) => {
        if (cancelled) return;
        setDeepLinkedTrack(track);
        setDeepLinkFailure(track ? "" : "missing");
      })
      .catch(() => {
        if (cancelled) return;
        deepLinkAttemptedFor.current = "";
        setDeepLinkFailure("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, [initialTrackId, loading, tracks]);

  useEffect(() => {
    return () => {
      stopPreview().catch(() => undefined);
    };
  }, []);

  useEffect(() => subscribePulseRadio(setRadio), []);

  async function stopPreview() {
    const sound = previewSound.current;
    previewSound.current = null;
    setPreviewingTrackId("");
    if (sound) await sound.unloadAsync().catch(() => undefined);
    await releaseMediaPlayback(PREVIEW_OWNER).catch(() => undefined);
  }

  async function previewTrack(track: PulseMusicTrack) {
    if (previewingTrackId === track.id) {
      await stopPreview();
      return;
    }
    const url = track.previewUrl || track.audioUrl;
    if (!url) {
      setMessage(t("discovery:music.previewUnavailable"));
      return;
    }
    await stopPreview();
    const granted = await claimMediaPlayback({ id: PREVIEW_OWNER, kind: "music_preview", pause: () => stopPreview(), stop: () => stopPreview() });
    if (!granted) {
      setMessage(t("discovery:music.previewBlocked"));
      return;
    }
    setBusyTrackId(track.id);
    setMessage(t("discovery:music.previewing", { title: track.title }));
    try {
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true, staysActiveInBackground: false });
      const created = await Audio.Sound.createAsync({ uri: url }, { shouldPlay: true, volume: 0.82 }, (status) => {
        if (status.isLoaded && status.didJustFinish) stopPreview().catch(() => undefined);
      });
      previewSound.current = created.sound;
      setPreviewingTrackId(track.id);
      await recordPulseMusicEvent(track.id, "play", "native_music_library").catch(() => undefined);
    } catch (error) {
      await releaseMediaPlayback(PREVIEW_OWNER).catch(() => undefined);
      setMessage(error instanceof Error ? error.message : t("discovery:music.previewFailed"));
    } finally {
      setBusyTrackId("");
    }
  }

  async function saveTrack(track: PulseMusicTrack) {
    setBusyTrackId(track.id);
    setMessage("");
    try {
      await recordPulseMusicEvent(track.id, "save", "native_music_library");
      setTracks((current) => current.map((item) => (item.id === track.id ? { ...item, saveCount: item.saveCount + 1 } : item)));
      setMessage(t("discovery:music.saveSuccess"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("discovery:music.saveFailed"));
    } finally {
      setBusyTrackId("");
    }
  }

  async function shareTrack(track: PulseMusicTrack) {
    setBusyTrackId(track.id);
    setMessage("");
    try {
      await recordPulseMusicEvent(track.id, "share", "native_music_library").catch(() => undefined);
      await Share.share({ title: track.title, message: `${track.title} · ${track.artist}\n${pulseMusicWebUrl(track.id)}` });
      setMessage(t("discovery:music.shareOpened"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("discovery:music.shareFailed"));
    } finally {
      setBusyTrackId("");
    }
  }

  async function reportTrack(track: PulseMusicTrack) {
    Alert.alert(t("discovery:music.reportTitle"), t("discovery:music.reportBody", { title: track.title }), [
      { text: t("discovery:music.reportCancel"), style: "cancel" },
      {
        text: t("discovery:music.reportConfirm"),
        style: "destructive",
        onPress: () => {
          setBusyTrackId(track.id);
          reportPulseMusic(track.id)
            .then(() => setMessage(t("discovery:music.reportSent")))
            .catch((error) => setMessage(error instanceof Error ? error.message : t("discovery:music.reportFailed")))
            .finally(() => setBusyTrackId(""));
        }
      }
    ]);
  }

  async function useTrack(track: PulseMusicTrack, surface: "reel" | "video" | "status" | "post") {
    const composerSurface = surface === "video" ? "post" : surface;
    await selectPulseMusicForSurface(track, composerSurface);
    // One key per complete sentence: the surface name is part of the sentence,
    // not a fragment to splice into it.
    const selectedKey =
      composerSurface === "post"
        ? "discovery:music.selectedForFeedComposer"
        : composerSurface === "reel"
          ? "discovery:music.selectedForReel"
          : "discovery:music.selectedForStatus";
    setMessage(t(selectedKey, { title: track.title }));
    navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true, composerMode: composerSurface } });
  }

  async function pickAudio() {
    const result = await DocumentPicker.getDocumentAsync({
      copyToCacheDirectory: true,
      multiple: false,
      type: ["audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac", "audio/m4a", "audio/*"]
    });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];
    setDraft((current) => ({
      ...current,
      audio: {
        uri: asset.uri,
        name: asset.name || `pulsesoc-music-${Date.now()}.m4a`,
        mimeType: normalizeAudioMime(asset.mimeType || asset.name || ""),
        size: asset.size || 0
      },
      title: current.title || titleFromFilename(asset.name || "")
    }));
    setMessage(t("discovery:music.audioSelected"));
  }

  async function pickCover() {
    const result = await DocumentPicker.getDocumentAsync({
      copyToCacheDirectory: true,
      multiple: false,
      type: ["image/jpeg", "image/png", "image/webp"]
    });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];
    setDraft((current) => ({
      ...current,
      cover: {
        uri: asset.uri,
        name: asset.name || `pulsesoc-cover-${Date.now()}.jpg`,
        mimeType: asset.mimeType || "image/jpeg",
        size: asset.size || 0
      }
    }));
    setMessage(t("discovery:music.coverSelected"));
  }

  async function uploadDraft() {
    if (!draft.audio || uploading) return;
    setUploading(true);
    setMessage(t("discovery:music.uploadingMessage"));
    try {
      const result = await uploadPulseMusic({
        audio: draft.audio,
        cover: draft.cover,
        title: draft.title,
        artist: draft.artist || uploaderName || "PulseSoc Artist",
        genre: draft.genre,
        language: draft.language,
        mood: draft.mood,
        description: draft.description,
        tags: draft.tags,
        rightsConfirmed: draft.rightsConfirmed
      });
      setMessage(result.message || t("discovery:music.uploadSuccess"));
      setDraft({ ...emptyDraft, artist: uploaderName });
      await load("refresh");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("discovery:music.uploadFailed"));
    } finally {
      setUploading(false);
    }
  }

  // Visitor destination with no visitor variant: the only correct rendering is
  // a refusal. Hooks above have all run, so hook order stays stable.
  if (!routeContext.isOwnProfile) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{PRIVATE_CONTENT_MESSAGE}</Text>
      </View>
    );
  }

  if (loading && !tracks.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("discovery:music.loadingTitle")}</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        data={focusedTracks}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.hero}>
              <View style={styles.heroCopy}>
                <Text style={styles.kicker}>{t("discovery:music.heroKicker")}</Text>
                <Text style={styles.title}>{t("discovery:music.heroTitle")}</Text>
                <Text style={styles.subtitle}>{t("discovery:music.heroSubtitle")}</Text>
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={radio.status === "playing" ? "Pause PulseSoc Radio" : radio.userWantsPlayback && radio.interruptedBy ? "Keep PulseSoc Radio paused" : "Play PulseSoc Radio"}
                accessibilityHint={radio.userWantsPlayback && radio.interruptedBy ? "Prevents PulseSoc Radio from resuming after active audio ends." : "PulseSoc Radio keeps playing as you move around the app."}
                style={styles.radioCard}
                onPress={() => togglePulseRadio().catch((error) => setMessage(error instanceof Error ? error.message : t("discovery:music.radioStartFailed")))}
              >
                <Text style={styles.radioIcon}>{radio.status === "playing" ? "Ⅱ" : radio.status === "connecting" || radio.status === "buffering" ? "…" : "▶"}</Text>
                <View style={styles.radioCopy}>
                  <Text style={styles.radioTitle}>{t("discovery:music.radioTitle")}</Text>
                  <Text style={styles.radioBody} numberOfLines={2}>{radio.message || t("discovery:music.radioBodyFallback")}</Text>
                  <Waveform waveform={radio.track ? [0.18, 0.38, 0.66, 0.42, 0.72, 0.5, 0.3, 0.58] : [0.12, 0.22, 0.3, 0.18, 0.28, 0.2]} active={radio.status === "playing" || radio.status === "buffering"} />
                </View>
              </Pressable>

              {radio.track ? (
                <View style={styles.radioControls}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("discovery:music.previousTrackLabel")}
                    testID="music-radio-previous"
                    style={styles.radioControlButton}
                    onPress={() => playPreviousTrack().catch(() => undefined)}
                  >
                    <Ionicons name="play-skip-back" size={18} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("discovery:music.seekBackwardLabel")}
                    testID="music-radio-seek-back"
                    style={styles.radioControlButton}
                    onPress={() => seekPulseRadioBy(-15000).catch(() => undefined)}
                  >
                    <Ionicons name="play-back" size={16} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("discovery:music.seekForwardLabel")}
                    testID="music-radio-seek-forward"
                    style={styles.radioControlButton}
                    onPress={() => seekPulseRadioBy(15000).catch(() => undefined)}
                  >
                    <Ionicons name="play-forward" size={16} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("discovery:music.nextTrackLabel")}
                    testID="music-radio-next"
                    style={styles.radioControlButton}
                    onPress={() => playNextTrack().catch(() => undefined)}
                  >
                    <Ionicons name="play-skip-forward" size={18} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={radio.shuffle ? t("discovery:music.disableShuffleLabel") : t("discovery:music.enableShuffleLabel")}
                    testID="music-radio-shuffle"
                    style={[styles.radioControlButton, radio.shuffle && styles.radioControlButtonActive]}
                    onPress={() => togglePulseRadioShuffle()}
                  >
                    <Ionicons name="shuffle" size={16} color={radio.shuffle ? colors.background : colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={
                      radio.repeatMode === "one"
                        ? t("discovery:music.repeatOneLabel")
                        : radio.repeatMode === "queue"
                          ? t("discovery:music.repeatQueueLabel")
                          : t("discovery:music.repeatOffLabel")
                    }
                    testID="music-radio-repeat"
                    style={[styles.radioControlButton, radio.repeatMode !== "off" && styles.radioControlButtonActive]}
                    onPress={() => cyclePulseRadioRepeatMode()}
                  >
                    <Ionicons
                      name={radio.repeatMode === "one" ? "repeat-outline" : "repeat"}
                      size={16}
                      color={radio.repeatMode !== "off" ? colors.background : colors.text}
                    />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("discovery:music.openQueueLabel")}
                    testID="music-radio-open-queue"
                    style={[styles.radioControlButton, styles.radioControlButtonWide]}
                    onPress={() => navigation.navigate("PulseQueue", { title: t("discovery:music.queueLabel") })}
                  >
                    <Ionicons name="list" size={16} color={colors.text} />
                    <Text style={styles.radioControlLabel}>{t("discovery:music.queueLabel")}</Text>
                  </Pressable>
                </View>
              ) : null}
            </View>

            <View style={styles.uploadPanel}>
              <View style={styles.panelHeader}>
                <View>
                  <Text style={styles.panelKicker}>{t("discovery:music.uploadKicker")}</Text>
                  <Text style={styles.panelTitle}>{t("discovery:music.uploadForReview")}</Text>
                </View>
                <Text style={[styles.statusPill, uploadReadyHint && styles.statusPillReady]}>{uploadReadyHint ? t("discovery:music.statusReady") : t("discovery:music.statusServerVerified")}</Text>
              </View>
              <Text style={styles.muted}>{t("discovery:music.uploadSupported")}</Text>
              <View style={styles.fileRow}>
                <Pressable accessibilityRole="button" accessibilityLabel={t("discovery:music.chooseAudioLabel")} style={styles.fileButton} disabled={uploading} onPress={() => pickAudio().catch((error) => setMessage(error instanceof Error ? error.message : t("discovery:music.audioPickerFailed")))}>
                  <Text style={styles.fileButtonTitle}>{t("discovery:music.audioFileTitle")}</Text>
                  <Text style={styles.fileButtonMeta} numberOfLines={1}>{draft.audio?.name || t("discovery:music.audioFileHint")}</Text>
                </Pressable>
                <Pressable accessibilityRole="button" accessibilityLabel={t("discovery:music.chooseCoverLabel")} style={styles.fileButton} disabled={uploading} onPress={() => pickCover().catch((error) => setMessage(error instanceof Error ? error.message : t("discovery:music.coverPickerFailed")))}>
                  <Text style={styles.fileButtonTitle}>{t("discovery:music.coverArtworkTitle")}</Text>
                  <Text style={styles.fileButtonMeta} numberOfLines={1}>{draft.cover?.name || t("discovery:music.coverArtworkHint")}</Text>
                </Pressable>
              </View>
              <TextInput style={styles.input} value={draft.title} onChangeText={(title) => setDraft((current) => ({ ...current, title }))} placeholder={t("discovery:music.songTitlePlaceholder")} placeholderTextColor={colors.muted} editable={!uploading} />
              <TextInput style={styles.input} value={draft.artist} onChangeText={(artist) => setDraft((current) => ({ ...current, artist }))} placeholder={t("discovery:music.artistNamePlaceholder")} placeholderTextColor={colors.muted} editable={!uploading} />
              <View style={styles.inputGrid}>
                <TextInput style={[styles.input, styles.gridInput]} value={draft.genre} onChangeText={(genre) => setDraft((current) => ({ ...current, genre }))} placeholder={t("discovery:music.genrePlaceholder")} placeholderTextColor={colors.muted} editable={!uploading} />
                <TextInput style={[styles.input, styles.gridInput]} value={draft.language} onChangeText={(nextLanguage) => setDraft((current) => ({ ...current, language: nextLanguage }))} placeholder={t("discovery:music.languagePlaceholder")} placeholderTextColor={colors.muted} editable={!uploading} />
                <TextInput style={[styles.input, styles.gridInput]} value={draft.mood} onChangeText={(nextMood) => setDraft((current) => ({ ...current, mood: nextMood }))} placeholder={t("discovery:music.moodPlaceholder")} placeholderTextColor={colors.muted} editable={!uploading} />
              </View>
              <TextInput style={[styles.input, styles.textArea]} value={draft.description} onChangeText={(description) => setDraft((current) => ({ ...current, description }))} placeholder={t("discovery:music.descriptionPlaceholder")} placeholderTextColor={colors.muted} multiline editable={!uploading} />
              <TextInput style={styles.input} value={draft.tags} onChangeText={(tags) => setDraft((current) => ({ ...current, tags }))} placeholder={t("discovery:music.tagsPlaceholder")} placeholderTextColor={colors.muted} editable={!uploading} />
              <View style={styles.rightsRow}>
                <Switch value={draft.rightsConfirmed} onValueChange={(rightsConfirmed) => setDraft((current) => ({ ...current, rightsConfirmed }))} disabled={uploading} thumbColor={draft.rightsConfirmed ? colors.accent : colors.muted} trackColor={{ false: colors.border, true: colors.signalDim }} />
                <Text style={styles.rightsText}>{t("discovery:music.rightsConfirmation")}</Text>
              </View>
              <Pressable accessibilityRole="button" accessibilityLabel={t("discovery:music.uploadButtonLabel")} style={[styles.uploadButton, (!draft.audio || uploading) && styles.disabled]} disabled={!draft.audio || uploading} onPress={uploadDraft}>
                {uploading ? <ActivityIndicator color={colors.background} /> : <Text style={styles.uploadButtonText}>{t("discovery:music.uploadForReview")}</Text>}
              </Pressable>
            </View>

            <View style={styles.searchPanel}>
              <Text style={styles.panelKicker}>{t("discovery:music.libraryKicker")}</Text>
              <View style={styles.searchRow}>
                <TextInput style={styles.searchInput} value={query} onChangeText={setQuery} placeholder={t("discovery:music.searchPlaceholder")} placeholderTextColor={colors.muted} returnKeyType="search" onSubmitEditing={() => load("search").catch(() => undefined)} />
                <Pressable accessibilityRole="button" accessibilityLabel={t("discovery:music.searchLabel")} style={styles.searchButton} onPress={() => load("search").catch(() => undefined)}>
                  <Text style={styles.searchButtonText}>{t("discovery:music.searchButton")}</Text>
                </Pressable>
              </View>
              <View style={styles.inputGrid}>
                <TextInput style={[styles.input, styles.gridInput]} value={genre} onChangeText={setGenre} placeholder={t("discovery:music.genrePlaceholder")} placeholderTextColor={colors.muted} />
                <TextInput style={[styles.input, styles.gridInput]} value={language} onChangeText={setLanguage} placeholder={t("discovery:music.languagePlaceholder")} placeholderTextColor={colors.muted} />
                <TextInput style={[styles.input, styles.gridInput]} value={mood} onChangeText={setMood} placeholder={t("discovery:music.moodPlaceholder")} placeholderTextColor={colors.muted} />
              </View>
              <View style={styles.laneRow}>
                {lanes.map((item) => (
                  <Pressable key={item.key || "best"} style={[styles.laneChip, lane === item.key && styles.laneChipActive]} onPress={() => setLane(item.key)} accessibilityRole="button" accessibilityState={{ selected: lane === item.key }}>
                    <Text style={[styles.laneText, lane === item.key && styles.laneTextActive]}>{t(item.label)}</Text>
                  </Pressable>
                ))}
              </View>
              {deepLinkNotice ? <Text accessibilityLiveRegion="polite" style={[styles.message, styles.warningMessage]}>{deepLinkNotice}</Text> : null}
              {message ? <Text accessibilityLiveRegion="polite" style={[styles.message, offline && styles.warningMessage]}>{message}</Text> : null}
            </View>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{t("discovery:music.emptyTitle")}</Text>
            <Text style={styles.emptyText}>{t("discovery:music.emptyBody")}</Text>
          </View>
        }
        renderItem={({ item }) => (
          <TrackCard
            track={item}
            busy={busyTrackId === item.id}
            previewing={previewingTrackId === item.id}
            highlighted={initialTrackId === item.id}
            onPreview={previewTrack}
            onSave={saveTrack}
            onShare={shareTrack}
            onReport={reportTrack}
            onUse={useTrack}
          />
        )}
      />
    </View>
  );
}

function TrackCard({
  track,
  busy,
  previewing,
  highlighted,
  onPreview,
  onSave,
  onShare,
  onReport,
  onUse
}: {
  track: PulseMusicTrack;
  busy: boolean;
  previewing: boolean;
  highlighted: boolean;
  onPreview: (track: PulseMusicTrack) => void | Promise<void>;
  onSave: (track: PulseMusicTrack) => void | Promise<void>;
  onShare: (track: PulseMusicTrack) => void | Promise<void>;
  onReport: (track: PulseMusicTrack) => void | Promise<void>;
  onUse: (track: PulseMusicTrack, surface: "reel" | "video" | "status" | "post") => void | Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <View style={[styles.trackCard, highlighted && styles.trackCardHighlighted]}>
      <View style={styles.trackTop}>
        <View style={styles.cover}>
          {track.coverArtUrl ? <Image source={{ uri: track.coverArtUrl }} style={styles.coverImage} /> : <Text style={styles.coverIcon}>♪</Text>}
        </View>
        <View style={styles.trackBody}>
          <Text style={styles.trackTitle} numberOfLines={1}>{track.title}</Text>
          <Text style={styles.trackMeta} numberOfLines={1}>{track.artist} · {track.genre} · {track.language} · {track.mood}</Text>
          <Waveform waveform={track.waveform} active={previewing} />
          <Text style={styles.trackStats}>
            {t("discovery:music.trackStats", {
              plays: track.playCount,
              uses: track.usageCount,
              // Kept as a string: the trend score is a server-side float, and
              // running it through number formatting would round it.
              trend: String(track.trendScore)
            })}
          </Text>
        </View>
      </View>
      <View style={styles.actionRow}>
        <ActionButton label={previewing ? t("discovery:music.actionStop") : t("discovery:music.actionPreview")} disabled={busy} onPress={() => onPreview(track)} />
        <ActionButton label={t("discovery:music.actionSave")} disabled={busy} onPress={() => onSave(track)} />
        <ActionButton label={t("discovery:music.actionShare")} disabled={busy} onPress={() => onShare(track)} />
        <ActionButton label={t("discovery:music.actionReport")} disabled={busy} warning onPress={() => onReport(track)} />
      </View>
      <View style={styles.useRow}>
        <ActionButton label={t("discovery:music.useInReel")} primary onPress={() => onUse(track, "reel")} />
        <ActionButton label={t("discovery:music.useInVideo")} onPress={() => onUse(track, "video")} />
        <ActionButton label={t("discovery:music.useInStatus")} onPress={() => onUse(track, "status")} />
      </View>
    </View>
  );
}

function ActionButton({ label, onPress, primary, warning, disabled }: { label: string; onPress: () => void; primary?: boolean; warning?: boolean; disabled?: boolean }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={disabled} style={[styles.actionButton, primary && styles.actionPrimary, warning && styles.actionWarning, disabled && styles.disabled]} onPress={onPress}>
      <Text style={[styles.actionText, primary && styles.actionPrimaryText, warning && styles.actionWarningText]}>{label}</Text>
    </Pressable>
  );
}

function Waveform({ waveform, active }: { waveform: number[]; active?: boolean }) {
  const bars = waveform.length ? waveform : [0.16, 0.32, 0.48, 0.62, 0.44, 0.7, 0.56, 0.38];
  return (
    <View style={styles.wave} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {bars.slice(0, 16).map((bar, index) => (
        <View key={`${index}-${bar}`} style={[styles.waveBar, { height: 8 + Math.max(0.08, Math.min(bar, 1)) * 22 }, active && styles.waveBarActive]} />
      ))}
    </View>
  );
}

function titleFromFilename(name: string) {
  return String(name || "")
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[-_]+/g, " ")
    .trim();
}

function normalizeAudioMime(value: string) {
  const text = String(value || "").toLowerCase();
  if (text.includes("wav")) return "audio/wav";
  if (text.includes("aac")) return "audio/aac";
  if (text.includes("mp3") || text.includes("mpeg")) return "audio/mpeg";
  return "audio/mp4";
}

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    borderColor: "rgba(121,210,255,0.24)",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexGrow: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  actionPrimary: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  actionPrimaryText: {
    color: colors.background
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  actionText: {
    color: colors.text,
    fontWeight: "900"
  },
  actionWarning: {
    borderColor: "rgba(255,95,126,0.36)"
  },
  actionWarningText: {
    color: colors.danger
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.text,
    fontWeight: "900",
    marginTop: 12
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 116
  },
  cover: {
    alignItems: "center",
    backgroundColor: "rgba(50,230,179,0.12)",
    borderColor: "rgba(50,230,179,0.34)",
    borderRadius: 18,
    borderWidth: 1,
    height: 72,
    justifyContent: "center",
    overflow: "hidden",
    width: 72
  },
  coverIcon: {
    color: colors.accent,
    fontSize: 31,
    fontWeight: "900"
  },
  coverImage: {
    height: "100%",
    width: "100%"
  },
  disabled: {
    opacity: 0.52
  },
  empty: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    padding: 28
  },
  emptyText: {
    color: colors.muted,
    marginTop: 6,
    textAlign: "center"
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  fileButton: {
    borderColor: "rgba(121,210,255,0.24)",
    borderRadius: 18,
    borderWidth: 1,
    flex: 1,
    gap: 5,
    minHeight: 72,
    padding: 12
  },
  fileButtonMeta: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  fileButtonTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  fileRow: {
    flexDirection: "row",
    gap: 10
  },
  gridInput: {
    flex: 1,
    minWidth: 96
  },
  header: {
    gap: 14
  },
  hero: {
    backgroundColor: "rgba(7,18,31,0.88)",
    borderColor: "rgba(50,230,179,0.24)",
    borderRadius: 28,
    borderWidth: 1,
    gap: 16,
    overflow: "hidden",
    padding: 18
  },
  heroCopy: {
    gap: 8
  },
  input: {
    backgroundColor: "rgba(5,12,22,0.72)",
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 15,
    borderWidth: 1,
    color: colors.text,
    minHeight: 48,
    paddingHorizontal: 13,
    paddingVertical: 10
  },
  inputGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 9
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 3,
    textTransform: "uppercase"
  },
  laneChip: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minHeight: 40,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  laneChipActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  laneRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  laneText: {
    color: colors.muted,
    fontWeight: "900"
  },
  laneTextActive: {
    color: colors.accent
  },
  message: {
    color: colors.accent,
    fontWeight: "800",
    lineHeight: 20
  },
  muted: {
    color: colors.muted,
    lineHeight: 20
  },
  panelHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12
  },
  panelKicker: {
    color: colors.accentStrong,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 2,
    textTransform: "uppercase"
  },
  panelTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  radioBody: {
    color: colors.accent,
    fontWeight: "800"
  },
  radioCard: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: "rgba(121,210,255,0.24)",
    borderRadius: 24,
    borderWidth: 1,
    flexDirection: "row",
    gap: 14,
    padding: 14
  },
  radioControlButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 4,
    height: 36,
    justifyContent: "center",
    width: 36
  },
  radioControlButtonActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  radioControlButtonWide: {
    paddingHorizontal: 12,
    width: "auto"
  },
  radioControlLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  radioControls: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10
  },
  radioCopy: {
    flex: 1,
    gap: 4
  },
  radioIcon: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    color: colors.background,
    fontSize: 20,
    fontWeight: "900",
    height: 54,
    lineHeight: 54,
    overflow: "hidden",
    textAlign: "center",
    width: 54
  },
  radioTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    letterSpacing: 2,
    textTransform: "uppercase"
  },
  rightsRow: {
    alignItems: "center",
    backgroundColor: "rgba(243,196,97,0.1)",
    borderColor: "rgba(243,196,97,0.28)",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    padding: 10
  },
  rightsText: {
    color: colors.text,
    flex: 1,
    fontWeight: "800",
    lineHeight: 20
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  searchButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 15,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 16
  },
  searchButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  searchInput: {
    backgroundColor: "rgba(5,12,22,0.72)",
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 15,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    minHeight: 48,
    paddingHorizontal: 13
  },
  searchPanel: {
    backgroundColor: colors.glass,
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 11,
    padding: 14
  },
  searchRow: {
    flexDirection: "row",
    gap: 9
  },
  statusPill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  statusPillReady: {
    borderColor: colors.accent,
    color: colors.accent
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "700",
    lineHeight: 22
  },
  textArea: {
    minHeight: 90,
    textAlignVertical: "top"
  },
  title: {
    color: colors.text,
    fontSize: 29,
    fontWeight: "900",
    lineHeight: 34
  },
  trackBody: {
    flex: 1,
    gap: 4
  },
  trackCard: {
    backgroundColor: "rgba(11,24,34,0.86)",
    borderColor: "rgba(121,210,255,0.18)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 12,
    padding: 13
  },
  trackCardHighlighted: {
    borderColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.25,
    shadowRadius: 12
  },
  trackMeta: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "700"
  },
  trackStats: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  trackTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  trackTop: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12
  },
  uploadButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 18,
    justifyContent: "center",
    minHeight: 50,
    padding: 12
  },
  uploadButtonText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  uploadPanel: {
    backgroundColor: colors.glassStrong,
    borderColor: "rgba(50,230,179,0.24)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 11,
    padding: 14
  },
  useRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  warningMessage: {
    color: colors.warning
  },
  wave: {
    alignItems: "flex-end",
    flexDirection: "row",
    gap: 3,
    height: 34,
    marginTop: 2
  },
  waveBar: {
    backgroundColor: "rgba(97,216,255,0.72)",
    borderRadius: 999,
    width: 4
  },
  waveBarActive: {
    backgroundColor: colors.accent
  }
});
