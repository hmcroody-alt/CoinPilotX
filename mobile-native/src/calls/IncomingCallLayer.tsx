import * as Notifications from "expo-notifications";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  AppState,
  AppStateStatus,
  Image,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  View
} from "react-native";
import { acceptCall, declineCall, getActiveCalls, markRingSeen, PulseCall, PulseCallParticipant } from "../api/calls";
import { callHaptic, startCallTone, stopCallTone } from "./callSignalMedia";
import { isIncomingRingingCall } from "./callToneLifecycle";
import { endCallKitCall, initNativeCallKit, reportIncomingCallKit } from "./callKitBridge";
import { navigationRef } from "../navigation/notificationRouting";
import { colors } from "../theme/colors";
import { createLogiNexusAmbientPulse, useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { qaIncomingCallFromUrl } from "./incomingCallQa";

const ACTIVE_CALL_REFRESH_MS = 4200;
const SILENCED_CALL_TTL_MS = 15 * 60 * 1000;

type IncomingCallLayerProps = {
  signedIn: boolean;
  currentUserId?: number;
};

export function IncomingCallLayer({ signedIn, currentUserId }: IncomingCallLayerProps) {
  const [incomingCall, setIncomingCall] = useState<PulseCall | null>(null);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const ignoredCalls = useRef<Map<string, number>>(new Map());
  const ringSeenCalls = useRef<Set<string>>(new Set());
  const callKitAnswered = useRef<Set<string>>(new Set());
  const appState = useRef<AppStateStatus>(AppState.currentState);
  const pulse = useRef(new Animated.Value(0)).current;
  const reducedMotion = useLogiNexusReducedMotion();

  const refreshActiveCalls = useCallback(async () => {
    if (!signedIn || appState.current !== "active") return;
    const active = await getActiveCalls();
    const calls = active.calls || [];
    pruneIgnoredCalls(ignoredCalls.current);
    const ringing = calls.find((call) => isIncomingRingingCall(call, currentUserId, ignoredCalls.current));

    if (ringing) {
      setIncomingCall(ringing);
      reportIncomingCallKit({
        callId: ringing.call_id,
        displayName: callerParticipant(ringing).display_name || callerParticipant(ringing).username || "PulseSoc caller",
        handle: callerParticipant(ringing).username || ringing.call_id,
        hasVideo: ringing.call_type === "video"
      });
      setError("");
      if (!ringSeenCalls.current.has(ringing.call_id)) {
        ringSeenCalls.current.add(ringing.call_id);
        markRingSeen(ringing.call_id).catch(() => undefined);
      }
    } else {
      setIncomingCall((current) => {
        if (current?.call_id && !callKitAnswered.current.has(current.call_id)) endCallKitCall(current.call_id);
        return null;
      });
    }
  }, [currentUserId, signedIn]);

  const seedQaCallFromUrl = useCallback((url: string | null) => {
    const fixture = qaIncomingCallFromUrl(url, currentUserId);
    if (!fixture) return false;
    if (!isIncomingRingingCall(fixture, currentUserId, ignoredCalls.current)) return false;
    setIncomingCall(fixture);
    setError("");
    return true;
  }, [currentUserId]);

  const accept = useCallback(async () => {
    if (!incomingCall?.call_id) return;
    setBusyAction("accept");
    setError("");
    callHaptic("answer");
    await stopCallTone();
    try {
      const call = await acceptCall(incomingCall.call_id);
      const acceptedCaller = callerParticipant(call);
      setIncomingCall(null);
      if (navigationRef.isReady()) {
        navigationRef.navigate("Call", {
          callId: call.call_id,
          conversationId: call.conversation_id,
          callType: call.call_type,
          direction: "incoming",
          title: acceptedCaller.display_name || acceptedCaller.username || "Incoming caller"
        });
      }
    } catch (acceptError) {
      setError(acceptError instanceof Error ? acceptError.message : "Call could not be answered.");
    } finally {
      setBusyAction("");
    }
  }, [incomingCall]);

  useEffect(() => {
    if (!signedIn) return;
    initNativeCallKit({
      onAnswered: (callId) => {
        callKitAnswered.current.add(callId);
        stopCallTone().catch(() => undefined);
        setIncomingCall(null);
        if (navigationRef.isReady()) {
          navigationRef.navigate("Call", { callId, direction: "incoming", title: "Incoming caller" });
        }
      },
      onEnded: (callId) => {
        callKitAnswered.current.delete(callId);
        ignoredCalls.current.set(callId, Date.now());
        stopCallTone().catch(() => undefined);
        setIncomingCall((current) => (current?.call_id === callId ? null : current));
      }
    }).catch(() => undefined);
  }, [signedIn]);

  const decline = useCallback(async () => {
    if (!incomingCall?.call_id) return;
    setBusyAction("decline");
    setError("");
    callHaptic("decline");
    await stopCallTone();
    try {
      await declineCall(incomingCall.call_id);
      setIncomingCall(null);
      ignoredCalls.current.set(incomingCall.call_id, Date.now());
    } catch (declineError) {
      setError(declineError instanceof Error ? declineError.message : "Call could not be declined.");
    } finally {
      setBusyAction("");
    }
  }, [incomingCall]);

  const ignore = useCallback(() => {
    if (!incomingCall?.call_id) return;
    stopCallTone().catch(() => undefined);
    ignoredCalls.current.set(incomingCall.call_id, Date.now());
    setIncomingCall(null);
  }, [incomingCall]);

  useEffect(() => {
    if (incomingCall?.call_id) startCallTone("ringtone").catch(() => undefined);
    return () => { stopCallTone().catch(() => undefined); };
  }, [incomingCall?.call_id]);

  useEffect(() => {
    if (!signedIn) {
      setIncomingCall(null);
      ringSeenCalls.current.clear();
      callKitAnswered.current.clear();
      return;
    }
    refreshActiveCalls().catch(() => undefined);
    const interval = setInterval(() => {
      refreshActiveCalls().catch(() => undefined);
    }, ACTIVE_CALL_REFRESH_MS);
    const appStateSubscription = AppState.addEventListener("change", (nextState) => {
      const wasBackgrounded = appState.current.match(/inactive|background/);
      appState.current = nextState;
      if (nextState === "active" && wasBackgrounded) refreshActiveCalls().catch(() => undefined);
    });
    const notificationSubscription = Notifications.addNotificationReceivedListener(() => {
      refreshActiveCalls().catch(() => undefined);
    });
    return () => {
      clearInterval(interval);
      appStateSubscription.remove();
      notificationSubscription.remove();
    };
  }, [refreshActiveCalls, signedIn]);

  useEffect(() => {
    if (!signedIn) return;
    Linking.getInitialURL().then(seedQaCallFromUrl).catch(() => undefined);
    const subscription = Linking.addEventListener("url", (event) => {
      seedQaCallFromUrl(event.url);
    });
    return () => subscription.remove();
  }, [seedQaCallFromUrl, signedIn]);

  useEffect(() => {
    if (!incomingCall) return;
    pulse.setValue(0);
    if (reducedMotion) {
      pulse.setValue(0.45);
      return undefined;
    }
    const animation = createLogiNexusAmbientPulse(pulse, { duration: 1800 });
    animation.start();
    return () => animation.stop();
  }, [incomingCall, pulse, reducedMotion]);

  const caller = useMemo(() => callerParticipant(incomingCall), [incomingCall]);

  if (!signedIn) return null;

  return (
    <>
      {incomingCall ? (
        <View style={styles.fullscreen}>
          <View style={styles.spaceLayer}>
            <Animated.View
              style={[
                styles.orbit,
                {
                  opacity: pulse.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0.28, 0.72, 0.28] }),
                  transform: [
                    { scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.84, 1.28] }) },
                    { rotate: pulse.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "28deg"] }) }
                  ]
                }
              ]}
            />
            <Animated.View
              style={[
                styles.orbitSecondary,
                {
                  opacity: pulse.interpolate({ inputRange: [0, 0.6, 1], outputRange: [0.18, 0.55, 0.18] }),
                  transform: [
                    { scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1.12, 0.86] }) },
                    { rotate: pulse.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "-34deg"] }) }
                  ]
                }
              ]}
            />
          </View>

          <View style={styles.incomingContent}>
            <Text style={styles.kicker}>Incoming PulseSoc call</Text>
            <Avatar participant={caller} callType={incomingCall.call_type} />
            <Text style={styles.callerName} numberOfLines={1}>{caller.display_name || caller.username || "Incoming caller"}</Text>
            <Text style={styles.callType}>{incomingCall.call_type === "video" ? "Video call" : "Voice call"}</Text>
            <Text style={styles.stateText}>{stateCopy(incomingCall.status)}</Text>
            {error ? <Text style={styles.error}>{error}</Text> : null}

            <View style={styles.primaryActions}>
              <Pressable accessibilityLabel="Decline call" disabled={busyAction === "decline"} style={[styles.portalButton, styles.declineButton]} onPress={decline}>
                <Text style={styles.portalText}>{busyAction === "decline" ? "..." : "Decline"}</Text>
              </Pressable>
              <Pressable accessibilityLabel="Accept call" disabled={busyAction === "accept"} style={[styles.portalButton, styles.acceptButton]} onPress={accept}>
                <Text style={styles.acceptText}>{busyAction === "accept" ? "..." : "Accept"}</Text>
              </Pressable>
            </View>

            <View style={styles.secondaryActions}>
              <Pressable accessibilityLabel="Silence incoming call" style={styles.textAction} onPress={ignore}>
                <Text style={styles.textActionCopy}>Silent ignore</Text>
              </Pressable>
              <Pressable accessibilityLabel="Remind me later" style={styles.textAction} onPress={ignore}>
                <Text style={styles.textActionCopy}>Remind me later</Text>
              </Pressable>
            </View>
          </View>
        </View>
      ) : null}

    </>
  );
}

