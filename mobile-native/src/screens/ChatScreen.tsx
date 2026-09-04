import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Audio } from "expo-av";
import { File } from "expo-file-system";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  Alert,
  Animated,
  Appearance,
  AppState,
  AppStateStatus,
  Easing,
  FlatList,
  Image,
  Keyboard,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GalacticAtmosphere } from "../components/GalacticAtmosphere";
import {
  cacheMessages,
  cancelPulseAiAction,
  confirmPulseAiAction,
  createLocalMessage,
  deleteMessage,
  drainMessengerQueue,
  enqueueMessengerMessage,
  getConversation,
  getPulseAiConversation,
  isRetryableMessengerSendError,
  loadCachedMessages,
  markConversationSeen,
  MessengerMessage,
  MessengerPresence,
  PULSE_AI_CONVERSATION_ID,
  PULSE_AI_DISPLAY_NAME,
  reactToMessage,
  reportMessage,
  sendConversationMessage,
  sendPulseAiMessage,
  sendTyping,
  updateCachedConversationPreview,
  UndxResponseComponent,
  syncConversation,
  uploadMessengerMedia
} from "../api/messenger";
import { mergeConversationMessages } from "../api/messengerOrdering";
import { APP_VERSION, PULSE_API_BASE_URL } from "../api/config";
import { PULSESOC_QA_MESSENGER_FIXTURES } from "../api/config";
import { recoverRoomConversation } from "../community/roomConversationRecovery";
import { buildUndxUiContext, UndxUiContext } from "../undx/undxContext";
import { clearMarketContext, peekMarketContext, takeMarketContextForSend } from "../undx/marketContext";
import { choiceRowsOf, describeTransition, readTapOutcome, toActionCard, UndxTapOutcome } from "../undx/actionCards";
import { NativeMediaViewer, NativeMediaViewerItem } from "../components/NativeMediaViewer";
import { ConversationControlCenter } from "../components/ConversationControlCenter";
import { ContentTranslation } from "../components/ContentTranslation";
import { PulseCommandAvatar, PulseCommandPanel } from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import {
  cycleVoicePlaybackRate,
  retryVoicePlayback,
  seekVoicePlayback,
  seekVoicePlaybackBy,
  stopVoiceMessagePlayback,
  subscribeVoicePlayback,
  toggleVoicePlayback,
  VoicePlaybackSnapshot
} from "../core/voiceMessagePlayback";
import { RootStackParamList } from "../navigation/types";
import { openNativeRoute } from "../navigation/nativeRouteActions";
import { presenceActivityText } from "../api/presence";
import { reportPresenceActivity } from "../api/presenceSession";
import { useAuth } from "../session/auth";
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
import { EmojiPicker, QUICK_REACTIONS } from "../emoji";
import { logiNexus } from "../theme/logiNexus";
import { formatFileSize, formatShortTime } from "../utils/format";

const PAGE_SIZE = 40;
const SYNC_INTERVAL_MS = 2500;
const PERSONAL_INTELLIGENCE_CAPABILITIES = new Set([
  "activity.daily_summary",
  "notifications.inbox.list",
  "notifications.explain",
  "notifications.group_summary",
  "search.global",
  "search.people",
  "search.content",
  "search.messages",
  "search.activity",
  "settings.inspect",
  "settings.explain",
  "settings.recommend",
  "security.sessions.list",
  "security.activity.summary",
  "security.device.list",
  "marketplace.search",
  "marketplace.listing.summary",
  "marketplace.order.status",
  "premium.status",
  "premium.entitlements",
  "ads.performance.summary",
  "live.search",
  "live.summary",
  "live.performance",
  "learning.search",
  "learning.progress",
  "memory.activity.inspect",
  "groups.list",
  "groups.search",
  "events.upcoming",
  "music.search",
  "account.health.summary",
  "verification.status",
  "support.tickets.list",
  "creator.analytics.summary",
  "localization.preferences",
  "presence.privacy.status"
]);

function isLocalMessengerFixtureConversation(conversationId: number) {
  return PULSESOC_QA_MESSENGER_FIXTURES && conversationId >= 9001 && conversationId <= 9006;
}

function qaFixtureTyping(conversationId: number) {
  return conversationId === 9003 ? "Maria is typing" : "";
}

function undxUndoCommand(component: UndxResponseComponent): string {
  const args = component.undo_arguments || {};
  const alertId = Number(args.alert_id || 0);
  if (component.undo_capability_id === "crypto.alerts.resume" && alertId > 0) {
    return `Resume alert ID ${alertId}`;
  }
  if (component.undo_capability_id === "crypto.alerts.pause" && alertId > 0) {
    return `Pause alert ID ${alertId}`;
  }
  if (component.undo_capability_id === "saved.post.set") {
    const postId = Number(args.post_id || 0);
    if (postId > 0 && typeof args.saved === "boolean") {
      return `${args.saved ? "Save" : "Unsave"} post ${postId}`;
    }
  }
  if (component.undo_capability_id === "social.follow" || component.undo_capability_id === "social.unfollow") {
    const targetUserId = Number(args.target_user_id || 0);
    if (targetUserId > 0) {
      return `${component.undo_capability_id === "social.follow" ? "Follow" : "Unfollow"} user ${targetUserId}`;
    }
  }
  return "";
}

