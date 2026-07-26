import { ResizeMode, Video } from "expo-av";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ComponentType, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  Alert,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  getLiveState,
  getLiveKitToken,
  joinLive,
  listLiveChat,
  listLiveNow,
  livePlaybackUrl,
  livePosterUrl,
  liveSupportsNativePlayback,
  liveSupportsNativeWebRtc,
  liveWebUrl,
  loadCachedLiveDiscovery,
  loadCachedLiveState,
  openLiveWebFallback,
  PulseLiveChatMessage,
  PulseLiveItem,
  PulseLiveState,
  cancelJoinRequest,
  confirmGuestPublishComplete,
  getLiveJoinStatus,
  reactToLive,
  requestToJoinLive,
  sendLiveChat
} from "../api/live";
import { sharePulseObject } from "../sharing/nativeShare";
import { useLiveBroadcastRoom } from "../live/useLiveBroadcastRoom";
import { canConnectAsCohostPublisher } from "../live/liveSession";
import { profileNavigationParams, profileTargetFromAuthor } from "../api/profileTarget";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "LiveDetail">>;
type NativeVideoViewProps = {
  videoTrack?: any;
  style?: any;
  objectFit?: "cover" | "contain";
  mirror?: boolean;
  zOrder?: number;
};

export function LiveScreen({ route, navigation }: Props) {
  const initialLiveId = Number(route?.params?.liveId || 0);
  const [items, setItems] = useState<PulseLiveItem[]>([]);
  const [scheduled, setScheduled] = useState<PulseLiveItem[]>([]);
  const [selected, setSelected] = useState<PulseLiveItem | null>(null);
  const [state, setState] = useState<PulseLiveState | null>(null);
  const [messages, setMessages] = useState<PulseLiveChatMessage[]>([]);
  const [chatBody, setChatBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [joined, setJoined] = useState(false);
  const [joinRequestId, setJoinRequestId] = useState(0);
  const [guestStatus, setGuestStatus] = useState("");
  const [guestError, setGuestError] = useState("");
  const [guestPublishing, setGuestPublishing] = useState(false);
  const [muted, setMuted] = useState(false);
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const [liveKitPlaybackFailed, setLiveKitPlaybackFailed] = useState(false);
  const [VideoViewComponent, setVideoViewComponent] = useState<ComponentType<NativeVideoViewProps> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const videoRef = useRef<Video>(null);
  const guestPublishKeyRef = useRef("");
  const room = useLiveBroadcastRoom();
  const connectLiveRoom = room.connect;
  const disconnectLiveRoom = room.disconnect;
  const setRemoteAudioEnabled = room.setRemoteAudioEnabled;

  useEffect(() => {
    if (Platform.OS === "web") return undefined;
    let mounted = true;
    import("@livekit/react-native")
      .then((module) => {
        if (mounted) setVideoViewComponent(() => module.VideoView as ComponentType<NativeVideoViewProps>);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const data = await listLiveNow({ limit: 24 });
      setItems(data.items || []);
      setScheduled(data.scheduled || []);
      if (initialLiveId) {
        const focused = (data.items || []).find((item) => item.id === initialLiveId);
        if (focused) openLive(focused).catch(() => undefined);
        else openLiveById(initialLiveId).catch(() => undefined);
      }
    } catch (loadError) {
      const cached = await loadCachedLiveDiscovery();
      if (cached.items?.length || cached.scheduled?.length) {
        setItems(cached.items || []);
        setScheduled(cached.scheduled || []);
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Live discovery could not load.");
      }
      if (initialLiveId) openLiveById(initialLiveId).catch(() => undefined);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function openLive(item: PulseLiveItem) {
    setSelected(item);
    setState(null);
    setMessages([]);
    setJoined(false);
    setPlaybackFailed(false);
    setLiveKitPlaybackFailed(false);
    await refreshLiveState(item.id, "open");
    handleJoin(item.id).catch(() => undefined);
  }

  async function openLiveById(liveId: number) {
    const cached = await loadCachedLiveState(liveId);
    if (cached?.discovery) {
      setSelected(cached.discovery);
      setState(cached);
      setMessages(cached.messages || []);
    } else {
      setSelected({ id: liveId, live_id: liveId, title: "PulseSoc Live", creator_name: "PulseSoc Creator" });
    }
    setPlaybackFailed(false);
    setLiveKitPlaybackFailed(false);
    await refreshLiveState(liveId, "open");
    handleJoin(liveId).catch(() => undefined);
  }

  async function refreshLiveState(liveId: number, mode: "open" | "poll" | "manual" = "manual") {
    if (mode !== "poll") setError("");
    try {
      const next = await getLiveState(liveId);
      setState(next);
      setMessages(next.messages || []);
      if (next.discovery) setSelected(next.discovery);
      setJoinRequestId(next.viewer_join_request?.requestId || next.guest?.requestId || 0);
      setGuestStatus(next.guest ? next.guest.status || "accepted" : next.viewer_join_request?.status || "");
      if (next.guest) setGuestError("");
    } catch (stateError) {
      const cached = await loadCachedLiveState(liveId);
      if (cached) {
        setState(cached);
        setMessages(cached.messages || []);
        setOffline(true);
      } else if (mode !== "poll") {
        setError(stateError instanceof Error ? stateError.message : "Live state could not load.");
      }
    }
  }

  async function handleJoin(liveId: number) {
    setBusy("join");
    try {
      const result = await joinLive(liveId);
      setJoined(Boolean(result.ok || result.status === "watching"));
      if (typeof result.viewer_count === "number") {
        setState((current) => current ? { ...current, viewer_count: result.viewer_count } : current);
      }
      await connectNativePlayback(liveId);
    } catch (joinError) {
      setError(joinError instanceof Error ? joinError.message : "Live join failed.");
    } finally {
      setBusy("");
    }
  }

  const connectNativePlayback = useCallback(
    async (liveId: number) => {
      if (room.connected || room.connecting) return;
      setLiveKitPlaybackFailed(false);
      const credentials = await getLiveKitToken(liveId, "viewer");
      if (!credentials) {
        setLiveKitPlaybackFailed(true);
        setError("PulseSoc could not mint native Live viewer credentials. Retry or wait for the provider room to become available.");
        return;
      }
      const ok = await connectLiveRoom(credentials, { publish: false });
      if (!ok) {
        setLiveKitPlaybackFailed(true);
        setError(room.error || "Native Live playback could not connect. Retry or wait for the provider room to become available.");
      }
    },
    [connectLiveRoom, room.connected, room.connecting, room.error]
  );

  async function handleLeave() {
    setJoined(false);
    await disconnectLiveRoom("viewer_left").catch(() => undefined);
    await releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
  }

  function toggleSound() {
    if (liveSupportsNativeWebRtc(state || selected)) {
      if (!room.connected || room.remoteAudioTrackCount <= 0) return;
      setRemoteAudioEnabled(!room.remoteAudioEnabled).catch((soundError) => {
        setError(soundError instanceof Error ? soundError.message : "Live audio control failed.");
      });
      return;
    }
    setMuted((value) => !value);
  }

  async function handleReact(reactionType = "fire") {
    if (!activeLiveId) return;
    setBusy(`react-${reactionType}`);
    try {
      await reactToLive(activeLiveId, reactionType);
      await refreshLiveState(activeLiveId, "manual");
    } catch (reactError) {
      setError(reactError instanceof Error ? reactError.message : "Live reaction failed.");
    } finally {
      setBusy("");
    }
  }

  async function handleSendChat() {
    if (!activeLiveId || !chatBody.trim()) return;
    const body = chatBody.trim();
    setChatBody("");
    setBusy("chat");
    try {
      const result = await sendLiveChat(activeLiveId, body);
      if (result.chat) setMessages((current) => mergeMessages([...current, result.chat ? result.chat : undefined]));
      await refreshLiveState(activeLiveId, "manual");
    } catch (chatError) {
      setChatBody(body);
      setError(chatError instanceof Error ? chatError.message : "Live chat could not send.");
    } finally {
      setBusy("");
    }
  }

  function closeViewer() {
    disconnectLiveRoom("viewer_closed").catch(() => undefined);
    setSelected(null);
    setState(null);
    setMessages([]);
    setJoined(false);
    setPlaybackFailed(false);
    setError("");
    if (initialLiveId && navigation?.canGoBack()) navigation.goBack();
  }

  function navigateToHostProfile(item: PulseLiveItem | null | undefined) {
    const target = profileTargetFromAuthor((item?.author || item?.creator) as Record<string, unknown> | undefined, item as unknown as Record<string, unknown>);
    const params = profileNavigationParams(target, item?.creator_name || "Profile");
    if (params) navigation?.navigate("ProfileDetail", params);
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [initialLiveId]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (selected?.id) {
      pollRef.current = setInterval(() => {
        refreshLiveState(selected.id, "poll").catch(() => undefined);
        listLiveChat(selected.id).then(setMessages).catch(() => undefined);
      }, 8000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [selected?.id]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (status) => {
      if (status !== "active") return;
      if (selected?.id) {
        refreshLiveState(selected.id, "manual").catch(() => undefined);
        listLiveChat(selected.id).then(setMessages).catch(() => undefined);
      } else {
        load("refresh").catch(() => undefined);
      }
    });
    return () => subscription.remove();
  }, [selected?.id]);

  const active = state?.discovery || selected;
  const activeLiveId = Number(active?.id || state?.live_id || 0);
  const playbackUrl = useMemo(() => livePlaybackUrl(state || active), [state, active]);
  const posterUrl = useMemo(() => livePosterUrl(state || active), [state, active]);
  const canPlayHls = liveSupportsNativePlayback(state || active) && !playbackFailed;
  const currentStatus = String(state?.status || active?.status || "").toLowerCase();
  const liveMayAcceptWebRtc = Boolean(activeLiveId && !["ended", "offline", "archived", "deleted", "failed"].includes(currentStatus));
  const canUseWebRtc = Boolean((liveSupportsNativeWebRtc(state || active) || (!canPlayHls && liveMayAcceptWebRtc)) && !playbackFailed && !liveKitPlaybackFailed);
  const playbackOwnerId = `live:${activeLiveId}`;
  const liveKitParticipants = useMemo(
    () =>
      room.participants
        .filter((participant) => !participant.isLocal && (participant.hasVideo || participant.hasAudio))
        .sort((a, b) => Number(b.isHost) - Number(a.isHost)),
    [room.participants]
  );
  const liveKitVideoParticipant = liveKitParticipants.find((participant) => participant.videoTrack);
  const soundLabel = canUseWebRtc
    ? room.connected
      ? room.remoteAudioTrackCount > 0
        ? room.remoteAudioEnabled
          ? "Sound on"
          : "Muted"
        : "Waiting audio"
      : room.reconnecting
        ? "Reconnecting"
        : "Connecting"
    : muted
      ? "Muted"
      : "Sound on";
  const currentGuest = state?.guest || null;
  const currentJoinRequest = state?.viewer_join_request || null;
  const canRequestGuest = Boolean(activeLiveId && state?.accepting_guests && joined && !currentGuest && !currentJoinRequest);
  const canCancelGuestRequest = Boolean(activeLiveId && joinRequestId && !currentGuest && ["pending", "requested"].includes(guestStatus));
  const guestIsLive = Boolean(currentGuest && room.connected && room.canPublish && room.localAudioTrackCount > 0);
  const guestActionLabel = guestPublishing
    ? "Joining…"
    : guestIsLive
      ? "Guest Live"
      : currentGuest
        ? "Join Guest"
        : canCancelGuestRequest
          ? "Cancel Guest"
          : currentJoinRequest
            ? "Waiting Host"
            : "Request Guest";

  const refreshGuestStatus = useCallback(async (liveId: number) => {
    const status = await getLiveJoinStatus(liveId);
    setJoinRequestId(status.request?.requestId || status.guest?.requestId || 0);
    setGuestStatus(status.guest ? status.guest.status || "accepted" : status.status);
    setGuestError(status.errorCode ? status.message : "");
    setState((current) =>
      current
        ? {
            ...current,
            viewer_role: status.guest ? "guest" : current.viewer_role,
            viewer_join_request: status.request,
            guest: status.guest
          }
        : current
    );
    return status;
  }, []);

  const requestGuestSeat = useCallback(async () => {
    if (!activeLiveId || guestPublishing || busy === "guest") return;
    setBusy("guest");
    setGuestError("");
    try {
      const result = await requestToJoinLive(activeLiveId, {
        cameraReady: Platform.OS !== "web",
        micReady: Platform.OS !== "web",
        networkQuality: room.connectionQuality || "good"
      });
      setJoinRequestId(Number(result.request_id || 0));
      setGuestStatus(String(result.status || "pending"));
      await refreshLiveState(activeLiveId, "manual");
      await refreshGuestStatus(activeLiveId).catch(() => undefined);
    } catch (requestError) {
      setGuestError(requestError instanceof Error ? requestError.message : "Co-host request failed.");
    } finally {
      setBusy("");
    }
  }, [activeLiveId, busy, guestPublishing, refreshGuestStatus, room.connectionQuality]);

  const cancelGuestSeatRequest = useCallback(async () => {
    if (!activeLiveId || !joinRequestId || busy === "guest") return;
    setBusy("guest");
    setGuestError("");
    try {
      await cancelJoinRequest(activeLiveId, joinRequestId);
      setJoinRequestId(0);
      setGuestStatus("cancelled");
      await refreshLiveState(activeLiveId, "manual");
    } catch (cancelError) {
      setGuestError(cancelError instanceof Error ? cancelError.message : "Could not cancel the co-host request.");
    } finally {
      setBusy("");
    }
  }, [activeLiveId, busy, joinRequestId]);

  const confirmGuestPublish = useCallback(
    async (liveId: number, credentialsTraceId = "") => {
      const guestId = state?.guest?.guestId || currentGuest?.guestId || 0;
      if (!guestId) throw new Error("PulseSoc approved this co-host slot, but no guest id was returned.");
      const deadline = Date.now() + 15000;
      let lastMessage = "";
      while (Date.now() < deadline) {
        const result = await confirmGuestPublishComplete(liveId, guestId, {
          traceId: credentialsTraceId,
          participantIdentity: room.participants.find((participant) => participant.isLocal)?.identity || ""
        });
        lastMessage = result.message;
        if (result.state === "live") {
          setGuestStatus("live");
          setState((current) => (current ? { ...current, guest: result.guest || current.guest, viewer_role: "guest" } : current));
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, Math.max(400, Math.min(result.retryAfterMs || 800, 1600))));
      }
      throw new Error(lastMessage || "LiveKit did not confirm guest audio/video before timeout.");
    },
    [currentGuest?.guestId, room.participants, state?.guest?.guestId]
  );

  const publishAsGuest = useCallback(async () => {
    if (!activeLiveId || guestPublishing || guestIsLive) return;
    const guestId = currentGuest?.guestId || state?.guest?.guestId || 0;
    if (!guestId) {
      await refreshGuestStatus(activeLiveId);
      return;
    }
    setGuestPublishing(true);
    setGuestError("");
    try {
      const credentials = await getLiveKitToken(activeLiveId, "cohost");
      if (!canConnectAsCohostPublisher(credentials)) {
        throw new Error("PulseSoc has not returned a verified co-host publishing token yet.");
      }
      const ok = await connectLiveRoom(credentials, { publish: true });
      if (!ok || room.error) {
        throw new Error(room.error || "Co-host media could not connect.");
      }
      await confirmGuestPublish(activeLiveId, credentials.traceId);
      await refreshLiveState(activeLiveId, "manual");
    } catch (publishError) {
      setGuestError(publishError instanceof Error ? publishError.message : "Co-host publish failed.");
    } finally {
      setGuestPublishing(false);
    }
  }, [
    activeLiveId,
    confirmGuestPublish,
    connectLiveRoom,
    currentGuest?.guestId,
    guestIsLive,
    guestPublishing,
    refreshGuestStatus,
    room.error,
    state?.guest?.guestId
  ]);

  const handleGuestAction = useCallback(() => {
    if (guestIsLive || guestPublishing) return;
    if (currentGuest) {
      publishAsGuest().catch(() => undefined);
      return;
    }
    if (canCancelGuestRequest) {
      Alert.alert("Cancel co-host request?", "The host will no longer see your request to join this Live.", [
        { text: "Keep waiting", style: "cancel" },
        { text: "Cancel request", style: "destructive", onPress: () => cancelGuestSeatRequest().catch(() => undefined) }
      ]);
      return;
    }
    if (canRequestGuest) requestGuestSeat().catch(() => undefined);
  }, [canCancelGuestRequest, canRequestGuest, cancelGuestSeatRequest, currentGuest, guestIsLive, guestPublishing, publishAsGuest, requestGuestSeat]);

  useEffect(() => {
    if (!activeLiveId || !currentGuest || guestIsLive || guestPublishing) return;
    const key = `${activeLiveId}:${currentGuest.guestId}:${currentGuest.status}`;
    if (!["active", "accepted", "joining", "joined", "publishing"].includes(String(currentGuest.status || ""))) return;
    if (guestPublishKeyRef.current === key) return;
    guestPublishKeyRef.current = key;
    publishAsGuest().catch(() => undefined);
  }, [activeLiveId, currentGuest, guestIsLive, guestPublishing, publishAsGuest]);

  useEffect(() => {
    if (!activeLiveId || !canPlayHls || !playbackUrl) {
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
      return;
    }
    if (canUseWebRtc) {
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
      return;
    }
    claimMediaPlayback({
      id: playbackOwnerId,
      kind: "live",
      pause: () => videoRef.current?.pauseAsync().then(() => undefined),
      stop: () => videoRef.current?.stopAsync().then(() => undefined)
    }).then((granted) => granted ? videoRef.current?.playAsync() : undefined).catch(() => undefined);
    return () => { releaseMediaPlayback(playbackOwnerId).catch(() => undefined); };
  }, [activeLiveId, canPlayHls, canUseWebRtc, playbackOwnerId, playbackUrl]);

  useEffect(() => {
    if (!activeLiveId || !canUseWebRtc || !joined) return;
    connectNativePlayback(activeLiveId).catch(() => undefined);
  }, [activeLiveId, canUseWebRtc, connectNativePlayback, joined]);

  useEffect(() => {
    if (!canUseWebRtc || !room.connected || !room.remoteAudioEnabled || room.remoteAudioTrackCount <= 0) return;
    setRemoteAudioEnabled(true).catch(() => undefined);
  }, [canUseWebRtc, room.connected, room.remoteAudioEnabled, room.remoteAudioTrackCount, setRemoteAudioEnabled]);

  useEffect(() => {
    setPlaybackFailed(false);
    setLiveKitPlaybackFailed(false);
  }, [playbackUrl, activeLiveId]);

  useEffect(
    () => () => {
      disconnectLiveRoom("viewer_unmounted").catch(() => undefined);
    },
    [disconnectLiveRoom]
  );

  if (loading && !items.length && !active) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Live</Text>
      </View>
    );
  }

  if (active) {
    return (
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.viewer}>
          <View style={styles.viewerTopBar}>
            <Pressable style={styles.closeButton} onPress={closeViewer}>
              <Text style={styles.closeText}>Close</Text>
            </Pressable>
            <View style={styles.liveBadge}>
              <View style={styles.liveDot} />
              <Text style={styles.liveBadgeText}>{String(state?.status || active.status || "live").toUpperCase()}</Text>
            </View>
            <Pressable style={styles.closeButton} onPress={() => openLiveWebFallback(activeLiveId).catch(() => undefined)}>
              <Text style={styles.closeText}>Web</Text>
            </Pressable>
          </View>

          <View style={styles.player}>
            {posterUrl ? <Image source={{ uri: posterUrl }} style={styles.poster} resizeMode="cover" blurRadius={playbackUrl ? 0 : 2} /> : null}
            {canUseWebRtc ? (
              <View style={styles.nativeStage}>
                {room.connected && liveKitVideoParticipant?.videoTrack && VideoViewComponent ? (
                  <VideoViewComponent videoTrack={liveKitVideoParticipant.videoTrack} style={StyleSheet.absoluteFill} objectFit="cover" mirror={false} zOrder={0} />
                ) : (
                  <View style={styles.unsupported}>
                    {room.connecting || room.reconnecting ? <ActivityIndicator color={colors.accent} /> : null}
                    <Text style={styles.unsupportedTitle}>
                      {room.connected ? "Waiting for host media" : room.reconnecting ? "Reconnecting to Live" : "Connecting native Live"}
                    </Text>
                    <Text style={styles.unsupportedText}>
                      {room.error || "PulseSoc is joining the existing LiveKit room and will show audio/video as soon as the host publishes media."}
                    </Text>
                    {room.error ? (
                      <Pressable style={styles.primaryButton} onPress={() => connectNativePlayback(activeLiveId).catch(() => undefined)}>
                        <Text style={styles.primaryButtonText}>Reconnect</Text>
                      </Pressable>
                    ) : null}
                  </View>
                )}
                {room.connected && liveKitParticipants.length ? (
                  <View style={styles.liveKitStatus}>
                    <View style={[styles.liveDot, styles.greenDot]} />
                    <Text style={styles.liveKitStatusText}>{room.remoteAudioTrackCount} audio · {room.remoteVideoTrackCount} video</Text>
                  </View>
                ) : null}
              </View>
            ) : canPlayHls && playbackUrl ? (
              <Video
                ref={videoRef}
                source={{ uri: playbackUrl }}
                style={styles.video}
                resizeMode={ResizeMode.COVER}
                shouldPlay={false}
                isMuted={muted}
                usePoster={Boolean(posterUrl)}
                posterSource={posterUrl ? { uri: posterUrl } : undefined}
                onError={() => {
                  setPlaybackFailed(true);
                  setError("Native playback could not start for this Live.");
                }}
              />
            ) : (
              <View style={styles.unsupported}>
                <Text style={styles.unsupportedTitle}>Live playback unavailable</Text>
                <Text style={styles.unsupportedText}>
                  PulseSoc did not return a native LiveKit room or HLS playback URL for this Live.
                </Text>
                <Pressable style={styles.primaryButton} onPress={() => openLiveWebFallback(activeLiveId).catch(() => undefined)}>
                  <Text style={styles.primaryButtonText}>Open Live Web Viewer</Text>
                </Pressable>
              </View>
            )}
            <Pressable style={styles.muteButton} onPress={toggleSound}>
              <Text style={styles.muteText}>{soundLabel}</Text>
            </Pressable>
          </View>

          <View style={styles.viewerInfo}>
            <Text style={styles.viewerTitle} numberOfLines={2}>{active.title || "PulseSoc Live"}</Text>
            <Pressable onPress={() => navigateToHostProfile(active)}>
              <Text style={styles.viewerMeta} numberOfLines={1}>{active.creator_name || active.author?.display_name || "PulseSoc Creator"} · {active.category || "Live"}</Text>
            </Pressable>
            <Text style={styles.viewerMeta}>{Number(state?.viewer_count || active.viewer_count || 0)} watching · {canUseWebRtc ? "webrtc native" : state?.playback?.preferred_transport || "state"} · {joined ? "joined" : "local leave available"}</Text>
            {offline ? <Text style={styles.offline}>Showing cached Live state</Text> : null}
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>

          <View style={styles.actionRow}>
            <Pressable style={styles.actionButton} disabled={busy === "join"} onPress={() => activeLiveId ? handleJoin(activeLiveId) : undefined}>
              <Text style={styles.actionText}>{joined ? "Refresh Join" : "Join"}</Text>
            </Pressable>
            <Pressable style={styles.actionButton} onPress={() => handleLeave().catch(() => undefined)}>
              <Text style={styles.actionText}>Leave</Text>
            </Pressable>
            <Pressable style={styles.actionButton} disabled={busy.startsWith("react")} onPress={() => handleReact("🔥")}>
              <Text style={styles.actionText}>Fire</Text>
            </Pressable>
            <Pressable style={styles.actionButton} onPress={() => sharePulseObject({
              kind: "live",
              url: liveWebUrl(activeLiveId),
              title: active.title || "PulseSoc Live",
              description: active.category,
              author: active.author?.display_name || active.creator?.display_name || active.creator_name,
              previewImageUrl: livePosterUrl(active)
            }).catch(() => undefined)}>
              <Text style={styles.actionText}>Share</Text>
            </Pressable>
          </View>

          {state?.accepting_guests || currentJoinRequest || currentGuest ? (
            <View style={styles.guestJoinPanel}>
              <View style={styles.guestJoinHeader}>
                <View style={[styles.liveDot, guestIsLive ? styles.greenDot : undefined]} />
                <Text style={styles.guestJoinTitle}>
                  {guestIsLive ? "Co-host live" : currentGuest ? "Host approved co-host" : currentJoinRequest ? "Co-host request pending" : "Join as guest"}
                </Text>
                <Text style={styles.guestJoinStatus}>{guestStatus || "ready"}</Text>
              </View>
              <Text style={styles.guestJoinText}>
                {guestIsLive
                  ? "Your camera and microphone are publishing through the same LiveKit room as the host."
                  : currentGuest
                    ? "Tap Join Guest to publish camera and microphone natively. PulseSoc confirms audio/video with the server before marking you live."
                    : currentJoinRequest
                      ? "Waiting for the host to accept. This screen will promote you into the Live automatically after approval."
                      : "Request a server-authoritative co-host seat. Camera and microphone publish only after host approval."}
              </Text>
              {guestError ? <Text style={styles.guestJoinError}>{guestError}</Text> : null}
              <Pressable
                style={[
                  styles.guestJoinButton,
                  guestIsLive ? styles.guestJoinButtonLive : undefined,
                  (!canRequestGuest && !canCancelGuestRequest && !currentGuest) || guestPublishing ? styles.disabledButton : undefined
                ]}
                disabled={guestIsLive || guestPublishing || (!canRequestGuest && !canCancelGuestRequest && !currentGuest)}
                onPress={handleGuestAction}
              >
                <Text style={styles.guestJoinButtonText}>{guestActionLabel}</Text>
              </Pressable>
            </View>
          ) : null}

          <FlatList
            data={messages}
            keyExtractor={(item) => String(item.id)}
            style={styles.chatList}
            contentContainerStyle={styles.chatContent}
            renderItem={({ item }) => <ChatLine message={item} />}
            ListEmptyComponent={<Text style={styles.emptyChat}>Live chat messages appear here.</Text>}
          />

          <View style={styles.chatComposer}>
            <TextInput
              style={styles.chatInput}
              value={chatBody}
              onChangeText={setChatBody}
              placeholder="Send a Live chat message"
              placeholderTextColor={colors.muted}
              multiline
            />
            <Pressable style={[styles.sendButton, busy === "chat" ? styles.disabledButton : undefined]} disabled={busy === "chat"} onPress={handleSendChat}>
              <Text style={styles.sendText}>Send</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    );
  }

  return (
    <FlatList
      style={styles.root}
      data={items}
      keyExtractor={(item) => String(item.id)}
      refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      contentContainerStyle={styles.content}
      ListHeaderComponent={
        <View style={styles.header}>
          <Text style={styles.title}>Live</Text>
          <Text style={styles.subtitle}>{offline ? "Showing saved Live discovery" : "Discover active PulseSoc broadcasts"}</Text>
          <View style={styles.headerActions}>
            <Pressable style={styles.primaryButton} onPress={() => load("refresh").catch(() => undefined)}>
              <Text style={styles.primaryButtonText}>Refresh</Text>
            </Pressable>
            <Pressable style={styles.secondaryButton} onPress={() => navigation?.navigate("LiveStudio")}>
              <Text style={styles.secondaryButtonText}>Go Live</Text>
            </Pressable>
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Text style={styles.sectionTitle}>Live now</Text>
        </View>
      }
      renderItem={({ item }) => <LiveCard item={item} onOpen={openLive} onHostPress={() => navigateToHostProfile(item)} />}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No one is live right now</Text>
          <Text style={styles.emptyText}>Native discovery uses the existing PulseSoc Live backend. Tap Go Live to start a native broadcast.</Text>
        </View>
      }
      ListFooterComponent={
        <View style={styles.footer}>
          <Text style={styles.sectionTitle}>Scheduled</Text>
          {scheduled.length ? scheduled.map((item) => <LiveCard key={`scheduled-${item.id}`} item={item} onOpen={openLive} onHostPress={() => navigateToHostProfile(item)} />) : (
            <Text style={styles.footerNote}>Scheduled Live/events will appear here when the existing API returns them to native.</Text>
          )}
        </View>
      }
    />
  );
}