function pruneIgnoredCalls(ignored: Map<string, number>) {
  const now = Date.now();
  Array.from(ignored.entries()).forEach(([callId, ignoredAt]) => {
    if (now - ignoredAt > SILENCED_CALL_TTL_MS) ignored.delete(callId);
  });
}

function callerParticipant(call: PulseCall | null): PulseCallParticipant {
  if (!call) return {};
  const participants = call.participants || [];
  return participants.find((item) => String(item.role || "").toLowerCase() === "caller") || participants[0] || call.participant || {};
}

function stateCopy(status?: string) {
  const value = String(status || "ringing");
  if (value === "connecting") return "Connecting through PulseSoc";
  if (value === "reconnecting") return "Reconnecting";
  if (value === "connected" || value === "active") return "Connected";
  if (value === "missed") return "Missed call";
  if (value === "failed") return "Call failed";
  if (value === "busy") return "User is busy";
  return "Ringing";
}

function Avatar({ participant, callType }: { participant: PulseCallParticipant; callType?: string }) {
  const initials = initialsFor(participant.display_name || participant.username || "?");
  return (
    <View style={styles.avatarShell}>
      {participant.avatar_url ? <Image source={{ uri: participant.avatar_url }} style={styles.avatarImage} /> : <Text style={styles.avatarInitials}>{initials}</Text>}
    </View>
  );
}

