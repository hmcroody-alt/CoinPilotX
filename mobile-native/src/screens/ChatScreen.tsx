import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Audio } from "expo-av";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  AppStateStatus,
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
  cacheMessages,
  createLocalMessage,
  getConversation,
  loadCachedMessages,
  markConversationSeen,
  MessengerMessage,
  sendConversationMessage,
  sendTyping,
  syncConversation,
  uploadMessengerMedia
} from "../api/messenger";
import { PULSE_API_BASE_URL } from "../api/config";
import { NativeMediaViewer, NativeMediaViewerItem } from "../components/NativeMediaViewer";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatFileSize, formatShortTime } from "../utils/format";

const PAGE_SIZE = 40;
const SYNC_INTERVAL_MS = 2500;

export function ChatScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "Chat">) {
  const conversationId = route.params.conversationId;
  const [messages, setMessages] = useState<MessengerMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState("");
  const [typing, setTyping] = useState("");
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [recordingStartedAt, setRecordingStartedAt] = useState<number>(0);
  const [uploading, setUploading] = useState(false);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTypingAt = useRef(0);
  const appState = useRef<AppStateStatus>(AppState.currentState);

  const newestMessageId = useMemo(
    () => messages.reduce((max, message) => Math.max(max, message.id > 0 ? message.id : 0), 0),
    [messages]
  );
  const oldestMessageId = useMemo(
    () => messages.filter((message) => message.id > 0).reduce((min, message) => Math.min(min, message.id), Number.MAX_SAFE_INTEGER),
    [messages]
  );
  const visibleMessages = useMemo(() => [...messages].reverse(), [messages]);

  const mergeMessages = useCallback((current: MessengerMessage[], incoming: MessengerMessage[]) => {
    const byKey = new Map<string, MessengerMessage>();
    [...current, ...incoming].forEach((message) => {
      const key = message.client_message_id || String(message.id);
      const existing = byKey.get(key);
      byKey.set(key, {
        ...existing,
        ...message,
        local_status: message.local_status || existing?.local_status,
        local_error: message.local_error || existing?.local_error
      });
    });
    return Array.from(byKey.values()).sort((a, b) => a.id - b.id);
  }, []);

  const replaceLocalMessage = useCallback((localId: number, next: MessengerMessage) => {
    setMessages((current) => mergeMessages(current.filter((message) => message.id !== localId), [next]));
  }, [mergeMessages]);

  const load = useCallback(async ({ refresh = false } = {}) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = await getConversation(conversationId, { limit: PAGE_SIZE });
      const nextMessages = data.messages || [];
      setMessages(nextMessages);
      await cacheMessages(conversationId, nextMessages);
      await markConversationSeen(conversationId).catch(() => undefined);
      setTyping(typingSummary(data.presence));
    } catch (loadError) {
      const cached = await loadCachedMessages(conversationId);
      if (cached.length) setMessages(cached);
      setError(loadError instanceof Error ? loadError.message : "Messages could not load.");
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [conversationId]);

  const loadOlder = useCallback(async () => {
    if (loadingOlder || oldestMessageId === Number.MAX_SAFE_INTEGER) return;
    setLoadingOlder(true);
    try {
      const data = await getConversation(conversationId, { limit: PAGE_SIZE, beforeId: oldestMessageId });
      if (data.messages?.length) {
        setMessages((current) => mergeMessages(data.messages || [], current));
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Older messages could not load.");
    } finally {
      setLoadingOlder(false);
    }
  }, [conversationId, loadingOlder, mergeMessages, oldestMessageId]);

  const sync = useCallback(async () => {
    if (appState.current !== "active") return;
    if (!newestMessageId) return;
    try {
      const data = await syncConversation(conversationId, newestMessageId);
      if (data.messages?.length) {
        setMessages((current) => {
          const merged = mergeMessages(current, data.messages || []);
          cacheMessages(conversationId, merged).catch(() => undefined);
          return merged;
        });
        await markConversationSeen(conversationId).catch(() => undefined);
      }
      setTyping(typingSummary(data.presence));
      setError("");
    } catch {
      setTyping("");
    }
  }, [conversationId, mergeMessages, newestMessageId]);

  const notifyTyping = useCallback((value: string) => {
    setDraft(value);
    const now = Date.now();
    if (now - lastTypingAt.current > 1800) {
      lastTypingAt.current = now;
      sendTyping(conversationId, true).catch(() => undefined);
    }
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => {
      sendTyping(conversationId, false).catch(() => undefined);
    }, 1200);
  }, [conversationId]);

  const sendPayload = useCallback(async (payload: {
    body?: string;
    message_type?: string;
    media_url?: string;
    thumbnail_url?: string;
    file_size?: number;
    duration_seconds?: number;
  }) => {
    const label = payload.body || payload.media_url || "Attachment";
    const local = createLocalMessage(conversationId, label, payload.message_type || "text");
    local.media_url = payload.media_url;
    local.thumbnail_url = payload.thumbnail_url;
    local.file_size = payload.file_size;
    local.duration_seconds = payload.duration_seconds;
    setMessages((current) => mergeMessages(current, [local]));
    try {
      const sent = await sendConversationMessage(conversationId, {
        ...payload,
        client_message_id: local.client_message_id,
        local_created_at: local.created_at
      });
      const serverMessage = sent.data || {
        ...local,
        id: Number(sent.message_id || Date.now()),
        message_id: Number(sent.message_id || Date.now()),
        delivery_status: "sent",
        local_status: "sent"
      };
      replaceLocalMessage(local.id, serverMessage);
      await sync();
    } catch (sendError) {
      setMessages((current) =>
        current.map((message) =>
          message.id === local.id
            ? {
                ...message,
                delivery_status: "failed",
                local_status: "failed",
                local_error: sendError instanceof Error ? sendError.message : "Send failed."
              }
            : message
        )
      );
    }
  }, [conversationId, mergeMessages, replaceLocalMessage, sync]);

  const submitText = useCallback(async () => {
    const body = draft.trim();
    if (!body) return;
    setDraft("");
    await sendTyping(conversationId, false).catch(() => undefined);
    await sendPayload({ body, message_type: "text" });
  }, [conversationId, draft, sendPayload]);

  const retryMessage = useCallback(async (message: MessengerMessage) => {
    setMessages((current) => current.filter((item) => item.id !== message.id));
    await sendPayload({
      body: message.body || "",
      message_type: message.message_type || "text",
      media_url: message.media_url,
      thumbnail_url: message.thumbnail_url,
      file_size: message.file_size,
      duration_seconds: message.duration_seconds
    });
  }, [sendPayload]);

  const uploadAndSend = useCallback(async (input: { uri: string; name: string; mimeType: string; voice?: boolean; durationSeconds?: number }) => {
    if (uploading) return;
    setUploading(true);
    try {
      const uploaded = await uploadMessengerMedia({
        conversationId,
        uri: input.uri,
        name: input.name,
        mimeType: input.mimeType,
        voice: input.voice,
        durationSeconds: input.durationSeconds
      });
      await sendPayload({
        body: input.name,
        message_type: uploaded.message_type || uploaded.type || (input.voice ? "voice" : "file"),
        media_url: uploaded.media_url,
        thumbnail_url: uploaded.thumbnail_url,
        file_size: uploaded.file_size,
        duration_seconds: input.durationSeconds
      });
    } catch (uploadError) {
      Alert.alert("Attachment failed", uploadError instanceof Error ? uploadError.message : "Attachment could not be sent.");
    } finally {
      setUploading(false);
    }
  }, [conversationId, sendPayload, uploading]);

  const attachImage = useCallback(async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert("Photos unavailable", "Photo access was not granted.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.82,
      allowsEditing: false
    });
    if (result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    await uploadAndSend({
      uri: asset.uri,
      name: asset.fileName || `pulsesoc-image-${Date.now()}.jpg`,
      mimeType: asset.mimeType || "image/jpeg"
    });
  }, [uploadAndSend]);

  const attachFile = useCallback(async () => {
    const result = await DocumentPicker.getDocumentAsync({
      copyToCacheDirectory: true,
      multiple: false
    });
    if (result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    await uploadAndSend({
      uri: asset.uri,
      name: asset.name || `pulsesoc-file-${Date.now()}`,
      mimeType: asset.mimeType || "application/octet-stream"
    });
  }, [uploadAndSend]);

  const toggleVoiceRecording = useCallback(async () => {
    if (recording) {
      const activeRecording = recording;
      setRecording(null);
      await activeRecording.stopAndUnloadAsync();
      const uri = activeRecording.getURI();
      const durationSeconds = Math.max(1, Math.round((Date.now() - recordingStartedAt) / 1000));
      if (uri) {
        await uploadAndSend({
          uri,
          name: `pulsesoc-voice-${Date.now()}.m4a`,
          mimeType: "audio/m4a",
          voice: true,
          durationSeconds
        });
      }
      return;
    }
    const permission = await Audio.requestPermissionsAsync();
    if (!permission.granted) {
      Alert.alert("Microphone unavailable", "Microphone access was not granted.");
      return;
    }
    await Audio.setAudioModeAsync({
      allowsRecordingIOS: true,
      playsInSilentModeIOS: true
    });
    const started = await Audio.Recording.createAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);
    setRecording(started.recording);
    setRecordingStartedAt(Date.now());
  }, [recording, recordingStartedAt, uploadAndSend]);

  useEffect(() => {
    let mounted = true;
    loadCachedMessages(conversationId).then((cached) => {
      if (mounted && cached.length) setMessages(cached);
    });
    load().catch(() => undefined);
    return () => {
      mounted = false;
      if (typingTimer.current) clearTimeout(typingTimer.current);
      sendTyping(conversationId, false).catch(() => undefined);
    };
  }, [conversationId, load]);

  useEffect(() => {
    const timer = setInterval(() => {
      sync().catch(() => undefined);
    }, SYNC_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [sync]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      const wasBackgrounded = appState.current.match(/inactive|background/);
      appState.current = nextState;
      if (wasBackgrounded && nextState === "active") {
        sync().catch(() => load().catch(() => undefined));
      }
    });
    return () => subscription.remove();
  }, [load, sync]);

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.title} numberOfLines={1}>{route.params.title || "Chat"}</Text>
        <Text style={styles.presence} numberOfLines={1}>{typing || "Secure PulseSoc messages"}</Text>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && messages.length === 0 ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : (
        <FlatList
          data={visibleMessages}
          inverted
          keyExtractor={(item) => `${item.id}-${item.client_message_id || ""}`}
          contentContainerStyle={styles.list}
          initialNumToRender={18}
          maxToRenderPerBatch={12}
          removeClippedSubviews
          updateCellsBatchingPeriod={40}
          windowSize={9}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          onEndReached={loadOlder}
          onEndReachedThreshold={0.2}
          ListFooterComponent={loadingOlder ? <ActivityIndicator color={colors.accent} /> : null}
          renderItem={({ item }) => <MessageBubble message={item} onRetry={() => retryMessage(item)} />}
        />
      )}
      <View style={styles.composer}>
        <View style={styles.tools}>
          <Pressable accessibilityLabel="Attach image" disabled={uploading} style={[styles.iconButton, uploading && styles.disabled]} onPress={attachImage}>
            <Text style={styles.iconText}>{uploading ? "..." : "Img"}</Text>
          </Pressable>
          <Pressable
            accessibilityLabel="Open camera"
            disabled={uploading}
            style={[styles.iconButton, uploading && styles.disabled]}
            onPress={() => navigation.navigate("CameraStudio", { target: "message", mode: "photo", conversationId, title: "Message Camera" })}
          >
            <Text style={styles.iconText}>Cam</Text>
          </Pressable>
          <Pressable accessibilityLabel="Attach file" disabled={uploading} style={[styles.iconButton, uploading && styles.disabled]} onPress={attachFile}>
            <Text style={styles.iconText}>File</Text>
          </Pressable>
          <Pressable accessibilityLabel="Record voice message" disabled={uploading && !recording} style={[styles.iconButton, recording && styles.recording, uploading && !recording && styles.disabled]} onPress={toggleVoiceRecording}>
            <Text style={styles.iconText}>{recording ? "Stop" : "Mic"}</Text>
          </Pressable>
        </View>
        <View style={styles.inputRow}>
          <TextInput
            multiline
            placeholder="Message"
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={draft}
            onChangeText={notifyTyping}
          />
          <Pressable style={({ pressed }) => [styles.sendButton, pressed && styles.pressed]} onPress={submitText}>
            <Text style={styles.sendText}>Send</Text>
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

