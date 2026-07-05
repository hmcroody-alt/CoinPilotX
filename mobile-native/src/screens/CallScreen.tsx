import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  AppStateStatus,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import {
  acceptCall,
  declineCall,
  endCall,
  getActiveCalls,
  getCallEvents,
  getCallStatus,
  loadCachedActiveCalls,
  loadCachedCallStatus,
  markCallConnected,
  markRingSeen,
  openCallWebFallback,
  PulseCall,
  PulseCallEvent,
  PulseCallType,
  requestCallJoinToken,
  sendCallControl,
  startConversationCall
} from "../api/calls";
import { useNativeCallRoom } from "../calls/useNativeCallRoom";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

const STATUS_REFRESH_MS = 3200;

export function CallScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "Call">) {
  const params = route.params || {};
  const initialCallId = params.callId ? String(params.callId) : "";
  const callType: PulseCallType = params.callType === "video" ? "video" : "audio";
  const [callId, setCallId] = useState(initialCallId);
  const [call, setCall] = useState<PulseCall | null>(null);
  const [activeCalls, setActiveCalls] = useState<PulseCall[]>([]);
  const [events, setEvents] = useState<PulseCallEvent[]>([]);
  const [loading, setLoading] = useState(Boolean(initialCallId));
  const [actionBusy, setActionBusy] = useState("");
  const [error, setError] = useState("");
  const [speakerEnabled, setSpeakerEnabled] = useState(true);
  const [minimized, setMinimized] = useState(false);
  const appState = useRef<AppStateStatus>(AppState.currentState);
  const room = useNativeCallRoom();

  const title = useMemo(() => {
    if (params.title) return params.title;
    if (call?.call_type === "video" || callType === "video") return "PulseSoc Video";
    return "PulseSoc Voice";
  }, [call?.call_type, callType, params.title]);
  const incoming = params.direction === "incoming" || call?.status === "ringing";
  const connected = room.connected || ["connected", "active"].includes(String(call?.status || ""));
  const canStartFromConversation = Boolean(params.conversationId && !callId);

  const refresh = useCallback(async () => {
    if (callId) {
      const next = await getCallStatus(callId);
      setCall(next);
      setEvents(next.events || []);
      setError("");
      return;
    }
    const data = await getActiveCalls();
    setActiveCalls(data.calls || []);
    setError("");
  }, [callId]);

  const startCall = useCallback(async (nextType: PulseCallType = callType) => {
    if (!params.conversationId) return;
    setActionBusy(nextType === "video" ? "video" : "voice");
    setError("");
    try {
      const next = await startConversationCall(params.conversationId, nextType);
      setCall(next);
      setCallId(next.call_id);
      const join = next.join?.token ? next.join : await requestCallJoinToken(next.call_id);
      const joined = await room.connect(join, { video: nextType === "video" });
      if (joined) await markCallConnected(next.call_id, { native_state: "connected" }).catch(() => undefined);
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "Call could not start.");
    } finally {
      setActionBusy("");
    }
  }, [callType, params.conversationId, room]);

  const answer = useCallback(async () => {
    if (!callId) return;
    setActionBusy("accept");
    setError("");
    try {
      const next = await acceptCall(callId);
      setCall(next);
      const join = next.join?.token ? next.join : await requestCallJoinToken(callId);
      const joined = await room.connect(join, { video: next.call_type === "video" || callType === "video" });
      if (joined) await markCallConnected(callId, { native_state: "connected" }).catch(() => undefined);
    } catch (answerError) {
      setError(answerError instanceof Error ? answerError.message : "Call could not be answered.");
    } finally {
      setActionBusy("");
    }
  }, [callId, callType, room]);

  const decline = useCallback(async () => {
    if (!callId) return;
    setActionBusy("decline");
    try {
      await declineCall(callId);
      await room.disconnect();
      navigation.goBack();
    } catch (declineError) {
      setError(declineError instanceof Error ? declineError.message : "Call could not be declined.");
    } finally {
      setActionBusy("");
    }
  }, [callId, navigation, room]);

  const hangup = useCallback(async () => {
    if (!callId) {
      navigation.goBack();
      return;
    }
    setActionBusy("end");
    try {
      await endCall(callId);
      await room.disconnect();
      navigation.goBack();
    } catch (hangupError) {
      setError(hangupError instanceof Error ? hangupError.message : "Call could not end.");
    } finally {
      setActionBusy("");
    }
  }, [callId, navigation, room]);

  const toggleAudio = useCallback(async () => {
    if (!callId) return;
    const enabled = !room.audioEnabled;
    await room.setMicrophoneEnabled(enabled);
    await sendCallControl(callId, enabled ? "unmute-audio" : "mute-audio").catch(() => undefined);
  }, [callId, room]);

  const toggleVideo = useCallback(async () => {
    if (!callId) return;
    const enabled = !room.videoEnabled;
    await room.setCameraEnabled(enabled);
    await sendCallControl(callId, enabled ? "enable-video" : "disable-video").catch(() => undefined);
  }, [callId, room]);

  const toggleSpeaker = useCallback(async () => {
    if (!callId) return;
    const enabled = !speakerEnabled;
    setSpeakerEnabled(enabled);
    await sendCallControl(callId, "speaker", { enabled }).catch(() => undefined);
  }, [callId, speakerEnabled]);

  const switchCamera = useCallback(async () => {
    if (!callId) return;
    await room.switchCamera();
    await sendCallControl(callId, "switch-camera").catch(() => undefined);
  }, [callId, room]);

  const toggleMinimized = useCallback(async () => {
    if (!callId) return;
    const next = !minimized;
    setMinimized(next);
    await sendCallControl(callId, next ? "minimize" : "restore").catch(() => undefined);
  }, [callId, minimized]);

  useEffect(() => {
    let mounted = true;
    if (callId) {
      loadCachedCallStatus(callId).then((cached) => {
        if (mounted && cached) setCall(cached);
      });
      markRingSeen(callId).catch(() => undefined);
      refresh().catch((loadError) => setError(loadError instanceof Error ? loadError.message : "Call could not load."));
    } else {
      loadCachedActiveCalls().then((cached) => {
        if (mounted) setActiveCalls(cached.calls || []);
      });
      refresh().catch(() => undefined);
    }
    setLoading(false);
    return () => {
      mounted = false;
    };
  }, [callId, refresh]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (appState.current === "active") refresh().catch(() => undefined);
    }, STATUS_REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      const wasBackgrounded = appState.current.match(/inactive|background/);
      appState.current = nextState;
      if (wasBackgrounded && nextState === "active") refresh().catch(() => undefined);
    });
    return () => subscription.remove();
  }, [refresh]);

  useEffect(() => {
    if (!callId) return;
    getCallEvents(callId).then(setEvents).catch(() => undefined);
  }, [callId]);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.hero}>
        <Text style={styles.kicker}>PulseSoc Native Calls</Text>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.subtitle}>
          {callId ? statusCopy(call, room.connectionState) : "Start or resume a server-authoritative PulseSoc call."}
        </Text>
      </View>

      {error || room.error ? <Text style={styles.error}>{error || room.error}</Text> : null}

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : canStartFromConversation ? (
        <View style={styles.panel}>
          <Text style={styles.panelTitle}>Start call</Text>
          <Text style={styles.panelText}>Uses the existing Communications V2 call engine, LiveKit token route, server permissions, and notification routing.</Text>
          <View style={styles.actionRow}>
            <ActionButton label="Voice" busy={actionBusy === "voice"} onPress={() => startCall("audio")} />
            <ActionButton label="Video" busy={actionBusy === "video"} onPress={() => startCall("video")} />
          </View>
        </View>
      ) : call ? (
        <>
          <View style={styles.callSurface}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{(call.call_type || callType) === "video" ? "VID" : "AUD"}</Text>
            </View>
            <Text style={styles.roomName}>{call.room_name || call.public_id || call.call_id}</Text>
            <Text style={styles.status}>{connected ? "Connected" : incoming ? "Ringing" : room.connecting ? "Connecting" : "Ready"}</Text>
            <Text style={styles.meta}>{participantSummary(call)}</Text>
          </View>

          {incoming && !connected ? (
            <View style={styles.actionRow}>
              <ActionButton label="Accept" tone="accent" busy={actionBusy === "accept"} onPress={answer} />
              <ActionButton label="Decline" tone="danger" busy={actionBusy === "decline"} onPress={decline} />
            </View>
          ) : (
            <View style={styles.controls}>
              <ActionButton label={room.audioEnabled ? "Mute" : "Unmute"} onPress={toggleAudio} disabled={!callId} />
              <ActionButton label={room.videoEnabled ? "Video Off" : "Video On"} onPress={toggleVideo} disabled={!callId} />
              <ActionButton label={speakerEnabled ? "Speaker" : "Earpiece"} onPress={toggleSpeaker} disabled={!callId} />
              <ActionButton label="Flip" onPress={switchCamera} disabled={!callId || !room.videoEnabled} />
              <ActionButton label={minimized ? "Restore" : "Minimize"} onPress={toggleMinimized} disabled={!callId} />
              <ActionButton label="End" tone="danger" busy={actionBusy === "end"} onPress={hangup} />
            </View>
          )}

          <View style={styles.panel}>
            <Text style={styles.panelTitle}>Native readiness</Text>
            <ReadinessLine label="Backend call state" value={call.status || "loaded"} />
            <ReadinessLine label="LiveKit token" value={call.join?.token ? "available" : call.livekit?.configured ? "request on accept" : "not returned"} />
            <ReadinessLine label="Native media runtime" value={room.supported ? room.connectionState : "web fallback"} />
            <ReadinessLine label="Participants" value={String(Math.max(1, call.participants?.length || room.participantCount || 1))} />
          </View>

          <View style={styles.panel}>
            <Text style={styles.panelTitle}>Recent events</Text>
            {events.length ? events.slice(0, 8).map((event, index) => (
              <Text key={`${event.id || index}-${event.event_type || event.type}`} style={styles.panelText}>
                {event.event_type || event.type || "call:event"} {event.created_at ? `• ${event.created_at}` : ""}
              </Text>
            )) : <Text style={styles.panelText}>No call events returned yet.</Text>}
          </View>
        </>
      ) : (
        <View style={styles.panel}>
          <Text style={styles.panelTitle}>Active calls</Text>
          {activeCalls.length ? activeCalls.map((item) => (
            <Pressable key={item.call_id} style={styles.callRow} onPress={() => setCallId(item.call_id)}>
              <Text style={styles.callRowTitle}>{item.call_type === "video" ? "Video call" : "Voice call"}</Text>
              <Text style={styles.panelText}>{item.status || "active"} • {item.call_id}</Text>
            </Pressable>
          )) : <Text style={styles.panelText}>No active calls returned by `/api/calls/active`.</Text>}
        </View>
      )}

      <Pressable style={styles.fallbackButton} onPress={() => openCallWebFallback(callId || undefined, params.conversationId)}>
        <Text style={styles.fallbackText}>Open safe web fallback</Text>
      </Pressable>
    </ScrollView>
  );
}

