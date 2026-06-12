import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, KeyboardAvoidingView, Platform, Text, TextInput, TouchableOpacity, View } from "react-native";
import { AudioSession } from "@livekit/react-native";
import { Room } from "livekit-client";
import { Camera, useCameraDevice, useCameraPermission, useMicrophonePermission } from "react-native-vision-camera";
import { colors } from "./theme";

type WebApiRequest = (path: string, options?: { method?: string; body?: unknown }) => Promise<Record<string, unknown>>;

type NativeLiveBroadcastProps = {
  onClose: () => void;
  onOpenWebPath: (path: string) => void;
  apiRequest: WebApiRequest;
};

type LiveChatMessage = {
  id?: number;
  body?: string;
  display_name?: string;
  message_type?: string;
};

function delay(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out. Check connection and try again.`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  }) as Promise<T>;
}

export function NativeLiveBroadcast({ onClose, onOpenWebPath, apiRequest }: NativeLiveBroadcastProps) {
  const [facing, setFacing] = useState<"front" | "back">("front");
  const [micMuted, setMicMuted] = useState(false);
  const [status, setStatus] = useState("Ready to preview.");
  const [busy, setBusy] = useState(false);
  const [liveId, setLiveId] = useState<number | null>(null);
  const [viewerCount, setViewerCount] = useState(0);
  const [chat, setChat] = useState<LiveChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isBroadcasting, setIsBroadcasting] = useState(false);
  const [connected, setConnected] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const device = useCameraDevice(facing);
  const cameraPermission = useCameraPermission();
  const microphonePermission = useMicrophonePermission();

  const hasCameraPermission = cameraPermission.hasPermission;
  const hasMicrophonePermission = microphonePermission.hasPermission;
  const canPreview = hasCameraPermission && hasMicrophonePermission && !!device;

  const title = useMemo(() => "PulseSoc Live", []);

  const requestPermissions = useCallback(async () => {
    setStatus("Requesting camera and microphone...");
    const cameraOk = cameraPermission.hasPermission || await cameraPermission.requestPermission();
    const micOk = microphonePermission.hasPermission || await microphonePermission.requestPermission();
    setStatus(cameraOk && micOk ? "Camera preview ready." : "Camera and microphone permission are required to go live.");
  }, [cameraPermission, microphonePermission]);

  useEffect(() => {
    requestPermissions().catch(() => setStatus("Camera permission could not be requested."));
  }, [requestPermissions]);

  useEffect(() => {
    if (!liveId || !isBroadcasting) return undefined;
    let cancelled = false;
    const pollState = async () => {
      try {
        const state = await apiRequest(`/api/pulse/live/${liveId}/state`);
        if (cancelled) return;
        setViewerCount(Number(state.viewer_count || 0));
        setChat(Array.isArray(state.messages) ? state.messages as LiveChatMessage[] : []);
      } catch {
        if (!cancelled) setStatus("Live state is reconnecting...");
      }
    };
    pollState();
    const timer = setInterval(pollState, 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [apiRequest, isBroadcasting, liveId]);

  const disconnectRoom = useCallback(async () => {
    const room = roomRef.current;
    roomRef.current = null;
    if (room) {
      try {
        room.disconnect();
      } catch {
        // Best-effort cleanup. The backend end-live call is the source of truth.
      }
    }
    await AudioSession.stopAudioSession().catch(() => undefined);
    setConnected(false);
  }, []);

  useEffect(() => () => {
    disconnectRoom().catch(() => undefined);
  }, [disconnectRoom]);

  const startLive = useCallback(async () => {
    if (!canPreview || busy) {
      await requestPermissions();
      return;
    }
    setBusy(true);
    setStatus("Creating LIVE feed post...");
    try {
      const created = await withTimeout(apiRequest("/api/pulse/live/start", {
        method: "POST",
        body: { title, category: "PulseSoc Mobile", source: "native_livekit" }
      }), 12000, "Creating live session");
      const nextLiveId = Number(created.live_id || 0);
      if (!nextLiveId) throw new Error("Live session was not created.");
      setLiveId(nextLiveId);
      setViewerCount(Number(created.viewer_count || 0));

      setStatus("Connecting camera to LiveKit...");
      const tokenResponse = await withTimeout(apiRequest(`/api/pulse/live/${nextLiveId}/livekit/token`, {
        method: "POST",
        body: { role: "publisher" }
      }), 12000, "Creating LiveKit token");
      const livekitUrl = String(tokenResponse.livekit_url || "");
      const token = String(tokenResponse.token || "");
      if (!livekitUrl || !token) throw new Error("LiveKit token is unavailable.");

      const room = new Room({
        adaptiveStream: true,
        dynacast: true
      });
      roomRef.current = room;
      setIsBroadcasting(true);
      setStatus("Starting broadcast camera...");
      await delay(300);
      await withTimeout(AudioSession.startAudioSession(), 6000, "Starting audio session");
      await withTimeout(room.connect(livekitUrl, token), 15000, "Connecting to LiveKit");
      await withTimeout((room.localParticipant as any).setCameraEnabled(true), 15000, "Starting live camera");
      await withTimeout((room.localParticipant as any).setMicrophoneEnabled(!micMuted), 8000, "Starting live microphone");
      setConnected(true);
      setStatus("You are live. A LIVE post is now in the feed.");
      await withTimeout(apiRequest(`/api/pulse/live/${nextLiveId}/browser-publish`, {
        method: "POST",
        body: { audio_tracks: micMuted ? 0 : 1, video_tracks: 1, source: "native_livekit" }
      }), 12000, "Forwarding live stream to Mux").catch(() => undefined);
    } catch (error) {
      await disconnectRoom();
      setIsBroadcasting(false);
      setStatus(error instanceof Error ? error.message : "Live could not start.");
    } finally {
      setBusy(false);
    }
  }, [apiRequest, busy, canPreview, disconnectRoom, micMuted, requestPermissions, title]);

  const endLive = useCallback(async () => {
    if (!liveId) return;
    setBusy(true);
    setStatus("Ending live and preparing replay...");
    try {
      await disconnectRoom();
      await withTimeout(apiRequest(`/api/pulse/live/${liveId}/end`, { method: "POST", body: {} }), 12000, "Ending live");
      setIsBroadcasting(false);
      setStatus("Live ended. Replay will appear in Videos when Mux recording is ready.");
      onOpenWebPath(`/pulse/live/${liveId}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Live could not end cleanly.");
    } finally {
      setBusy(false);
    }
  }, [apiRequest, disconnectRoom, liveId, onOpenWebPath]);

  const toggleMic = useCallback(async () => {
    const next = !micMuted;
    setMicMuted(next);
    const room = roomRef.current;
    if (room && connected) {
      await (room.localParticipant as any).setMicrophoneEnabled(!next).catch(() => undefined);
    }
  }, [connected, micMuted]);

  const sendChat = useCallback(async () => {
    const body = chatInput.trim();
    if (!body || !liveId) return;
    setChatInput("");
    await apiRequest(`/api/pulse/live/${liveId}/chat`, { method: "POST", body: { body } }).catch(error => {
      setStatus(error instanceof Error ? error.message : "Chat failed.");
    });
  }, [apiRequest, chatInput, liveId]);

  const sendReaction = useCallback(async (reaction: string) => {
    if (!liveId) return;
    await apiRequest(`/api/pulse/live/${liveId}/react`, { method: "POST", body: { reaction_type: reaction } }).catch(() => undefined);
  }, [apiRequest, liveId]);

  return (
    <View style={styles.overlay}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.shell}>
        <View style={styles.header}>
          <View>
            <Text style={styles.kicker}>PulseSoc Native Live</Text>
            <Text style={styles.title}>{isBroadcasting ? "You are LIVE" : "Go Live"}</Text>
          </View>
          <TouchableOpacity style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeText}>Close</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.preview}>
          {canPreview ? (
            <Camera
              style={styles.camera}
              device={device!}
              isActive={!isBroadcasting}
              resizeMode="cover"
              mirrorMode="auto"
            />
          ) : (
            <View style={styles.permissionPanel}>
              <Text style={styles.permissionTitle}>Camera preview needs permission</Text>
              <Text style={styles.muted}>PulseSoc needs camera and microphone access before a broadcast can start.</Text>
              <TouchableOpacity style={styles.primaryButton} onPress={requestPermissions}>
                <Text style={styles.primaryText}>Allow Camera & Mic</Text>
              </TouchableOpacity>
            </View>
          )}
          <View style={styles.liveBadge}>
            <Text style={styles.liveBadgeText}>{isBroadcasting ? "LIVE" : "PREVIEW"}</Text>
          </View>
          <View style={styles.viewerBadge}>
            <Text style={styles.viewerText}>{viewerCount} watching</Text>
          </View>
        </View>

        <Text style={styles.status}>{status}</Text>

        <View style={styles.controls}>
          <TouchableOpacity style={styles.controlButton} onPress={() => setFacing(facing === "front" ? "back" : "front")} disabled={busy || isBroadcasting}>
            <Text style={styles.controlText}>Flip</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.controlButton, micMuted && styles.dangerButton]} onPress={toggleMic} disabled={busy}>
            <Text style={styles.controlText}>{micMuted ? "Mic Off" : "Mic On"}</Text>
          </TouchableOpacity>
          {!isBroadcasting ? (
            <TouchableOpacity style={styles.goLiveButton} onPress={startLive} disabled={busy}>
              {busy ? <ActivityIndicator color="#06111f" /> : <Text style={styles.goLiveText}>Start Live</Text>}
            </TouchableOpacity>
          ) : (
            <TouchableOpacity style={styles.endButton} onPress={endLive} disabled={!liveId}>
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.endText}>End Live</Text>}
            </TouchableOpacity>
          )}
        </View>

        <View style={styles.reactionRow}>
          {["🔥", "💚", "🚀", "👏"].map(reaction => (
            <TouchableOpacity key={reaction} style={styles.reactionButton} onPress={() => sendReaction(reaction)} disabled={!liveId}>
              <Text style={styles.reactionText}>{reaction}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <View style={styles.chatPanel}>
          <Text style={styles.chatTitle}>Live chat</Text>
          <FlatList
            data={chat.slice(-24)}
            keyExtractor={(item, index) => String(item.id || index)}
            renderItem={({ item }) => (
              <Text style={styles.chatLine}>
                <Text style={styles.chatName}>{item.display_name || "Viewer"}: </Text>{item.body || ""}
              </Text>
            )}
            ListEmptyComponent={<Text style={styles.muted}>Chat appears here during the broadcast.</Text>}
          />
          <View style={styles.chatComposer}>
            <TextInput
              value={chatInput}
              onChangeText={setChatInput}
              placeholder="Say something live..."
              placeholderTextColor={colors.muted}
              style={styles.chatInput}
            />
            <TouchableOpacity style={styles.sendButton} onPress={sendChat} disabled={!liveId || !chatInput.trim()}>
              <Text style={styles.sendText}>Send</Text>
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = {
  overlay: {
    ...StyleSheetAbsoluteFill(),
    backgroundColor: colors.background,
    zIndex: 100
  },
  shell: {
    flex: 1,
    padding: 14,
    gap: 10,
    backgroundColor: colors.background
  },
  header: {
    flexDirection: "row" as const,
    justifyContent: "space-between" as const,
    alignItems: "center" as const,
    paddingTop: 4
  },
  kicker: { color: colors.accentAlt, fontWeight: "800" as const, fontSize: 12 },
  title: { color: colors.text, fontSize: 30, fontWeight: "900" as const },
  closeButton: { borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 14, minHeight: 42, justifyContent: "center" as const },
  closeText: { color: colors.text, fontWeight: "800" as const },
  preview: { flex: 1.05, minHeight: 310, borderRadius: 24, overflow: "hidden" as const, borderWidth: 1, borderColor: colors.border, backgroundColor: "#020812" },
  camera: { flex: 1 },
  permissionPanel: { flex: 1, alignItems: "center" as const, justifyContent: "center" as const, padding: 18 },
  permissionTitle: { color: colors.text, fontSize: 20, fontWeight: "900" as const, textAlign: "center" as const, marginBottom: 8 },
  liveBadge: { position: "absolute" as const, top: 12, left: 12, backgroundColor: "#ff335d", borderRadius: 999, paddingHorizontal: 10, minHeight: 28, justifyContent: "center" as const, shadowColor: "#ff335d", shadowOpacity: 0.7, shadowRadius: 16 },
  liveBadgeText: { color: "#fff", fontWeight: "900" as const, fontSize: 12 },
  viewerBadge: { position: "absolute" as const, top: 12, right: 12, backgroundColor: "rgba(5,11,20,.74)", borderRadius: 999, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 10, minHeight: 28, justifyContent: "center" as const },
  viewerText: { color: colors.text, fontWeight: "800" as const, fontSize: 12 },
  status: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  controls: { flexDirection: "row" as const, gap: 8, alignItems: "center" as const },
  controlButton: { minHeight: 46, paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: colors.border, justifyContent: "center" as const },
  controlText: { color: colors.text, fontWeight: "900" as const },
  dangerButton: { borderColor: colors.danger, backgroundColor: "rgba(255,107,122,.14)" },
  goLiveButton: { flex: 1, minHeight: 50, borderRadius: 999, alignItems: "center" as const, justifyContent: "center" as const, backgroundColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.45, shadowRadius: 18 },
  goLiveText: { color: "#06111f", fontSize: 16, fontWeight: "900" as const },
  endButton: { flex: 1, minHeight: 50, borderRadius: 999, alignItems: "center" as const, justifyContent: "center" as const, backgroundColor: colors.danger },
  endText: { color: "#fff", fontSize: 16, fontWeight: "900" as const },
  reactionRow: { flexDirection: "row" as const, gap: 8 },
  reactionButton: { width: 52, height: 46, borderRadius: 999, alignItems: "center" as const, justifyContent: "center" as const, borderWidth: 1, borderColor: colors.border, backgroundColor: "rgba(54,229,143,.08)" },
  reactionText: { fontSize: 22 },
  chatPanel: { flex: 0.86, minHeight: 230, borderWidth: 1, borderColor: colors.border, borderRadius: 20, padding: 12, backgroundColor: colors.surface },
  chatTitle: { color: colors.text, fontWeight: "900" as const, fontSize: 16, marginBottom: 8 },
  chatLine: { color: colors.text, fontSize: 13, lineHeight: 18, marginBottom: 5 },
  chatName: { color: colors.accentAlt, fontWeight: "900" as const },
  chatComposer: { flexDirection: "row" as const, gap: 8, alignItems: "center" as const, marginTop: 8 },
  chatInput: { flex: 1, minHeight: 44, borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 12, color: colors.text, backgroundColor: "#081322" },
  sendButton: { minHeight: 44, paddingHorizontal: 14, borderRadius: 999, backgroundColor: colors.accent, justifyContent: "center" as const },
  sendText: { color: "#06111f", fontWeight: "900" as const },
  muted: { color: colors.muted, fontSize: 13, lineHeight: 19, textAlign: "center" as const },
  primaryButton: { minHeight: 48, borderRadius: 999, backgroundColor: colors.accent, paddingHorizontal: 18, justifyContent: "center" as const, marginTop: 14 },
  primaryText: { color: "#06111f", fontWeight: "900" as const }
};

function StyleSheetAbsoluteFill() {
  return {
    position: "absolute" as const,
    top: 0,
    right: 0,
    bottom: 0,
    left: 0
  };
}
