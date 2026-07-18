import { Ionicons } from "@expo/vector-icons";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ComponentType, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  AppState,
  AppStateStatus,
  Image,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  acceptCall,
  declineCall,
  endCall,
  getCallStatus,
  loadCachedCallStatus,
  markCallConnected,
  markRingSeen,
  openCallWebFallback,
  PulseCall,
  PulseCallParticipant,
  PulseCallType,
  requestCallJoinToken,
  sendCallControl,
  startConversationCall,
  submitCallQuality
} from "../api/calls";
import { useNativeCallRoom } from "../calls/useNativeCallRoom";
import { pausePulseRadio } from "../core/pulseRadio";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

const STATUS_REFRESH_MS = 4200;
const TERMINAL_CALL_STATES = new Set(["ended", "declined", "missed", "failed", "busy", "cancelled"]);

type NativeVideoViewProps = {
  videoTrack?: any;
  style?: any;
  objectFit?: "cover" | "contain";
  mirror?: boolean;
  zOrder?: number;
};

export function CallScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "Call">) {
  const params = route.params || {};
  const initialCallId = params.callId ? String(params.callId) : "";
  const requestedType: PulseCallType = params.callType === "video" ? "video" : "audio";
  const [callId, setCallId] = useState(initialCallId);
  const [call, setCall] = useState<PulseCall | null>(null);
  const [loading, setLoading] = useState(Boolean(initialCallId || params.conversationId));
  const [actionBusy, setActionBusy] = useState("");
  const [error, setError] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [VideoViewComponent, setVideoViewComponent] = useState<ComponentType<NativeVideoViewProps> | null>(null);
  const autoStartRequested = useRef(false);
  const joinRequested = useRef(false);
  const qualitySubmitted = useRef(false);
  const connectedAtMs = useRef(0);
  const appState = useRef<AppStateStatus>(AppState.currentState);
  const room = useNativeCallRoom();
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const glow = useRef(new Animated.Value(0)).current;

  const callType: PulseCallType = call?.call_type === "video" ? "video" : requestedType;
  const incoming = params.direction === "incoming";
  const terminal = TERMINAL_CALL_STATES.has(String(call?.status || ""));
  const connected = room.connected || ["connected", "active"].includes(String(call?.status || ""));
  const caller = useMemo(() => callParticipant(call, incoming), [call, incoming]);
  const title = params.title || caller.display_name || caller.username || (callType === "video" ? "PulseSoc Video" : "PulseSoc Voice");

  useEffect(() => {
    pausePulseRadio().catch(() => undefined);
    if (Platform.OS !== "web") {
      import("@livekit/react-native")
        .then((module) => setVideoViewComponent(() => module.VideoView as ComponentType<NativeVideoViewProps>))
        .catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    if (reducedMotion) return undefined;
    const animation = Animated.loop(Animated.sequence([
      Animated.timing(glow, { toValue: 1, duration: 1600, useNativeDriver: true }),
      Animated.timing(glow, { toValue: 0, duration: 1600, useNativeDriver: true })
    ]));
    animation.start();
    return () => animation.stop();
  }, [glow, reducedMotion]);

  const refresh = useCallback(async () => {
    if (!callId) return;
    const next = await getCallStatus(callId);
    setCall(next);
    setError("");
    setLoading(false);
  }, [callId]);

  const connectProvider = useCallback(async (target: PulseCall, targetType: PulseCallType) => {
    if (!target.call_id || joinRequested.current || room.connected || room.connecting) return;
    joinRequested.current = true;
    try {
      const join = target.join?.token ? target.join : await requestCallJoinToken(target.call_id);
      const joined = await room.connect(join, { video: targetType === "video" });
      if (!joined) throw new Error("The secure media room could not connect.");
      connectedAtMs.current = Date.now();
      await markCallConnected(target.call_id, {
        native_state: "connected",
        platform: Platform.OS,
        media: targetType
      }).catch(() => undefined);
    } catch (joinError) {
      joinRequested.current = false;
      setError(joinError instanceof Error ? joinError.message : "Call media could not connect.");
    }
  }, [room.connect, room.connected, room.connecting]);

  const startCall = useCallback(async () => {
    if (!params.conversationId) return;
    setActionBusy("start");
    setError("");
    try {
      const next = await startConversationCall(params.conversationId, requestedType);
      setCall(next);
      setCallId(next.call_id);
      await connectProvider(next, requestedType);
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "Call could not start.");
    } finally {
      setLoading(false);
      setActionBusy("");
    }
  }, [connectProvider, params.conversationId, requestedType]);

  useEffect(() => {
    if (!params.conversationId || params.direction !== "outgoing" || callId || autoStartRequested.current) return;
    autoStartRequested.current = true;
    startCall().catch(() => undefined);
  }, [callId, params.conversationId, params.direction, startCall]);

  useEffect(() => {
    let mounted = true;
    if (!callId) return undefined;
    loadCachedCallStatus(callId).then((cached) => {
      if (mounted && cached) setCall(cached);
    });
    markRingSeen(callId).catch(() => undefined);
    refresh().catch((loadError) => {
      if (mounted) {
        setLoading(false);
        setError(loadError instanceof Error ? loadError.message : "Call state could not load.");
      }
    });
    return () => { mounted = false; };
  }, [callId, refresh]);

  useEffect(() => {
    if (!call || terminal || incoming && ["created", "ringing"].includes(String(call.status || ""))) return;
    if (["accepted", "connecting", "connected", "active", "reconnecting"].includes(String(call.status || ""))) {
      connectProvider(call, callType).catch(() => undefined);
    }
  }, [call, callType, connectProvider, incoming, terminal]);

  useEffect(() => {
    if (!terminal) return;
    room.disconnect(`backend_${call?.status || "ended"}`).catch(() => undefined);
  }, [call?.status, room.disconnect, terminal]);

  useEffect(() => {
    if (!connected) return undefined;
    if (!connectedAtMs.current) connectedAtMs.current = Date.now();
    const update = () => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - connectedAtMs.current) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [connected]);

  useEffect(() => {
    if (!callId) return undefined;
    const timer = setInterval(() => {
      if (appState.current === "active") refresh().catch(() => undefined);
    }, STATUS_REFRESH_MS);
    return () => clearInterval(timer);
  }, [callId, refresh]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      const wasBackgrounded = appState.current.match(/inactive|background/);
      appState.current = nextState;
      if (callId) {
        sendCallControl(callId, "visibility", { visible: nextState === "active", app_state: nextState }).catch(() => undefined);
      }
      if (wasBackgrounded && nextState === "active") refresh().catch(() => undefined);
    });
    return () => subscription.remove();
  }, [callId, refresh]);

  const reportQuality = useCallback(async (reason: string) => {
    if (!callId || qualitySubmitted.current) return;
    qualitySubmitted.current = true;
    await submitCallQuality(callId, {
      rating: room.connectionQuality === "excellent" ? 5 : room.connectionQuality === "good" ? 4 : room.connectionQuality === "poor" ? 2 : 3,
      connection_quality: room.connectionQuality,
      duration_seconds: elapsedSeconds,
      reconnect_count: room.reconnectCount,
      participant_count: room.participantCount,
      call_type: callType,
      platform: Platform.OS,
      reason
    }).catch(() => undefined);
  }, [callId, callType, elapsedSeconds, room.connectionQuality, room.participantCount, room.reconnectCount]);

  const answer = useCallback(async () => {
    if (!callId) return;
    setActionBusy("accept");
    setError("");
    try {
      const next = await acceptCall(callId);
      setCall(next);
      await connectProvider(next, next.call_type === "video" ? "video" : requestedType);
    } catch (answerError) {
      setError(answerError instanceof Error ? answerError.message : "Call could not be answered.");
    } finally {
      setActionBusy("");
    }
  }, [callId, connectProvider, requestedType]);

  const decline = useCallback(async () => {
    if (!callId) return navigation.goBack();
    setActionBusy("decline");
    try {
      await declineCall(callId);
      await room.disconnect("declined");
      navigation.goBack();
    } catch (declineError) {
      setError(declineError instanceof Error ? declineError.message : "Call could not be declined.");
    } finally {
      setActionBusy("");
    }
  }, [callId, navigation, room.disconnect]);

  const hangup = useCallback(async () => {
    setActionBusy("end");
    try {
      await reportQuality("native_hangup");
      if (callId) await endCall(callId);
      await room.disconnect("native_hangup");
      navigation.goBack();
    } catch (hangupError) {
      setError(hangupError instanceof Error ? hangupError.message : "Call could not end.");
    } finally {
      setActionBusy("");
    }
  }, [callId, navigation, reportQuality, room.disconnect]);

  const runMediaAction = useCallback(async (action: () => Promise<void>, backendAction: Parameters<typeof sendCallControl>[1], payload: Record<string, unknown> = {}) => {
    setError("");
    try {
      await action();
      if (callId) await sendCallControl(callId, backendAction, payload).catch(() => undefined);
    } catch (mediaError) {
      setError(mediaError instanceof Error ? mediaError.message : "Media control failed.");
    }
  }, [callId]);

  const minimize = useCallback(async () => {
    if (callId) await sendCallControl(callId, "minimize").catch(() => undefined);
    if (navigation.canGoBack()) navigation.goBack();
  }, [callId, navigation]);

  const statusLabel = callStatusLabel(call, room.connectionState, room.reconnecting, incoming);
  const showVideo = callType === "video" && Boolean(room.remoteVideoTrack || room.localVideoTrack);

  return (
    <View style={styles.screen}>
      <Pressable accessibilityRole="button" accessibilityLabel="Show or hide call controls" style={StyleSheet.absoluteFill} onPress={() => setControlsVisible((visible) => !visible)}>
        {showVideo && VideoViewComponent && room.remoteVideoTrack ? (
          <VideoViewComponent videoTrack={room.remoteVideoTrack} style={styles.remoteVideo} objectFit="cover" />
        ) : (
          <View style={styles.audioBackground}>
            <View style={styles.planet} />
            <Animated.View style={[styles.signalHalo, { opacity: glow.interpolate({ inputRange: [0, 1], outputRange: [0.22, 0.64] }), transform: [{ scale: glow.interpolate({ inputRange: [0, 1], outputRange: [0.88, 1.12] }) }] }]} />
            <Avatar participant={caller} large />
          </View>
        )}
      </Pressable>

      <View pointerEvents="box-none" style={[styles.topLayer, { paddingTop: Math.max(insets.top + 10, 22) }]}>
        <View style={styles.topBar}>
          <CircleButton label="Minimize call" icon="chevron-down" onPress={minimize} />
          <View style={styles.identity}>
            <Text style={styles.kicker}>{callType === "video" ? "PULSESOC VIDEO" : "PULSESOC VOICE"}</Text>
            <Text style={styles.title} numberOfLines={1}>{title}</Text>
            <View style={styles.statusRow}>
              <View style={[styles.liveDot, room.reconnecting && styles.warningDot]} />
              <Text style={styles.status}>{statusLabel}{connected ? ` · ${formatDuration(elapsedSeconds)}` : ""}</Text>
            </View>
          </View>
          <CircleButton label="Call options" icon="ellipsis-horizontal" onPress={() => openCallWebFallback(callId || undefined, params.conversationId)} />
        </View>

        {showVideo && VideoViewComponent && room.localVideoTrack ? (
          <View style={styles.localPreviewShell}>
            <VideoViewComponent videoTrack={room.localVideoTrack} style={styles.localPreview} objectFit="cover" mirror zOrder={2} />
          </View>
        ) : null}

        {room.reconnecting ? (
          <View style={styles.connectionBanner}>
            <ActivityIndicator color={colors.accent} size="small" />
            <Text style={styles.connectionBannerText}>Reconnecting securely… media will resume automatically.</Text>
          </View>
        ) : null}
        {error || room.error ? (
          <View style={[styles.connectionBanner, styles.errorBanner]}>
            <Ionicons name="warning-outline" color="#ff6b8d" size={18} />
            <Text style={styles.errorText} numberOfLines={3}>{error || room.error}</Text>
          </View>
        ) : null}
      </View>

      {loading ? (
        <View style={styles.centerState}>
          <ActivityIndicator size="large" color={colors.accent} />
          <Text style={styles.centerStateTitle}>Opening secure call</Text>
          <Text style={styles.centerStateCopy}>Synchronizing PulseSoc and the encrypted media room.</Text>
        </View>
      ) : incoming && !connected && !terminal ? (
        <View style={[styles.incomingActions, { paddingBottom: Math.max(insets.bottom + 28, 42) }]}>
          <CallControl label="Decline" icon="call" danger busy={actionBusy === "decline"} onPress={decline} />
          <CallControl label="Accept" icon={callType === "video" ? "videocam" : "call"} active busy={actionBusy === "accept"} onPress={answer} />
        </View>
      ) : terminal ? (
        <View style={styles.centerState}>
          <Ionicons name="checkmark-circle-outline" size={58} color={colors.accent} />
          <Text style={styles.centerStateTitle}>Call {String(call?.status || "ended")}</Text>
          <Text style={styles.centerStateCopy}>{formatDuration(elapsedSeconds)} · {room.reconnectCount} reconnects</Text>
          <Pressable style={styles.doneButton} onPress={() => navigation.goBack()}><Text style={styles.doneText}>Done</Text></Pressable>
        </View>
      ) : controlsVisible ? (
        <View style={[styles.controlDock, { paddingBottom: Math.max(insets.bottom + 16, 28) }]}>
          <View style={styles.qualityPill}>
            <Ionicons name="shield-checkmark-outline" size={15} color={colors.accent} />
            <Text style={styles.qualityText}>{room.reconnecting ? "RECONNECTING" : `${room.connectionQuality.toUpperCase()} · ${Math.max(1, room.participantCount)} IN CALL`}</Text>
          </View>
          <View style={styles.controlRow}>
            <CallControl label={room.audioEnabled ? "Mute" : "Unmute"} icon={room.audioEnabled ? "mic" : "mic-off"} active={!room.audioEnabled} onPress={() => runMediaAction(() => room.setMicrophoneEnabled(!room.audioEnabled), room.audioEnabled ? "mute-audio" : "unmute-audio")} />
            <CallControl label={room.videoEnabled ? "Camera" : "Camera off"} icon={room.videoEnabled ? "videocam" : "videocam-off"} active={room.videoEnabled} onPress={() => runMediaAction(() => room.setCameraEnabled(!room.videoEnabled), room.videoEnabled ? "disable-video" : "enable-video")} />
            <CallControl label={room.speakerEnabled ? "Speaker" : "Earpiece"} icon={room.speakerEnabled ? "volume-high" : "ear-outline"} active={room.speakerEnabled} onLongPress={() => runMediaAction(room.showAudioRoutePicker, "speaker", { picker: true })} onPress={() => runMediaAction(() => room.setSpeakerEnabled(!room.speakerEnabled), "speaker", { enabled: !room.speakerEnabled })} />
            <CallControl label="Flip" icon="camera-reverse" disabled={!room.videoEnabled} onPress={() => runMediaAction(room.switchCamera, "switch-camera")} />
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel="End call" disabled={actionBusy === "end"} style={({ pressed }) => [styles.endButton, pressed && styles.pressed]} onPress={hangup}>
            <Ionicons name="call" color="#fff" size={30} style={styles.endIcon} />
            <Text style={styles.endText}>{actionBusy === "end" ? "Ending…" : "End Call"}</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

function CircleButton({ label, icon, onPress }: { label: string; icon: keyof typeof Ionicons.glyphMap; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} style={({ pressed }) => [styles.circleButton, pressed && styles.pressed]} onPress={onPress}>
      <Ionicons name={icon} color={colors.text} size={24} />
    </Pressable>
  );
}

function CallControl({ label, icon, active, danger, busy, disabled, onLongPress, onPress }: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  active?: boolean;
  danger?: boolean;
  busy?: boolean;
  disabled?: boolean;
  onLongPress?: () => void;
  onPress: () => void;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={busy || disabled} style={({ pressed }) => [styles.control, active && styles.controlActive, danger && styles.controlDanger, disabled && styles.disabled, pressed && styles.pressed]} onLongPress={onLongPress} onPress={onPress}>
      {busy ? <ActivityIndicator color={danger ? "#ff6b8d" : colors.accent} /> : <Ionicons name={icon} color={danger ? "#ff6b8d" : active ? "#04100d" : colors.text} size={27} style={danger ? styles.declineIcon : undefined} />}
      <Text style={[styles.controlLabel, active && !danger && styles.controlActiveLabel, danger && styles.dangerLabel]}>{label}</Text>
    </Pressable>
  );
}

function Avatar({ participant, large = false }: { participant: PulseCallParticipant; large?: boolean }) {
  const name = participant.display_name || participant.username || "PulseSoc";
  return (
    <View style={[styles.avatarShell, large && styles.avatarLarge]}>
      {participant.avatar_url ? <Image source={{ uri: participant.avatar_url }} style={styles.avatarImage} /> : <Text style={[styles.avatarInitials, large && styles.avatarInitialsLarge]}>{initialsFor(name)}</Text>}
      <View style={styles.avatarLiveDot} />
    </View>
  );
}

function callParticipant(call: PulseCall | null, incoming: boolean): PulseCallParticipant {
  if (!call) return {};
  const participants = call.participants || [];
  const desiredRole = incoming ? "caller" : "callee";
  return participants.find((item) => String(item.role || "").toLowerCase() === desiredRole) || participants[0] || call.participant || {};
}

function initialsFor(name: string) {
  return String(name || "PS").trim().split(/\s+/).slice(0, 2).map((part) => part[0]?.toUpperCase() || "").join("") || "PS";
}

function callStatusLabel(call: PulseCall | null, providerState: string, reconnecting: boolean, incoming: boolean) {
  if (reconnecting) return "Reconnecting";
  if (providerState === "connected") return "Encrypted · Connected";
  if (providerState === "connecting") return "Connecting";
  const status = String(call?.status || "");
  if (incoming && ["created", "ringing"].includes(status)) return "Incoming call";
  if (status === "ringing") return "Ringing";
  if (TERMINAL_CALL_STATES.has(status)) return status;
  return status || "Preparing call";
}

function formatDuration(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#030812", overflow: "hidden" },
  remoteVideo: { ...StyleSheet.absoluteFillObject },
  audioBackground: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center", backgroundColor: "#030812" },
  planet: { position: "absolute", width: 520, height: 520, borderRadius: 260, backgroundColor: "rgba(25,64,102,0.24)", right: -220, top: 160, borderWidth: 1, borderColor: "rgba(97,216,255,0.12)" },
  signalHalo: { position: "absolute", width: 280, height: 280, borderRadius: 140, borderWidth: 2, borderColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.8, shadowRadius: 38 },
  topLayer: { ...StyleSheet.absoluteFillObject, justifyContent: "flex-start" },
  topBar: { alignItems: "center", flexDirection: "row", gap: 12, paddingHorizontal: 18 },
  circleButton: { width: 52, height: 52, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(4,12,25,0.78)", borderWidth: 1, borderColor: "rgba(97,216,255,0.25)" },
  identity: { flex: 1, alignItems: "center", minWidth: 0 },
  kicker: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 2 },
  title: { color: colors.text, fontSize: 22, fontWeight: "900", marginTop: 3, maxWidth: "100%" },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 },
  liveDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent },
  warningDot: { backgroundColor: "#ffbf55" },
  status: { color: "#aebbd0", fontSize: 13, fontWeight: "700" },
  localPreviewShell: { position: "absolute", top: 112, right: 18, width: 112, height: 160, borderRadius: 24, overflow: "hidden", borderWidth: 2, borderColor: colors.accent, backgroundColor: "#091522", shadowColor: colors.accent, shadowOpacity: 0.35, shadowRadius: 18 },
  localPreview: { width: "100%", height: "100%" },
  connectionBanner: { alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 9, backgroundColor: "rgba(7,24,37,0.92)", borderColor: "rgba(48,230,185,0.4)", borderWidth: 1, borderRadius: 18, marginTop: 20, maxWidth: "90%", paddingHorizontal: 14, paddingVertical: 10 },
  errorBanner: { borderColor: "rgba(255,83,125,0.48)" },
  connectionBannerText: { color: colors.text, fontWeight: "700", fontSize: 13, flexShrink: 1 },
  errorText: { color: "#ff92aa", fontWeight: "700", fontSize: 13, flexShrink: 1 },
  centerState: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center", gap: 12, padding: 32 },
  centerStateTitle: { color: colors.text, fontSize: 25, fontWeight: "900", textAlign: "center" },
  centerStateCopy: { color: "#99a8be", fontSize: 15, lineHeight: 22, textAlign: "center" },
  doneButton: { marginTop: 12, minWidth: 150, borderRadius: 24, backgroundColor: colors.accent, alignItems: "center", paddingVertical: 14 },
  doneText: { color: "#03100d", fontWeight: "900", fontSize: 16 },
  incomingActions: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", justifyContent: "center", gap: 56, paddingHorizontal: 24, paddingTop: 24, backgroundColor: "rgba(2,7,14,0.75)" },
  controlDock: { position: "absolute", bottom: 0, left: 0, right: 0, alignItems: "center", gap: 18, paddingTop: 18, paddingHorizontal: 16, backgroundColor: "rgba(2,7,14,0.88)", borderTopColor: "rgba(97,216,255,0.18)", borderTopWidth: 1 },
  qualityPill: { flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: "rgba(48,230,185,0.08)", borderWidth: 1, borderColor: "rgba(48,230,185,0.24)", borderRadius: 18, paddingHorizontal: 12, paddingVertical: 7 },
  qualityText: { color: "#aef4df", fontSize: 11, fontWeight: "900", letterSpacing: 1 },
  controlRow: { flexDirection: "row", justifyContent: "space-between", width: "100%", maxWidth: 430 },
  control: { alignItems: "center", justifyContent: "center", gap: 7, width: 76, minHeight: 76, borderRadius: 28, backgroundColor: "rgba(17,29,45,0.94)", borderWidth: 1, borderColor: "rgba(97,216,255,0.2)" },
  controlActive: { backgroundColor: colors.accent, borderColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.4, shadowRadius: 14 },
  controlDanger: { borderColor: "rgba(255,83,125,0.55)", backgroundColor: "rgba(70,12,30,0.82)" },
  controlLabel: { color: colors.text, fontSize: 11, fontWeight: "800" },
  controlActiveLabel: { color: "#03100d" },
  dangerLabel: { color: "#ff9bb1" },
  declineIcon: { transform: [{ rotate: "135deg" }] },
  endButton: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10, backgroundColor: "#e53f64", borderRadius: 26, minHeight: 52, width: "100%", maxWidth: 270, shadowColor: "#ff416c", shadowOpacity: 0.36, shadowRadius: 16 },
  endIcon: { transform: [{ rotate: "135deg" }] },
  endText: { color: "#fff", fontWeight: "900", fontSize: 16 },
  avatarShell: { width: 96, height: 96, borderRadius: 48, alignItems: "center", justifyContent: "center", backgroundColor: "#122239", borderWidth: 2, borderColor: colors.accent, overflow: "visible" },
  avatarLarge: { width: 196, height: 196, borderRadius: 98, borderWidth: 3, shadowColor: colors.accent, shadowOpacity: 0.55, shadowRadius: 30 },
  avatarImage: { width: "100%", height: "100%", borderRadius: 999 },
  avatarInitials: { color: colors.text, fontWeight: "900", fontSize: 28 },
  avatarInitialsLarge: { fontSize: 54 },
  avatarLiveDot: { position: "absolute", right: 5, bottom: 8, width: 22, height: 22, borderRadius: 11, backgroundColor: colors.accent, borderWidth: 4, borderColor: "#03101b" },
  disabled: { opacity: 0.35 },
  pressed: { opacity: 0.72, transform: [{ scale: 0.96 }] }
});
