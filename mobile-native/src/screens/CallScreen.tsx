import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppState,
  AppStateStatus,
  Pressable,
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
import { PulseCommandAction, PulseCommandAvatar, PulseCommandHeader, PulseCommandMetric, PulseCommandPanel } from "../components/PulseCommand";
import { LogiNexusScrollContainer, LogiNexusStatePanel } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { pausePulseRadio } from "../core/pulseRadio";

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
  const insets = useSafeAreaInsets();

  useEffect(() => {
    pausePulseRadio().catch(() => undefined);
  }, []);

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
    if (next && navigation.canGoBack()) navigation.goBack();
  }, [callId, minimized, navigation]);

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

  const statusLabel = callId ? callStatusLabel(call, room.connectionState, connected, incoming, room.connecting) : "Ready";
  const providerBoundary = room.supported ? "Native media runtime" : "Provider fallback";

  return (
    <LogiNexusScrollContainer bottomDock={false} contentStyle={[styles.content, { paddingTop: Math.max(insets.top + 12, 24) }]}>
      <PulseCommandHeader
        title={title}
        subtitle={callId ? statusCopy(call, room.connectionState) : "Start or resume a server-authoritative PulseSoc call."}
        status={statusLabel}
        tone={connected ? "safety" : incoming ? "danger" : "default"}
        actions={<PulseCommandAction compact label="Web fallback" tone="warning" onPress={() => openCallWebFallback(callId || undefined, params.conversationId)} />}
      />

      {error || room.error ? (
        <LogiNexusStatePanel state="error" title="Call state interrupted" body={error || room.error} style={styles.statePanel} />
      ) : null}

      {loading ? (
        <LogiNexusStatePanel state="loading" title="Synchronizing call" body="Loading PulseSoc call state, provider readiness, and participant signals." loading />
      ) : canStartFromConversation ? (
        <PulseCommandPanel style={styles.panel}>
          <View style={styles.panelHeader}>
            <PulseCommandAvatar label="CA" active tone="safety" />
            <View style={styles.panelCopy}>
              <Text style={styles.panelTitle}>Start Pulse call</Text>
              <Text style={styles.panelText}>Uses the existing Communications V2 call engine, LiveKit token route, server permissions, and notification routing.</Text>
            </View>
          </View>
          <View style={styles.actionRow}>
            <ActionButton label="Voice" busy={actionBusy === "voice"} onPress={() => startCall("audio")} />
            <ActionButton label="Video" tone="accent" busy={actionBusy === "video"} onPress={() => startCall("video")} />
          </View>
        </PulseCommandPanel>
      ) : call ? (
        <>
          <PulseCommandPanel tone={connected ? "safety" : incoming ? "danger" : "intelligence"} style={styles.callSurface}>
            <PulseCommandAvatar label={(call.call_type || callType) === "video" ? "VC" : "AC"} active={connected || incoming} tone={connected ? "safety" : incoming ? "danger" : "intelligence"} />
            <Text style={styles.roomName}>{call.room_name || call.public_id || call.call_id}</Text>
            <Text style={styles.status}>{statusLabel}</Text>
            <Text style={styles.meta}>{participantSummary(call)}</Text>
            <View style={styles.metricRow}>
              <PulseCommandMetric value={call.call_type === "video" ? "Video" : "Voice"} label="mode" tone="intelligence" />
              <PulseCommandMetric value={Math.max(1, call.participants?.length || room.participantCount || 1)} label="participants" tone="safety" />
              <PulseCommandMetric value={room.supported ? "Native" : "Fallback"} label="media" tone={room.supported ? "default" : "warning"} />
            </View>
          </PulseCommandPanel>

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

          <PulseCommandPanel style={styles.panel}>
            <Text style={styles.panelTitle}>Native readiness</Text>
            <ReadinessLine label="Backend call state" value={call.status || "loaded"} />
            <ReadinessLine label="LiveKit token" value={call.join?.token ? "available" : call.livekit?.configured ? "request on accept" : "not returned"} />
            <ReadinessLine label={providerBoundary} value={room.supported ? room.connectionState : "safe web handoff"} />
            <ReadinessLine label="Participants" value={String(Math.max(1, call.participants?.length || room.participantCount || 1))} />
          </PulseCommandPanel>

          <PulseCommandPanel style={styles.panel}>
            <Text style={styles.panelTitle}>Recent events</Text>
            {events.length ? events.slice(0, 8).map((event, index) => (
              <Text key={`${event.id || index}-${event.event_type || event.type}`} style={styles.panelText}>
                {event.event_type || event.type || "call:event"} {event.created_at ? `• ${event.created_at}` : ""}
              </Text>
            )) : <Text style={styles.panelText}>No call events returned yet.</Text>}
          </PulseCommandPanel>
        </>
      ) : (
        <PulseCommandPanel style={styles.panel}>
          <Text style={styles.panelTitle}>Active calls</Text>
          {activeCalls.length ? activeCalls.map((item) => (
            <Pressable key={item.call_id} style={styles.callRow} onPress={() => setCallId(item.call_id)}>
              <Text style={styles.callRowTitle}>{item.call_type === "video" ? "Video call" : "Voice call"}</Text>
              <Text style={styles.panelText}>{item.status || "active"} • {item.call_id}</Text>
            </Pressable>
          )) : (
            <LogiNexusStatePanel state="empty" title="No active call signals" body="Incoming, outgoing, and active PulseSoc calls will appear here." style={styles.inlineStatePanel} />
          )}
        </PulseCommandPanel>
      )}

      <PulseCommandAction label="Open safe web fallback" tone="warning" onPress={() => openCallWebFallback(callId || undefined, params.conversationId)} />
    </LogiNexusScrollContainer>
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

function callStatusLabel(call: PulseCall | null, connectionState: string, connected: boolean, incoming: boolean, connecting: boolean) {
  if (connected) return "Connected";
  if (incoming) return "Ringing";
  if (connecting) return "Connecting";
  if (call?.status === "ended") return "Ended";
  if (call?.status === "failed") return "Failed";
  if (connectionState && connectionState !== "idle") return connectionState;
  return call?.status || "Ready";
}

function participantSummary(call: PulseCall) {
  const names = (call.participants || []).map((participant) => participant.display_name || participant.username).filter(Boolean);
  if (names.length) return names.slice(0, 3).join(", ");
  return call.conversation_id ? `Conversation ${call.conversation_id}` : "PulseSoc participants";
}

const styles = StyleSheet.create({
  content: {
    gap: logiNexus.spacing.lg
  },
  panel: {
    gap: logiNexus.spacing.md
  },
  panelHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.md
  },
  panelCopy: {
    flex: 1,
    gap: 4,
    minWidth: 0
  },
  panelTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
  },
  panelText: {
    ...logiNexus.typography.body,
    color: colors.muted,
  },
  callSurface: {
    alignItems: "center",
    gap: logiNexus.spacing.sm,
    minHeight: 300,
    justifyContent: "center",
  },
  roomName: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    textAlign: "center"
  },
  status: {
    ...logiNexus.typography.label,
    color: colors.accent,
    textTransform: "uppercase"
  },
  meta: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    textAlign: "center"
  },
  metricRow: {
    alignSelf: "stretch",
    flexDirection: "row",
    gap: logiNexus.spacing.sm,
    paddingTop: logiNexus.spacing.sm
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: logiNexus.spacing.sm
  },
  controls: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: logiNexus.spacing.sm
  },
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.glass,
    borderColor: "rgba(97,216,255,0.32)",
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    minHeight: 46,
    minWidth: 104,
    justifyContent: "center",
    paddingHorizontal: logiNexus.spacing.lg
  },
  accentButton: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  dangerButton: {
    borderColor: colors.danger
  },
  actionText: {
    ...logiNexus.typography.button,
    color: colors.text,
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
    borderBottomColor: "rgba(255,255,255,0.06)",
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    justifyContent: "space-between",
    paddingVertical: logiNexus.spacing.sm
  },
  readinessValue: {
    ...logiNexus.typography.metadata,
    color: colors.text,
    maxWidth: "48%",
    textAlign: "right"
  },
  callRow: {
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 4,
    padding: logiNexus.spacing.md
  },
  callRowTitle: {
    ...logiNexus.typography.button,
    color: colors.text,
  },
  statePanel: {
    flex: 0,
    minHeight: 148
  },
  inlineStatePanel: {
    flex: 0,
    minHeight: 160
  }
});