function MessageBubble({ message, onRetry }: { message: MessengerMessage; onRetry: () => void }) {
  const mine = Boolean(message.is_mine);
  const status = message.local_status || message.delivery_status || "sent";
  return (
    <View style={[styles.bubbleWrap, mine ? styles.mineWrap : styles.theirWrap]}>
      <View style={[styles.bubble, mine ? styles.mineBubble : styles.theirBubble]}>
        <MessageMedia message={message} />
        {message.body ? <Text style={styles.body}>{message.body}</Text> : null}
        <View style={styles.metaRow}>
          <Text style={styles.meta}>{formatShortTime(message.created_at)}</Text>
          {mine ? <Text style={styles.meta}>{statusLabel(status, message.seen_at)}</Text> : null}
        </View>
        {status === "failed" ? (
          <Pressable style={styles.retry} onPress={onRetry}>
            <Text style={styles.retryText}>Retry failed send</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

function MessageMedia({ message }: { message: MessengerMessage }) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const type = (message.message_type || "text").toLowerCase();
  const mediaUrl = absoluteMediaUrl(message.media_url);
  const thumbnailUrl = absoluteMediaUrl(message.thumbnail_url || message.media_url);
  if (!mediaUrl) return null;
  const viewerItem: NativeMediaViewerItem = {
    id: Number(message.id || message.message_id || 0),
    kind: type === "video" ? "video" : type === "image" || type === "gif" ? "image" : "file",
    url: mediaUrl,
    thumbnailUrl,
    title: type === "video" ? "Video attachment" : type === "image" || type === "gif" ? "Image attachment" : "Messenger attachment",
    subtitle: message.body || statusLabel(message.local_status || message.delivery_status || "sent", message.seen_at),
    sourceUrl: mediaUrl
  };
  if (type === "image" || type === "gif") {
    return (
      <>
        <Pressable onPress={() => setViewerOpen(true)}>
          <Image source={{ uri: thumbnailUrl || mediaUrl }} style={styles.image} resizeMode="cover" />
        </Pressable>
        <NativeMediaViewer visible={viewerOpen} items={[viewerItem]} title="Messenger media" onClose={() => setViewerOpen(false)} />
      </>
    );
  }
  if (type === "voice" || type === "audio") {
    return (
      <View style={styles.attachment}>
        <Text style={styles.attachmentTitle}>{type === "voice" ? "Voice message" : "Audio message"}</Text>
        <Text style={styles.attachmentMeta}>{message.duration_seconds ? `${message.duration_seconds}s` : "Ready to play"}</Text>
      </View>
    );
  }
  return (
    <Pressable style={styles.attachment} onPress={() => (type === "video" ? setViewerOpen(true) : undefined)}>
      <Text style={styles.attachmentTitle}>{type === "video" ? "Video attachment" : "File attachment"}</Text>
      <Text style={styles.attachmentMeta}>{type === "video" ? "Open viewer" : formatFileSize(message.file_size)}</Text>
      <NativeMediaViewer visible={viewerOpen} items={[viewerItem]} title="Messenger media" onClose={() => setViewerOpen(false)} />
    </Pressable>
  );
}

function absoluteMediaUrl(value?: string) {
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith("/")) return `${PULSE_API_BASE_URL}${value}`;
  return value;
}

function statusLabel(status: string, seenAt?: string) {
  if (seenAt || status === "seen" || status === "read") return "Read";
  if (status === "failed") return "Failed";
  if (status === "sending") return "Sending";
  if (status === "delivered") return "Delivered";
  return "Sent";
}

function typingSummary(presence?: { typing?: Array<{ display_name?: string; is_typing?: boolean }> }) {
  const names = presence?.typing?.filter((item) => item.is_typing !== false).map((item) => item.display_name || "Someone") || [];
  if (!names.length) return "";
  if (names.length === 1) return `${names[0]} is typing`;
  return `${names.slice(0, 2).join(", ")} are typing`;
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  header: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    padding: 16
  },
  title: {
    color: colors.text,
    fontSize: 23,
    fontWeight: "900"
  },
  presence: {
    color: colors.muted,
    fontSize: 13
  },
  error: {
    color: colors.warning,
    paddingHorizontal: 16,
    paddingTop: 10
  },
  center: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center"
  },
  list: {
    gap: 8,
    padding: 14
  },
  bubbleWrap: {
    flexDirection: "row"
  },
  mineWrap: {
    justifyContent: "flex-end"
  },
  theirWrap: {
    justifyContent: "flex-start"
  },
  bubble: {
    borderRadius: 8,
    gap: 6,
    maxWidth: "84%",
    minWidth: 88,
    padding: 10
  },
  mineBubble: {
    backgroundColor: colors.accentStrong
  },
  theirBubble: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth
  },
  body: {
    color: colors.text,
    fontSize: 16,
    lineHeight: 22
  },
  metaRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "flex-end"
  },
  meta: {
    color: "#d8e3f0",
    fontSize: 11
  },
  image: {
    aspectRatio: 1.12,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    width: 220
  },
  attachment: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderRadius: 8,
    gap: 3,
    minWidth: 190,
    padding: 10
  },
  attachmentTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  attachmentMeta: {
    color: "#d8e3f0",
    fontSize: 12
  },
  retry: {
    alignSelf: "flex-start",
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  retryText: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "800"
  },
  composer: {
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 12
  },
  tools: {
    flexDirection: "row",
    gap: 8
  },
  iconButton: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 36,
    minWidth: 56,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  recording: {
    borderColor: colors.danger
  },
  disabled: {
    opacity: 0.55
  },
  iconText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  inputRow: {
    alignItems: "flex-end",
    flexDirection: "row",
    gap: 8
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    flex: 1,
    maxHeight: 118,
    minHeight: 46,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  sendButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 46,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  pressed: {
    opacity: 0.82
  },
  sendText: {
    color: "#08110f",
    fontWeight: "900"
  }
});
