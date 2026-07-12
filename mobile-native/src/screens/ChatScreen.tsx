import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Audio } from "expo-av";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  AppState,
  AppStateStatus,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  cacheMessages,
  createLocalMessage,
  deleteMessage,
  getConversation,
  loadCachedMessages,
  markConversationSeen,
  MessengerMessage,
  reactToMessage,
  reportMessage,
  sendConversationMessage,
  sendTyping,
  syncConversation,
  uploadMessengerMedia
} from "../api/messenger";
import { PULSE_API_BASE_URL } from "../api/config";
import { NativeMediaViewer, NativeMediaViewerItem } from "../components/NativeMediaViewer";
import { PulseCommandAction, PulseCommandHeader, PulseCommandPanel } from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import {
  messageAccessibilityLabel,
  messageActionRules,
  messageDeliveryLabel,
  messagePreview,
  optimisticReaction,
  reactionIcon,
  typingSummary
} from "../pulseCommand/domain";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { formatFileSize, formatShortTime } from "../utils/format";

const PAGE_SIZE = 40;
const SYNC_INTERVAL_MS = 2500;

export function ChatScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "Chat">) {
  const conversationId = route.params.conversationId;
  const insets = useSafeAreaInsets();
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
  const [replyTo, setReplyTo] = useState<MessengerMessage | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<MessengerMessage | null>(null);
  const [statusMessage, setStatusMessage] = useState("");
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
      if (cached.length) {
        setMessages(cached);
        setError("");
        setStatusMessage("Showing cached messages while PulseSoc reconnects.");
      } else {
        setError(loadError instanceof Error ? loadError.message : "Messages could not load.");
      }
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
    reply_to_message_id?: number;
    reply_preview?: string;
  }) => {
    const label = payload.body || payload.media_url || "Attachment";
    const local = createLocalMessage(conversationId, label, payload.message_type || "text");
    local.media_url = payload.media_url;
    local.thumbnail_url = payload.thumbnail_url;
    local.file_size = payload.file_size;
    local.duration_seconds = payload.duration_seconds;
    local.reply_to_message_id = payload.reply_to_message_id;
    local.reply_preview = payload.reply_preview;
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
    const currentReply = replyTo;
    setReplyTo(null);
    await sendTyping(conversationId, false).catch(() => undefined);
    await sendPayload({
      body,
      message_type: "text",
      reply_to_message_id: currentReply?.message_id,
      reply_preview: currentReply ? messagePreview(currentReply) : undefined
    });
  }, [conversationId, draft, replyTo, sendPayload]);

  const retryMessage = useCallback(async (message: MessengerMessage) => {
    setMessages((current) => current.filter((item) => item.id !== message.id));
    await sendPayload({
      body: message.body || "",
      message_type: message.message_type || "text",
      media_url: message.media_url,
      thumbnail_url: message.thumbnail_url,
      file_size: message.file_size,
      duration_seconds: message.duration_seconds,
      reply_to_message_id: message.reply_to_message_id,
      reply_preview: message.reply_preview
    });
  }, [sendPayload]);

  const react = useCallback(async (message: MessengerMessage, reactionType = "pulse") => {
    if (message.id <= 0) {
      setStatusMessage("Pending messages can be reacted to after the server accepts them.");
      return;
    }
    const previous = message.reactions || {};
    setMessages((current) => current.map((item) => item.id === message.id ? { ...item, reactions: optimisticReaction(previous, reactionType), viewer_reaction: reactionType } : item));
    setStatusMessage("Reaction sent.");
    try {
      const result = await reactToMessage(message.id, reactionType);
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? {
                ...item,
                reactions: result.reactions || item.reactions,
                viewer_reaction: result.removed ? "" : result.reaction_type || reactionType
              }
            : item
        )
      );
    } catch (reactionError) {
      setMessages((current) => current.map((item) => item.id === message.id ? { ...item, reactions: previous, viewer_reaction: message.viewer_reaction } : item));
      setStatusMessage(reactionError instanceof Error ? reactionError.message : "Reaction failed.");
    }
  }, []);

  const removeMessage = useCallback(async (message: MessengerMessage, scope: "self" | "everyone" = "self") => {
    if (message.id <= 0) {
      setMessages((current) => current.filter((item) => item.id !== message.id));
      setStatusMessage("Local pending message removed.");
      return;
    }
    try {
      await deleteMessage(message.id, scope);
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? { ...item, body: "", deleted_at: new Date().toISOString(), delivery_status: "deleted", message_type: "system" }
            : item
        )
      );
      setStatusMessage(scope === "everyone" ? "Message deletion requested." : "Message hidden from this device.");
    } catch (deleteError) {
      setStatusMessage(deleteError instanceof Error ? deleteError.message : "Delete failed.");
    }
  }, []);

  const report = useCallback(async (message: MessengerMessage) => {
    if (message.id <= 0) {
      setStatusMessage("Pending messages cannot be reported until the server accepts them.");
      return;
    }
    try {
      const result = await reportMessage(message.id, "Reported from native Pulse Command");
      setStatusMessage(result.message || "Message report sent to Trust & Safety.");
    } catch (reportError) {
      setStatusMessage(reportError instanceof Error ? reportError.message : "Report failed.");
    }
  }, []);

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
      <LogiNexusScreenShell bottomDock={false} contentStyle={styles.shellContent}>
      <View style={styles.header}>
        <PulseCommandHeader
          title={route.params.title || "Chat"}
          subtitle={typing || "Secure PulseSoc message channel"}
          status={error ? "Reconnecting" : "Live channel"}
          tone={error ? "warning" : "default"}
          actions={
            <View style={styles.callActions}>
              <PulseCommandAction
                compact
                label="Voice"
                onPress={() => navigation.navigate("Call", { conversationId, callType: "audio", direction: "outgoing", title: route.params.title || "PulseSoc Voice" })}
              />
              <PulseCommandAction
                compact
                label="Video"
                tone="intelligence"
                onPress={() => navigation.navigate("Call", { conversationId, callType: "video", direction: "outgoing", title: route.params.title || "PulseSoc Video" })}
              />
            </View>
          }
        />
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && messages.length === 0 ? (
        <LogiNexusStatePanel state="loading" title="Opening chat" body="Loading conversation history from the server." loading style={styles.loadingPanel} />
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
          ListFooterComponent={loadingOlder ? <Text style={styles.loadingOlder}>Loading older messages...</Text> : null}
          ListEmptyComponent={<LogiNexusStatePanel state="empty" title="No messages yet" body="Messages in this chat will appear here." style={styles.emptyMessages} />}
          renderItem={({ item }) => (
            <MessageBubble
              message={item}
              onRetry={() => retryMessage(item)}
              onReact={() => react(item)}
              onLongPress={() => setSelectedMessage(item)}
            />
          )}
        />
      )}
      <PulseCommandPanel style={[styles.composer, { paddingBottom: Math.max(insets.bottom, 10) + 10 }]}>
        {statusMessage ? (
          <Pressable accessibilityRole="button" accessibilityLabel="Dismiss message status" style={styles.statusBanner} onPress={() => setStatusMessage("")}>
            <Text style={styles.statusBannerText}>{statusMessage}</Text>
          </Pressable>
        ) : null}
        {replyTo ? (
          <View style={styles.replyComposer}>
            <View style={styles.replyCopy}>
              <Text style={styles.replyTitle}>Replying to {replyTo.is_mine ? "your message" : replyTo.sender_display_name || "sender"}</Text>
              <Text style={styles.replyPreview} numberOfLines={1}>{messagePreview(replyTo)}</Text>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel="Cancel reply" style={styles.replyCancel} onPress={() => setReplyTo(null)}>
              <Text style={styles.replyCancelText}>Cancel</Text>
            </Pressable>
          </View>
        ) : null}
        <View style={styles.tools}>
          <Pressable accessibilityRole="button" accessibilityLabel="Attach image" disabled={uploading} style={[styles.iconButton, uploading && styles.disabled]} onPress={attachImage}>
            <Text style={styles.iconText}>{uploading ? "..." : "Img"}</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Open camera"
            disabled={uploading}
            style={[styles.iconButton, uploading && styles.disabled]}
            onPress={() => navigation.navigate("CameraStudio", { target: "message", mode: "photo", conversationId, title: "Message Camera" })}
          >
            <Text style={styles.iconText}>Cam</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Attach file" disabled={uploading} style={[styles.iconButton, uploading && styles.disabled]} onPress={attachFile}>
            <Text style={styles.iconText}>File</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel={recording ? "Stop recording voice message" : "Record voice message"} disabled={uploading && !recording} style={[styles.iconButton, recording && styles.recording, uploading && !recording && styles.disabled]} onPress={toggleVoiceRecording}>
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
            accessibilityLabel="Message composer"
          />
          <Pressable accessibilityRole="button" accessibilityLabel="Send message" disabled={!draft.trim()} style={({ pressed }) => [styles.sendButton, !draft.trim() && styles.sendDisabled, pressed && styles.pressed]} onPress={submitText}>
            <Text style={styles.sendText}>Send</Text>
          </Pressable>
        </View>
      </PulseCommandPanel>
      <MessageActionSheet
        message={selectedMessage}
        onClose={() => setSelectedMessage(null)}
        onReply={(message) => {
          setReplyTo(message);
          setSelectedMessage(null);
        }}
        onReact={(message, reactionType) => {
          react(message, reactionType).catch(() => undefined);
          setSelectedMessage(null);
        }}
        onRetry={(message) => {
          retryMessage(message).catch(() => undefined);
          setSelectedMessage(null);
        }}
        onDelete={(message, scope) => {
          removeMessage(message, scope).catch(() => undefined);
          setSelectedMessage(null);
        }}
        onReport={(message) => {
          report(message).catch(() => undefined);
          setSelectedMessage(null);
        }}
        onSafety={() => {
          setSelectedMessage(null);
          navigation.navigate("SafetyHub", { section: "blocks", title: "Safety Hub" });
        }}
      />
      </LogiNexusScreenShell>
    </KeyboardAvoidingView>
  );
}

