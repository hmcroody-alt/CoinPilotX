import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Audio } from "expo-av";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Alert,
  Animated,
  AppState,
  AppStateStatus,
  Easing,
  FlatList,
  Image,
  Keyboard,
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
  drainMessengerQueue,
  enqueueMessengerMessage,
  getConversation,
  isRetryableMessengerSendError,
  loadCachedMessages,
  markConversationSeen,
  MessengerMessage,
  reactToMessage,
  reportMessage,
  sendConversationMessage,
  sendTyping,
  updateCachedConversationPreview,
  syncConversation,
  uploadMessengerMedia
} from "../api/messenger";
import { PULSE_API_BASE_URL } from "../api/config";
import { PULSESOC_QA_MESSENGER_FIXTURES } from "../api/config";
import { NativeMediaViewer, NativeMediaViewerItem } from "../components/NativeMediaViewer";
import { ConversationControlCenter } from "../components/ConversationControlCenter";
import { PulseCommandAvatar, PulseCommandPanel } from "../components/PulseCommand";
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

function isLocalMessengerFixtureConversation(conversationId: number) {
  return PULSESOC_QA_MESSENGER_FIXTURES && conversationId >= 9001 && conversationId <= 9006;
}

function qaFixtureTyping(conversationId: number) {
  return conversationId === 9003 ? "Maria is typing" : "";
}

function LiveStatusDot({ warning }: { warning: boolean }) {
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    let loop: Animated.CompositeAnimation | null = null;
    AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (reduced) return;
      loop = Animated.loop(Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1100, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 1100, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
      ]));
      loop.start();
    }).catch(() => undefined);
    return () => loop?.stop();
  }, [pulse]);
  return (
    <View style={styles.threadStatusSignal}>
      <Animated.View style={[styles.threadStatusHalo, warning && styles.threadStatusWarning, { opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.08, 0.42] }), transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.8, 1.75] }) }] }]} />
      <View style={[styles.threadStatusDot, warning && styles.threadStatusWarning]} />
    </View>
  );
}

function AmbientPulseField() {
  const drift = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    let loop: Animated.CompositeAnimation | null = null;
    AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (reduced) return;
      loop = Animated.loop(Animated.sequence([
        Animated.timing(drift, { toValue: 1, duration: 9000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(drift, { toValue: 0, duration: 9000, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
      ]));
      loop.start();
    }).catch(() => undefined);
    return () => loop?.stop();
  }, [drift]);
  return (
    <View pointerEvents="none" style={styles.ambientField}>
      <Animated.View style={[styles.ambientOrbLarge, { opacity: drift.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1] }), transform: [{ translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -34] }) }, { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, 24] }) }, { scale: drift.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1.08] }) }] }]} />
      <Animated.View style={[styles.ambientOrbSmall, { opacity: drift.interpolate({ inputRange: [0, 1], outputRange: [1, 0.55] }), transform: [{ translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, 20] }) }, { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -18] }) }] }]} />
      <View style={styles.ambientSignalLine} />
    </View>
  );
}

function SignalIconButton({
  accessibilityLabel,
  icon,
  onPress,
  tone = "signal",
  active = false,
  disabled = false,
  size = 44
}: {
  accessibilityLabel: string;
  icon: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  tone?: "signal" | "intelligence" | "danger";
  active?: boolean;
  disabled?: boolean;
  size?: number;
}) {
  const pulse = useRef(new Animated.Value(0)).current;
  const color = tone === "danger" ? colors.danger : tone === "intelligence" ? "#a77cff" : colors.accent;
  useEffect(() => {
    let loop: Animated.CompositeAnimation | null = null;
    AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (reduced) return;
      loop = Animated.loop(Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1350, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 1350, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
      ]));
      loop.start();
    }).catch(() => undefined);
    return () => loop?.stop();
  }, [pulse]);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.signalButton, { borderColor: `${color}88`, height: size, opacity: disabled ? 0.45 : pressed ? 0.72 : 1, width: size }, active && { backgroundColor: `${color}24` }]}
    >
      <Animated.View pointerEvents="none" style={[styles.signalButtonHalo, { backgroundColor: color, opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.05, active ? 0.28 : 0.15] }), transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.72, 1.16] }) }] }]} />
      <Ionicons name={icon} size={Math.round(size * 0.46)} color={color} />
    </Pressable>
  );
}

