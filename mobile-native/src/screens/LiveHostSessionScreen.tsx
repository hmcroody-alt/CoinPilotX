import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ComponentType, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  Image,
  InputAccessoryView,
  Keyboard,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import {
  endLive,
  confirmHostLivePublish,
  getLiveRtcToken,
  getLiveState,
  liveWebUrl,
  listGuestManagement,
  listLiveChat,
  moderateLiveChat,
  muteGuest,
  reactToLive,
  removeGuest,
  respondToJoinRequest,
  sendLiveChat,
  unmuteGuest,
  type LiveStageCapacity,
  type PulseLiveChatMessage
} from "../api/live";
import { sharePulseObject } from "../sharing/nativeShare";
import { elapsedLabel, formatViewerCount, type LiveGuest, type LiveGuestRequest } from "../live/liveSession";
import { useLiveBroadcastRoom, type LiveParticipant } from "../live/useLiveBroadcastRoom";
import { RootStackParamList } from "../navigation/types";
import { claimLivePlaybackOwner, releaseLivePlaybackOwner } from "../live/livePlaybackOwnership";
import { colors } from "../theme/colors";
import { useAuth } from "../session/auth";
import { GlassCircleButton, GlassPill, LiveBottomSheet, ToolTile } from "../live/liveHostUi";
import { mergeLiveChat } from "../live/liveEventContinuity";
import { LiveReactionLayer, type ReactionLayerHandle } from "../live/LiveReactionLayer";
import { LiveChatComposer, LiveChatMessageRow, LiveChatStream, type LiveChatModerationAction } from "../live/LiveChatOverlay";
import { RtcVideoView } from "../live/RtcVideoView";
import { listPulseRadioTracks, recordPulseRadioPlay } from "../api/radio";
import { recordPulseMusicEvent, searchPulseMusic, type PulseMusicTrack } from "../api/music";

type NativeVideoViewProps = {
  videoTrack?: any;
  style?: any;
  objectFit?: "cover" | "contain";
  mirror?: boolean;
  zOrder?: number;
};

type SheetKey = "guests" | "comments" | "reactions" | "share" | "music" | "more" | null;
type LayoutMode = "spotlight" | "grid";

const STATE_POLL_MS = 5000;
const CHAT_POLL_MS = 3500;
const COMMENT_ACCESSORY_ID = "pulsesoc-live-comment-accessory";

const REACTIONS: { emoji: string; type: string; label: string }[] = [
  { emoji: "❤️", type: "heart", label: "Love" },
  { emoji: "🔥", type: "fire", label: "Fire" },
  { emoji: "👏", type: "clap", label: "Clap" },
  { emoji: "💜", type: "purple_heart", label: "Vibe" },
  { emoji: "✨", type: "sparkle", label: "Sparkle" },
  { emoji: "🙌", type: "praise", label: "Praise" }
];

const COMING_SOON: Record<string, string> = {
  screen_share: "Screen Share is landing in an upcoming native build.",
  watch_party: "Watch Party co-viewing is coming to native soon.",
  games: "Live Games launch inside the broadcast in a later build.",
  filters: "Camera filters & effects arrive in a later build.",
  replay: "Replays are being saved and will appear here soon."
};

function signalMeta(quality: string) {
  const q = (quality || "").toLowerCase();
  if (q === "excellent" || q === "good") return { color: colors.accent, label: "Strong" };
  if (q === "poor") return { color: colors.warning, label: "Weak" };
  if (q === "lost" || q === "failed") return { color: colors.danger, label: "Lost" };
  return { color: colors.muted, label: "—" };
}