function MessageBubble({
  message,
  onRetry,
  onReact,
  onLongPress
}: {
  message: MessengerMessage;
  onRetry: () => void;
  onReact: () => void;
  onLongPress: () => void;
}) {
  const mine = Boolean(message.is_mine);
  const status = message.local_status || message.delivery_status || "sent";
  const deleted = Boolean(message.deleted_at || status === "deleted");
  const moderated = Boolean(message.moderated_at || message.moderation_state);
  const body = deleted ? "This message was deleted." : moderated ? "This message is unavailable after safety review." : message.body;
  return (
    <View style={[styles.bubbleWrap, mine ? styles.mineWrap : styles.theirWrap]} accessible accessibilityLabel={messageAccessibilityLabel(message)}>
      <Pressable onLongPress={onLongPress} style={[styles.bubble, mine ? styles.mineBubble : styles.theirBubble, moderated && styles.moderatedBubble]}>
        {!mine ? <Text style={styles.senderLabel}>{message.sender_display_name || (message.sender_trust_state === "intelligence" ? "UNDX" : "PulseSoc member")}</Text> : null}
        {message.reply_preview ? (
          <View style={styles.replyBlock}>
            <Text style={styles.replyTitle}>Reply</Text>
            <Text style={styles.replyPreview} numberOfLines={2}>{message.reply_preview}</Text>
          </View>
        ) : null}
        {!deleted && !moderated ? <MessageMedia message={message} /> : null}
        {body ? <Text style={[styles.body, (deleted || moderated) && styles.systemBody]}>{body}</Text> : null}
        {message.forwarded ? <Text style={styles.forwarded}>Forwarded signal</Text> : null}
        <View style={styles.metaRow}>
          <Text style={styles.meta}>{formatShortTime(message.created_at)}</Text>
          {message.edited_at ? <Text style={styles.meta}>Edited</Text> : null}
          {mine ? <Text style={styles.meta}>{messageDeliveryLabel(status, message.seen_at)}</Text> : null}
        </View>
        <ReactionRow reactions={message.reactions} viewerReaction={message.viewer_reaction} onReact={onReact} />
        {status === "failed" ? (
          <Pressable style={styles.retry} onPress={onRetry}>
            <Text style={styles.retryText}>Retry failed send</Text>
          </Pressable>
        ) : null}
      </Pressable>
    </View>
  );
}