export function ChatScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "Chat">) {
  const conversationId = route.params.conversationId;
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<MessengerMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState("");
  const [initialFetchComplete, setInitialFetchComplete] = useState(false);
  const [usingCachedMessages, setUsingCachedMessages] = useState(false);
  const [typing, setTyping] = useState("");
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [recordingStartedAt, setRecordingStartedAt] = useState<number>(0);
  const [uploading, setUploading] = useState(false);
  const [replyTo, setReplyTo] = useState<MessengerMessage | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<MessengerMessage | null>(null);
  const [attachmentSheetOpen, setAttachmentSheetOpen] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [controlCenterOpen, setControlCenterOpen] = useState(false);
  const [threadTitle, setThreadTitle] = useState(route.params.title || "Messenger");
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTypingAt = useRef(0);
  const appState = useRef<AppStateStatus>(AppState.currentState);
  const qaChatState = PULSESOC_QA_MESSENGER_FIXTURES ? String(process.env.EXPO_PUBLIC_PULSESOC_QA_CHAT_STATE || "") : "";
  const draftKey = `pulsesoc.native.messenger.draft.${conversationId}`;

  useEffect(() => {
    const show = Keyboard.addListener("keyboardWillShow", (event) => { setKeyboardVisible(true); setKeyboardHeight(event.endCoordinates.height); });
    const hide = Keyboard.addListener("keyboardWillHide", () => { setKeyboardVisible(false); setKeyboardHeight(0); });
    return () => { show.remove(); hide.remove(); };
  }, []);

  useEffect(() => {
    AsyncStorage.getItem(draftKey).then((saved) => {
      if (saved) setDraft(saved);
      else if (qaChatState === "keyboard") setDraft("A multiline PulseSoc draft stays visible and persists while the keyboard is open.");
    }).catch(() => undefined);
  }, [draftKey, qaChatState]);

  useEffect(() => {
    const timer = setTimeout(() => AsyncStorage.setItem(draftKey, draft).catch(() => undefined), 180);
    return () => clearTimeout(timer);
  }, [draft, draftKey]);

  useEffect(() => {
    if (!messages.length) return;
    if (qaChatState === "context-menu") setSelectedMessage(messages.find((message) => !message.is_mine) || messages[0]);
    if (qaChatState === "attachment-sheet") setAttachmentSheetOpen(true);
    if (qaChatState === "reply-keyboard") setReplyTo(messages.find((message) => !message.is_mine) || messages[0]);
    if (qaChatState === "control-center") setControlCenterOpen(true);
  }, [messages.length, qaChatState]);

  useEffect(() => {
    if (route.params.openControlCenter) setControlCenterOpen(true);
  }, [route.params.openControlCenter]);

  const newestMessageId = useMemo(
    () => messages.reduce((max, message) => Math.max(max, message.id > 0 ? message.id : 0), 0),
    [messages]
  );
  const oldestMessageId = useMemo(
    () => messages.filter((message) => message.id > 0).reduce((min, message) => Math.min(min, message.id), Number.MAX_SAFE_INTEGER),
    [messages]
  );
  const visibleMessages = useMemo(() => [...messages].reverse(), [messages]);
  const hasMessages = messages.length > 0;
  const showInitialLoading = loading && !hasMessages && !initialFetchComplete && !error;
  const showFatalError = Boolean(error && !hasMessages && !loading);
  const showEmptyConversation = Boolean(initialFetchComplete && !loading && !error && !hasMessages);
  const headerStatus = error
    ? hasMessages
      ? "Reconnecting"
      : "Messages unavailable"
    : usingCachedMessages
      ? "Cached history"
      : "Live channel";

  const mergeMessages = useCallback((current: MessengerMessage[], incoming: MessengerMessage[]) => {
    const byKey = new Map<string, MessengerMessage>();
    [...current, ...incoming].forEach((message) => {
      const key = message.client_message_id || String(message.id);
      const existing = byKey.get(key);
      const serverAccepted = message.id > 0 && Boolean(message.client_message_id);
      byKey.set(key, {
        ...existing,
        ...message,
        local_status: serverAccepted ? undefined : message.local_status || existing?.local_status,
        local_error: serverAccepted ? undefined : message.local_error || existing?.local_error,
        delivery_status: serverAccepted ? message.delivery_status || "sent" : message.delivery_status || existing?.delivery_status
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
      if (data.conversation) {
        const title = String(data.conversation.title || data.conversation.name || "").trim();
        if (title && title !== "[object Object]") setThreadTitle(title);
      }
      setMessages(nextMessages);
      setUsingCachedMessages(false);
      setStatusMessage("");
      await cacheMessages(conversationId, nextMessages);
      await markConversationSeen(conversationId).catch(() => undefined);
      setTyping(typingSummary(data.presence));
    } catch (loadError) {
      const cached = await loadCachedMessages(conversationId);
      if (cached.length) {
        setMessages(cached);
        setUsingCachedMessages(true);
        setError("");
        setStatusMessage("Showing cached messages while PulseSoc reconnects.");
      } else {
        setUsingCachedMessages(false);
        setError(loadError instanceof Error ? loadError.message : "Messages could not load.");
      }
    } finally {
      setInitialFetchComplete(true);
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
    if (isLocalMessengerFixtureConversation(conversationId)) {
      setTyping(qaFixtureTyping(conversationId));
      setUsingCachedMessages(false);
      setError("");
      setStatusMessage("");
      return;
    }
    try {
      const queued = await drainMessengerQueue(conversationId);
      if (queued.length) {
        setStatusMessage("Messages reconnected.");
        setMessages((current) => {
          const reconciled = mergeMessages(current, queued);
          cacheMessages(conversationId, reconciled).catch(() => undefined);
          return reconciled;
        });
      }
      if (!newestMessageId) return;
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
      setUsingCachedMessages(false);
      setError("");
    } catch {
      setTyping("");
      if (messages.length) setStatusMessage("Realtime reconnecting. Message history remains visible.");
    }
  }, [conversationId, mergeMessages, messages.length, newestMessageId]);

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
    media_ids?: number[];
    attachment_ids?: number[];
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
      await updateCachedConversationPreview(conversationId, label, serverMessage.created_at || new Date().toISOString()).catch(() => undefined);
      await sync();
      return "sent" as const;
    } catch (sendError) {
      if (isRetryableMessengerSendError(sendError)) {
        await enqueueMessengerMessage(conversationId, {
          ...payload,
          client_message_id: local.client_message_id,
          local_created_at: local.created_at
        }).catch(() => undefined);
        setMessages((current) => {
          const queuedMessages = current.map((message): MessengerMessage =>
            message.id === local.id
              ? {
                  ...message,
                  delivery_status: "queued",
                  local_status: "queued",
                  local_error: undefined
                }
              : message
          );
          cacheMessages(conversationId, queuedMessages).catch(() => undefined);
          return queuedMessages;
        });
        return "queued" as const;
      }
      setMessages((current) => {
        const failedMessages = current.map((message): MessengerMessage =>
          message.id === local.id
            ? {
                ...message,
                delivery_status: "failed",
                local_status: "failed",
                local_error: sendError instanceof Error ? sendError.message : "Message could not be sent."
              }
            : message
        );
        cacheMessages(conversationId, failedMessages).catch(() => undefined);
        return failedMessages;
      });
      throw sendError;
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

  const uploadAndSend = useCallback(async (input: { uri: string; name: string; mimeType: string; sizeBytes?: number; voice?: boolean; durationSeconds?: number }) => {
    if (uploading) return;
    setUploading(true);
    setStatusMessage(input.voice ? "Sending voice message…" : "Uploading attachment…");
    try {
      const uploaded = await uploadMessengerMedia({
        conversationId,
        uri: input.uri,
        name: input.name,
        mimeType: input.mimeType,
        sizeBytes: "sizeBytes" in input ? Number(input.sizeBytes || 0) : 0,
        voice: input.voice,
        durationSeconds: input.durationSeconds
      });
      const attachmentId = Number(uploaded.attachment_id || 0);
      if (!attachmentId) {
        throw new Error("PulseSoc did not return a durable Messenger attachment. Please retry.");
      }
      const delivery = await sendPayload({
        body: input.name,
        message_type: uploaded.message_type || uploaded.type || (input.voice ? "voice" : "file"),
        media_url: uploaded.media_url,
        thumbnail_url: uploaded.thumbnail_url,
        file_size: uploaded.file_size,
        duration_seconds: input.durationSeconds,
        attachment_ids: [attachmentId]
      });
      setStatusMessage(
        delivery === "queued"
          ? input.voice
            ? "Voice message queued. It will send when realtime reconnects."
            : "Attachment queued. It will send when realtime reconnects."
          : input.voice
            ? "Voice message sent."
            : "Attachment sent."
      );
    } catch (uploadError) {
      const message = uploadError instanceof Error ? uploadError.message : "Attachment could not be sent.";
      setStatusMessage(message);
      Alert.alert("Attachment failed", message);
    } finally {
      setUploading(false);
    }
  }, [conversationId, sendPayload, uploading]);

  const attachImage = useCallback(async () => {
    try {
      const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!permission.granted) {
        Alert.alert("Photos unavailable", "Allow photo access in Settings to share an image.");
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 0.78,
        allowsEditing: false,
        base64: false
      });
      if (result.canceled || !result.assets[0]) return;
      const asset = result.assets[0];
      await uploadAndSend({
        uri: asset.uri,
        name: asset.fileName || `pulsesoc-image-${Date.now()}.jpg`,
        mimeType: asset.mimeType || "image/jpeg",
        sizeBytes: asset.fileSize || 0
      });
    } catch (imageError) {
      const message = imageError instanceof Error ? imageError.message : "The image picker could not open.";
      setStatusMessage(message);
      Alert.alert("Image sharing unavailable", message);
    }
  }, [uploadAndSend]);

  const attachVideo = useCallback(async () => {
    try {
      const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!permission.granted) {
        Alert.alert("Videos unavailable", "Allow photo access in Settings to share a video.");
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Videos,
        allowsEditing: false,
        videoMaxDuration: 120,
        videoQuality: ImagePicker.UIImagePickerControllerQualityType.Medium
      });
      if (result.canceled || !result.assets[0]) return;
      const asset = result.assets[0];
      await uploadAndSend({
        uri: asset.uri,
        name: asset.fileName || `pulsesoc-video-${Date.now()}.mov`,
        mimeType: asset.mimeType || "video/quicktime",
        sizeBytes: asset.fileSize || 0
      });
    } catch (videoError) {
      const message = videoError instanceof Error ? videoError.message : "The video picker could not open.";
      setStatusMessage(message);
      Alert.alert("Video sharing unavailable", message);
    }
  }, [uploadAndSend]);

  const attachFile = useCallback(async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        copyToCacheDirectory: true,
        multiple: false
      });
      if (result.canceled || !result.assets[0]) return;
      const asset = result.assets[0];
      await uploadAndSend({
        uri: asset.uri,
        name: asset.name || `pulsesoc-file-${Date.now()}`,
        mimeType: asset.mimeType || "application/octet-stream",
        sizeBytes: asset.size || 0
      });
    } catch (fileError) {
      const message = fileError instanceof Error ? fileError.message : "The file picker could not open.";
      setStatusMessage(message);
      Alert.alert("File sharing unavailable", message);
    }
  }, [uploadAndSend]);

  const toggleVoiceRecording = useCallback(async () => {
    try {
      if (recording) {
        const activeRecording = recording;
        setRecording(null);
        await activeRecording.stopAndUnloadAsync();
        await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
        const uri = activeRecording.getURI();
        const durationSeconds = Math.max(1, Math.round((Date.now() - recordingStartedAt) / 1000));
        if (!uri) throw new Error("The recording did not produce an audio file.");
        await uploadAndSend({
          uri,
          name: `pulsesoc-voice-${Date.now()}.m4a`,
          mimeType: "audio/mp4",
          voice: true,
          durationSeconds
        });
        return;
      }
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        Alert.alert("Microphone unavailable", "Allow microphone access in Settings to send a voice message.");
        return;
      }
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true
      });
      const started = await Audio.Recording.createAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);
      setRecording(started.recording);
      setRecordingStartedAt(Date.now());
      setStatusMessage("Recording voice message… tap the microphone again to send.");
    } catch (recordingError) {
      setRecording(null);
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
      const message = recordingError instanceof Error ? recordingError.message : "Voice recording could not start.";
      setStatusMessage(message);
      Alert.alert("Voice message unavailable", message);
    }
  }, [recording, recordingStartedAt, uploadAndSend]);

  useEffect(() => {
    let mounted = true;
    loadCachedMessages(conversationId).then((cached) => {
      if (mounted && cached.length) {
        setMessages(cached);
        setUsingCachedMessages(true);
      }
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
    <View style={styles.root}>
      <LogiNexusScreenShell bottomDock={false} contentStyle={styles.shellContent}>
      <View style={[styles.header, { paddingTop: Math.max(insets.top, 10) }]}>
        <View style={styles.threadHeader}>
          <Pressable accessibilityRole="button" accessibilityLabel="Back to conversations" style={styles.backButton} onPress={() => navigation.goBack()}><Text style={styles.backButtonText}>‹</Text></Pressable>
          <PulseCommandAvatar label={route.params.title || "Chat"} imageUrl={route.params.avatarUrl} active={isPresenceActive(route.params.presence)} size={48} />
          <View style={styles.threadIdentity}>
            <Text style={styles.threadTitle} numberOfLines={1}>{threadTitle}</Text>
            <View style={styles.threadStatusRow}><LiveStatusDot warning={Boolean(error)} /><Text style={styles.threadSubtitle} numberOfLines={1}>{typing || (isPresenceActive(route.params.presence) ? "Online · Direct" : headerStatus)}</Text></View>
          </View>
          <View style={styles.callActions}>
            <SignalIconButton accessibilityLabel="Start audio call" icon="call-outline" onPress={() => navigation.navigate("Call", { conversationId, callType: "audio", direction: "outgoing", title: route.params.title || "PulseSoc Voice" })} />
            <SignalIconButton accessibilityLabel="Start video call" icon="videocam-outline" tone="intelligence" onPress={() => navigation.navigate("Call", { conversationId, callType: "video", direction: "outgoing", title: route.params.title || "PulseSoc Video" })} />
            <SignalIconButton accessibilityLabel="Open conversation controls" icon="ellipsis-vertical" onPress={() => setControlCenterOpen(true)} />
          </View>
        </View>
      </View>
      {error && hasMessages ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Retry loading messages" style={styles.errorBanner} onPress={() => load({ refresh: true })}>
          <Text style={styles.error}>{error}</Text>
        </Pressable>
      ) : null}
      {showInitialLoading ? (
        <LogiNexusStatePanel state="loading" title="Opening chat" body="Loading conversation history from the server." loading style={styles.loadingPanel} />
      ) : showFatalError ? (
        <LogiNexusStatePanel state="error" title="Messages could not load" body={error || "PulseSoc could not load this conversation. Tap retry to reconnect to the canonical message history."} style={styles.loadingPanel}>
          <Pressable accessibilityRole="button" accessibilityLabel="Retry loading messages" style={styles.retryStateButton} onPress={() => load({ refresh: true })}>
            <Text style={styles.retryStateText}>Retry</Text>
          </Pressable>
        </LogiNexusStatePanel>
      ) : (
        <>
        <AmbientPulseField />
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
          ListEmptyComponent={showEmptyConversation ? <LogiNexusStatePanel state="empty" title="No messages yet" body="Messages in this chat will appear here." style={styles.emptyMessages} /> : null}
          renderItem={({ item }) => (
            <MessageBubble
              message={item}
              onRetry={() => retryMessage(item)}
              onReact={() => react(item)}
              onLongPress={() => setSelectedMessage(item)}
            />
          )}
        />
        </>
      )}
      <PulseCommandPanel style={[styles.composer, { paddingBottom: Math.max(insets.bottom, 10) + 10 }, keyboardVisible && styles.composerKeyboard, keyboardVisible && { bottom: keyboardHeight }]}>
        <View pointerEvents="none" style={styles.composerSignalLine} />
        {statusMessage && !keyboardVisible ? (
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
        <View style={styles.inputRow}>
          <SignalIconButton accessibilityLabel={uploading ? "Uploading attachment" : "Add attachment"} icon={uploading ? "cloud-upload-outline" : "add"} disabled={uploading} size={46} onPress={() => setAttachmentSheetOpen(true)} />
          <TextInput
            multiline
            autoFocus={qaChatState === "keyboard" || qaChatState === "reply-keyboard"}
            placeholder="Message"
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={draft}
            onChangeText={notifyTyping}
            accessibilityLabel="Message composer"
          />
          <SignalIconButton accessibilityLabel="Add smiling emoji" icon="happy-outline" size={42} onPress={() => setDraft((current) => `${current}😊`)} />
          <SignalIconButton accessibilityLabel={recording ? "Stop and send voice message" : "Record voice message"} icon={recording ? "stop" : "mic-outline"} tone={recording ? "danger" : "signal"} active={Boolean(recording)} disabled={uploading} size={42} onPress={() => toggleVoiceRecording().catch(() => undefined)} />
          <Pressable accessibilityRole="button" accessibilityLabel="Send message" disabled={!draft.trim()} style={({ pressed }) => [styles.sendButton, !draft.trim() && styles.sendDisabled, pressed && styles.pressed]} onPress={submitText}>
            <Text style={styles.sendText}>➤</Text>
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
      <AttachmentActionSheet
        visible={attachmentSheetOpen}
        recording={Boolean(recording)}
        onClose={() => setAttachmentSheetOpen(false)}
        onImage={() => { setAttachmentSheetOpen(false); attachImage().catch(() => undefined); }}
        onVideo={() => { setAttachmentSheetOpen(false); attachVideo().catch(() => undefined); }}
        onCamera={() => { setAttachmentSheetOpen(false); navigation.navigate("CameraStudio", { target: "message", mode: "photo", conversationId, title: "Message Camera" }); }}
        onFile={() => { setAttachmentSheetOpen(false); attachFile().catch(() => undefined); }}
        onVoice={() => { setAttachmentSheetOpen(false); toggleVoiceRecording().catch(() => undefined); }}
      />
      <ConversationControlCenter
        visible={controlCenterOpen}
        conversationId={conversationId}
        title={route.params.title || "Conversation"}
        messages={messages}
        connected={!error}
        onClose={() => setControlCenterOpen(false)}
        onStartCall={(callType) => {
          setControlCenterOpen(false);
          navigation.navigate("Call", {
            conversationId,
            callType,
            direction: "outgoing",
            title: route.params.title || (callType === "video" ? "PulseSoc Video" : "PulseSoc Voice")
          });
        }}
        onOpenSafety={(section) => {
          setControlCenterOpen(false);
          navigation.navigate("SafetyHub", { section, title: section === "reports" ? "Report Conversation" : "Blocked Users" });
        }}
      />
      </LogiNexusScreenShell>
    </View>
  );
}

function AttachmentActionSheet({ visible, recording, onClose, onImage, onVideo, onCamera, onFile, onVoice }: { visible: boolean; recording: boolean; onClose: () => void; onImage: () => void; onVideo: () => void; onCamera: () => void; onFile: () => void; onVoice: () => void }) {
  return (
    <Modal transparent animationType="slide" visible={visible} onRequestClose={onClose}>
      <Pressable accessibilityRole="button" accessibilityLabel="Close attachment sheet" style={styles.sheetBackdrop} onPress={onClose}>
        <PulseCommandPanel style={styles.attachmentSheet}>
          <View style={styles.sheetHandle} />
          <Text style={styles.sheetTitle}>Add attachment</Text>
          <Text style={styles.sheetPreview}>Share through PulseSoc’s secure media channel.</Text>
          <View style={styles.sheetGrid}>
            <MediaSheetAction icon="images-outline" label="Photo" detail="Optimized image" onPress={onImage} />
            <MediaSheetAction icon="videocam-outline" label="Video" detail="Up to 2 minutes" tone="intelligence" onPress={onVideo} />
            <MediaSheetAction icon="camera-outline" label="Camera" detail="Capture now" onPress={onCamera} />
            <MediaSheetAction icon="document-text-outline" label="Document" detail="Secure file" tone="intelligence" onPress={onFile} />
            <MediaSheetAction icon={recording ? "stop" : "mic-outline"} label={recording ? "Stop & send" : "Voice note"} detail={recording ? "Recording now" : "Fast audio message"} tone={recording ? "danger" : "signal"} onPress={onVoice} />
          </View>
        </PulseCommandPanel>
      </Pressable>
    </Modal>
  );
}

function MediaSheetAction({ icon, label, detail, onPress, tone = "signal" }: { icon: keyof typeof Ionicons.glyphMap; label: string; detail: string; onPress: () => void; tone?: "signal" | "intelligence" | "danger" }) {
  const color = tone === "danger" ? colors.danger : tone === "intelligence" ? "#a77cff" : colors.accent;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label}. ${detail}`} style={({ pressed }) => [styles.mediaSheetAction, { borderColor: `${color}66` }, pressed && styles.pressed]} onPress={onPress}>
      <View style={[styles.mediaSheetIcon, { backgroundColor: `${color}16`, borderColor: `${color}72` }]}><Ionicons name={icon} size={23} color={color} /></View>
      <View style={styles.mediaSheetCopy}><Text numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.82} style={styles.mediaSheetLabel}>{label}</Text><Text style={styles.mediaSheetDetail}>{detail}</Text></View>
      <Ionicons name="chevron-forward" size={16} color={colors.muted} />
    </Pressable>
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
    return <VoiceMessageCard url={mediaUrl} durationSeconds={Number(message.duration_seconds || 0)} title={type === "voice" ? "Voice message" : "Audio message"} />;
  }
  return (
    <Pressable style={styles.attachment} onPress={() => (type === "video" ? setViewerOpen(true) : undefined)}>
      <Text style={styles.attachmentTitle}>{type === "video" ? "Video attachment" : "File attachment"}</Text>
      <Text style={styles.attachmentMeta}>{type === "video" ? "Open viewer" : formatFileSize(message.file_size)}</Text>
      <NativeMediaViewer visible={viewerOpen} items={[viewerItem]} title="Messenger media" onClose={() => setViewerOpen(false)} />
    </Pressable>
  );
}

function VoiceMessageCard({ url, durationSeconds, title }: { url: string; durationSeconds: number; title: string }) {
  const soundRef = useRef<Audio.Sound | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(Math.max(1, durationSeconds));
  const [rate, setRate] = useState(1);
  useEffect(() => () => { soundRef.current?.unloadAsync().catch(() => undefined); }, []);
  async function togglePlayback() {
    if (!soundRef.current) {
      const created = await Audio.Sound.createAsync({ uri: url }, { shouldPlay: true, rate, shouldCorrectPitch: true }, (status) => {
        if (!status.isLoaded) return;
        setPlaying(status.isPlaying);
        setPosition(Math.floor(status.positionMillis / 1000));
        setDuration(Math.max(1, Math.floor((status.durationMillis || duration * 1000) / 1000)));
        if (status.didJustFinish) setPosition(0);
      });
      soundRef.current = created.sound;
      return;
    }
    const status = await soundRef.current.getStatusAsync();
    if (status.isLoaded && status.isPlaying) await soundRef.current.pauseAsync();
    else await soundRef.current.playAsync();
  }
  async function cycleRate() {
    const next = rate === 1 ? 1.5 : rate === 1.5 ? 2 : 1;
    setRate(next);
    await soundRef.current?.setRateAsync(next, true).catch(() => undefined);
  }
  const progress = Math.min(1, position / Math.max(1, duration));
  return (
    <View style={styles.voiceCard}>
      <Text style={styles.attachmentTitle}>{title}</Text>
      <View style={styles.voiceControls}>
        <Pressable accessibilityRole="button" accessibilityLabel={playing ? "Pause voice message" : "Play voice message"} style={styles.voicePlay} onPress={() => togglePlayback().catch(() => undefined)}><Text style={styles.voicePlayText}>{playing ? "Ⅱ" : "▶"}</Text></Pressable>
        <View style={styles.voiceTimeline}>
          <View style={styles.waveform}>{Array.from({ length: 22 }).map((_, index) => <View key={index} style={[styles.waveBar, { height: 7 + ((index * 7) % 18), opacity: index / 22 <= progress ? 1 : 0.38 }]} />)}</View>
          <View style={styles.voiceTimeRow}><Text style={styles.attachmentMeta}>{formatDuration(position)}</Text><Text style={styles.attachmentMeta}>{formatDuration(duration)}</Text></View>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel={`Playback speed ${rate} times`} style={styles.voiceRate} onPress={() => cycleRate().catch(() => undefined)}><Text style={styles.voiceRateText}>{rate}x</Text></Pressable>
      </View>
    </View>
  );
}

function formatDuration(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function isPresenceActive(value?: string) {
  return ["online", "active", "available", "typing"].includes(String(value || "").toLowerCase());
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
    backgroundColor: "rgba(7,15,32,0.96)",
    borderBottomColor: "rgba(97,216,255,0.26)",
    borderBottomWidth: 1,
    padding: logiNexus.spacing.sm,
    paddingBottom: logiNexus.spacing.sm
  },
  threadHeader: { alignItems: "center", flexDirection: "row", gap: 8, minHeight: 62 },
  backButton: { alignItems: "center", backgroundColor: "rgba(255,255,255,0.035)", borderColor: colors.border, borderRadius: 13, borderWidth: 1, height: 46, justifyContent: "center", width: 42 },
  backButtonText: { color: colors.text, fontSize: 30, fontWeight: "400", marginTop: -3 },
  threadIdentity: { flex: 1, gap: 3, minWidth: 0 },
  threadTitle: { color: colors.text, fontSize: 18, fontWeight: "900" },
  threadStatusRow: { alignItems: "center", flexDirection: "row", gap: 5 },
  threadStatusSignal: { alignItems: "center", height: 10, justifyContent: "center", width: 10 },
  threadStatusHalo: { backgroundColor: colors.accent, borderRadius: 6, height: 10, position: "absolute", width: 10 },
  threadStatusDot: { backgroundColor: colors.accent, borderRadius: 5, height: 8, width: 8 },
  threadStatusWarning: { backgroundColor: colors.warning },
  threadSubtitle: { color: colors.accent, flex: 1, fontSize: 11 },
  callActions: {
    flexDirection: "row",
    gap: 5
  },
  signalButton: {
    alignItems: "center",
    backgroundColor: "rgba(4,16,28,0.9)",
    borderRadius: 15,
    borderWidth: 1,
    justifyContent: "center",
    overflow: "hidden"
  },
  signalButtonHalo: {
    borderRadius: 999,
    height: "86%",
    position: "absolute",
    width: "86%"
  },
  error: {
    color: colors.warning,
    paddingHorizontal: 16,
    paddingTop: 10
  },
  errorBanner: {
    backgroundColor: "rgba(255, 204, 102, 0.08)",
    borderColor: "rgba(255, 204, 102, 0.34)",
    borderRadius: logiNexus.radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    marginHorizontal: logiNexus.spacing.md,
    marginTop: logiNexus.spacing.xs,
    paddingBottom: logiNexus.spacing.sm
  },
  retryStateButton: {
    alignSelf: "center",
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.capsule,
    marginTop: logiNexus.spacing.md,
    minHeight: 44,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  retryStateText: {
    color: "#03120f",
    fontSize: 13,
    fontWeight: "900",
    textAlign: "center"
  },
  loadingPanel: {
    margin: logiNexus.spacing.lg
  },
  list: {
    gap: 8,
    padding: 12,
    paddingTop: 8,
    zIndex: 1
  },
  ambientField: { bottom: 80, left: 0, overflow: "hidden", position: "absolute", right: 0, top: 76 },
  ambientOrbLarge: { backgroundColor: "rgba(75,108,200,0.12)", borderRadius: 180, height: 360, position: "absolute", right: -140, top: 70, width: 360 },
  ambientOrbSmall: { backgroundColor: "rgba(89,213,224,0.1)", borderRadius: 110, height: 220, position: "absolute", right: -18, top: 140, width: 220 },
  ambientSignalLine: { backgroundColor: "rgba(97,216,255,0.07)", height: 1, left: -40, position: "absolute", right: -40, top: "48%", transform: [{ rotate: "-11deg" }] },
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
    backgroundColor: "rgba(37,83,158,0.82)",
    borderColor: "rgba(93,174,255,0.58)",
    borderBottomRightRadius: 6,
    borderWidth: 1
  },
  theirBubble: {
    backgroundColor: "rgba(12,24,43,0.88)",
    borderBottomLeftRadius: 6,
    borderColor: "rgba(105,218,240,0.28)",
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
  voiceCard: { backgroundColor: "rgba(3,18,32,0.76)", borderColor: "rgba(97,216,255,0.2)", borderRadius: 14, borderWidth: 1, gap: 8, minWidth: 250, padding: 10 },
  voiceControls: { alignItems: "center", flexDirection: "row", gap: 9 },
  voicePlay: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 24, height: 46, justifyContent: "center", width: 46 },
  voicePlayText: { color: "#04130f", fontSize: 17, fontWeight: "900" },
  voiceTimeline: { flex: 1, gap: 4, minWidth: 120 },
  waveform: { alignItems: "center", flexDirection: "row", gap: 2, height: 30 },
  waveBar: { backgroundColor: colors.accentStrong, borderRadius: 2, flex: 1, minWidth: 2 },
  voiceTimeRow: { flexDirection: "row", justifyContent: "space-between" },
  voiceRate: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: 1, minHeight: 34, minWidth: 40, justifyContent: "center" },
  voiceRateText: { color: colors.text, fontSize: 12, fontWeight: "900" },
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
    backgroundColor: "rgba(2,10,20,0.98)",
    borderColor: "rgba(65,236,198,0.48)",
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    borderWidth: 1,
    gap: 10,
    marginHorizontal: 0,
    marginTop: 0,
    overflow: "hidden",
    padding: 12,
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: -8 },
    shadowOpacity: 0.22,
    shadowRadius: 24
  },
  composerSignalLine: {
    backgroundColor: "rgba(65,236,198,0.78)",
    borderRadius: 2,
    height: 2,
    left: 18,
    position: "absolute",
    right: 18,
    top: 0
  },
  composerKeyboard: {
    left: 0,
    marginHorizontal: 10,
    position: "absolute",
    right: 0,
    zIndex: 20
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
    gap: 9,
    minHeight: 58
  },
  input: {
    backgroundColor: "rgba(2,9,19,0.92)",
    borderColor: "rgba(97,216,255,0.5)",
    borderRadius: 28,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    maxHeight: 118,
    minHeight: 54,
    paddingHorizontal: 17,
    paddingVertical: 13
  },
  sendButton: {
    alignItems: "center",
    backgroundColor: "rgba(65,236,198,0.96)",
    borderRadius: 999,
    minHeight: 56,
    minWidth: 56,
    justifyContent: "center",
    paddingHorizontal: 14,
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.34,
    shadowRadius: 14
  },
  pressed: {
    opacity: 0.82
  },
  sendDisabled: {
    backgroundColor: "rgba(146,161,181,0.2)",
    borderColor: "rgba(146,161,181,0.18)",
    borderWidth: 1,
    opacity: 0.82
  },
  sendText: {
    color: "#08110f",
    fontSize: 20,
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
  attachmentSheet: {
    gap: logiNexus.spacing.md,
    padding: logiNexus.spacing.lg,
    paddingBottom: logiNexus.spacing.xxl
  },
  sheetHandle: {
    alignSelf: "center",
    backgroundColor: colors.muted,
    borderRadius: 2,
    height: 4,
    opacity: 0.7,
    width: 44
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
  },
  mediaSheetAction: {
    alignItems: "center",
    backgroundColor: "rgba(5,16,31,0.9)",
    borderRadius: 16,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 68,
    minWidth: "47%",
    padding: 10
  },
  mediaSheetIcon: {
    alignItems: "center",
    borderRadius: 13,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    width: 42
  },
  mediaSheetCopy: {
    flex: 1,
    gap: 2,
    minWidth: 0
  },
  mediaSheetLabel: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 13,
    fontWeight: "900",
    lineHeight: 17
  },
  mediaSheetDetail: {
    color: colors.muted,
    fontSize: 10,
    lineHeight: 14
  }
});