export function LiveHostSessionScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "NativeLiveHost">) {
  const insets = useSafeAreaInsets();
  const { authState } = useAuth();
  const liveId = Number(route.params?.liveId || 0);
  const title = String(route.params?.title || "PulseSoc Live");
  const room = useLiveBroadcastRoom();

  const VideoViewComponent = RtcVideoView as ComponentType<NativeVideoViewProps>;
  const [connecting, setConnecting] = useState(true);
  const [fatalError, setFatalError] = useState("");
  const [viewerCount, setViewerCount] = useState(0);
  const [category, setCategory] = useState("");
  const [requests, setRequests] = useState<LiveGuestRequest[]>([]);
  const [activeGuests, setActiveGuests] = useState<LiveGuest[]>([]);
  // Stages 5 and 40. Null until the server has said something. The panel renders
  // the count it can see until then; it never invents a ceiling.
  const [stage, setStage] = useState<LiveStageCapacity | null>(null);
  const [messages, setMessages] = useState<PulseLiveChatMessage[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [ending, setEnding] = useState(false);
  const [busyRequestId, setBusyRequestId] = useState(0);
  const [busyGuestId, setBusyGuestId] = useState(0);
  const [moderatingId, setModeratingId] = useState(0);
  const [sheet, setSheet] = useState<SheetKey>(null);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("spotlight");
  const [trayExpanded, setTrayExpanded] = useState(true);
  const [toolNote, setToolNote] = useState("");
  const [musicTracks, setMusicTracks] = useState<PulseMusicTrack[]>([]);
  const [musicQueue, setMusicQueue] = useState<PulseMusicTrack[]>([]);
  const [musicQuery, setMusicQuery] = useState("");
  const [musicLoading, setMusicLoading] = useState(false);
  const [musicError, setMusicError] = useState("");

  // Comment composer + keyboard controller. Draft/sending/error live here so the
  // same draft survives every dismissal path and is shared by the ambient
  // over-stage composer and the expanded Comments sheet.
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [keyboardVisible, setKeyboardVisible] = useState(false);

  const startedAtRef = useRef<number>(0);
  const endedRef = useRef(false);
  const publishConfirmKeyRef = useRef("");
  const reactionRef = useRef<ReactionLayerHandle>(null);
  const composerLift = useRef(new Animated.Value(0)).current;
  const inlineInputRef = useRef<TextInput>(null);
  const sheetInputRef = useRef<TextInput>(null);

  useEffect(() => {
    let cancelled = false;
    async function connect() {
      if (liveId <= 0) {
        setFatalError("This broadcast is missing a live id and cannot start.");
        setConnecting(false);
        return;
      }
      try {
        await claimLivePlaybackOwner("host", liveId).catch(() => undefined);
        const credentials = await getLiveRtcToken(liveId, "host");
        if (cancelled) return;
        if (!credentials || !credentials.canPublish) {
          await releaseLivePlaybackOwner("host", liveId);
          setFatalError("PulseSoc did not grant a publish token for this broadcast. It cannot go live.");
          setConnecting(false);
          return;
        }
        // The host token is minted with a 2h TTL, but Agora reuses the ORIGINAL
        // join token on every reconnect, so a broadcast that runs past the TTL
        // cannot recover from a network drop unless the client re-mints. This
        // fetcher is what the room uses to refresh in place; it re-hits the
        // endpoint, which re-checks host authority server-side, so a host whose
        // broadcast was ended will not be re-issued a publish token.
        const ok = await room.startBroadcast(credentials, {
          publish: true,
          refreshCredentials: () => getLiveRtcToken(liveId, "host")
        });
        if (cancelled) return;
        if (!ok) {
          await releaseLivePlaybackOwner("host", liveId);
          setFatalError(room.getLastConnectError?.() || room.error || "The native broadcast could not connect to Agora.");
          setConnecting(false);
          return;
        }
        startedAtRef.current = Date.now();
        setConnecting(false);
      } catch (error) {
        if (cancelled) return;
        await releaseLivePlaybackOwner("host", liveId);
        setFatalError(error instanceof Error && error.message ? error.message : "The native broadcast could not start.");
        setConnecting(false);
      }
    }
    connect();
    return () => {
      cancelled = true;
      releaseLivePlaybackOwner("host", liveId).catch(() => undefined);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveId]);

  useEffect(() => {
    if (!room.connected) return undefined;
    const interval = setInterval(() => {
      if (startedAtRef.current > 0) setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [room.connected]);

  // Take global audio ownership while broadcasting so the live mic/audio preempts
  // Pulse Radio, reels, and status playback (mediaPlaybackCoordinator priority: live > reel/status > radio).
  useEffect(() => {
    if (!room.connected) return undefined;
    claimLivePlaybackOwner("host", liveId).catch(() => undefined);
    return () => {
      releaseLivePlaybackOwner("host", liveId);
    };
  }, [room.connected, liveId]);

  // Keyboard-aware layout. We lift only the composer above the keyboard and hide
  // nonessential lower controls while typing — the camera stage is never resized
  // or remounted, and the previous layout restores exactly when the keyboard
  // closes. The lift is driven by a native-driven Animated value so we don't
  // rerender the live stage on every keyboard frame.
  useEffect(() => {
    const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const onShow = (event: any) => {
      setKeyboardVisible(true);
      Animated.timing(composerLift, {
        toValue: event?.endCoordinates?.height ?? 0,
        duration: event?.duration || 220,
        useNativeDriver: true
      }).start();
    };
    const onHide = (event: any) => {
      setKeyboardVisible(false);
      Animated.timing(composerLift, {
        toValue: 0,
        duration: event?.duration || 180,
        useNativeDriver: true
      }).start();
    };
    const showSub = Keyboard.addListener(showEvent, onShow);
    const hideSub = Keyboard.addListener(hideEvent, onHide);
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, [composerLift]);

  const refreshLiveMeta = useCallback(async () => {
    if (liveId <= 0) return;
    const [state, management] = await Promise.all([
      getLiveState(liveId).catch(() => null),
      listGuestManagement(liveId).catch(() => ({
        requests: [] as LiveGuestRequest[],
        guests: [] as LiveGuest[],
        stage: null as LiveStageCapacity | null
      }))
    ]);
    if (state) {
      setViewerCount(Number(state.viewer_count || 0));
      if (state.discovery?.category) setCategory(String(state.discovery.category));
    }
    setRequests(management.requests);
    setActiveGuests(management.guests);
    // Stages 5 and 40. Only overwritten when the server actually answered — a
    // failed poll must leave the last known capacity in place rather than
    // collapsing the backstage panel to "0 of 0" for one refresh cycle.
    if (management.stage) setStage(management.stage);
  }, [liveId]);

  const refreshChat = useCallback(async () => {
    if (liveId <= 0) return;
    const chat = await listLiveChat(liveId).catch(() => [] as PulseLiveChatMessage[]);
    // Stage 27. Merge rather than replace. The endpoint returns a trailing
    // window, so assigning it wholesale drops anything that has scrolled past
    // the window and re-mounts every row on screen; on a busy Live that reads
    // as the comments clearing themselves every few seconds. The merge also
    // means a guest coming on stage — which changes nothing here — cannot
    // produce a visible blink even if a refresh happens to land at the same
    // moment.
    setMessages((previous) => mergeLiveChat(previous, chat));
  }, [liveId]);

  useEffect(() => {
    if (!room.connected) return undefined;
    refreshLiveMeta().catch(() => undefined);
    refreshChat().catch(() => undefined);
    const metaInterval = setInterval(() => refreshLiveMeta().catch(() => undefined), STATE_POLL_MS);
    const chatInterval = setInterval(() => refreshChat().catch(() => undefined), CHAT_POLL_MS);
    return () => {
      clearInterval(metaInterval);
      clearInterval(chatInterval);
    };
  }, [room.connected, refreshLiveMeta, refreshChat]);

  useEffect(() => {
    if (!room.connected || liveId <= 0) return;
    const audioTracks = Number(room.localAudioTrackCount || 0);
    const videoTracks = room.provider === "agora"
      ? Number("localVideoTrackCount" in room ? room.localVideoTrackCount || 0 : 0)
      : room.localVideoTrack ? 1 : 0;
    // Agora reports channel membership before the first encoded media frames.
    // Do not reconcile the backend to LIVE until both host tracks are proven.
    if (room.provider === "agora" && (audioTracks <= 0 || videoTracks <= 0)) return;
    if (audioTracks <= 0 && videoTracks <= 0) return;
    const key = `${liveId}:${audioTracks}:${videoTracks}:${room.reconnectCount}`;
    if (publishConfirmKeyRef.current === key) return;
    publishConfirmKeyRef.current = key;
    confirmHostLivePublish(liveId, { audioTracks, videoTracks })
      .then((result) => {
        if (result.ok) {
          setToolNote(result.message || "Native Agora media is confirmed for viewers.");
          refreshLiveMeta().catch(() => undefined);
        } else if (result.retryable) {
          setTimeout(() => {
            publishConfirmKeyRef.current = "";
          }, Math.max(800, Math.min(result.retryAfterMs || 1500, 3000)));
        } else if (result.message) {
          setToolNote(result.message);
        }
      })
      .catch((error) => {
        setToolNote(error instanceof Error ? error.message : "PulseSoc could not confirm native Live media yet.");
      });
  }, [liveId, refreshLiveMeta, room.connected, room.localAudioTrackCount, room.localVideoTrack, room.reconnectCount]);

  const finishBroadcast = useCallback(async () => {
    if (endedRef.current) return;
    endedRef.current = true;
    setEnding(true);
    const endTappedAt = Date.now();
    // ZERO-DELAY LIVE END: the server acknowledgement must never hold the host
    // on this screen. Fire the end call immediately, tear down local media
    // through the one verified stopBroadcast path (unchanged), and release the
    // UI. Replay finalization is server-owned and continues in the background
    // whatever this device does next — including being killed.
    const endAck = endLive(liveId).catch(() => null);
    const localRelease = room.stopBroadcast("host_ended")
      .then(() => {
        console.log(`[live-end] local media released in ${Date.now() - endTappedAt}ms`);
      })
      .catch(() => undefined);
    console.log(`[live-end] navigation released in ${Date.now() - endTappedAt}ms`);
    navigation.goBack();
    endAck.then((result) => {
      console.log(`[live-end] server ack in ${Date.now() - endTappedAt}ms status=${result ? result.replayStatus || result.recordingStatus || "ok" : "failed"}`);
      if (!result) {
        // One best-effort retry; the backend replay reconciler also repairs a
        // session whose end call never landed, so this is belt-and-braces.
        endLive(liveId).catch(() => undefined);
      }
    });
    localRelease.catch(() => undefined);
  }, [liveId, navigation, room]);

  const confirmEnd = useCallback(() => {
    Keyboard.dismiss();
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => undefined);
    Alert.alert("End broadcast?", "This ends the live for everyone watching.", [
      { text: "Keep streaming", style: "cancel" },
      { text: "End live", style: "destructive", onPress: () => finishBroadcast().catch(() => undefined) }
    ]);
  }, [finishBroadcast]);

  const respond = useCallback(
    async (request: LiveGuestRequest, action: "accept" | "deny") => {
      setBusyRequestId(request.requestId);
      try {
        await respondToJoinRequest(liveId, request.requestId, action);
        setRequests((current) => current.filter((item) => item.requestId !== request.requestId));
      } catch (error) {
        Alert.alert("Could not update request", error instanceof Error ? error.message : "Please try again.");
      } finally {
        setBusyRequestId(0);
      }
    },
    [liveId]
  );

  const acceptAll = useCallback(async () => {
    // Stage 46. Only as many as there are seats. The server is the authority and
    // will refuse the rest anyway, but a host who taps "accept all" and watches
    // most of the queue bounce has no way to tell a capacity limit from a bug —
    // and the ones that bounced stay in the list looking un-actioned. Requests
    // beyond the ceiling are deliberately left pending rather than denied, so
    // they are still there when a guest leaves.
    const seats = stage ? Math.max(0, stage.slotsAvailable) : requests.length;
    const pending = requests.slice(0, seats);
    if (!pending.length) return;
    for (const request of pending) {
      await respondToJoinRequest(liveId, request.requestId, "accept").catch(() => undefined);
    }
    const accepted = new Set(pending.map((item) => item.requestId));
    setRequests((current) => current.filter((item) => !accepted.has(item.requestId)));
    refreshLiveMeta().catch(() => undefined);
  }, [requests, liveId, refreshLiveMeta, stage]);

  const moderateGuest = useCallback(
    async (guest: LiveGuest, action: "mute" | "unmute" | "remove") => {
      setBusyGuestId(guest.guestId);
      try {
        if (action === "remove") {
          await removeGuest(liveId, guest.guestId);
          setActiveGuests((current) => current.filter((item) => item.guestId !== guest.guestId));
        } else {
          await (action === "mute" ? muteGuest : unmuteGuest)(liveId, guest.guestId);
          setActiveGuests((current) =>
            current.map((item) => (item.guestId === guest.guestId ? { ...item, audioMuted: action === "mute" } : item))
          );
        }
      } catch (error) {
        Alert.alert("Could not update guest", error instanceof Error ? error.message : "Please try again.");
      } finally {
        setBusyGuestId(0);
      }
    },
    [liveId]
  );

  const confirmRemoveGuest = useCallback(
    (guest: LiveGuest) => {
      Alert.alert("Remove guest?", `Remove ${guest.displayName} from the broadcast? They stop publishing immediately.`, [
        { text: "Cancel", style: "cancel" },
        { text: "Remove", style: "destructive", onPress: () => moderateGuest(guest, "remove").catch(() => undefined) }
      ]);
    },
    [moderateGuest]
  );

  const toggleMic = useCallback(() => {
    room.setMicrophoneEnabled(!room.audioEnabled).catch((error) => Alert.alert("Microphone", error instanceof Error ? error.message : "Failed."));
  }, [room]);

  const toggleCamera = useCallback(() => {
    room.setCameraEnabled(!room.videoEnabled).catch((error) => Alert.alert("Camera", error instanceof Error ? error.message : "Failed."));
  }, [room]);

  const flipCamera = useCallback(() => {
    room.switchCamera().catch((error) => Alert.alert("Flip camera", error instanceof Error ? error.message : "Failed."));
  }, [room]);

  // The single intentional dismissal action. Everything that needs to close the
  // keyboard funnels through here rather than scattering Keyboard.dismiss calls.
  const closeComposer = useCallback(() => {
    Keyboard.dismiss();
  }, []);

  const openSheet = useCallback((key: SheetKey) => {
    Keyboard.dismiss();
    setSheet(key);
  }, []);

  const closeSheet = useCallback(() => {
    Keyboard.dismiss();
    setSheet(null);
  }, []);

  const submitDraft = useCallback(async () => {
    const body = draft.trim();
    if (!body || sending || !room.connected) return;
    setSending(true);
    setSendError("");
    try {
      const result = await sendLiveChat(liveId, body);
      if (result?.chat) setMessages((current) => [...current, result.chat]);
      setDraft("");
      Keyboard.dismiss();
    } catch (error) {
      setSendError(error instanceof Error && error.message ? error.message : "Comment didn't send. Tap send to try again.");
    } finally {
      setSending(false);
    }
  }, [draft, sending, room.connected, liveId]);

  const react = useCallback(
    (item: { emoji: string; type: string }) => {
      reactionRef.current?.burst(item.emoji);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
      reactToLive(liveId, item.type).catch(() => undefined);
    },
    [liveId]
  );

  const shareLive = useCallback(async () => {
    Keyboard.dismiss();
    setSheet(null);
    const url = liveWebUrl(liveId);
    await sharePulseObject({
      kind: "live",
      url,
      title: "Watch my PulseSoc Live",
      description: "Join the live broadcast on PulseSoc."
    }).catch(() => undefined);
  }, [liveId]);

  const toggleLayout = useCallback(() => {
    Keyboard.dismiss();
    setLayoutMode((mode) => (mode === "spotlight" ? "grid" : "spotlight"));
  }, []);

  const flagComingSoon = useCallback((key: keyof typeof COMING_SOON) => {
    setToolNote(COMING_SOON[key]);
  }, []);

  const loadMusic = useCallback(async (mode: "trending" | "search" = "trending") => {
    setMusicLoading(true);
    setMusicError("");
    try {
      const result = await searchPulseMusic({
        query: mode === "search" ? musicQuery : "",
        lane: mode === "search" ? "" : "trending",
        limit: 12
      });
      setMusicTracks(result.tracks);
      if (!result.tracks.length) setMusicError(mode === "search" ? "No approved tracks matched this search." : "No approved PulseSoc Music tracks are available right now.");
    } catch (error) {
      setMusicError(error instanceof Error && error.message ? error.message : "PulseSoc Music could not load.");
    } finally {
      setMusicLoading(false);
    }
  }, [musicQuery]);

  useEffect(() => {
    if (sheet !== "music" || musicTracks.length || musicLoading) return;
    loadMusic("trending").catch(() => undefined);
  }, [loadMusic, musicLoading, musicTracks.length, sheet]);

  const startLiveTrack = useCallback(async (track: PulseMusicTrack, source = "native_live_host_music") => {
    if (!room.connected || !room.canPublish) {
      setMusicError("Start the Live broadcast before adding music.");
      return;
    }
    setMusicError("");
    try {
      await room.startLiveMusicMixing({
        id: track.id,
        title: track.title,
        artist: track.artist,
        audioUrl: track.audioUrl || track.previewUrl,
        coverArtUrl: track.coverArtUrl
      });
      setMusicQueue((current) => [track, ...current.filter((item) => item.id !== track.id)].slice(0, 8));
      await recordPulseMusicEvent(track.id, "play", source).catch(() => undefined);
    } catch (error) {
      setMusicError(error instanceof Error && error.message ? error.message : "PulseSoc Music could not start.");
    }
  }, [room]);

  const startPulseRadioInLive = useCallback(async () => {
    if (!room.connected || !room.canPublish) {
      setMusicError("Start the Live broadcast before adding PulseSoc Radio.");
      return;
    }
    setMusicLoading(true);
    setMusicError("");
    try {
      const radioTracks = await listPulseRadioTracks(12);
      const first = radioTracks[0];
      if (!first) throw new Error("PulseSoc Radio has no approved playable tracks right now.");
      const track: PulseMusicTrack = {
        id: first.id,
        title: first.title,
        artist: first.artist,
        audioUrl: first.audioUrl,
        previewUrl: first.audioUrl,
        coverArtUrl: first.coverArtUrl || "",
        artistUserId: 0,
        durationSeconds: 0,
        waveform: [0.18, 0.38, 0.66, 0.42, 0.72, 0.5, 0.3, 0.58],
        genre: "radio",
        language: "music",
        mood: "live",
        licenseLabel: "approved",
        moderationStatus: "approved",
        approvedByAdmin: true,
        active: true,
        playCount: 0,
        usageCount: 0,
        trendScore: 0,
        saveCount: 0,
        shareCount: 0
      };
      await startLiveTrack(track, "native_live_pulse_radio");
      await recordPulseRadioPlay(first.id).catch(() => undefined);
    } catch (error) {
      setMusicError(error instanceof Error && error.message ? error.message : "PulseSoc Radio could not start in Live.");
    } finally {
      setMusicLoading(false);
    }
  }, [room.canPublish, room.connected, startLiveTrack]);

  const toggleLiveMusicPlayback = useCallback(() => {
    const status = room.liveMusic.status;
    if (status === "playing" || status === "loading") {
      room.pauseLiveMusicMixing().catch((error) => setMusicError(error instanceof Error ? error.message : "PulseSoc Music could not pause."));
      return;
    }
    if (status === "paused") {
      room.resumeLiveMusicMixing().catch((error) => setMusicError(error instanceof Error ? error.message : "PulseSoc Music could not resume."));
      return;
    }
    if (musicQueue[0]) startLiveTrack(musicQueue[0]).catch(() => undefined);
    else startPulseRadioInLive().catch(() => undefined);
  }, [musicQueue, room, startLiveTrack, startPulseRadioInLive]);

  const playNextLiveMusic = useCallback(() => {
    if (!musicQueue.length) {
      startPulseRadioInLive().catch(() => undefined);
      return;
    }
    const currentId = room.liveMusic.track?.id || "";
    const currentIndex = musicQueue.findIndex((track) => track.id === currentId);
    const next = musicQueue[(currentIndex + 1 + musicQueue.length) % musicQueue.length] || musicQueue[0];
    startLiveTrack(next).catch(() => undefined);
  }, [musicQueue, room.liveMusic.track?.id, startLiveTrack, startPulseRadioInLive]);

  // Host-side per-comment moderation. Pin/unpin/remove are host-authoritative and
  // enforced again server-side; the local list is updated optimistically so the
  // console reflects the action immediately, then reconciled by the next poll.
  const moderateComment = useCallback(
    async (message: PulseLiveChatMessage, action: LiveChatModerationAction) => {
      if (moderatingId) return;
      setModeratingId(message.id);
      const previous = messages;
      setMessages((current) => {
        if (action === "delete") return current.filter((item) => item.id !== message.id);
        if (action === "pin" || action === "unpin") {
          const pinned = action === "pin";
          return current.map((item) =>
            item.id === message.id ? { ...item, pinned } : pinned ? { ...item, pinned: false } : item
          );
        }
        return current;
      });
      try {
        await moderateLiveChat(liveId, message.id, action);
        setToolNote(
          action === "delete"
            ? "Comment removed."
            : action === "pin"
              ? "Comment pinned."
              : action === "unpin"
                ? "Comment unpinned."
                : "Comment reported for review."
        );
      } catch (error) {
        setMessages(previous);
        setToolNote(error instanceof Error && error.message ? error.message : "Moderation action didn't go through.");
      } finally {
        setModeratingId(0);
      }
    },
    [moderatingId, messages, liveId]
  );

  const guests = useMemo(() => room.participants.filter((participant) => !participant.isLocal), [room.participants]);
  const localParticipant = useMemo(() => room.participants.find((participant) => participant.isLocal) || null, [room.participants]);
  const stageParticipants = useMemo(
    () => [localParticipant, ...guests].filter(Boolean) as LiveParticipant[],
    [localParticipant, guests]
  );

  const pinnedMessage = useMemo(() => {
    const pinned = messages.filter((message) => message.pinned);
    return pinned.length ? pinned[pinned.length - 1] : null;
  }, [messages]);
  const streamMessages = useMemo(
    () => (pinnedMessage ? messages.filter((message) => message.id !== pinnedMessage.id) : messages),
    [messages, pinnedMessage]
  );

  const signal = signalMeta(room.connectionQuality);
  const hostName = authState.user?.display_name || authState.user?.username || "You";
  const hostVerified = ["active", "verified", "pro", "premium"].includes(String(authState.user?.premium_status || "").toLowerCase());

  const liveLabel = room.connected ? "LIVE" : room.reconnecting ? "RECONNECTING" : "GOING LIVE";

  if (fatalError) {
    return (
      <View style={[styles.center, { paddingTop: insets.top }]}>
        <Ionicons name="alert-circle" size={40} color={colors.danger} />
        <Text style={styles.errorTitle}>Broadcast could not start</Text>
        <Text style={styles.errorBody}>{fatalError}</Text>
        <Pressable style={styles.exitButton} onPress={() => navigation.goBack()}>
          <Text style={styles.exitText}>Back to Live Studio</Text>
        </Pressable>
      </View>
    );
  }

  const showFloatingGuests = layoutMode === "spotlight" && guests.length > 0 && Boolean(VideoViewComponent);

  // Lift the composer above the keyboard by (keyboardHeight - bottom inset) so we
  // never double-count the safe area and never leave a blank gap when it closes.
  // Guard the inset floor so the interpolation input range stays monotonic on
  // devices with no home indicator (insets.bottom === 0).
  const liftFloor = Math.max(insets.bottom, 1);
  const composerTranslateY = composerLift.interpolate({
    inputRange: [0, liftFloor, liftFloor + 2000],
    outputRange: [0, 0, -2000],
    extrapolate: "clamp"
  });

  return (
    <View style={styles.root}>
      {/* ---------- STAGE ---------- */}
      <View style={StyleSheet.absoluteFill}>
        {stageParticipants.length && VideoViewComponent ? (
          layoutMode === "grid" ? (
            <View style={styles.grid}>
              {stageParticipants.map((participant) => (
                <StageTile
                  key={participant.identity}
                  participant={participant}
                  VideoView={VideoViewComponent}
                  split={stageParticipants.length > 1}
                />
              ))}
            </View>
          ) : (
            <StageHero participant={localParticipant} VideoView={VideoViewComponent} />
          )
        ) : (
          <View style={styles.center}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.connectingText}>{connecting ? "Connecting your broadcast…" : "Waiting for camera…"}</Text>
          </View>
        )}
        {/* Legibility scrims */}
        <LinearGradient colors={["rgba(2,5,12,0.72)", "rgba(2,5,12,0)"]} style={styles.scrimTop} pointerEvents="none" />
        <LinearGradient colors={["rgba(2,5,12,0)", "rgba(2,5,12,0.82)"]} style={styles.scrimBottom} pointerEvents="none" />
      </View>

      {/* ---------- REACTIONS ---------- */}
      <LiveReactionLayer ref={reactionRef} />

      {/* ---------- TOP BAR ---------- */}
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]} pointerEvents="box-none">
        <Pressable style={styles.roundGlass} onPress={confirmEnd} accessibilityRole="button" accessibilityLabel="Minimize live">
          <Ionicons name="chevron-down" size={22} color={colors.text} />
        </Pressable>
        <GlassPill tone="danger" style={styles.liveBadge}>
          <View style={styles.liveDot} />
          <Text style={styles.liveText}>{liveLabel}</Text>
          <Text style={styles.elapsed}>{elapsedLabel(elapsed)}</Text>
        </GlassPill>
        <View style={{ flex: 1 }} />
        <GlassPill>
          <Ionicons name="eye" size={14} color={colors.text} />
          <Text style={styles.viewerText}>{formatViewerCount(viewerCount)}</Text>
          <Ionicons name="cellular" size={14} color={signal.color} />
        </GlassPill>
        <Pressable style={styles.roundGlass} onPress={() => openSheet("more")} accessibilityRole="button" accessibilityLabel="More options">
          <Ionicons name="ellipsis-horizontal" size={20} color={colors.text} />
        </Pressable>
      </View>

      {/* ---------- HOST IDENTITY ---------- */}
      <View style={[styles.identity, { top: insets.top + 60 }]} pointerEvents="box-none">
        <View style={styles.identityRow}>
          <View style={styles.hostAvatar}>
            <Text style={styles.hostAvatarText}>{(hostName[0] || "?").toUpperCase()}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <View style={styles.hostNameRow}>
              <Text style={styles.hostName} numberOfLines={1}>
                {hostName}
              </Text>
              {hostVerified ? <Ionicons name="checkmark-circle" size={15} color={colors.accentStrong} /> : null}
            </View>
            <Text style={styles.hostSub} numberOfLines={1}>
              {title}
            </Text>
          </View>
        </View>
        {category ? (
          <GlassPill style={styles.categoryChip} onPress={() => openSheet("music")}>
            <Ionicons name="musical-notes" size={13} color={colors.creator} />
            <Text style={styles.categoryText}>PulseSoc Live · {category}</Text>
          </GlassPill>
        ) : null}
      </View>

      {/* ---------- ACTION RAIL ---------- */}
      <View style={[styles.rail, { top: insets.top + 150 }]} pointerEvents="box-none">
        <GlassCircleButton icon="people" label="Guests" badge={requests.length} onPress={() => openSheet("guests")} />
        <GlassCircleButton icon="chatbubble-ellipses" label="Comments" onPress={() => (sheet === "comments" ? closeSheet() : openSheet("comments"))} />
        <GlassCircleButton icon="heart" label="Reactions" tone="danger" onPress={() => openSheet("reactions")} />
        <GlassCircleButton icon="arrow-redo" label="Share" onPress={shareLive} />
        <GlassCircleButton icon="musical-notes" label="Music" tone="creator" onPress={() => openSheet("music")} />
        <GlassCircleButton icon="ellipsis-horizontal" label="More" onPress={() => openSheet("more")} />
      </View>

      {/* ---------- FLOATING GUEST TILES ---------- */}
      {showFloatingGuests && VideoViewComponent ? (
        <View style={[styles.guestColumn, { top: insets.top + 150 }]} pointerEvents="box-none">
          {guests.slice(0, 3).map((participant) => (
            <FloatingGuestTile key={participant.identity} participant={participant} VideoView={VideoViewComponent} />
          ))}
          <Pressable style={styles.inviteTile} onPress={() => openSheet("guests")} accessibilityRole="button" accessibilityLabel="Invite guest">
            <Ionicons name="add" size={26} color={colors.accent} />
            <Text style={styles.inviteText}>Invite guest</Text>
          </Pressable>
        </View>
      ) : null}

      {/* ---------- TAP-OUTSIDE DISMISS ---------- */}
      {keyboardVisible ? (
        <Pressable
          style={StyleSheet.absoluteFill}
          onPress={closeComposer}
          accessibilityRole="button"
          accessibilityLabel="Tap to dismiss keyboard"
        />
      ) : null}

      {/* ---------- BOTTOM CLUSTER ---------- */}
      <Animated.View
        style={[styles.bottom, { paddingBottom: insets.bottom + 10, transform: [{ translateY: composerTranslateY }] }]}
        pointerEvents="box-none"
      >
        {room.error ? <Text style={styles.inlineError}>{room.error}</Text> : null}

        {/* The broadcast is live and the microphone could not be confirmed. This
            used to be a full-screen "Broadcast could not start" dead end that
            ended a session which was, in fact, already on air. It is a banner
            with a retry now, because the host needs to know their audio is in
            doubt AND needs to be able to stay live while they act on it. */}
        {room.audioWarning ? (
          <View style={styles.audioWarningBanner} accessibilityRole="alert">
            <Ionicons name="warning" size={16} color="#FFB020" />
            <Text style={styles.audioWarningText}>{room.audioWarning}</Text>
            <Pressable
              onPress={() => room.recheckAudio().catch(() => undefined)}
              disabled={room.audioBusy}
              accessibilityRole="button"
              accessibilityLabel="Recheck microphone"
              hitSlop={8}
            >
              <Text style={styles.audioWarningAction}>{room.audioBusy ? "Checking…" : "Retry"}</Text>
            </Pressable>
          </View>
        ) : null}

        {!keyboardVisible ? (
          <Pressable onPress={() => openSheet("comments")} style={styles.chatTap} accessibilityRole="button" accessibilityLabel="Open comments">
            <LiveChatStream messages={streamMessages} pinned={pinnedMessage} maxVisible={3} />
          </Pressable>
        ) : null}

        <LiveChatComposer
          value={draft}
          onChangeText={setDraft}
          onSend={submitDraft}
          onEmoji={() => openSheet("reactions")}
          onGuests={() => openSheet("guests")}
          guestCount={guests.length}
          disabled={!room.connected}
          sending={sending}
          errorText={sendError}
          inputRef={inlineInputRef}
          inputAccessoryViewID={Platform.OS === "ios" ? COMMENT_ACCESSORY_ID : undefined}
        />

        {!keyboardVisible ? (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.controlTray}
            style={styles.controlTrayScroll}
          >
            <GlassCircleButton icon={room.audioEnabled ? "mic" : "mic-off"} label={room.audioEnabled ? "Mute" : "Unmute"} active={!room.audioEnabled} tone="danger" size={48} onPress={toggleMic} />
            <GlassCircleButton icon={room.videoEnabled ? "videocam" : "videocam-off"} label="Camera" active={!room.videoEnabled} tone="danger" size={48} onPress={toggleCamera} />
            <GlassCircleButton icon="camera-reverse" label="Flip" size={48} disabled={!room.videoEnabled} onPress={flipCamera} />
            <GlassCircleButton icon="volume-high" label="Audio" active={room.speakerEnabled} size={48} onPress={() => room.showAudioRoutePicker().catch(() => undefined)} />
            <GlassCircleButton icon="sparkles" label="Effects" tone="intelligence" size={48} onPress={() => { openSheet("more"); flagComingSoon("filters"); }} />
            <GlassCircleButton icon={layoutMode === "grid" ? "grid" : "albums"} label="Layout" active={layoutMode === "grid"} tone="creator" size={48} onPress={toggleLayout} />
            <GlassCircleButton icon="exit" label={ending ? "Ending…" : "End"} solid tone="danger" size={48} disabled={ending} onPress={confirmEnd} haptics={false} />
          </ScrollView>
        ) : null}

        {!keyboardVisible && requests.length > 0 ? (
          <View style={styles.tray}>
            <LinearGradient colors={["rgba(13,25,40,0.96)", "rgba(6,13,23,0.98)"]} style={StyleSheet.absoluteFill} />
            <Pressable style={styles.trayHeader} onPress={() => setTrayExpanded((value) => !value)} accessibilityRole="button">
              <Ionicons name="hand-left" size={16} color={colors.accent} />
              <Text style={styles.trayTitle}>Guest requests</Text>
              <View style={styles.trayCount}>
                <Text style={styles.trayCountText}>{requests.length}</Text>
              </View>
              <View style={{ flex: 1 }} />
              <Pressable onPress={() => openSheet("guests")} hitSlop={8}>
                <Text style={styles.traySeeAll}>See all</Text>
              </Pressable>
              <Ionicons name={trayExpanded ? "chevron-down" : "chevron-up"} size={18} color={colors.muted} />
            </Pressable>
            {trayExpanded ? (
              <>
                <View style={styles.trayCards}>
                  {requests.slice(0, 2).map((request) => (
                    <View key={request.requestId} style={styles.trayCard}>
                      <View style={styles.trayCardAvatar}>
                        <Text style={styles.trayCardAvatarText}>{(request.displayName[0] || "?").toUpperCase()}</Text>
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.trayCardName} numberOfLines={1}>
                          {request.displayName}
                        </Text>
                        <Text style={styles.trayCardMeta} numberOfLines={1}>
                          Wants to join · {request.cameraReady ? "camera" : "audio"}
                        </Text>
                      </View>
                      <View style={styles.trayCardActions}>
                        <Pressable
                          style={styles.trayDecline}
                          disabled={busyRequestId === request.requestId}
                          onPress={() => respond(request, "deny").catch(() => undefined)}
                        >
                          <Text style={styles.trayDeclineText}>Decline</Text>
                        </Pressable>
                        <Pressable
                          style={styles.trayAccept}
                          disabled={busyRequestId === request.requestId}
                          onPress={() => respond(request, "accept").catch(() => undefined)}
                        >
                          <Text style={styles.trayAcceptText}>Accept</Text>
                        </Pressable>
                      </View>
                    </View>
                  ))}
                </View>
                <View style={styles.trayFooter}>
                  <Pressable style={styles.trayAcceptAll} onPress={() => acceptAll().catch(() => undefined)}>
                    <Text style={styles.trayAcceptAllText}>Accept all ({requests.length})</Text>
                  </Pressable>
                  <Pressable style={styles.trayInvite} onPress={() => openSheet("guests")}>
                    <Text style={styles.trayInviteText}>Invite guests</Text>
                  </Pressable>
                </View>
              </>
            ) : null}
          </View>
        ) : null}
      </Animated.View>

      {/* ---------- SHEETS ---------- */}
      <LiveBottomSheet visible={sheet === "guests"} onClose={closeSheet} title="Guests" subtitle="Manage who is on stage">
        {/* Stages 5, 40 and 46. The ceiling comes from the server, so "stage
            full" is the real limit for this deployment rather than a constant
            compiled into the app — and it is stated before an approval is
            refused, not after. Until the server has answered, only the count
            the panel can actually see is shown. */}
        <Text style={styles.sheetLabel}>
          On stage · {activeGuests.length}
          {stage && stage.maxGuests > 0 ? ` of ${stage.maxGuests}` : ""}
        </Text>
        {stage?.stageFull ? (
          <Text style={styles.sheetEmpty}>The stage is full. Remove a guest to make room for another.</Text>
        ) : null}
        {stage && !stage.multiGuestEnabled ? (
          <Text style={styles.sheetEmpty}>Guests are turned off for this deployment. Your broadcast is unaffected.</Text>
        ) : null}
        {activeGuests.length === 0 ? (
          <Text style={styles.sheetEmpty}>No guests are publishing yet. Accepted guests appear here to mute or remove.</Text>
        ) : (
          activeGuests.map((guest) => (
            <View key={guest.guestId} style={styles.manageRow}>
              <View style={styles.manageAvatar}>
                <Text style={styles.manageAvatarText}>{(guest.displayName[0] || "?").toUpperCase()}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.manageName} numberOfLines={1}>
                  {guest.displayName}
                </Text>
                <Text style={styles.manageMeta} numberOfLines={1}>
                  {guest.roleLabel} · {guest.audioMuted ? "muted" : "live audio"}
                </Text>
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={guest.audioMuted ? `Unmute ${guest.displayName}` : `Mute ${guest.displayName}`}
                accessibilityState={{ disabled: busyGuestId === guest.guestId }}
                style={[styles.manageBtn, guest.audioMuted ? styles.manageBtnAccent : styles.manageBtnOutline]}
                disabled={busyGuestId === guest.guestId}
                onPress={() => moderateGuest(guest, guest.audioMuted ? "unmute" : "mute").catch(() => undefined)}
              >
                <Ionicons name={guest.audioMuted ? "mic-off" : "mic"} size={16} color={guest.audioMuted ? colors.background : colors.text} />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Remove ${guest.displayName} from stage`}
                accessibilityHint="Ends this guest's stream and returns them to the audience"
                accessibilityState={{ disabled: busyGuestId === guest.guestId }}
                style={[styles.manageBtn, styles.manageBtnDanger]}
                disabled={busyGuestId === guest.guestId}
                onPress={() => confirmRemoveGuest(guest)}
              >
                <Ionicons name="person-remove" size={16} color={colors.danger} />
              </Pressable>
            </View>
          ))
        )}

        <Text style={[styles.sheetLabel, { marginTop: 8 }]}>Requests · {requests.length}</Text>
        {requests.length === 0 ? (
          <Text style={styles.sheetEmpty}>No pending requests. Viewers who ask to join appear here.</Text>
        ) : (
          requests.map((request) => (
            <View key={request.requestId} style={styles.manageRow}>
              <View style={styles.manageAvatar}>
                <Text style={styles.manageAvatarText}>{(request.displayName[0] || "?").toUpperCase()}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.manageName} numberOfLines={1}>
                  {request.displayName}
                </Text>
                <Text style={styles.manageMeta} numberOfLines={1}>
                  @{request.username || "guest"} · {request.cameraReady ? "camera ready" : "audio only"}
                </Text>
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Deny ${request.displayName}'s request to join`}
                accessibilityState={{ disabled: busyRequestId === request.requestId }}
                style={[styles.manageBtn, styles.manageBtnOutline]}
                disabled={busyRequestId === request.requestId}
                onPress={() => respond(request, "deny").catch(() => undefined)}
              >
                <Ionicons name="close" size={18} color={colors.text} />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Accept ${request.displayName} onto the stage`}
                accessibilityState={{ disabled: busyRequestId === request.requestId || Boolean(stage?.stageFull) }}
                // Stage 46. Disabled on a full stage rather than allowed through
                // to a 409. Deny stays enabled, so a host can still clear the
                // queue honestly instead of being stuck with it.
                style={[styles.manageBtn, styles.manageBtnAccent, stage?.stageFull ? { opacity: 0.4 } : null]}
                disabled={busyRequestId === request.requestId || Boolean(stage?.stageFull)}
                onPress={() => respond(request, "accept").catch(() => undefined)}
              >
                <Ionicons name="checkmark" size={18} color={colors.background} />
              </Pressable>
            </View>
          ))
        )}
        {requests.length > 0 && !stage?.stageFull ? (
          // Stage 46. "Accept all" is capped at the seats that actually exist.
          // Offering to admit nine people onto a stage with two free slots
          // guarantees seven refusals the host cannot explain to their viewers.
          <Pressable style={styles.sheetPrimary} onPress={() => acceptAll().catch(() => undefined)}>
            <Text style={styles.sheetPrimaryText}>
              Accept all ({stage ? Math.min(requests.length, stage.slotsAvailable) : requests.length})
            </Text>
          </Pressable>
        ) : null}
      </LiveBottomSheet>

      <LiveBottomSheet visible={sheet === "comments"} onClose={closeSheet} title="Comments" subtitle={`${messages.length} in this live`} maxHeightRatio={0.82}>
        {messages.length === 0 ? (
          <Text style={styles.sheetEmpty}>No comments yet. Say hello to your viewers.</Text>
        ) : (
          messages.map((message) => (
            <LiveChatMessageRow
              key={message.id}
              message={message}
              moderation={{ canModerate: true, onModerate: moderateComment, busyMessageId: moderatingId || null }}
            />
          ))
        )}
        <View style={styles.sheetComposer}>
          <LiveChatComposer
            value={draft}
            onChangeText={setDraft}
            onSend={submitDraft}
            onEmoji={() => openSheet("reactions")}
            guestCount={0}
            disabled={!room.connected}
            sending={sending}
            errorText={sendError}
            placeholder="Reply to your community…"
            inputRef={sheetInputRef}
            inputAccessoryViewID={Platform.OS === "ios" ? COMMENT_ACCESSORY_ID : undefined}
          />
        </View>
      </LiveBottomSheet>

      <LiveBottomSheet visible={sheet === "reactions"} onClose={closeSheet} title="Reactions" subtitle="Send love to the room" accent={colors.danger} maxHeightRatio={0.42}>
        <View style={styles.reactionGrid}>
          {REACTIONS.map((item) => (
            <Pressable key={item.type} style={styles.reactionTile} onPress={() => react(item)} accessibilityRole="button" accessibilityLabel={item.label}>
              <Text style={styles.reactionEmoji}>{item.emoji}</Text>
              <Text style={styles.reactionLabel}>{item.label}</Text>
            </Pressable>
          ))}
        </View>
      </LiveBottomSheet>

      <LiveBottomSheet visible={sheet === "music"} onClose={closeSheet} title="Music" subtitle="Soundtrack your broadcast" accent={colors.creator} maxHeightRatio={0.5}>
        <View style={styles.musicCard}>
          <View style={styles.musicArt}>
            {room.liveMusic.track?.coverArtUrl ? <Image source={{ uri: room.liveMusic.track.coverArtUrl }} style={styles.musicCover} /> : <Ionicons name="musical-notes" size={28} color={colors.creator} />}
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.musicTitle}>{room.liveMusic.track?.title || "PulseSoc Music"}</Text>
            <Text style={styles.musicMeta}>
              {room.liveMusic.track ? `${room.liveMusic.track.artist} · ${room.liveMusic.status}` : "Choose a track or PulseSoc Radio"}
            </Text>
          </View>
        </View>
        <View style={styles.musicTransport}>
          <Pressable accessibilityRole="button" accessibilityLabel="Stop Live music" onPress={() => room.stopLiveMusicMixing().catch((error) => setMusicError(error instanceof Error ? error.message : "PulseSoc Music could not stop."))}>
            <Ionicons name="stop" size={22} color={room.liveMusic.track ? colors.text : colors.muted} />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel={room.liveMusic.status === "playing" ? "Pause Live music" : "Play Live music"} style={styles.musicPlay} onPress={toggleLiveMusicPlayback}>
            <Ionicons name={room.liveMusic.status === "playing" || room.liveMusic.status === "loading" ? "pause" : "play"} size={24} color={colors.background} />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Next Live music track" onPress={playNextLiveMusic}>
            <Ionicons name="play-skip-forward" size={22} color={musicQueue.length || musicTracks.length ? colors.text : colors.muted} />
          </Pressable>
        </View>
        <View style={styles.levelGroup}>
          <LiveLevelControl label="Mic" value={room.liveMusic.micVolume} onChange={(value) => room.setLiveMicVolume(value).catch((error) => setMusicError(error instanceof Error ? error.message : "Mic level could not update."))} />
          <LiveLevelControl label="Music" value={room.liveMusic.musicVolume} onChange={(value) => room.setLiveMusicVolume(value).catch((error) => setMusicError(error instanceof Error ? error.message : "Music level could not update."))} />
        </View>
        <View style={styles.musicSearchRow}>
          <TextInput
            style={styles.musicSearchInput}
            value={musicQuery}
            onChangeText={setMusicQuery}
            placeholder="Search songs or artists"
            placeholderTextColor={colors.muted}
            returnKeyType="search"
            onSubmitEditing={() => loadMusic("search").catch(() => undefined)}
          />
          <Pressable style={styles.musicSearchButton} onPress={() => loadMusic("search").catch(() => undefined)} accessibilityRole="button" accessibilityLabel="Search PulseSoc Music">
            <Ionicons name="search" size={18} color={colors.background} />
          </Pressable>
        </View>
        <Pressable style={[styles.radioMixButton, (!room.connected || musicLoading) && styles.disabled]} onPress={startPulseRadioInLive} disabled={musicLoading || !room.connected} accessibilityRole="button" accessibilityLabel="Start PulseSoc Radio in this Live">
          <Ionicons name="radio" size={16} color={colors.background} />
          <Text style={styles.radioMixButtonText}>{musicLoading ? "Loading…" : "Start PulseSoc Radio"}</Text>
        </Pressable>
        {musicError || room.liveMusic.error ? <Text style={styles.musicError}>{musicError || room.liveMusic.error}</Text> : null}
        {musicTracks.slice(0, 6).map((track) => (
          <Pressable key={track.id} style={styles.musicRow} onPress={() => startLiveTrack(track).catch(() => undefined)} accessibilityRole="button" accessibilityLabel={`Play ${track.title} by ${track.artist} in Live`}>
            <View style={styles.musicRowArt}>
              {track.coverArtUrl ? <Image source={{ uri: track.coverArtUrl }} style={styles.musicCover} /> : <Ionicons name="musical-notes" size={18} color={colors.creator} />}
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.musicRowTitle} numberOfLines={1}>{track.title}</Text>
              <Text style={styles.musicRowMeta} numberOfLines={1}>{track.artist} · {track.genre} · {track.licenseLabel}</Text>
            </View>
            <Ionicons name={room.liveMusic.track?.id === track.id && room.liveMusic.status === "playing" ? "volume-high" : "play"} size={18} color={colors.creator} />
          </Pressable>
        ))}
        {!musicLoading && !musicTracks.length && !musicError ? (
          <Text style={styles.sheetEmpty}>Search PulseSoc Music or start PulseSoc Radio. Nothing plays until you choose it.</Text>
        ) : null}
      </LiveBottomSheet>

      <LiveBottomSheet visible={sheet === "more"} onClose={() => { closeSheet(); setToolNote(""); }} title="Live tools" subtitle="Everything for this broadcast" maxHeightRatio={0.66}>
        {toolNote ? (
          <View style={styles.noteBanner}>
            <Ionicons name="information-circle" size={16} color={colors.accent} />
            <Text style={styles.noteText}>{toolNote}</Text>
          </View>
        ) : null}
        <View style={styles.toolGrid}>
          <ToolTile icon="stats-chart" label="Analytics" tone="creator" onPress={() => setToolNote(`${formatViewerCount(viewerCount)} watching · ${elapsedLabel(elapsed)} live · ${guests.length} on stage · signal ${signal.label}`)} />
          <ToolTile icon={layoutMode === "grid" ? "grid" : "albums"} label="Layout" onPress={() => { toggleLayout(); setToolNote(`Layout set to ${layoutMode === "spotlight" ? "Grid" : "Spotlight"}.`); }} />
          <ToolTile icon="phone-portrait" label="Screen share" onPress={() => flagComingSoon("screen_share")} />
          <ToolTile icon="tv" label="Watch party" onPress={() => flagComingSoon("watch_party")} />
          <ToolTile icon="game-controller" label="Games" onPress={() => flagComingSoon("games")} />
          <ToolTile icon="sparkles" label="Filters" tone="intelligence" onPress={() => flagComingSoon("filters")} />
          <ToolTile icon="shield-checkmark" label="Moderation" onPress={() => openSheet("comments")} />
          <ToolTile icon="film" label="Replay" onPress={() => flagComingSoon("replay")} />
        </View>
      </LiveBottomSheet>

      {/* ---------- iOS KEYBOARD ACCESSORY ---------- */}
      {Platform.OS === "ios" ? (
        <InputAccessoryView nativeID={COMMENT_ACCESSORY_ID}>
          <View style={styles.accessoryBar}>
            <Pressable
              onPress={closeComposer}
              style={styles.accessoryDismiss}
              accessibilityRole="button"
              accessibilityLabel="Dismiss keyboard"
              hitSlop={8}
            >
              <Ionicons name="chevron-down" size={18} color={colors.text} />
              <Text style={styles.accessoryDismissText}>Done</Text>
            </Pressable>
            <Text style={styles.accessoryContext} numberOfLines={1}>
              Commenting as {hostName}
            </Text>
            <Pressable
              onPress={submitDraft}
              disabled={!draft.trim() || sending}
              style={[styles.accessorySend, (!draft.trim() || sending) && styles.accessorySendDisabled]}
              accessibilityRole="button"
              accessibilityLabel="Send comment"
              accessibilityState={{ disabled: !draft.trim() || sending }}
            >
              <Text style={styles.accessorySendText}>{sending ? "Sending…" : "Send"}</Text>
            </Pressable>
          </View>
        </InputAccessoryView>
      ) : null}
    </View>
  );
}

/* ---------- Stage renderers ---------- */

function StageHero({ participant, VideoView }: { participant: LiveParticipant | null; VideoView: ComponentType<NativeVideoViewProps> }) {
  if (!participant) return <View style={styles.stageFallback} />;
  if (participant.hasVideo && participant.videoTrack) {
    return <VideoView videoTrack={participant.videoTrack} style={StyleSheet.absoluteFillObject} objectFit="cover" mirror zOrder={0} />;
  }
  return (
    <View style={styles.stageFallback}>
      <Ionicons name="videocam-off" size={40} color={colors.muted} />
      <Text style={styles.stageFallbackText}>Your camera is off</Text>
    </View>
  );
}

function StageTile({ participant, VideoView, split }: { participant: LiveParticipant; VideoView: ComponentType<NativeVideoViewProps>; split: boolean }) {
  return (
    <View style={[styles.tile, split && styles.tileSplit]}>
      {participant.hasVideo && participant.videoTrack ? (
        <VideoView videoTrack={participant.videoTrack} style={StyleSheet.absoluteFillObject} objectFit="cover" mirror={participant.isLocal} zOrder={participant.isLocal ? 1 : 0} />
      ) : (
        <View style={styles.tilePlaceholder}>
          <Text style={styles.tilePlaceholderText}>{participant.name}</Text>
          <Text style={styles.tilePlaceholderHint}>{participant.audioMuted ? "Muted" : "Camera off"}</Text>
        </View>
      )}
      <View style={styles.tileLabel}>
        <Text style={styles.tileLabelText} numberOfLines={1}>
          {participant.isLocal ? "You" : participant.name}
          {participant.audioMuted ? " · muted" : ""}
        </Text>
      </View>
    </View>
  );
}

function FloatingGuestTile({ participant, VideoView }: { participant: LiveParticipant; VideoView: ComponentType<NativeVideoViewProps> }) {
  return (
    <View style={styles.guestTile}>
      {participant.hasVideo && participant.videoTrack ? (
        <VideoView videoTrack={participant.videoTrack} style={StyleSheet.absoluteFillObject} objectFit="cover" zOrder={0} />
      ) : (
        <View style={styles.guestPlaceholder}>
          <Ionicons name="person" size={22} color={colors.muted} />
        </View>
      )}
      <View style={styles.guestBadge}>
        <Ionicons name={participant.audioMuted ? "mic-off" : "mic"} size={11} color={participant.audioMuted ? colors.danger : colors.accent} />
        <Text style={styles.guestName} numberOfLines={1}>
          {participant.name}
        </Text>
      </View>
    </View>
  );
}

function LiveLevelControl({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  const [width, setWidth] = useState(1);
  const percent = Math.round(Math.max(0, Math.min(value, 1)) * 100);
  return (
    <View style={styles.levelControl}>
      <View style={styles.levelHeader}>
        <Text style={styles.levelLabel}>{label}</Text>
        <Text style={styles.levelValue}>{percent}%</Text>
      </View>
      <Pressable
        accessibilityRole="adjustable"
        accessibilityLabel={`${label} level`}
        accessibilityValue={{ min: 0, max: 100, now: percent }}
        accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
        onAccessibilityAction={(event) => {
          onChange(Math.max(0, Math.min(value + (event.nativeEvent.actionName === "increment" ? 0.08 : -0.08), 1)));
        }}
        onLayout={(event) => setWidth(Math.max(1, event.nativeEvent.layout.width))}
        onPress={(event) => onChange(event.nativeEvent.locationX / width)}
        style={styles.levelTrack}
      >
        <View style={[styles.levelFill, { width: `${percent}%` }]} />
        <View style={[styles.levelThumb, { left: `${percent}%` }]} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: "#02040a",
    flex: 1
  },
  center: {
    alignItems: "center",
    flex: 1,
    gap: 12,
    justifyContent: "center",
    padding: 24
  },
  connectingText: {
    color: colors.muted,
    fontSize: 15
  },
  scrimTop: {
    height: 190,
    left: 0,
    position: "absolute",
    right: 0,
    top: 0
  },
  scrimBottom: {
    bottom: 0,
    height: 360,
    left: 0,
    position: "absolute",
    right: 0
  },
  stageFallback: {
    alignItems: "center",
    backgroundColor: "#05070f",
    flex: 1,
    gap: 8,
    justifyContent: "center"
  },
  stageFallbackText: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "700"
  },
  grid: {
    flex: 1,
    flexDirection: "row",
    flexWrap: "wrap"
  },
  tile: {
    backgroundColor: "#05070f",
    height: "100%",
    width: "100%"
  },
  tileSplit: {
    height: "50%",
    width: "50%"
  },
  tilePlaceholder: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    gap: 4,
    justifyContent: "center"
  },
  tilePlaceholderHint: {
    color: colors.muted,
    fontSize: 13
  },
  tilePlaceholderText: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  tileLabel: {
    backgroundColor: "rgba(2,4,10,0.65)",
    borderRadius: 8,
    bottom: 10,
    left: 10,
    maxWidth: "70%",
    paddingHorizontal: 8,
    paddingVertical: 4,
    position: "absolute"
  },
  tileLabelText: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "700"
  },
  topBar: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    left: 0,
    paddingHorizontal: 14,
    position: "absolute",
    right: 0,
    top: 0
  },
  roundGlass: {
    alignItems: "center",
    backgroundColor: "rgba(6,14,24,0.55)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 999,
    borderWidth: 1,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  liveBadge: {
    backgroundColor: colors.danger,
    borderColor: colors.danger
  },
  liveDot: {
    backgroundColor: "#fff",
    borderRadius: 4,
    height: 8,
    width: 8
  },
  liveText: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 1
  },
  elapsed: {
    color: "#fff",
    fontSize: 13,
    fontVariant: ["tabular-nums"],
    fontWeight: "800",
    marginLeft: 2
  },
  viewerText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  identity: {
    gap: 8,
    left: 14,
    position: "absolute",
    right: 120
  },
  identityRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  hostAvatar: {
    alignItems: "center",
    backgroundColor: colors.intelligence,
    borderColor: "rgba(255,255,255,0.4)",
    borderRadius: 999,
    borderWidth: 1.5,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  hostAvatarText: {
    color: "#fff",
    fontSize: 18,
    fontWeight: "900"
  },
  hostNameRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 5
  },
  hostName: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "900"
  },
  hostSub: {
    color: "rgba(244,247,251,0.75)",
    fontSize: 13,
    fontWeight: "600",
    marginTop: 1
  },
  categoryChip: {
    alignSelf: "flex-start"
  },
  categoryText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  rail: {
    alignItems: "center",
    bottom: 200,
    gap: 16,
    justifyContent: "center",
    position: "absolute",
    right: 8
  },
  guestColumn: {
    gap: 10,
    position: "absolute",
    right: 78
  },
  guestTile: {
    backgroundColor: "#05070f",
    borderColor: "rgba(121,210,255,0.35)",
    borderRadius: 16,
    borderWidth: 1,
    height: 132,
    overflow: "hidden",
    width: 104
  },
  guestPlaceholder: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center"
  },
  guestBadge: {
    alignItems: "center",
    backgroundColor: "rgba(2,4,10,0.7)",
    borderBottomLeftRadius: 15,
    borderBottomRightRadius: 15,
    bottom: 0,
    flexDirection: "row",
    gap: 4,
    left: 0,
    paddingHorizontal: 8,
    paddingVertical: 5,
    position: "absolute",
    right: 0
  },
  guestName: {
    color: "#fff",
    flex: 1,
    fontSize: 11,
    fontWeight: "700"
  },
  inviteTile: {
    alignItems: "center",
    borderColor: colors.accent,
    borderRadius: 16,
    borderStyle: "dashed",
    borderWidth: 1.5,
    gap: 4,
    height: 96,
    justifyContent: "center",
    width: 104
  },
  inviteText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800"
  },
  bottom: {
    bottom: 0,
    gap: 12,
    left: 0,
    paddingHorizontal: 14,
    position: "absolute",
    right: 0
  },
  chatTap: {
    alignSelf: "flex-start",
    maxWidth: "100%"
  },
  inlineError: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "700"
  },
  // Warning, not error. The broadcast is running; only the microphone is
  // unconfirmed. Styling this like `inlineError` would tell the host their
  // stream had failed, which is the exact wrong reading.
  audioWarningBanner: {
    alignItems: "center",
    backgroundColor: "rgba(255,176,32,0.16)",
    borderColor: "rgba(255,176,32,0.42)",
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  audioWarningText: {
    color: "#FFD79A",
    flex: 1,
    fontSize: 12,
    fontWeight: "600",
    lineHeight: 16
  },
  audioWarningAction: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800"
  },
  controlTrayScroll: {
    flexGrow: 0
  },
  controlTray: {
    alignItems: "center",
    gap: 14,
    paddingHorizontal: 2
  },
  tray: {
    borderColor: "rgba(121,210,255,0.18)",
    borderRadius: 20,
    borderWidth: 1,
    overflow: "hidden",
    padding: 14
  },
  trayHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  trayTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  trayCount: {
    alignItems: "center",
    backgroundColor: colors.intelligence,
    borderRadius: 999,
    height: 22,
    justifyContent: "center",
    minWidth: 22,
    paddingHorizontal: 6
  },
  trayCountText: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "900"
  },
  traySeeAll: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800",
    marginRight: 6
  },
  trayCards: {
    flexDirection: "row",
    gap: 10,
    marginTop: 12
  },
  trayCard: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 14,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    gap: 8,
    padding: 10
  },
  trayCardAvatar: {
    alignItems: "center",
    backgroundColor: colors.creator,
    borderRadius: 999,
    height: 34,
    justifyContent: "center",
    width: 34
  },
  trayCardAvatarText: {
    color: colors.background,
    fontSize: 13,
    fontWeight: "900"
  },
  trayCardName: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  trayCardMeta: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600"
  },
  trayCardActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  trayDecline: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  trayDeclineText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  trayAccept: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  trayAcceptText: {
    color: colors.background,
    fontSize: 12,
    fontWeight: "900"
  },
  trayFooter: {
    flexDirection: "row",
    gap: 10,
    marginTop: 12
  },
  trayAcceptAll: {
    alignItems: "center",
    backgroundColor: colors.intelligence,
    borderRadius: 14,
    flex: 1,
    paddingVertical: 13
  },
  trayAcceptAllText: {
    color: "#fff",
    fontSize: 14,
    fontWeight: "900"
  },
  trayInvite: {
    alignItems: "center",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 14,
    borderWidth: 1,
    flex: 1,
    paddingVertical: 13
  },
  trayInviteText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  /* Sheets */
  sheetLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.5,
    textTransform: "uppercase"
  },
  sheetEmpty: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600",
    lineHeight: 20
  },
  sheetPrimary: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 14,
    marginTop: 6,
    paddingVertical: 14
  },
  sheetPrimaryText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  sheetComposer: {
    marginTop: 6
  },
  manageRow: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderRadius: 14,
    flexDirection: "row",
    gap: 10,
    padding: 10
  },
  manageAvatar: {
    alignItems: "center",
    backgroundColor: colors.intelligence,
    borderRadius: 999,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  manageAvatarText: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "900"
  },
  manageName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  manageMeta: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600"
  },
  manageBtn: {
    alignItems: "center",
    borderRadius: 999,
    height: 38,
    justifyContent: "center",
    width: 38
  },
  manageBtnAccent: {
    backgroundColor: colors.accent
  },
  manageBtnOutline: {
    borderColor: "rgba(255,255,255,0.18)",
    borderWidth: 1
  },
  manageBtnDanger: {
    borderColor: colors.danger,
    borderWidth: 1
  },
  reactionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between"
  },
  reactionTile: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderRadius: 16,
    gap: 6,
    marginBottom: 10,
    paddingVertical: 16,
    width: "31%"
  },
  reactionEmoji: {
    fontSize: 30
  },
  reactionLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  musicCard: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderRadius: 16,
    flexDirection: "row",
    gap: 12,
    padding: 14
  },
  musicArt: {
    alignItems: "center",
    backgroundColor: "rgba(66,231,212,0.12)",
    borderRadius: 12,
    height: 56,
    justifyContent: "center",
    overflow: "hidden",
    width: 56
  },
  musicCover: {
    height: "100%",
    width: "100%"
  },
  musicError: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "700",
    lineHeight: 18
  },
  musicTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  musicMeta: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600",
    marginTop: 2
  },
  musicTransport: {
    alignItems: "center",
    flexDirection: "row",
    gap: 26,
    justifyContent: "center",
    paddingVertical: 6
  },
  musicPlay: {
    alignItems: "center",
    backgroundColor: colors.creator,
    borderRadius: 999,
    height: 56,
    justifyContent: "center",
    width: 56
  },
  levelGroup: {
    gap: 12,
    paddingVertical: 4
  },
  levelControl: {
    gap: 7
  },
  levelHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  levelLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.8,
    textTransform: "uppercase"
  },
  levelValue: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  levelTrack: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderRadius: 999,
    height: 18,
    justifyContent: "center",
    overflow: "hidden"
  },
  levelFill: {
    backgroundColor: colors.creator,
    borderRadius: 999,
    height: 18
  },
  levelThumb: {
    backgroundColor: colors.text,
    borderColor: colors.creator,
    borderRadius: 999,
    borderWidth: 2,
    height: 18,
    marginLeft: -9,
    position: "absolute",
    width: 18
  },
  musicSearchRow: {
    flexDirection: "row",
    gap: 8
  },
  musicSearchInput: {
    backgroundColor: "rgba(255,255,255,0.05)",
    borderColor: "rgba(255,255,255,0.12)",
    borderRadius: 14,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    fontSize: 14,
    minHeight: 44,
    paddingHorizontal: 12
  },
  musicSearchButton: {
    alignItems: "center",
    backgroundColor: colors.creator,
    borderRadius: 14,
    justifyContent: "center",
    minHeight: 44,
    width: 48
  },
  radioMixButton: {
    alignItems: "center",
    backgroundColor: colors.creator,
    borderRadius: 16,
    flexDirection: "row",
    gap: 8,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 14
  },
  radioMixButtonText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  },
  musicRow: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: "rgba(255,255,255,0.1)",
    borderRadius: 14,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 58,
    padding: 9
  },
  musicRowArt: {
    alignItems: "center",
    backgroundColor: "rgba(66,231,212,0.1)",
    borderRadius: 10,
    height: 42,
    justifyContent: "center",
    overflow: "hidden",
    width: 42
  },
  musicRowTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  musicRowMeta: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    marginTop: 2
  },
  disabled: {
    opacity: 0.52
  },
  noteBanner: {
    alignItems: "center",
    backgroundColor: "rgba(50,230,179,0.1)",
    borderColor: "rgba(50,230,179,0.3)",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    padding: 12
  },
  noteText: {
    color: colors.text,
    flex: 1,
    fontSize: 13,
    fontWeight: "700"
  },
  toolGrid: {
    flexDirection: "row",
    flexWrap: "wrap"
  },
  errorTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900",
    textAlign: "center"
  },
  errorBody: {
    color: colors.muted,
    fontSize: 15,
    textAlign: "center"
  },
  exitButton: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    marginTop: 8,
    paddingHorizontal: 20,
    paddingVertical: 12
  },
  exitText: {
    color: colors.text,
    fontWeight: "800"
  },
  accessoryBar: {
    alignItems: "center",
    backgroundColor: "rgba(10,18,30,0.98)",
    borderTopColor: "rgba(255,255,255,0.12)",
    borderTopWidth: 1,
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  accessoryDismiss: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.08)",
    borderRadius: 999,
    flexDirection: "row",
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  accessoryDismissText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  accessoryContext: {
    color: colors.muted,
    flex: 1,
    fontSize: 12,
    fontWeight: "700",
    textAlign: "center"
  },
  accessorySend: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    paddingHorizontal: 18,
    paddingVertical: 7
  },
  accessorySendDisabled: {
    opacity: 0.45
  },
  accessorySendText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  }
});