function ReactionRow({ reactions, viewerReaction, onReact }: { reactions?: Record<string, number>; viewerReaction?: string; onReact: () => void }) {
  const entries = Object.entries(reactions || {}).filter(([, count]) => Number(count || 0) > 0).slice(0, 4);
  if (!entries.length && !viewerReaction) return null;
  return (
    <View style={styles.reactionRow}>
      {entries.map(([reaction, count]) => (
        <Pressable key={reaction} accessibilityRole="button" accessibilityLabel={`React ${reaction}`} style={[styles.reactionPill, viewerReaction === reaction && styles.reactionActive]} onPress={onReact}>
          <Text style={styles.reactionText}>{reactionIcon(reaction)} {count}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function MessageActionSheet({
  message,
  onClose,
  onReply,
  onReact,
  onRetry,
  onDelete,
  onReport,
  onSafety
}: {
  message: MessengerMessage | null;
  onClose: () => void;
  onReply: (message: MessengerMessage) => void;
  onReact: (message: MessengerMessage, reactionType: string) => void;
  onRetry: (message: MessengerMessage) => void;
  onDelete: (message: MessengerMessage, scope: "self" | "everyone") => void;
  onReport: (message: MessengerMessage) => void;
  onSafety: () => void;
}) {
  if (!message) return null;
  const actions = messageActionRules(message);
  const canReact = actions.find((action) => action.key === "react")?.available;
  const actionIsAvailable = (key: ReturnType<typeof messageActionRules>[number]["key"]) => actions.find((action) => action.key === key)?.available;
  return (
    <Modal transparent animationType="fade" visible onRequestClose={onClose}>
      <Pressable style={styles.sheetBackdrop} onPress={onClose}>
        <PulseCommandPanel style={styles.sheet}>
          <Text style={styles.sheetTitle}>Message controls</Text>
          <Text style={styles.sheetPreview} numberOfLines={2}>{messagePreview(message)}</Text>
          {canReact ? (
            <View style={styles.reactionChoices}>
              {["pulse", "spark", "thanks", "seen"].map((reaction) => (
                <Pressable key={reaction} style={styles.reactionChoice} onPress={() => onReact(message, reaction)}>
                  <Text style={styles.reactionText}>{reactionIcon(reaction)} {reaction}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}
          <View style={styles.sheetGrid}>
            {actionIsAvailable("reply") ? <SheetAction label="Reply" onPress={() => onReply(message)} /> : null}
            {actionIsAvailable("retry") ? <SheetAction label="Retry" tone="warning" onPress={() => onRetry(message)} /> : null}
            {actionIsAvailable("report") ? <SheetAction label="Report" tone="warning" onPress={() => onReport(message)} /> : null}
            {actionIsAvailable("safety") ? <SheetAction label="Mute / Block" tone="safety" onPress={onSafety} /> : null}
            {actionIsAvailable("deleteSelf") ? <SheetAction label="Delete for me" tone="danger" onPress={() => onDelete(message, "self")} /> : null}
            {actionIsAvailable("deleteEveryone") ? <SheetAction label="Delete for everyone" tone="danger" onPress={() => onDelete(message, "everyone")} /> : null}
          </View>
        </PulseCommandPanel>
      </Pressable>
    </Modal>
  );
}

function SheetAction({ label, onPress, tone = "default" }: { label: string; onPress: () => void; tone?: "default" | "warning" | "danger" | "safety" }) {
  const textColor = tone === "danger" ? colors.danger : tone === "warning" ? colors.warning : tone === "safety" ? colors.accent : colors.text;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} style={styles.sheetAction} onPress={onPress}>
      <Text style={[styles.sheetActionText, { color: textColor }]}>{label}</Text>
    </Pressable>
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
    subtitle: message.body || messageDeliveryLabel(message.local_status || message.delivery_status || "sent", message.seen_at),
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

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  shellContent: {
    flex: 1
  },
  header: {
    padding: logiNexus.spacing.md,
    paddingBottom: logiNexus.spacing.sm
  },
  callActions: {
    flexDirection: "row",
    gap: logiNexus.spacing.sm
  },
  error: {
    color: colors.warning,
    paddingHorizontal: 16,
    paddingTop: 10
  },
  loadingPanel: {
    margin: logiNexus.spacing.lg
  },
  list: {
    gap: 8,
    padding: 12,
    paddingTop: 8
  },
  emptyMessages: {
    marginTop: logiNexus.spacing.xxl
  },
  loadingOlder: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    padding: logiNexus.spacing.md,
    textAlign: "center"
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
    borderRadius: 17,
    gap: 6,
    maxWidth: "84%",
    minWidth: 88,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  mineBubble: {
    backgroundColor: "rgba(34, 171, 119, 0.58)",
    borderColor: "rgba(56, 221, 160, 0.26)",
    borderBottomRightRadius: 6,
    borderWidth: 1
  },
  theirBubble: {
    backgroundColor: "rgba(255,255,255,0.052)",
    borderBottomLeftRadius: 6,
    borderColor: "rgba(105,218,240,0.13)",
    borderWidth: 1
  },
  body: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 21
  },
  forwarded: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  metaRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "flex-end"
  },
  meta: {
    color: colors.muted,
    fontSize: 9
  },
  moderatedBubble: {
    borderColor: "rgba(255, 204, 102, 0.35)"
  },
  systemBody: {
    color: colors.muted,
    fontStyle: "italic"
  },
  reactionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6
  },
  reactionPill: {
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 7,
    paddingVertical: 3
  },
  reactionActive: {
    borderColor: colors.accent
  },
  reactionText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "capitalize"
  },
  replyBlock: {
    backgroundColor: "rgba(97,216,255,0.08)",
    borderLeftColor: colors.accent,
    borderLeftWidth: 2,
    borderRadius: 10,
    gap: 2,
    padding: 7
  },
  replyTitle: {
    color: colors.accentStrong,
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  replyPreview: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  image: {
    aspectRatio: 1.12,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 12,
    width: 220
  },
  attachment: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderColor: "rgba(97,216,255,0.24)",
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 3,
    minWidth: 190,
    padding: 10
  },
  attachmentTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  attachmentMeta: {
    color: colors.muted,
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
    gap: 8,
    marginHorizontal: 10,
    marginTop: 8,
    padding: 9
  },
  statusBanner: {
    backgroundColor: "rgba(97,216,255,0.08)",
    borderColor: "rgba(97,216,255,0.24)",
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 8
  },
  statusBannerText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  replyComposer: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    padding: 8
  },
  replyCopy: {
    flex: 1,
    minWidth: 0
  },
  replyCancel: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  replyCancelText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  tools: {
    flexDirection: "row",
    gap: 8
  },
  iconButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 44,
    minWidth: 44,
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
    backgroundColor: "rgba(3, 7, 18, 0.72)",
    borderColor: colors.border,
    borderRadius: 999,
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
    borderRadius: 999,
    minHeight: 46,
    minWidth: 48,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  pressed: {
    opacity: 0.82
  },
  sendDisabled: {
    backgroundColor: colors.disabled,
    opacity: 0.72
  },
  sendText: {
    color: "#08110f",
    fontWeight: "900"
  },
  senderLabel: {
    color: colors.accentStrong,
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  sheetBackdrop: {
    backgroundColor: "rgba(0,0,0,0.58)",
    flex: 1,
    justifyContent: "flex-end",
    padding: logiNexus.spacing.md
  },
  sheet: {
    gap: logiNexus.spacing.md,
    padding: logiNexus.spacing.lg
  },
  sheetTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  sheetPreview: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  reactionChoices: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  reactionChoice: {
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  sheetGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  sheetAction: {
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 42,
    minWidth: "47%",
    justifyContent: "center",
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  sheetActionText: {
    fontSize: 13,
    fontWeight: "900"
  }
});