function initialsFor(name: string) {
  const parts = String(name || "?").trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase() || "").join("") || "?";
}

const styles = StyleSheet.create({
  fullscreen: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#06090f",
    justifyContent: "center",
    overflow: "hidden",
    zIndex: 50
  },
  spaceLayer: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center"
  },
  orbit: {
    borderColor: "rgba(37,208,167,0.52)",
    borderRadius: 180,
    borderWidth: 2,
    height: 360,
    position: "absolute",
    width: 360
  },
  orbitSecondary: {
    borderColor: "rgba(79,140,255,0.45)",
    borderRadius: 230,
    borderWidth: 1,
    height: 460,
    position: "absolute",
    width: 460
  },
  incomingContent: {
    alignItems: "center",
    gap: 14,
    padding: 24
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  avatarShell: {
    alignItems: "center",
    backgroundColor: "rgba(32,36,43,0.92)",
    borderColor: "rgba(37,208,167,0.5)",
    borderRadius: 72,
    borderWidth: 1,
    height: 144,
    justifyContent: "center",
    shadowColor: colors.accent,
    shadowOpacity: 0.36,
    shadowRadius: 28,
    width: 144
  },
  avatarImage: {
    borderRadius: 68,
    height: 136,
    width: 136
  },
  avatarInitials: {
    color: colors.text,
    fontSize: 40,
    fontWeight: "900"
  },
  callerName: {
    color: colors.text,
    fontSize: 29,
    fontWeight: "900",
    maxWidth: "92%",
    textAlign: "center"
  },
  callType: {
    color: colors.muted,
    fontSize: 16,
    fontWeight: "800"
  },
  stateText: {
    color: "#c9d6e6",
    fontSize: 13,
    fontWeight: "700"
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "800",
    textAlign: "center"
  },
  primaryActions: {
    flexDirection: "row",
    gap: 18,
    marginTop: 18
  },
  portalButton: {
    alignItems: "center",
    borderRadius: 999,
    height: 72,
    justifyContent: "center",
    minWidth: 116,
    paddingHorizontal: 22
  },
  acceptButton: {
    backgroundColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.42,
    shadowRadius: 22
  },
  declineButton: {
    backgroundColor: "rgba(255,107,107,0.18)",
    borderColor: colors.danger,
    borderWidth: 1
  },
  portalText: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  acceptText: {
    color: "#06120f",
    fontSize: 15,
    fontWeight: "900"
  },
  secondaryActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    justifyContent: "center"
  },
  textAction: {
    borderColor: "rgba(255,255,255,0.12)",
    borderRadius: 999,
    borderWidth: 1,
    minHeight: 40,
    justifyContent: "center",
    paddingHorizontal: 16
  },
  textActionCopy: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  }
});