function ActionButton({
  label,
  tone = "default",
  busy = false,
  disabled = false,
  onPress
}: {
  label: string;
  tone?: "default" | "accent" | "danger";
  busy?: boolean;
  disabled?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityLabel={label}
      disabled={busy || disabled}
      style={({ pressed }) => [
        styles.actionButton,
        tone === "accent" && styles.accentButton,
        tone === "danger" && styles.dangerButton,
        (busy || disabled) && styles.disabled,
        pressed && styles.pressed
      ]}
      onPress={onPress}
    >
      <Text style={[styles.actionText, tone === "accent" && styles.darkText]}>{busy ? "..." : label}</Text>
    </Pressable>
  );
}

function ReadinessLine({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.readinessRow}>
      <Text style={styles.panelText}>{label}</Text>
      <Text style={styles.readinessValue}>{value}</Text>
    </View>
  );
}

function statusCopy(call: PulseCall | null, connectionState: string) {
  if (!call) return "Loading call state from PulseSoc.";
  if (call.status === "ringing") return "Incoming call routed by the existing PulseSoc notification and call engine.";
  if (connectionState === "connected" || call.status === "connected" || call.status === "active") return "Connected through the native call foundation.";
  if (call.status === "ended") return "Call has ended.";
  return `Call state: ${call.status || connectionState || "ready"}.`;
}