function LiveCard({ item, onOpen, onHostPress }: { item: PulseLiveItem; onOpen: (item: PulseLiveItem) => void; onHostPress: () => void }) {
  return (
    <Pressable style={styles.card} onPress={() => onOpen(item)}>
      <View style={styles.thumb}>
        {item.thumbnail_url || item.preview_url ? <Image source={{ uri: item.thumbnail_url || item.preview_url }} style={styles.thumbImage} /> : <View style={styles.thumbFallback} />}
        <View style={styles.liveChip}>
          <View style={styles.liveDot} />
          <Text style={styles.liveChipText}>{String(item.status || "live").toUpperCase()}</Text>
        </View>
      </View>
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle} numberOfLines={2}>{item.title || "PulseSoc Live"}</Text>
        <Pressable onPress={onHostPress}>
          <Text style={styles.cardMeta} numberOfLines={1}>{item.creator_name || "PulseSoc Creator"} · {item.category || "Live"}</Text>
        </Pressable>
        <Text style={styles.cardMeta}>{Number(item.viewer_count || 0)} watching · {item.playback?.preferred_transport || "state"} · {formatShortTime(item.started_at || item.scheduled_at || "")}</Text>
      </View>
    </Pressable>
  );
}

function ChatLine({ message }: { message: PulseLiveChatMessage }) {
  const blocked = message.moderation_status === "blocked";
  return (
    <View style={[styles.chatLine, blocked ? styles.blockedChatLine : undefined]}>
      <Text style={styles.chatName}>{message.display_name || "Viewer"}</Text>
      <Text style={styles.chatText}>{blocked ? "Message blocked by moderation." : message.body}</Text>
    </View>
  );
}