function undxUndoLabel(component: UndxResponseComponent): string {
  if (component.undo_capability_id === "saved.post.set") {
    return component.undo_arguments?.saved === true ? "Undo · Save again" : "Undo · Remove from Saved";
  }
  if (component.undo_capability_id === "social.follow") {
    return "Undo · Follow again";
  }
  if (component.undo_capability_id === "social.unfollow") {
    return "Undo · Unfollow";
  }
  return "Undo · Resume";
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

function readOriginRoute(navigation: { getState?: () => unknown }): string | null {
  try {
    const state = navigation.getState?.() as { index?: number; routes?: Array<{ name?: string }> } | undefined;
    if (!state || !Array.isArray(state.routes)) return null;
    const index = typeof state.index === "number" ? state.index : state.routes.length - 1;
    const prior = state.routes[index - 1];
    return prior && typeof prior.name === "string" ? prior.name : null;
  } catch {
    return null;
  }
}

async function collectUndxUiContext(
  navigation: { getState?: () => unknown },
  conversationId: number,
  selectedTaskId?: string
): Promise<UndxUiContext> {
  const [screenReaderEnabled, reduceMotionEnabled] = await Promise.all([
    AccessibilityInfo.isScreenReaderEnabled().catch(() => null),
    AccessibilityInfo.isReduceMotionEnabled().catch(() => null)
  ]);
  let timezone: string | null = null;
  try {
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    timezone = null;
  }
  return buildUndxUiContext({
    surface: "undx_chat",
    originRoute: readOriginRoute(navigation),
    platform: Platform.OS,
    appVersion: APP_VERSION || null,
    screenReaderEnabled,
    reduceMotionEnabled,
    colorScheme: Appearance.getColorScheme(),
    timezone,
    selectedConversationId: conversationId,
    selectedTaskId
  });
}

export function ChatScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "Chat">) {
  const conversationId = route.params.conversationId;
  const assistantConversation = conversationId === PULSE_AI_CONVERSATION_ID;
  const insets = useSafeAreaInsets();
  const { authState } = useAuth();
  const selfUserId = Number(authState.user?.user_id || 0);
  const [messages, setMessages] = useState<MessengerMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const [reactionPickerFor, setReactionPickerFor] = useState<MessengerMessage | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState("");
  const [initialFetchComplete, setInitialFetchComplete] = useState(false);
  const [usingCachedMessages, setUsingCachedMessages] = useState(false);
  const [typing, setTyping] = useState("");
  // Live peer presence, refreshed from every conversation fetch and sync.
  // route.params.presence is only a snapshot taken at navigation time; relying
  // on it would leave the header showing "Online" for as long as the thread
  // stayed open, which is exactly the staleness this system exists to remove.
  const [peerPresence, setPeerPresence] = useState<PeerPresence | null>(null);
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [recordingStartedAt, setRecordingStartedAt] = useState<number>(0);
  const [recordingElapsed, setRecordingElapsed] = useState(0);
  const [recordingLevels, setRecordingLevels] = useState<number[]>(() => Array.from({ length: 24 }, () => 0.14));
  const [uploading, setUploading] = useState(false);
  const [replyTo, setReplyTo] = useState<MessengerMessage | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<MessengerMessage | null>(null);
  const [attachmentSheetOpen, setAttachmentSheetOpen] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  // The market-context chip: an asset screen just handed off, and the member
  // should see — and be able to end — what "it" currently means. Dismissing
  // clears the parked envelope too, so an unsent context never rides along
  // after the member said no to it.
  const [marketChip, setMarketChip] = useState(assistantConversation ? peekMarketContext() : null);
  const [controlCenterOpen, setControlCenterOpen] = useState(false);
  const [undxComponents, setUndxComponents] = useState<UndxResponseComponent[]>([]);
  const [undxActionBusy, setUndxActionBusy] = useState(false);
  // Approvals this screen has already submitted. The server consumes a token exactly
  // once, so a second press could only ever produce an error — but the mutation is
  // real, and a user who double-taps deserves the receipt rather than a failure. The
  // press is dropped here instead of being sent and rejected.
  const undxSpentTokens = useRef<Set<string>>(new Set());
  // What the last press came back with, and which card it belongs to.
  //
  // Keyed by token rather than held as a bare string, so the sentence is drawn against
  // the card that was actually pressed. A rail can hold more than one card, and an
  // outcome with no owner would attach itself to whichever one rendered first.
  const [undxTapOutcome, setUndxTapOutcome] = useState<(UndxTapOutcome & { token: string }) | null>(null);
  const [threadTitle, setThreadTitle] = useState(assistantConversation ? PULSE_AI_DISPLAY_NAME : route.params.title || "Messenger");
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTypingAt = useRef(0);
  const appState = useRef<AppStateStatus>(AppState.currentState);
  const qaChatState = PULSESOC_QA_MESSENGER_FIXTURES ? String(process.env.EXPO_PUBLIC_PULSESOC_QA_CHAT_STATE || "") : "";
  const draftKey = `pulsesoc.native.messenger.draft.${conversationId}`;

  const confirmUndxAction = useCallback((token: string) => {
    if (!token || undxSpentTokens.current.has(token)) {
      return;
    }
    undxSpentTokens.current.add(token);
    setUndxTapOutcome(null);
    setUndxActionBusy(true);
    confirmPulseAiAction(token)
      .then((result) => {
        // The response replaces the confirmation card with whatever the server now
        // says is true — a verified receipt, or a typed failure. The client never
        // synthesises a success from the fact that the request returned.
        setUndxComponents(result.response_components || []);
        setStatusMessage(result.message || "UNDX action finished.");
      })
      .catch((actionError) => {
        // The sentence goes on the card as well as in the banner. The banner is the
        // one that used to carry it alone, and it is not drawn while the keyboard is
        // up — which is the state a person is in the moment they tap Confirm on a card
        // they produced by typing. On its own it answered "did my tap do anything?"
        // with a blank screen and two dimmed buttons.
        const outcome = readTapOutcome(actionError);
        // Re-armed only when the request never reached a server that answered. A token
        // is redeemable exactly once, so a second press can produce the write or the
        // sentence saying it already ran — never a second write.
        if (outcome.retryable) undxSpentTokens.current.delete(token);
        setUndxTapOutcome({ ...outcome, token });
        setStatusMessage(outcome.message);
      })
      .finally(() => setUndxActionBusy(false));
  }, []);

  const openUndxResult = useCallback((deepLink?: string) => {
    const nativePath = nativePathFromDeepLink(deepLink);
    if (!nativePath) {
      setStatusMessage("This result cannot be opened in the app yet.");
      return;
    }
    try {
      const alertMatch = /^\/pulse\/alerts\/(\d+)\/?$/.exec(nativePath);
      if (alertMatch) {
        navigation.navigate("CryptoAlertManagement", {
          alertId: Number(alertMatch[1]),
          title: "Crypto alert",
        });
        return;
      }
      openNativeRoute(navigation, nativePath);
    } catch {
      setStatusMessage("This result could not be opened. Try again from the PulseSoc website.");
    }
  }, [navigation]);

  useEffect(() => () => {
    stopVoiceMessagePlayback("conversation_closed").catch(() => undefined);
  }, []);

  useEffect(() => {
    const show = Keyboard.addListener("keyboardWillShow", () => setKeyboardVisible(true));
    const hide = Keyboard.addListener("keyboardWillHide", () => setKeyboardVisible(false));
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
    if (qaChatState === "voice-recording") {
      setRecordingElapsed(12);
      setRecordingLevels(Array.from({ length: 24 }, (_, index) => 0.16 + ((index * 17) % 68) / 100));
    }
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
  const showVoiceCapture = Boolean(recording) || qaChatState === "voice-recording";
  const headerStatus = error
    ? hasMessages
      ? "Reconnecting"
      : "Messages unavailable"
    : usingCachedMessages
      ? "Cached history"
      : "Live channel";
  // Presence beats connection state, and live presence beats the navigation
  // snapshot. When the server has told us nothing about the peer we show
  // connection status instead of guessing.
  const presenceSubtitle = peerPresenceSubtitle(peerPresence);
  const headerSubtitle = assistantConversation
    ? typing || (error ? "Service reconnecting" : usingCachedMessages ? "Cached history" : "Always available · PulseSoc Intelligence")
    : typing || presenceSubtitle || headerStatus;
  const peerIsOnline = Boolean(peerPresence?.online);

  const mergeMessages = useCallback(
    (current: MessengerMessage[], incoming: MessengerMessage[]) => mergeConversationMessages(current, incoming),
    []
  );

  const replaceLocalMessage = useCallback((localId: number, next: MessengerMessage) => {
    setMessages((current) => mergeMessages(current.filter((message) => message.id !== localId), [next]));
  }, [mergeMessages]);

  const load = useCallback(async ({ refresh = false } = {}) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = assistantConversation
        ? await getPulseAiConversation({ limit: PAGE_SIZE })
        : await getConversation(conversationId, { limit: PAGE_SIZE });
      const nextMessages = data.messages || [];
      if (data.conversation) {
        const title = String(data.conversation.title || data.conversation.name || "").trim();
        if (title && title !== "[object Object]") setThreadTitle(title);
      }
      setMessages(nextMessages);
      setUsingCachedMessages(false);
      setStatusMessage("");
      await cacheMessages(conversationId, nextMessages);
      if (!assistantConversation) await markConversationSeen(conversationId).catch(() => undefined);
      setTyping(typingSummary(data.presence));
      if (!assistantConversation) setPeerPresence(peerPresenceFrom(data.presence, selfUserId));
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
  }, [assistantConversation, conversationId, selfUserId]);

  const retryLoad = useCallback(async () => {
    const roomId = route.params.roomId;
    if (!roomId) {
      await load({ refresh: true });
      return;
    }
    setRefreshing(true);
    setError("");
    try {
      const repaired = await recoverRoomConversation(roomId, conversationId);
      if (repaired.changed) {
        navigation.replace("Chat", { ...route.params, conversationId: repaired.conversationId, roomId });
        return;
      }
      await load({ refresh: true });
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : "Messages could not load.");
    } finally {
      setRefreshing(false);
    }
  }, [conversationId, load, navigation, route.params]);

  const loadOlder = useCallback(async () => {
    if (assistantConversation) return;
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
  }, [assistantConversation, conversationId, loadingOlder, mergeMessages, oldestMessageId]);

  const sync = useCallback(async () => {
    if (appState.current !== "active") return;
    if (assistantConversation) {
      if (!messages.length) return;
      try {
        const data = await getPulseAiConversation({ limit: 80 });
        setMessages((current) => {
          const merged = mergeMessages(current, data.messages || []);
          cacheMessages(conversationId, merged).catch(() => undefined);
          return merged;
        });
        setUsingCachedMessages(false);
        setError("");
        setStatusMessage("");
      } catch {
        if (messages.length) setStatusMessage("UNDX reconnecting. Conversation history remains visible.");
      }
      return;
    }
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
      setPeerPresence(peerPresenceFrom(data.presence, selfUserId));
      setUsingCachedMessages(false);
      setError("");
    } catch {
      setTyping("");
      // A failed sync means we no longer know whether the peer is online, so we
      // drop the claim rather than keep displaying a stale one.
      setPeerPresence(null);
      if (messages.length) setStatusMessage("Realtime reconnecting. Message history remains visible.");
    }
  }, [assistantConversation, conversationId, mergeMessages, messages.length, newestMessageId, selfUserId]);

  const notifyTyping = useCallback((value: string) => {
    setDraft(value);
    if (assistantConversation) return;
    const now = Date.now();
    if (now - lastTypingAt.current > 1800) {
      lastTypingAt.current = now;
      sendTyping(conversationId, true).catch(() => undefined);
      // Mirror typing onto the unified presence session so subsystems outside
      // Messenger see the same activity. The server ages this out on its own
      // TTL, so a crash mid-keystroke cannot leave the indicator stuck on.
      reportPresenceActivity("typing", String(conversationId)).catch(() => undefined);
    }
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => {
      sendTyping(conversationId, false).catch(() => undefined);
      reportPresenceActivity("idle", "").catch(() => undefined);
    }, 1200);
  }, [assistantConversation, conversationId]);

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
    if (assistantConversation) {
      if ((payload.message_type || "text") !== "text" || payload.media_url || payload.attachment_ids?.length || payload.media_ids?.length) {
        setStatusMessage("UNDX can chat by text right now.");
        return "failed" as const;
      }
      const body = (payload.body || "").trim();
      if (!body) return "failed" as const;
      const local = createLocalMessage(conversationId, body, "text");
      setMessages((current) => mergeMessages(current, [local]));
      setTyping("UNDX is typing");
      setStatusMessage("UNDX is thinking...");
      try {
        // Market Pulse → UNDX bridge: the envelope parked by an asset screen
        // rides along on the first send only. The server persists it per
        // conversation, so later turns inherit it without a resend — and a
        // resend would falsely re-stamp a minutes-old snapshot as fresh.
        const marketContext = takeMarketContextForSend();
        const data = await sendPulseAiMessage({
          body,
          client_message_id: local.client_message_id,
          ui_context: {
            ...(await collectUndxUiContext(navigation, conversationId, route.params.undxTaskId)),
            ...(marketContext ? { market_context: marketContext } : {})
          }
        });
        const nextMessages = data.messages || [];
        setMessages(nextMessages);
        await cacheMessages(conversationId, nextMessages);
        await updateCachedConversationPreview(conversationId, body, new Date().toISOString()).catch(() => undefined);
        setTyping("");
        setStatusMessage("");
        setUndxComponents(data.response_components || []);
        return "sent" as const;
      } catch (sendError) {
        setTyping("");
        const cached = await loadCachedMessages(conversationId);
        const failedMessages = mergeMessages(cached.length ? cached : messages, [{
          ...local,
          delivery_status: "failed",
          local_status: "failed",
          local_error: sendError instanceof Error ? sendError.message : "UNDX could not respond."
        }]);
        setMessages(failedMessages);
        await cacheMessages(conversationId, failedMessages);
        setStatusMessage(sendError instanceof Error ? sendError.message : "UNDX is temporarily unavailable.");
        throw sendError;
      }
    }
    const payloadType = normalizedMessageType(payload.message_type || "text");
    const label = payload.body?.trim() || mediaPreviewLabel(payloadType, Boolean(payload.media_url));
    const local = createLocalMessage(conversationId, payload.body || "", payload.message_type || "text");
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
  }, [assistantConversation, conversationId, mergeMessages, messages, navigation, replaceLocalMessage, route.params.undxTaskId, sync]);

  const submitText = useCallback(async () => {
    const body = draft.trim();
    if (!body) return;
    setDraft("");
    const currentReply = replyTo;
    setReplyTo(null);
    // Clearing the typing indicator is a fire-and-forget network signal; awaiting it
    // here would delay the optimistic bubble by a full round-trip. sendPayload inserts
    // the local message synchronously, so let it run first and never block on typing.
    if (!assistantConversation) void sendTyping(conversationId, false).catch(() => undefined);
    await sendPayload({
      body,
      message_type: "text",
      reply_to_message_id: currentReply?.message_id,
      reply_preview: currentReply ? messagePreview(currentReply) : undefined
    });
  }, [assistantConversation, conversationId, draft, replyTo, sendPayload]);

  const retryMessage = useCallback(async (message: MessengerMessage) => {
    setMessages((current) => current.filter((item) => item.id !== message.id));
    await sendPayload({
      body: isVoiceLikeMessage(message) ? "" : message.body || "",
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
    setMessages((current) => current.map((item) => item.id === message.id ? { ...item, reactions: optimisticReaction(previous, reactionType, message.viewer_reaction), viewer_reaction: reactionType } : item));
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
      const result = await reportMessage(message.id, "Reported from the PulseSoc app");
      setStatusMessage(result.message || "Message report sent to Trust & Safety.");
    } catch (reportError) {
      setStatusMessage(reportError instanceof Error ? reportError.message : "Report failed.");
    }
  }, []);

  const uploadAndSend = useCallback(async (input: { uri: string; name: string; mimeType: string; sizeBytes?: number; voice?: boolean; durationSeconds?: number }) => {
    if (assistantConversation) {
      setStatusMessage("UNDX can chat in text for now. Attachments work in chats with people, but not yet with UNDX.");
      return;
    }
    if (uploading) return;
    setUploading(true);
    setStatusMessage(input.voice ? "Sending voice message…" : "Uploading attachment…");
    reportPresenceActivity(input.voice ? "sending_files" : "uploading_media", String(conversationId)).catch(() => undefined);
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
        body: input.voice ? "" : input.name,
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
      Alert.alert(input.voice ? "Voice message failed" : "Attachment failed", message);
    } finally {
      setUploading(false);
      reportPresenceActivity("idle", "").catch(() => undefined);
    }
  }, [assistantConversation, conversationId, sendPayload, uploading]);

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
        reportPresenceActivity("idle", "").catch(() => undefined);
        const stopped = await activeRecording.stopAndUnloadAsync();
        await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
        const uri = activeRecording.getURI();
        const durationSeconds = Math.max(1, Math.round(Number(stopped.durationMillis || Date.now() - recordingStartedAt) / 1000));
        setRecordingElapsed(0);
        setRecordingLevels(Array.from({ length: 24 }, () => 0.14));
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
      setRecordingElapsed(0);
      setRecordingLevels(Array.from({ length: 24 }, () => 0.14));
      const started = await Audio.Recording.createAsync(
        {
          // Voice-tuned recording: mono AAC at 24 kHz / 32 kbps instead of the
          // stereo HIGH_QUALITY preset. Speech stays fully intelligible while
          // the file is roughly 4-8x smaller, and upload time is the dominant
          // share of voice-send latency on cellular.
          isMeteringEnabled: true,
          android: {
            extension: ".m4a",
            outputFormat: Audio.AndroidOutputFormat.MPEG_4,
            audioEncoder: Audio.AndroidAudioEncoder.AAC,
            sampleRate: 24000,
            numberOfChannels: 1,
            bitRate: 32000
          },
          ios: {
            extension: ".m4a",
            outputFormat: Audio.IOSOutputFormat.MPEG4AAC,
            audioQuality: Audio.IOSAudioQuality.MEDIUM,
            sampleRate: 24000,
            numberOfChannels: 1,
            bitRate: 32000,
            linearPCMBitDepth: 16,
            linearPCMIsBigEndian: false,
            linearPCMIsFloat: false
          },
          web: {
            mimeType: "audio/webm",
            bitsPerSecond: 32000
          }
        },
        (status) => {
          if (!status.isRecording) return;
          setRecordingElapsed(Math.max(0, Math.floor(status.durationMillis / 1000)));
          const level = Math.max(0.08, Math.min(1, (Number(status.metering ?? -54) + 60) / 60));
          setRecordingLevels((current) => [...current.slice(-23), level]);
        },
        160
      );
      setRecording(started.recording);
      setRecordingStartedAt(Date.now());
      setStatusMessage("Recording voice message… tap the microphone again to send.");
      reportPresenceActivity("recording_voice", String(conversationId)).catch(() => undefined);
    } catch (recordingError) {
      setRecording(null);
      reportPresenceActivity("idle", "").catch(() => undefined);
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
      const message = recordingError instanceof Error ? recordingError.message : "Voice recording could not start.";
      setStatusMessage(message);
      Alert.alert("Voice message unavailable", message);
    }
  }, [recording, recordingStartedAt, uploadAndSend]);

  const cancelVoiceRecording = useCallback(async () => {
    const activeRecording = recording;
    if (!activeRecording) return;
    setRecording(null);
    reportPresenceActivity("idle", "").catch(() => undefined);
    try {
      await activeRecording.stopAndUnloadAsync();
      const uri = activeRecording.getURI();
      if (uri) {
        const file = new File(uri);
        if (file.exists) file.delete();
      }
    } catch {
      // The recorder may already be stopped by an interruption; local teardown still wins.
    } finally {
      setRecordingElapsed(0);
      setRecordingLevels(Array.from({ length: 24 }, () => 0.14));
      setStatusMessage("Voice recording discarded.");
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
    }
  }, [recording]);

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
      if (!assistantConversation) sendTyping(conversationId, false).catch(() => undefined);
    };
  }, [assistantConversation, conversationId, load]);

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
      {/* The atmosphere is the first paint layer. Keeping it after the header
          lets its opaque deep-space gradient cover the identity and call
          controls even though it cannot receive touches. */}
      <GalacticAtmosphere variant="messages" testID="messages-galactic-atmosphere" />
      <View style={[styles.header, { paddingTop: Math.max(insets.top, 10) }]}>
        <View style={styles.threadHeader}>
          <Pressable accessibilityRole="button" accessibilityLabel="Back to conversations" style={styles.backButton} onPress={() => navigation.goBack()}><Text style={styles.backButtonText}>‹</Text></Pressable>
          <PulseCommandAvatar label={assistantConversation ? PULSE_AI_DISPLAY_NAME : route.params.title || "Chat"} imageUrl={assistantConversation ? undefined : route.params.avatarUrl} active={assistantConversation || peerIsOnline} size={48} tone={assistantConversation ? "intelligence" : "default"} />
          <View style={styles.threadIdentity}>
            <Text style={styles.threadTitle} numberOfLines={1}>{threadTitle}</Text>
            <View style={styles.threadStatusRow}><LiveStatusDot warning={Boolean(error)} /><Text style={styles.threadSubtitle} numberOfLines={1}>{headerSubtitle}</Text></View>
          </View>
          <View style={styles.callActions}>
            {!assistantConversation ? <SignalIconButton accessibilityLabel="Start audio call" icon="call-outline" onPress={() => navigation.navigate("Call", { conversationId, callType: "audio", direction: "outgoing", title: threadTitle })} /> : null}
            {!assistantConversation ? <SignalIconButton accessibilityLabel="Start video call" icon="videocam-outline" tone="intelligence" onPress={() => navigation.navigate("Call", { conversationId, callType: "video", direction: "outgoing", title: threadTitle })} /> : null}
            <SignalIconButton accessibilityLabel="Open conversation controls" icon="ellipsis-vertical" onPress={() => setControlCenterOpen(true)} />
          </View>
        </View>
      </View>
      {error && hasMessages ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Retry loading messages" style={styles.errorBanner} onPress={() => retryLoad()}>
          <Text style={styles.error}>{error}</Text>
        </Pressable>
      ) : null}
      {showInitialLoading ? (
        <LogiNexusStatePanel state="loading" title="Opening chat" body="Loading conversation history from the server." loading style={styles.loadingPanel} />
      ) : showFatalError ? (
        <LogiNexusStatePanel state="error" title="Messages could not load" body={error || "PulseSoc could not load this conversation. Tap retry to reconnect to the canonical message history."} style={styles.loadingPanel}>
          <Pressable accessibilityRole="button" accessibilityLabel="Retry loading messages" style={styles.retryStateButton} onPress={() => retryLoad()}>
            <Text style={styles.retryStateText}>Retry</Text>
          </Pressable>
        </LogiNexusStatePanel>
      ) : (
        <>
        <FlatList
          data={visibleMessages}
          inverted
          // Same omission as the action rail below, on the same default. A message that
          // failed to send carries a Retry control, and a person retries it while still
          // looking at the composer they typed it into.
          keyboardShouldPersistTaps="handled"
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
          ListEmptyComponent={showEmptyConversation ? <LogiNexusStatePanel state="empty" title={assistantConversation ? "UNDX is ready" : "No messages yet"} body={assistantConversation ? "Message UNDX to start the conversation." : "Messages in this chat will appear here."} style={styles.emptyMessages} /> : null}
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
      {assistantConversation && undxComponents.length ? (
        <ScrollView
          accessibilityLabel="UNDX action cards"
          nestedScrollEnabled
          showsVerticalScrollIndicator
          // Without this the rail takes React Native's default of "never", under which
          // the first touch anywhere outside the focused input is consumed to dismiss
          // the keyboard and is never delivered to the child beneath it.
          //
          // A person reaches this rail by typing, so the keyboard is up when the card
          // arrives, and the first press of Confirm was therefore swallowed every time.
          // Observed on the iPhone 17 Pro Max simulator: two presses in a row closed the
          // keyboard and did nothing else — no request left the device, the card did not
          // change, and both controls stayed live. It reads exactly like a dead button.
          //
          // "handled" rather than "always": a tap that no control claims should still
          // put the keyboard away, which is what a tap on the empty part of the rail
          // means. Every other scrollable in this app already says "handled"; this one
          // and the message list below were the omissions.
          keyboardShouldPersistTaps="handled"
          style={styles.undxActionRailViewport}
          contentContainerStyle={styles.undxActionRail}
        >
          {undxComponents.map((component, index) => {
            // Both server dialects are read through one normaliser, so an agent
            // `action_confirmation` and a V4/V5 `confirmation_card` reach the same
            // controls. Comparing `component.component` to a literal here is what
            // previously left agent confirmations unapprovable.
            const card = toActionCard(component);
            const spent = Boolean(card.confirmationToken) && undxSpentTokens.current.has(card.confirmationToken);
            // The outcome of pressing *this* card, or null. Matched on the token so a
            // rail holding two cards cannot show one card's answer under the other.
            const outcome =
              card.confirmationToken && undxTapOutcome?.token === card.confirmationToken ? undxTapOutcome : null;
            return (
            <View key={`${component.component}-${component.confirmation_id || index}`} style={styles.undxActionCard}>
              <Text style={styles.undxActionKicker}>{card.kicker}</Text>
              <Text style={styles.undxActionTitle}>{card.title}</Text>
              <Text style={styles.undxActionBody}>
                {card.kind === "result"
                  ? component.relevance_reason ||
                    (component.capability_id === "saved.items.list"
                      ? `${component.record_count ?? component.records?.length ?? 0} saved item${(component.record_count ?? component.records?.length ?? 0) === 1 ? "" : "s"}`
                      : component.canonical_content_id
                        ? `Canonical ID ${component.canonical_content_id}`
                        : describeTransition(card))
                  : describeTransition(card)}
              </Text>
              {card.risk ? <Text style={styles.undxActionRisk}>{card.risk}</Text> : null}
              {/*
                The chooser's rows. Every other list on this card reads `records`, and a
                chooser sends `candidates` — so until this block existed the card drew a
                kicker, a title and "more than one of your alerts matches that", and
                nothing else. The person was asked to choose between things they could
                not see.

                The row itself — its number, its label, and the message that answers
                with it — is derived by `choiceRowsOf` rather than here. That is the
                lesson this file's own header records about `isConfirmation`: a decision
                spelled out inline in a two-thousand-line render is a decision nothing
                tests, and the last one of those left agent confirmations unapprovable
                for a release.

                `row.reply` is the same number `row.position` draws, which is what makes
                the tap and the typing agree: the server reads a lone number as the
                position it published, so there is one way to answer and the card shows
                it.
              */}
              {card.kind === "question" && choiceRowsOf(component).length ? (
                <View style={styles.undxAlertList}>
                  {choiceRowsOf(component).map((row, rowIndex) => (
                    <Pressable
                      key={`choice-${row.position}-${rowIndex}`}
                      accessibilityRole="button"
                      accessibilityLabel={`Choose ${row.position}: ${row.label}`}
                      style={styles.undxAlertRow}
                      onPress={() => { sendPayload({ body: row.reply }).catch(() => undefined); }}
                    >
                      <View style={styles.undxChoiceBody}>
                        <Text style={styles.undxChoiceIndex}>{row.position}</Text>
                        <View>
                          <Text style={styles.undxAlertTitle}>{row.label}</Text>
                          {row.detail ? <Text style={styles.undxAlertMeta}>{row.detail}</Text> : null}
                        </View>
                      </View>
                      <Text style={styles.undxAlertOpen}>Choose ›</Text>
                    </Pressable>
                  ))}
                </View>
              ) : null}
              {component.component === "crypto_alert_card" && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const alertId = Number(record.alert_id || record.id || 0);
                    const symbol = String(record.symbol || "Crypto");
                    const displayName = String(record.display_name || `${symbol} alert`);
                    const condition = String(record.condition || "alert");
                    const threshold = record.threshold ?? record.threshold_value ?? "";
                    const status = String(record.status || (record.paused ? "paused" : "active"));
                    return (
                      <Pressable
                        key={`${alertId || symbol}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open ${symbol} alert ${alertId}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(alertId > 0 ? `/pulse/alerts/${alertId}` : card.deepLink)}
                      >
                        <View>
                          <Text style={styles.undxAlertTitle}>{displayName}</Text>
                          <Text style={styles.undxAlertMeta}>{condition} {String(threshold)} · {status}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "content_result" && component.capability_id === "saved.items.list" && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const itemId = Number(record.item_id || 0);
                    const contentType = String(record.content_type || "content");
                    const title = String(record.title || `Saved ${contentType}`);
                    const preview = String(record.preview_text || "Open this saved PulseSoc item.");
                    const sourceUrl = String(record.source_url || "/pulse/saved");
                    return (
                      <Pressable
                        key={`${itemId || sourceUrl}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open saved ${contentType}: ${title}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={1}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={2}>{contentType} · {preview}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "content_result" && ["feed.posts.list", "feed.posts.get"].includes(String(component.capability_id || "")) && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const postId = Number(record.post_id || 0);
                    const title = String(record.title || record.body || `PulseSoc post ${postId}`);
                    const author = String(record.author_name || "PulseSoc Member");
                    const reactions = Number(record.reaction_count || 0);
                    const comments = Number(record.comment_count || 0);
                    const sourceUrl = String(record.source_url || `/pulse/post/${postId}`);
                    return (
                      <Pressable
                        key={`${postId}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open post ${postId} by ${author}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={2}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={1}>{author} · {reactions} reactions · {comments} comments</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "content_result" && component.capability_id === "comments.list" && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const commentId = Number(record.comment_id || 0);
                    const author = String(record.author_name || "PulseSoc Member");
                    const body = String(record.body || "Comment");
                    const sourceUrl = String(record.source_url || "/pulse");
                    return (
                      <Pressable
                        key={`${commentId}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open comment ${commentId} by ${author}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={2}>{body}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={1}>{author} · comment {commentId}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "content_result" && [
                "feed.post.performance.summary", "feed.comments.summary",
              ].includes(String(component.capability_id || "")) && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const postId = Number(record.post_id || 0);
                    const sourceUrl = String(record.source_url || `/pulse/post/${postId}`);
                    const isPerformance = component.capability_id === "feed.post.performance.summary";
                    const title = String(record.title || (isPerformance ? "Post performance" : "Comment summary"));
                    const detail = isPerformance
                      ? `${Number(record.views || 0)} views · ${Number(record.reactions || 0)} reactions · ${Number(record.comments || 0)} comments · ${Number(record.shares || 0)} shares · ${Number(record.saves || 0)} saves`
                      : String(record.summary || "No visible comments are available.");
                    return (
                      <Pressable
                        key={`${postId}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open post ${postId} ${isPerformance ? "performance" : "comment summary"}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={2}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={3}>{detail}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {["search_results", "content_result"].includes(String(component.component || "")) && [
                "reels.search", "reels.get", "reels.performance.summary", "reels.comments.summary",
                "status.list", "status.get", "status.viewer.summary", "status.reaction.summary",
              ].includes(String(component.capability_id || "")) && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const isReel = String(component.capability_id || "").startsWith("reels.");
                    const entityId = Number(isReel ? record.reel_id : record.status_id);
                    const sourceUrl = String(record.source_url || (isReel ? `/pulse/reels/${entityId}` : `/pulse/status/${entityId}`));
                    const title = String(record.title || record.caption || record.body || (isReel ? `Reel ${entityId}` : `Status ${entityId}`));
                    const detail = component.capability_id === "reels.performance.summary"
                      ? `${Number(record.reactions || 0)} reactions · ${Number(record.comments || 0)} comments · ${Number(record.shares || 0)} shares · ${Math.round(Number(record.completion_rate || 0) * 100)}% completion`
                      : component.capability_id === "reels.comments.summary"
                        ? String(record.summary || "No visible comments are available.")
                        : component.capability_id === "status.viewer.summary"
                          ? `${Number(record.viewer_count || 0)} viewers`
                          : component.capability_id === "status.reaction.summary"
                            ? `${Number(record.reactions || 0)} reactions`
                            : `${String(record.visibility || "visible")} · ${String(record.created_at || "")}`;
                    return (
                      <Pressable
                        key={`${isReel ? "reel" : "status"}-${entityId}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open ${isReel ? "Reel" : "Status"} ${entityId}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={2}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={3}>{detail}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "content_result" &&
              PERSONAL_INTELLIGENCE_CAPABILITIES.has(String(component.capability_id || "")) &&
              component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const title = String(
                      record.title ||
                      record.display_name ||
                      record.name ||
                      record.username ||
                      record.kind ||
                      record.source ||
                      "PulseSoc activity"
                    );
                    const timestamp = String(record.timestamp || record.created_at || "");
                    const source = String(record.source || "");
                    const detail = String(
                      record.detail ||
                      record.summary ||
                      record.body ||
                      record.description ||
                      [source, timestamp].filter(Boolean).join(" · ") ||
                      "Authorized PulseSoc result"
                    );
                    const sourceUrl = String(
                      record.native_route ||
                      record.source_url ||
                      component.deep_link ||
                      "/pulse/ai"
                    );
                    return (
                      <Pressable
                        key={`${String(component.capability_id)}-${String(record.source_id || record.id || recordIndex)}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open ${title}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={2}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={3}>{detail}</Text>
                          {source || timestamp ? (
                            <Text style={styles.undxAlertMeta} numberOfLines={1}>
                              {[source, timestamp].filter(Boolean).join(" · ")}
                            </Text>
                          ) : null}
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "profile_result" && [
                "profile.get", "profile.activity.summary", "profile.relationship.summary",
              ].includes(String(component.capability_id || "")) && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const userId = Number(record.user_id || 0);
                    const title = String(record.display_name || record.username || "Your PulseSoc account");
                    const detail = component.capability_id === "profile.activity.summary"
                      ? `${Number(record.posts || 0)} posts · ${Number(record.reels || 0)} reels · ${Number(record.statuses || 0)} statuses`
                      : component.capability_id === "profile.relationship.summary"
                        ? `${Number(record.followers || 0)} followers · ${Number(record.following || 0)} following`
                        : String(record.bio || `@${String(record.username || "")}`);
                    return (
                      <Pressable key={`${userId}-${recordIndex}`} accessibilityRole="link"
                        accessibilityLabel="Open your PulseSoc profile" style={styles.undxAlertRow}
                        onPress={() => openUndxResult(String(record.source_url || "/pulse/profile"))}>
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={1}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={2}>{detail}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "profile_result" && component.capability_id === "social.followers.list" && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const userId = Number(record.user_id || 0);
                    const displayName = String(record.display_name || record.username || "PulseSoc Member");
                    const username = String(record.username || "");
                    const profileUrl = String(record.profile_url || (userId > 0 ? `/pulse/profile/${userId}` : "/pulse/profile"));
                    return (
                      <Pressable
                        key={`${userId || username}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open ${displayName}'s profile`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(profileUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={1}>{displayName}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={1}>{username ? `@${username}` : "PulseSoc profile"}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "conversation_result" && component.capability_id === "conversations.list" && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const conversationId = Number(record.conversation_id || 0);
                    const title = String(record.title || "PulseSoc conversation");
                    const kind = String(record.conversation_type || "conversation");
                    const unread = Number(record.unread_count || 0);
                    const sourceUrl = String(record.source_url || (conversationId > 0 ? `/pulse/messages/${conversationId}` : "/pulse/messages"));
                    return (
                      <Pressable
                        key={`${conversationId || title}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open conversation ${title}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={1}>{title}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={1}>{kind}{unread > 0 ? ` · ${unread} unread` : " · read"}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "conversation_result" && component.capability_id === "messages.list" && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const messageId = Number(record.message_id || 0);
                    const senderId = Number(record.sender_user_id || 0);
                    const body = String(record.body || `[${String(record.message_type || "message")}]`);
                    const sourceUrl = String(record.source_url || "/pulse/messages");
                    return (
                      <Pressable
                        key={`${messageId}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open message ${messageId}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={2}>{body}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={1}>User {senderId} · message {messageId}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {component.component === "conversation_result" && [
                "messages.search", "conversations.summarize", "messages.suggest", "messages.draft",
              ].includes(String(component.capability_id || "")) && component.records?.length ? (
                <View style={styles.undxAlertList}>
                  {component.records.map((record, recordIndex) => {
                    const conversationId = Number(record.conversation_id || 0);
                    const text = String(record.summary || record.body || "Messenger result");
                    const sourceUrl = String(record.source_url || (conversationId > 0 ? `/pulse/messages/${conversationId}` : "/pulse/messages"));
                    const meta = record.draft_id
                      ? `Unsent draft · ${String(record.draft_id)}`
                      : record.message_count
                        ? `${Number(record.message_count)} messages · ${Number(record.participant_count || 0)} participants`
                        : record.based_on_message_id
                          ? `Suggestion · based on message ${Number(record.based_on_message_id)}`
                          : `Conversation ${conversationId} · message ${Number(record.message_id || 0)}`;
                    return (
                      <Pressable
                        key={`${String(record.draft_id || record.suggestion_id || record.message_id || recordIndex)}-${recordIndex}`}
                        accessibilityRole="link"
                        accessibilityLabel={`Open Messenger result ${recordIndex + 1}`}
                        style={styles.undxAlertRow}
                        onPress={() => openUndxResult(sourceUrl)}
                      >
                        <View style={styles.undxSavedCopy}>
                          <Text style={styles.undxAlertTitle} numberOfLines={3}>{text}</Text>
                          <Text style={styles.undxAlertMeta} numberOfLines={1}>{meta}</Text>
                        </View>
                        <Text style={styles.undxAlertOpen}>Open ›</Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {card.kind === "confirmation" && card.expiresAt ? <Text style={styles.undxActionRisk}>Approval expires {card.expiresAt}</Text> : null}
              {card.kind === "receipt" && !card.verified ? <Text style={styles.undxActionRisk}>{component.verification_detail || "UNDX could not read this back, so it is not claiming the change is saved."}</Text> : null}
              {card.idempotentReplay ? <Text style={styles.undxActionRisk}>Already done earlier — not repeated.</Text> : null}
              {card.verified && card.undoCapabilityId && undxUndoCommand(component) ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Undo UNDX action"
                  disabled={undxActionBusy}
                  style={styles.undxActionConfirm}
                  onPress={() => {
                    setUndxActionBusy(true);
                    sendPayload({ body: undxUndoCommand(component) })
                      .catch(() => undefined)
                      .finally(() => setUndxActionBusy(false));
                  }}
                >
                  {undxActionBusy ? <ActivityIndicator color="#06101b" /> : <Text style={styles.undxActionConfirmText}>{undxUndoLabel(component)}</Text>}
                </Pressable>
              ) : null}
              {card.kind === "result" && card.deepLink ? (
                <Pressable accessibilityRole="link" accessibilityLabel={`Open ${component.content_type || "PulseSOC"} result`} style={styles.undxActionConfirm} onPress={() => openUndxResult(card.deepLink)}>
                  <Text style={styles.undxActionConfirmText}>Open</Text>
                </Pressable>
              ) : null}
              {card.kind !== "result" && card.kind !== "confirmation" && card.deepLink ? (
                <Pressable accessibilityRole="link" accessibilityLabel="Open the affected PulseSOC screen" style={styles.undxActionCancel} onPress={() => openUndxResult(card.deepLink)}>
                  <Text style={styles.undxActionCancelText}>Open in PulseSoc</Text>
                </Pressable>
              ) : null}
              {/*
                What the press came back with, on the card the press was on.

                Drawn above the controls and unconditional on the keyboard, because the
                banner it used to live in alone is hidden while the keyboard is up —
                and a person taps Confirm on a card they summoned by typing, so that is
                precisely the state they are in. The server distinguishes six ways an
                approval can be dead and sends one sentence for each; this is where the
                sentence is finally read.
              */}
              {outcome ? (
                <Text accessibilityLabel="UNDX action outcome" style={styles.undxActionOutcome}>
                  {outcome.message}
                </Text>
              ) : null}
              {card.confirmationToken && outcome && !outcome.retryable ? (
                /*
                  The approval is dead and the server said so, so there is nothing left
                  to approve or to call off — both controls could only produce the same
                  refusal again. What is left is the card itself, which without this
                  would sit there permanently inert: Confirm disabled by the spent set,
                  Cancel disabled by the same flag, and no way to clear it.
                */
                <View style={styles.undxActionButtons}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Dismiss UNDX confirmation"
                    style={styles.undxActionCancel}
                    onPress={() => {
                      setUndxComponents((previous) =>
                        previous.filter((entry) => toActionCard(entry).confirmationToken !== card.confirmationToken),
                      );
                      setUndxTapOutcome(null);
                    }}
                  >
                    <Text style={styles.undxActionCancelText}>Dismiss</Text>
                  </Pressable>
                </View>
              ) : card.confirmationToken ? (
                <View style={styles.undxActionButtons}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Cancel UNDX action"
                    disabled={undxActionBusy || spent}
                    style={styles.undxActionCancel}
                    onPress={() => {
                      const token = card.confirmationToken;
                      if (!token) return;
                      setUndxActionBusy(true);
                      cancelPulseAiAction(token)
                        .then((result) => {
                          undxSpentTokens.current.add(token);
                          setUndxComponents([]);
                          setUndxTapOutcome(null);
                          setStatusMessage(result.message || "UNDX action cancelled.");
                        })
                        .catch((actionError) => {
                          // Same reasoning as Confirm: a refusal the person cannot see
                          // is a button that did nothing as far as they can tell.
                          const outcome = readTapOutcome(actionError);
                          setUndxTapOutcome({ ...outcome, token });
                          setStatusMessage(outcome.message);
                        })
                        .finally(() => setUndxActionBusy(false));
                    }}
                  >
                    <Text style={styles.undxActionCancelText}>Cancel</Text>
                  </Pressable>
                  <Pressable accessibilityRole="button" accessibilityLabel="Confirm UNDX action" disabled={undxActionBusy || spent} style={styles.undxActionConfirm} onPress={() => confirmUndxAction(card.confirmationToken)}>
                    {undxActionBusy ? <ActivityIndicator color="#06101b" /> : <Text style={styles.undxActionConfirmText}>Confirm</Text>}
                  </Pressable>
                </View>
              ) : null}
            </View>
            );
          })}
        </ScrollView>
      ) : null}
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} keyboardVerticalOffset={0} style={styles.composerAvoider}>
      <PulseCommandPanel style={[styles.composer, { paddingBottom: keyboardVisible ? 8 : Math.max(insets.bottom, 8) }, keyboardVisible && styles.composerKeyboard]}>
        <View pointerEvents="none" style={styles.composerSignalLine} />
        <View style={styles.composerMetaRow}>
          <View style={styles.composerMetaIdentity}><LiveStatusDot warning={Boolean(error)} /><Text style={styles.composerKicker}>PULSE LINK</Text></View>
          <Text style={[styles.composerState, showVoiceCapture && styles.composerStateRecording]}>{showVoiceCapture ? "RECORDING" : uploading ? "SENDING MEDIA" : error ? "RECONNECTING" : assistantConversation ? "UNDX · READY" : "SECURE · READY"}</Text>
        </View>
        {assistantConversation && marketChip ? (
          <View style={styles.marketContextChip}>
            <Text style={styles.marketContextChipText} numberOfLines={1}>
              Discussing {marketChip.asset.name} · {marketChip.asset.symbol}
            </Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Stop discussing ${marketChip.asset.symbol}`}
              hitSlop={8}
              onPress={() => {
                clearMarketContext();
                setMarketChip(null);
              }}
            >
              <Text style={styles.marketContextChipDismiss}>✕</Text>
            </Pressable>
          </View>
        ) : null}
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
        {showVoiceCapture ? (
          <VoiceCaptureDock
            elapsed={recordingElapsed}
            levels={recordingLevels}
            disabled={uploading || qaChatState === "voice-recording"}
            onCancel={() => cancelVoiceRecording().catch(() => undefined)}
            onSend={() => toggleVoiceRecording().catch(() => undefined)}
          />
        ) : <View style={styles.inputRow}>
          <SignalIconButton accessibilityLabel={assistantConversation ? "UNDX attachment support unavailable" : uploading ? "Uploading attachment" : "Add attachment"} icon={uploading ? "cloud-upload-outline" : "add"} disabled={uploading || assistantConversation} size={46} onPress={() => assistantConversation ? setStatusMessage("UNDX can chat by text right now.") : setAttachmentSheetOpen(true)} />
          <TextInput
            multiline
            autoFocus={qaChatState === "keyboard" || qaChatState === "reply-keyboard"}
            placeholder={assistantConversation ? "Message UNDX…" : "Message"}
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={draft}
            onChangeText={notifyTyping}
            accessibilityLabel={assistantConversation ? "Message UNDX composer" : "Message composer"}
          />
          <SignalIconButton accessibilityLabel="Add emoji" icon="happy-outline" size={42} onPress={() => setEmojiPickerOpen(true)} />
          <SignalIconButton accessibilityLabel={assistantConversation ? "UNDX voice messages unavailable" : "Record voice message"} icon="mic-outline" disabled={uploading || assistantConversation} size={42} onPress={() => assistantConversation ? setStatusMessage("UNDX cannot receive voice messages yet.") : toggleVoiceRecording().catch(() => undefined)} />
          <Pressable accessibilityRole="button" accessibilityLabel="Send message" disabled={!draft.trim()} style={({ pressed }) => [styles.sendButton, !draft.trim() && styles.sendDisabled, pressed && styles.pressed]} onPress={submitText}>
            <Text style={styles.sendText}>➤</Text>
          </Pressable>
        </View>}
      </PulseCommandPanel>
      </KeyboardAvoidingView>
      <EmojiPicker
        visible={emojiPickerOpen}
        stayOpenOnSelect
        onClose={() => setEmojiPickerOpen(false)}
        onSelect={(emoji) => setDraft((current) => `${current}${emoji}`)}
      />
      <EmojiPicker
        visible={reactionPickerFor !== null}
        onClose={() => setReactionPickerFor(null)}
        onSelect={(emoji) => {
          if (reactionPickerFor) react(reactionPickerFor, emoji).catch(() => undefined);
          setReactionPickerFor(null);
        }}
      />
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
        onReactMore={(message) => {
          setSelectedMessage(null);
          setReactionPickerFor(message);
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
        title={assistantConversation ? PULSE_AI_DISPLAY_NAME : route.params.title || "Conversation"}
        messages={messages}
        connected={!error}
        activityStatus={peerPresenceControlLabel(peerPresence)}
        assistantConversation={assistantConversation}
        onClose={() => setControlCenterOpen(false)}
        onStartCall={!assistantConversation ? (callType) => {
          setControlCenterOpen(false);
          navigation.navigate("Call", {
            conversationId,
            callType,
            direction: "outgoing",
            title: threadTitle
          });
        } : undefined}
        onOpenSafety={(section) => {
          setControlCenterOpen(false);
          navigation.navigate("SafetyHub", { section, title: section === "reports" ? "Report Conversation" : "Blocked Users" });
        }}
      />
      </LogiNexusScreenShell>
    </View>
  );
}

function VoiceCaptureDock({ elapsed, levels, disabled, onCancel, onSend }: { elapsed: number; levels: number[]; disabled: boolean; onCancel: () => void; onSend: () => void }) {
  return (
    <View accessibilityLabel={`Recording voice message. ${formatDuration(elapsed)}`} style={styles.voiceCaptureDock}>
      <Pressable accessibilityRole="button" accessibilityLabel="Discard voice recording" disabled={disabled} style={({ pressed }) => [styles.voiceCaptureCancel, pressed && styles.pressed]} onPress={onCancel}>
        <Ionicons name="trash-outline" size={20} color="#ff6685" />
      </Pressable>
      <View style={styles.voiceCaptureBody}>
        <View style={styles.voiceCaptureHeader}>
          <View style={styles.voiceCaptureLive}><View style={styles.voiceCaptureLiveDot} /><Text style={styles.voiceCaptureKicker}>LIVE VOICE PULSE</Text></View>
          <Text style={styles.voiceCaptureTime}>{formatDuration(elapsed)}</Text>
        </View>
        <View pointerEvents="none" style={styles.voiceCaptureWaveform}>
          {levels.map((level, index) => (
            <View
              key={index}
              style={[
                styles.voiceCaptureBar,
                index % 3 === 1 && styles.voiceCaptureBarPurple,
                { height: 5 + Math.round(Math.max(0.08, level) * 23) }
              ]}
            />
          ))}
        </View>
      </View>
      <Pressable accessibilityRole="button" accessibilityLabel="Stop and send voice message" disabled={disabled} style={({ pressed }) => [styles.voiceCaptureSend, pressed && styles.pressed, disabled && styles.disabled]} onPress={onSend}>
        <Ionicons name="send" size={21} color="#03120f" />
      </Pressable>
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
  const body = deleted ? "This message was deleted." : moderated ? "This message is unavailable after safety review." : displayMessageBody(message);
  const voiceMessage = isVoiceLikeMessage(message);
  return (
    <View style={[styles.bubbleWrap, mine ? styles.mineWrap : styles.theirWrap]} accessible={!voiceMessage} accessibilityLabel={messageAccessibilityLabel(message)}>
      <Pressable onLongPress={onLongPress} style={[styles.bubble, mine ? styles.mineBubble : styles.theirBubble, moderated && styles.moderatedBubble]}>
        {!mine ? <Text style={styles.senderLabel}>{message.sender_display_name || (message.sender_trust_state === "intelligence" ? "UNDX" : "PulseSoc member")}</Text> : null}
        {message.reply_preview ? (
          <View style={styles.replyBlock}>
            <Text style={styles.replyTitle}>Reply</Text>
            <Text style={styles.replyPreview} numberOfLines={2}>{message.reply_preview}</Text>
          </View>
        ) : null}
        {!deleted && !moderated ? <MessageMedia message={message} /> : null}
        {body ? (
          deleted || moderated ? (
            <Text style={[styles.body, styles.systemBody]}>{body}</Text>
          ) : (
            <ContentTranslation
              contentType="chat"
              contentRef={message.message_id || message.id || message.client_message_id || "pending"}
              text={body}
              sourceLanguage={
                typeof (message as Record<string, unknown>).source_language === "string"
                  ? ((message as Record<string, unknown>).source_language as string)
                  : typeof (message as Record<string, unknown>).language === "string"
                    ? ((message as Record<string, unknown>).language as string)
                    : "auto"
              }
              textStyle={styles.body}
              controlsMode="compact"
            />
          )
        ) : null}
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
  onReactMore,
  onRetry,
  onDelete,
  onReport,
  onSafety
}: {
  message: MessengerMessage | null;
  onClose: () => void;
  onReply: (message: MessengerMessage) => void;
  onReact: (message: MessengerMessage, reactionType: string) => void;
  onReactMore: (message: MessengerMessage) => void;
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
              {QUICK_REACTIONS.map((reaction) => (
                <Pressable
                  key={reaction}
                  accessibilityRole="button"
                  accessibilityLabel={`React ${reaction}`}
                  style={[styles.reactionChoice, message.viewer_reaction === reaction && styles.reactionActive]}
                  onPress={() => onReact(message, reaction)}
                >
                  <Text style={styles.quickReactionGlyph} allowFontScaling={false}>{reaction}</Text>
                </Pressable>
              ))}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="More reactions"
                style={styles.reactionChoice}
                onPress={() => onReactMore(message)}
              >
                <Text style={styles.quickReactionGlyph} allowFontScaling={false}>➕</Text>
              </Pressable>
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
  if (isVoiceType(type) && !mediaUrl) {
    return (
      <View accessible accessibilityRole="text" accessibilityLabel={`${messageAccessibilityLabel(message)}. Voice message unavailable.`} style={styles.voiceUnavailable}>
        <Ionicons name="alert-circle-outline" size={18} color={colors.danger} />
        <Text style={styles.voiceUnavailableText}>Voice message unavailable</Text>
      </View>
    );
  }
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
        <Pressable
          accessibilityRole="imagebutton"
          accessibilityLabel={`${messageAccessibilityLabel(message)}. ${type === "gif" ? "GIF" : "Image"} attachment.`}
          accessibilityHint="Opens the full-screen viewer"
          onPress={() => setViewerOpen(true)}
        >
          <Image source={{ uri: thumbnailUrl || mediaUrl }} style={styles.image} resizeMode="cover" />
        </Pressable>
        <NativeMediaViewer visible={viewerOpen} items={[viewerItem]} title="Messenger media" onClose={() => setViewerOpen(false)} />
      </>
    );
  }
  if (isVoiceType(type)) {
    return <VoiceMessageCard message={message} url={mediaUrl} />;
  }
  return (
    <Pressable style={styles.attachment} onPress={() => (type === "video" ? setViewerOpen(true) : undefined)}>
      <Text style={styles.attachmentTitle}>{type === "video" ? "Video attachment" : "File attachment"}</Text>
      <Text style={styles.attachmentMeta}>{type === "video" ? "Open viewer" : formatFileSize(message.file_size)}</Text>
      <NativeMediaViewer visible={viewerOpen} items={[viewerItem]} title="Messenger media" onClose={() => setViewerOpen(false)} />
    </Pressable>
  );
}

const VoiceMessageCard = memo(function VoiceMessageCard({ message, url }: { message: MessengerMessage; url: string }) {
  const messageId = String(message.message_id || message.id);
  const metadataDurationMillis = Math.max(0, Number(message.duration_seconds || message.duration || 0) * 1000);
  const [snapshot, setSnapshot] = useState<VoicePlaybackSnapshot>({
    messageId,
    status: "idle",
    positionMillis: 0,
    durationMillis: metadataDurationMillis,
    rate: 1,
    error: ""
  });
  const timelineWidth = useRef(1);
  const waveform = useMemo(() => normalizedWaveform(message.waveform, messageId), [message.waveform, messageId]);
  useEffect(() => subscribeVoicePlayback(messageId, metadataDurationMillis, setSnapshot), [messageId, metadataDurationMillis]);
  const durationMillis = Math.max(snapshot.durationMillis, metadataDurationMillis);
  const progress = Math.min(1, snapshot.positionMillis / Math.max(1, durationMillis));
  const playing = snapshot.status === "playing";
  const loading = snapshot.status === "loading";
  const failed = snapshot.status === "error";
  const request = { messageId, url, durationMillis: metadataDurationMillis };
  const toggle = () => (failed ? retryVoicePlayback(request) : toggleVoicePlayback(request));
  const changeRate = async () => {
    const next = await cycleVoicePlaybackRate(messageId);
    AccessibilityInfo.announceForAccessibility(`Playback speed ${next} times`);
  };
  return (
    <View style={styles.voiceCard}>
      <View accessible accessibilityRole="text" accessibilityLabel={messageAccessibilityLabel(message)} style={styles.voiceSemanticSummary} />
      <View style={styles.voiceControls}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={failed ? "Retry voice message" : playing ? "Pause voice message" : "Play voice message"}
          accessibilityState={{ busy: loading }}
          hitSlop={4}
          style={({ pressed }) => [styles.voicePlay, failed && styles.voicePlayError, pressed && styles.voicePressed]}
          onPress={() => toggle().catch(() => undefined)}
        >
          {loading ? <ActivityIndicator color="#03120f" size="small" /> : <Ionicons name={failed ? "refresh" : playing ? "pause" : "play"} size={19} color="#03120f" />}
        </Pressable>
        <Pressable
          accessibilityRole="adjustable"
          accessibilityLabel="Voice message progress"
          accessibilityValue={{ min: 0, max: Math.max(1, Math.round(durationMillis / 1000)), now: Math.round(snapshot.positionMillis / 1000), text: `${formatDuration(snapshot.positionMillis / 1000)} of ${formatDuration(durationMillis / 1000)}` }}
          accessibilityActions={[{ name: "increment", label: "Forward 5 seconds" }, { name: "decrement", label: "Back 5 seconds" }]}
          style={styles.voiceTimeline}
          onAccessibilityAction={(event) => seekVoicePlaybackBy(messageId, event.nativeEvent.actionName === "increment" ? 5000 : -5000).catch(() => undefined)}
          onLayout={(event) => { timelineWidth.current = Math.max(1, event.nativeEvent.layout.width); }}
          onPress={(event) => seekVoicePlayback(messageId, event.nativeEvent.locationX / timelineWidth.current).catch(() => undefined)}
        >
          {failed ? <Text numberOfLines={1} style={styles.voiceError}>Couldn’t play · Retry</Text> : (
            <View style={styles.waveform}>
              {waveform.map((level, index) => (
                <View key={index} style={[styles.waveBar, index % 4 === 2 && styles.waveBarPurple, index / waveform.length <= progress ? styles.waveBarPlayed : styles.waveBarPending, { height: 7 + level * 14 }]} />
              ))}
            </View>
          )}
        </Pressable>
        <Text accessibilityLabel={`Duration ${formatDuration(durationMillis / 1000)}`} style={styles.voiceDuration}>{formatDuration(durationMillis / 1000)}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel={`Playback speed ${snapshot.rate} times`} hitSlop={5} style={({ pressed }) => [styles.voiceRate, pressed && styles.voicePressed]} onPress={() => changeRate().catch(() => undefined)}>
          <Text style={styles.voiceRateText}>{snapshot.rate}x</Text>
        </Pressable>
      </View>
    </View>
  );
});

function formatDuration(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function normalizedWaveform(input: number[] | undefined, seed: string) {
  if (Array.isArray(input) && input.length) {
    return input.slice(0, 22).map((value) => Math.max(0.08, Math.min(1, Number(value || 0) > 1 ? Number(value || 0) / 100 : Number(value || 0))));
  }
  const numericSeed = seed.split("").reduce((total, value) => total + value.charCodeAt(0), 0);
  return Array.from({ length: 18 }, (_, index) => 0.18 + (((numericSeed + index * 17) % 70) / 100));
}

function normalizedMessageType(value?: string) {
  return String(value || "").trim().toLowerCase();
}

function isVoiceType(type?: string) {
  return ["voice", "audio", "voice_message", "audio_message"].includes(normalizedMessageType(type));
}

function isVoiceLikeMessage(message: MessengerMessage) {
  return isVoiceType(message.message_type || message.type);
}

function displayMessageBody(message: MessengerMessage) {
  return message.body || "";
}

function mediaPreviewLabel(type: string, hasMedia: boolean) {
  if (isVoiceType(type)) return "Voice message";
  if (type === "image" || type === "gif") return "Photo";
  if (type === "video") return "Video";
  if (type === "file" || type === "document") return "File attachment";
  return hasMedia ? "Attachment" : "Message";
}

type PeerPresence = {
  status: string;
  activity: string;
  activity_context: string;
  last_seen_text: string;
  online: boolean;
};

/**
 * True only when the server has affirmatively said a human is online.
 *
 * "active", "available" and "typing" used to count here, which meant a typing
 * event or a loosely-named field could paint a green ring on someone who was
 * not connected at all. Only the one canonical token the presence service
 * emits is accepted now.
 */
function isPresenceActive(value?: string) {
  return String(value || "").toLowerCase() === "online";
}

function isAssistantPresenceValue(value?: string) {
  return String(value || "").toLowerCase() === "assistant";
}

/**
 * Pull this thread's peer out of the presence array the server returns with
 * every conversation fetch and sync.
 *
 * Returning null when the peer is absent is load-bearing: an absent entry means
 * we know nothing, and the header must then fall back to connection state
 * rather than to a remembered "Online".
 */
function peerPresenceFrom(presence: MessengerPresence | undefined, selfUserId: number): PeerPresence | null {
  const users = Array.isArray(presence?.users) ? presence?.users || [] : [];
  const peer = users.find((item) => Number(item?.user_id || 0) !== selfUserId && Number(item?.user_id || 0) !== 0);
  if (!peer) return null;
  const record = peer as Record<string, unknown>;
  const status = String(record.status || "").toLowerCase();
  return {
    status,
    activity: String(record.activity || "idle").toLowerCase(),
    activity_context: String(record.activity_context || ""),
    last_seen_text: String(record.last_seen_text || ""),
    online: status === "online"
  };
}

/**
 * Render the peer's presence as a single header line.
 *
 * An offline peer shows their real last-seen sentence when the server supplied
 * one and a plain "Offline" when it did not. It never shows a fabricated
 * timestamp, and it never silently upgrades unknown state to "Online".
 */
function peerPresenceSubtitle(presence: PeerPresence | null) {
  if (!presence) return "";
  if (presence.online) {
    // Activity wording comes from the shared presence module, not a local map.
    // Two copies of this vocabulary is how Messenger and Live end up calling
    // the same state different things.
    const activity = presenceActivityText(presence.activity);
    return activity ? `${activity} · Direct` : "Online · Direct";
  }
  return presence.last_seen_text || "Offline";
}

/**
 * Control-centre presence label. Same server-authoritative source as the header
 * subtitle, minus the "· Direct" decoration the control sheet renders itself.
 * Returns "" when we have no presence record so the sheet shows an honest
 * "Presence unavailable" rather than assuming the peer is online.
 */
function peerPresenceControlLabel(presence: PeerPresence | null) {
  if (!presence) return "";
  if (presence.online) {
    return presenceActivityText(presence.activity) || "Online";
  }
  return presence.last_seen_text || "Offline";
}

function absoluteMediaUrl(value?: string) {
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith("/")) return `${PULSE_API_BASE_URL}${value}`;
  return value;
}

function nativePathFromDeepLink(value?: string) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    if (/^https?:\/\//i.test(raw)) return new URL(raw).pathname + new URL(raw).search;
  } catch {
    return "";
  }
  if (raw.startsWith("pulsesoc://")) return raw.replace(/^pulsesoc:\/\/[^/]*(\/?)/i, "/");
  return raw.startsWith("/") ? raw : "";
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: "transparent",
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
    paddingBottom: logiNexus.spacing.sm,
    position: "relative",
    zIndex: 3
  },
  threadHeader: { alignItems: "center", flexDirection: "row", gap: 8, minHeight: 56 },
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
  voiceCard: { minWidth: 222, paddingVertical: 1 },
  voiceSemanticSummary: { height: 1, left: 0, opacity: 0, position: "absolute", top: 0, width: 1 },
  voiceControls: { alignItems: "center", flexDirection: "row", gap: 7, minHeight: 44 },
  voicePlay: { alignItems: "center", backgroundColor: colors.accent, borderColor: "rgba(255,255,255,0.5)", borderRadius: 22, borderWidth: 1, height: 44, justifyContent: "center", shadowColor: colors.accent, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.25, shadowRadius: 8, width: 44 },
  voicePlayError: { backgroundColor: colors.danger, borderColor: "rgba(255,255,255,0.58)" },
  voicePressed: { opacity: 0.78, transform: [{ scale: 0.96 }] },
  voiceTimeline: { flex: 1, justifyContent: "center", minHeight: 44, minWidth: 92 },
  waveform: { alignItems: "center", flexDirection: "row", gap: 2, height: 28 },
  waveBar: { borderRadius: 2, flex: 1, maxWidth: 4, minWidth: 2 },
  waveBarPlayed: { backgroundColor: colors.accentStrong, opacity: 1 },
  waveBarPending: { backgroundColor: "rgba(185,205,222,0.42)" },
  waveBarPurple: { borderColor: "rgba(167,124,255,0.72)", borderWidth: StyleSheet.hairlineWidth },
  voiceError: { color: colors.danger, fontSize: 11, fontWeight: "800" },
  voiceUnavailable: { alignItems: "center", flexDirection: "row", gap: 7, minHeight: 44, minWidth: 210 },
  voiceUnavailableText: { color: colors.danger, fontSize: 12, fontWeight: "800" },
  voiceDuration: { color: colors.text, fontSize: 11, fontVariant: ["tabular-nums"], fontWeight: "800" },
  voiceRate: { alignItems: "center", backgroundColor: "rgba(167,124,255,0.13)", borderColor: "rgba(167,124,255,0.62)", borderRadius: 11, borderWidth: 1, minHeight: 32, minWidth: 36, justifyContent: "center" },
  voiceRateText: { color: "#d7caff", fontSize: 12, fontWeight: "900" },
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
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    borderWidth: 1,
    gap: 5,
    marginHorizontal: 0,
    marginTop: 0,
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingTop: 7,
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: -8 },
    shadowOpacity: 0.22,
    shadowRadius: 24
  },
  composerAvoider: {
    backgroundColor: colors.background,
    flexShrink: 0,
    width: "100%"
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
    borderRadius: 18,
    marginHorizontal: 8,
    shadowOpacity: 0.16
  },
  composerMetaRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 18,
    paddingHorizontal: 4
  },
  composerMetaIdentity: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  composerKicker: {
    color: colors.accent,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 1.4
  },
  composerState: {
    color: colors.muted,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 0.8
  },
  composerStateRecording: {
    color: colors.danger
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
  marketContextChip: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: "rgba(97,216,255,0.08)",
    borderColor: "rgba(97,216,255,0.24)",
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 5
  },
  marketContextChipText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800",
    maxWidth: 240
  },
  marketContextChipDismiss: {
    color: colors.muted,
    fontSize: 12,
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
    alignItems: "center",
    flexDirection: "row",
    gap: 7,
    minHeight: 48
  },
  voiceCaptureDock: {
    alignItems: "center",
    backgroundColor: "rgba(4,18,31,0.96)",
    borderColor: "rgba(255,75,116,0.46)",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 58,
    paddingHorizontal: 8,
    paddingVertical: 7,
    shadowColor: "#ff4b74",
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.18,
    shadowRadius: 18
  },
  voiceCaptureCancel: { alignItems: "center", backgroundColor: "rgba(255,75,116,0.1)", borderColor: "rgba(255,102,133,0.5)", borderRadius: 16, borderWidth: 1, height: 42, justifyContent: "center", width: 42 },
  voiceCaptureBody: { flex: 1, gap: 4, minWidth: 0 },
  voiceCaptureHeader: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  voiceCaptureLive: { alignItems: "center", flexDirection: "row", gap: 6 },
  voiceCaptureLiveDot: { backgroundColor: "#ff4b74", borderRadius: 4, height: 7, shadowColor: "#ff4b74", shadowOpacity: 0.72, shadowRadius: 7, width: 7 },
  voiceCaptureKicker: { color: "#ff8da6", fontSize: 9, fontWeight: "900", letterSpacing: 1.1 },
  voiceCaptureTime: { color: colors.text, fontSize: 11, fontVariant: ["tabular-nums"], fontWeight: "900" },
  voiceCaptureWaveform: { alignItems: "center", flexDirection: "row", gap: 2, height: 28 },
  voiceCaptureBar: { backgroundColor: colors.accent, borderRadius: 2, flex: 1, maxWidth: 5, minWidth: 2 },
  voiceCaptureBarPurple: { backgroundColor: "#a77cff" },
  voiceCaptureSend: { alignItems: "center", backgroundColor: colors.accent, borderColor: "rgba(255,255,255,0.56)", borderRadius: 23, borderWidth: 1, height: 46, justifyContent: "center", shadowColor: colors.accent, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.42, shadowRadius: 14, width: 46 },
  input: {
    backgroundColor: "rgba(2,9,19,0.92)",
    borderColor: "rgba(97,216,255,0.5)",
    borderRadius: 23,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    fontSize: 16,
    maxHeight: 76,
    minHeight: 44,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  sendButton: {
    alignItems: "center",
    backgroundColor: "rgba(65,236,198,0.96)",
    borderRadius: 999,
    minHeight: 46,
    minWidth: 46,
    justifyContent: "center",
    paddingHorizontal: 11,
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.34,
    shadowRadius: 14
  },
  pressed: {
    opacity: 0.82
  },
  undxActionRail: {
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  undxActionRailViewport: { flexGrow: 0, maxHeight: 330 },
  undxActionCard: {
    backgroundColor: "rgba(15,25,46,0.98)",
    borderColor: "rgba(167,124,255,0.62)",
    borderRadius: 16,
    borderWidth: 1,
    gap: 6,
    padding: 14
  },
  undxActionKicker: { color: "#a77cff", fontSize: 10, fontWeight: "900", letterSpacing: 1.2 },
  undxActionTitle: { color: colors.text, fontSize: 16, fontWeight: "900" },
  undxActionBody: { color: colors.text, fontSize: 14, lineHeight: 20 },
  undxActionRisk: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  undxAlertList: { gap: 8, marginTop: 4 },
  undxChoiceBody: { alignItems: "center", flexDirection: "row", flex: 1, gap: 10, paddingRight: 10 },
  // The number is the handle on the row. It is drawn because the server already
  // assigned it and the person is expected to be able to type it back.
  undxChoiceIndex: { color: colors.accent, fontSize: 15, fontWeight: "900", minWidth: 18, textAlign: "center" },
  undxAlertRow: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: 1, flexDirection: "row", justifyContent: "space-between", minHeight: 58, paddingHorizontal: 12, paddingVertical: 8 },
  undxSavedCopy: { flex: 1, paddingRight: 10 },
  undxAlertTitle: { color: colors.text, fontSize: 14, fontWeight: "900" },
  undxAlertMeta: { color: colors.muted, fontSize: 12, marginTop: 2 },
  undxAlertOpen: { color: colors.accent, fontSize: 13, fontWeight: "900" },
  // Deliberately not styled as an error. Four of the six things it says are "nothing
  // changed", which is information rather than a fault, and one of them reports a write
  // that already ran. Red would misdescribe most of what it carries.
  undxActionOutcome: { color: colors.text, fontSize: 13, lineHeight: 19, marginTop: 2 },
  undxActionButtons: { flexDirection: "row", gap: 8, marginTop: 4 },
  undxActionCancel: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: 1, flex: 1, minHeight: 44, justifyContent: "center" },
  undxActionCancelText: { color: colors.text, fontWeight: "900" },
  undxActionConfirm: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 12, flex: 1, minHeight: 44, justifyContent: "center" },
  undxActionConfirmText: { color: "#06101b", fontWeight: "900" },
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
  quickReactionGlyph: {
    fontSize: 24
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
