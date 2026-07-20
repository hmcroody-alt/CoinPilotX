import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ComponentType, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  endLive,
  getLiveKitToken,
  getLiveState,
  listGuestManagement,
  muteGuest,
  removeGuest,
  respondToJoinRequest,
  unmuteGuest
} from "../api/live";
import { elapsedLabel, formatViewerCount, type LiveGuest, type LiveGuestRequest } from "../live/liveSession";
import { useLiveBroadcastRoom, type LiveParticipant } from "../live/useLiveBroadcastRoom";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type NativeVideoViewProps = {
  videoTrack?: any;
  style?: any;
  objectFit?: "cover" | "contain";
  mirror?: boolean;
  zOrder?: number;
};

const STATE_POLL_MS = 5000;

export function LiveHostSessionScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "NativeLiveHost">) {
  const insets = useSafeAreaInsets();
  const liveId = Number(route.params?.liveId || 0);
  const room = useLiveBroadcastRoom();
  const [VideoViewComponent, setVideoViewComponent] = useState<ComponentType<NativeVideoViewProps> | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [fatalError, setFatalError] = useState("");
  const [viewerCount, setViewerCount] = useState(0);
  const [requests, setRequests] = useState<LiveGuestRequest[]>([]);
  const [activeGuests, setActiveGuests] = useState<LiveGuest[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [ending, setEnding] = useState(false);
  const [busyRequestId, setBusyRequestId] = useState(0);
  const [busyGuestId, setBusyGuestId] = useState(0);
  const startedAtRef = useRef<number>(0);
  const endedRef = useRef(false);

  useEffect(() => {
    if (Platform.OS !== "web") {
      import("@livekit/react-native")
        .then((module) => setVideoViewComponent(() => module.VideoView as ComponentType<NativeVideoViewProps>))
        .catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function connect() {
      if (liveId <= 0) {
        setFatalError("This broadcast is missing a live id and cannot start.");
        setConnecting(false);
        return;
      }
      try {
        const credentials = await getLiveKitToken(liveId, "host");
        if (cancelled) return;
        if (!credentials || !credentials.canPublish) {
          setFatalError("PulseSoc did not grant a publish token for this broadcast. It cannot go live.");
          setConnecting(false);
          return;
        }
        const ok = await room.connect(credentials, { publish: true });
        if (cancelled) return;
        if (!ok) {
          setFatalError(room.error || "The native broadcast could not connect to LiveKit.");
          setConnecting(false);
          return;
        }
        startedAtRef.current = Date.now();
        setConnecting(false);
      } catch (error) {
        if (cancelled) return;
        setFatalError(error instanceof Error && error.message ? error.message : "The native broadcast could not start.");
        setConnecting(false);
      }
    }
    connect();
    return () => {
      cancelled = true;
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

  const refreshLiveMeta = useCallback(async () => {
    if (liveId <= 0) return;
    const [state, management] = await Promise.all([
      getLiveState(liveId).catch(() => null),
      listGuestManagement(liveId).catch(() => ({ requests: [] as LiveGuestRequest[], guests: [] as LiveGuest[] }))
    ]);
    if (state) setViewerCount(Number(state.viewer_count || 0));
    setRequests(management.requests);
    setActiveGuests(management.guests);
  }, [liveId]);

  useEffect(() => {
    if (!room.connected) return undefined;
    refreshLiveMeta().catch(() => undefined);
    const interval = setInterval(() => refreshLiveMeta().catch(() => undefined), STATE_POLL_MS);
    return () => clearInterval(interval);
  }, [room.connected, refreshLiveMeta]);

  const finishBroadcast = useCallback(async () => {
    if (endedRef.current) return;
    endedRef.current = true;
    setEnding(true);
    await endLive(liveId).catch(() => undefined);
    await room.disconnect("host_ended").catch(() => undefined);
    setEnding(false);
    navigation.goBack();
  }, [liveId, navigation, room]);

  const confirmEnd = useCallback(() => {
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

  const guests = useMemo(() => room.participants.filter((participant) => !participant.isLocal), [room.participants]);
  const stageParticipants = useMemo(() => {
    const local = room.participants.find((participant) => participant.isLocal);
    return [local, ...guests].filter(Boolean) as LiveParticipant[];
  }, [room.participants, guests]);

  if (fatalError) {
    return (
      <View style={[styles.center, { paddingTop: insets.top }]}>
        <Text style={styles.errorTitle}>Broadcast could not start</Text>
        <Text style={styles.errorBody}>{fatalError}</Text>
        <Pressable style={styles.exitButton} onPress={() => navigation.goBack()}>
          <Text style={styles.exitText}>Back to Live Studio</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <View style={styles.stage}>
        {stageParticipants.length && VideoViewComponent ? (
          <View style={styles.stageGrid}>
            {stageParticipants.map((participant) => (
              <View key={participant.identity} style={[styles.tile, stageParticipants.length > 1 && styles.tileSplit]}>
                {participant.hasVideo && participant.videoTrack ? (
                  <VideoViewComponent
                    videoTrack={participant.videoTrack}
                    style={StyleSheet.absoluteFillObject}
                    objectFit="cover"
                    mirror={participant.isLocal}
                    zOrder={participant.isLocal ? 1 : 0}
                  />
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
            ))}
          </View>
        ) : (
          <View style={styles.center}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.connectingText}>{connecting ? "Connecting your broadcast…" : "Waiting for camera…"}</Text>
          </View>
        )}

        <View style={[styles.topBar, { paddingTop: insets.top + 8 }]} pointerEvents="box-none">
          <View style={styles.liveBadge}>
            <View style={styles.liveDot} />
            <Text style={styles.liveText}>{room.connected ? "LIVE" : room.reconnecting ? "RECONNECTING" : "GOING LIVE"}</Text>
          </View>
          <Text style={styles.elapsed}>{elapsedLabel(elapsed)}</Text>
          <View style={styles.viewerPill}>
            <Text style={styles.viewerText}>{formatViewerCount(viewerCount)} watching</Text>
          </View>
        </View>
      </View>

      <ScrollView style={styles.panel} contentContainerStyle={[styles.panelContent, { paddingBottom: insets.bottom + 16 }]}>
        {room.error ? <Text style={styles.inlineError}>{room.error}</Text> : null}

        <View style={styles.controlRow}>
          <ControlButton label={room.audioEnabled ? "Mute" : "Unmute"} active={room.audioEnabled} onPress={toggleMic} />
          <ControlButton label={room.videoEnabled ? "Camera off" : "Camera on"} active={room.videoEnabled} onPress={toggleCamera} />
          <ControlButton label="Flip" active onPress={flipCamera} disabled={!room.videoEnabled} />
          <ControlButton
            label="Audio"
            active={room.speakerEnabled}
            onPress={() => room.showAudioRoutePicker().catch(() => undefined)}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Guest requests</Text>
          {requests.length === 0 ? (
            <Text style={styles.sectionEmpty}>No pending requests. Viewers who ask to join will appear here.</Text>
          ) : (
            requests.map((request) => (
              <View key={request.requestId} style={styles.requestRow}>
                <View style={styles.requestBody}>
                  <Text style={styles.requestName} numberOfLines={1}>
                    {request.displayName}
                  </Text>
                  <Text style={styles.requestMeta} numberOfLines={1}>
                    @{request.username || "guest"} · {request.cameraReady ? "camera ready" : "audio only"}
                  </Text>
                </View>
                <Pressable
                  style={[styles.requestAction, styles.acceptAction]}
                  disabled={busyRequestId === request.requestId}
                  onPress={() => respond(request, "accept").catch(() => undefined)}
                >
                  <Text style={styles.acceptText}>Accept</Text>
                </Pressable>
                <Pressable
                  style={[styles.requestAction, styles.denyAction]}
                  disabled={busyRequestId === request.requestId}
                  onPress={() => respond(request, "deny").catch(() => undefined)}
                >
                  <Text style={styles.denyText}>Deny</Text>
                </Pressable>
              </View>
            ))
          )}
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>On stage</Text>
          {activeGuests.length === 0 ? (
            <Text style={styles.sectionEmpty}>No guests are publishing yet. Accepted guests appear here to mute or remove.</Text>
          ) : (
            activeGuests.map((guest) => (
              <View key={guest.guestId} style={styles.requestRow}>
                <View style={styles.requestBody}>
                  <Text style={styles.requestName} numberOfLines={1}>
                    {guest.displayName}
                  </Text>
                  <Text style={styles.requestMeta} numberOfLines={1}>
                    {guest.roleLabel} · {guest.audioMuted ? "muted" : "live audio"}
                  </Text>
                </View>
                <Pressable
                  style={[styles.requestAction, guest.audioMuted ? styles.acceptAction : styles.denyAction]}
                  disabled={busyGuestId === guest.guestId}
                  onPress={() => moderateGuest(guest, guest.audioMuted ? "unmute" : "mute").catch(() => undefined)}
                >
                  <Text style={guest.audioMuted ? styles.acceptText : styles.denyText}>{guest.audioMuted ? "Unmute" : "Mute"}</Text>
                </Pressable>
                <Pressable
                  style={[styles.requestAction, styles.denyAction]}
                  disabled={busyGuestId === guest.guestId}
                  onPress={() => confirmRemoveGuest(guest)}
                >
                  <Text style={styles.removeText}>Remove</Text>
                </Pressable>
              </View>
            ))
          )}
        </View>

        <Pressable style={styles.endButton} onPress={confirmEnd} disabled={ending}>
          <Text style={styles.endText}>{ending ? "Ending…" : "End live"}</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

function ControlButton({
  label,
  active,
  onPress,
  disabled
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      style={[styles.control, active ? styles.controlActive : styles.controlInactive, disabled && styles.controlDisabled]}
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
    >
      <Text style={[styles.controlText, !active && styles.controlTextInactive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  acceptAction: {
    backgroundColor: colors.accent
  },
  acceptText: {
    color: colors.background,
    fontWeight: "800"
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
  control: {
    borderRadius: 14,
    flex: 1,
    paddingVertical: 14
  },
  controlActive: {
    backgroundColor: colors.surface
  },
  controlDisabled: {
    opacity: 0.4
  },
  controlInactive: {
    backgroundColor: "rgba(255,255,255,0.06)"
  },
  controlRow: {
    flexDirection: "row",
    gap: 8
  },
  controlText: {
    color: colors.text,
    fontWeight: "800",
    textAlign: "center"
  },
  controlTextInactive: {
    color: colors.muted
  },
  denyAction: {
    borderColor: colors.border,
    borderWidth: 1
  },
  denyText: {
    color: colors.text,
    fontWeight: "800"
  },
  elapsed: {
    color: colors.text,
    fontSize: 15,
    fontVariant: ["tabular-nums"],
    fontWeight: "800"
  },
  endButton: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: 16,
    paddingVertical: 16
  },
  endText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "900"
  },
  errorBody: {
    color: colors.muted,
    fontSize: 15,
    textAlign: "center"
  },
  errorTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900",
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
  inlineError: {
    color: colors.danger,
    fontWeight: "700"
  },
  liveBadge: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: 999,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 6
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
  panel: {
    backgroundColor: colors.background,
    flex: 1
  },
  panelContent: {
    gap: 20,
    padding: 16
  },
  requestAction: {
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  requestBody: {
    flex: 1,
    gap: 2
  },
  requestMeta: {
    color: colors.muted,
    fontSize: 13
  },
  requestName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  removeText: {
    color: colors.danger,
    fontWeight: "800"
  },
  requestRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  root: {
    backgroundColor: "#02040a",
    flex: 1
  },
  section: {
    gap: 10
  },
  sectionEmpty: {
    color: colors.muted,
    fontSize: 14
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  stage: {
    backgroundColor: "#02040a",
    flex: 1,
    overflow: "hidden"
  },
  stageGrid: {
    flex: 1,
    flexDirection: "row",
    flexWrap: "wrap"
  },
  tile: {
    backgroundColor: "#05070f",
    height: "100%",
    width: "100%"
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
  tileSplit: {
    height: "50%",
    width: "50%"
  },
  topBar: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    left: 0,
    paddingHorizontal: 16,
    position: "absolute",
    right: 0,
    top: 0
  },
  viewerPill: {
    backgroundColor: "rgba(2,4,10,0.6)",
    borderRadius: 999,
    marginLeft: "auto",
    paddingHorizontal: 12,
    paddingVertical: 6
  },
  viewerText: {
    color: "#fff",
    fontSize: 13,
    fontWeight: "700"
  }
});