function mergeMessages(messages: Array<PulseLiveChatMessage | undefined>) {
  const seen = new Set<number>();
  return messages.filter((message): message is PulseLiveChatMessage => {
    if (!message || seen.has(message.id)) return false;
    seen.add(message.id);
    return true;
  });
}

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 42,
    justifyContent: "center"
  },
  actionRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  actionText: {
    color: colors.text,
    fontWeight: "800"
  },
  blockedChatLine: {
    opacity: 0.62
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginBottom: 12,
    padding: 10
  },
  cardBody: {
    flex: 1,
    justifyContent: "center"
  },
  cardMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  cardTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center"
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  chatComposer: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    flexDirection: "row",
    gap: 8,
    padding: 10
  },
  chatContent: {
    gap: 8,
    padding: 12
  },
  chatInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    maxHeight: 92,
    minHeight: 44,
    padding: 10
  },
  chatLine: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 9
  },
  chatList: {
    flex: 1
  },
  chatName: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  chatText: {
    color: colors.text,
    marginTop: 2
  },
  closeButton: {
    minWidth: 54,
    padding: 8
  },
  closeText: {
    color: colors.text,
    fontWeight: "800"
  },
  content: {
    padding: 16,
    paddingBottom: 36
  },
  disabledButton: {
    opacity: 0.62
  },
  empty: {
    alignItems: "center",
    padding: 28
  },
  emptyChat: {
    color: colors.muted,
    textAlign: "center"
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 8,
    textAlign: "center"
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  error: {
    color: colors.danger,
    marginTop: 10
  },
  footer: {
    paddingTop: 8
  },
  footerNote: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 8
  },
  header: {
    gap: 8,
    marginBottom: 14
  },
  headerActions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8
  },
  liveBadge: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  liveBadgeText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  liveChip: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.55)",
    borderRadius: 999,
    flexDirection: "row",
    gap: 6,
    left: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
    position: "absolute",
    top: 8
  },
  liveChipText: {
    color: colors.text,
    fontSize: 10,
    fontWeight: "900"
  },
  liveDot: {
    backgroundColor: colors.danger,
    borderRadius: 999,
    height: 8,
    width: 8
  },
  greenDot: {
    backgroundColor: colors.accent
  },
  guestJoinButton: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 10,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 16
  },
  guestJoinButtonLive: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accent,
    borderWidth: 1
  },
  guestJoinButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  guestJoinError: {
    color: colors.danger,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 8
  },
  guestJoinHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  guestJoinPanel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginHorizontal: 14,
    marginBottom: 8,
    padding: 12
  },
  guestJoinStatus: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    marginLeft: "auto",
    textTransform: "uppercase"
  },
  guestJoinText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 7
  },
  guestJoinTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  liveKitStatus: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.56)",
    borderRadius: 999,
    flexDirection: "row",
    gap: 7,
    left: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    position: "absolute",
    top: 12
  },
  liveKitStatusText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  muteButton: {
    backgroundColor: "rgba(0,0,0,0.56)",
    borderRadius: 999,
    bottom: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    position: "absolute",
    right: 12
  },
  muteText: {
    color: colors.text,
    fontWeight: "800"
  },
  nativeStage: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#02050b"
  },
  offline: {
    color: colors.warning,
    marginTop: 6
  },
  player: {
    aspectRatio: 16 / 9,
    backgroundColor: "#02050b",
    overflow: "hidden",
    position: "relative"
  },
  poster: {
    ...StyleSheet.absoluteFillObject,
    height: "100%",
    width: "100%"
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  primaryButtonText: {
    color: "#04110d",
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  secondaryButtonText: {
    color: colors.text,
    fontWeight: "900"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 14
  },
  sendButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  sendText: {
    color: "#04110d",
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    lineHeight: 21
  },
  thumb: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    height: 92,
    overflow: "hidden",
    width: 116
  },
  thumbFallback: {
    backgroundColor: colors.surfaceRaised,
    flex: 1
  },
  thumbImage: {
    height: "100%",
    width: "100%"
  },
  title: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900"
  },
  unsupported: {
    alignItems: "center",
    flex: 1,
    gap: 10,
    justifyContent: "center",
    padding: 20
  },
  unsupportedText: {
    color: colors.muted,
    lineHeight: 20,
    textAlign: "center"
  },
  unsupportedTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  video: {
    ...StyleSheet.absoluteFillObject
  },
  viewer: {
    flex: 1
  },
  viewerInfo: {
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    padding: 14
  },
  viewerMeta: {
    color: colors.muted,
    marginTop: 5
  },
  viewerTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  viewerTopBar: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: 10
  }
});