function participantSummary(call: PulseCall) {
  const names = (call.participants || []).map((participant) => participant.display_name || participant.username).filter(Boolean);
  if (names.length) return names.slice(0, 3).join(", ");
  return call.conversation_id ? `Conversation ${call.conversation_id}` : "PulseSoc participants";
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  content: {
    gap: 16,
    padding: 16,
    paddingBottom: 32
  },
  hero: {
    gap: 6
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  error: {
    backgroundColor: "rgba(255,107,107,0.12)",
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.danger,
    padding: 12
  },
  center: {
    alignItems: "center",
    minHeight: 180,
    justifyContent: "center"
  },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 14
  },
  panelTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  panelText: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  callSurface: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    minHeight: 260,
    justifyContent: "center",
    padding: 20
  },
  avatar: {
    alignItems: "center",
    backgroundColor: colors.accentStrong,
    borderRadius: 42,
    height: 84,
    justifyContent: "center",
    width: 84
  },
  avatarText: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  roomName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800",
    textAlign: "center"
  },
  status: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "900"
  },
  meta: {
    color: colors.muted,
    fontSize: 13,
    textAlign: "center"
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  controls: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 46,
    minWidth: 104,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  accentButton: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  dangerButton: {
    borderColor: colors.danger
  },
  actionText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  darkText: {
    color: "#07110f"
  },
  disabled: {
    opacity: 0.48
  },
  pressed: {
    opacity: 0.82
  },
  readinessRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between"
  },
  readinessValue: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  callRow: {
    backgroundColor: "rgba(255,255,255,0.04)",
    borderRadius: 8,
    gap: 4,
    padding: 10
  },
  callRowTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  fallbackButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 44,
    justifyContent: "center"
  },
  fallbackText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  }
});
