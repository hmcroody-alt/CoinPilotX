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
  useWindowDimensions,
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
import { buildUndxSendContext, clearMarketContext, peekMarketContext } from "../undx/marketContext";
import { choiceRowsOf, describeTransition, readTapOutcome, toActionCard, UndxTapOutcome } from "../undx/actionCards";
import { goBackFromUndxChat } from "../undx/undxChatTarget";
import {
  ConversationGalleryProvider,
  ConversationMediaGalleryViewer,
  useConversationGallery
} from "../media/ConversationMediaGalleryHost";
import { gallerySeedFromMessage, useConversationMediaGallery } from "../media/useConversationMediaGallery";
import { ConversationMediaItem } from "../media/conversationMediaCollection";
import { isMultiMediaMessage, mediaTileColumns, messageMediaTiles } from "../media/messageMediaTiles";
import {
  MessengerMediaAccessState,
  MessengerMediaMeta,
  messengerMediaCacheIdentity,
  useMessengerMediaAccessUrl
} from "../media/messengerMediaAccess";
import { exceedsLimit, limitMessage, maxDurationSeconds } from "../media/storedVideoPolicy";
import { openDocument } from "../media/mediaActions";
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
import { translate, useTranslation } from "../i18n";
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
  const { t } = useTranslation();
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
  /**
   * The conversation's media gallery, owned here rather than in a bubble.
   *
   * It has to live above the list. The collection is the whole conversation's
   * media, which a message cell cannot see — a cell only knows itself, and only
   * the cells near the viewport are mounted at all. Hoisting it is what makes
   * "tap the 17th photo, land on the 17th photo" a property of the code rather
   * than a coincidence of where the thread happened to be scrolled.
   */
  const mediaGallery = useConversationMediaGallery(conversationId, { online: !usingCachedMessages });
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
  // should see — and be able to end — what "it" currently means. The chip is a
  // render of the parked envelope and nothing else, and the request is built
  // from that same envelope by `buildUndxSendContext`, so what the member reads
  // and what the assistant is told cannot come apart. Dismissing does not just
  // hide the words: it arms a clear that the next send carries to the server,
  // which is holding its own copy and would otherwise keep resolving "it".
  const [marketChip, setMarketChip] = useState(assistantConversation ? peekMarketContext() : null);
  // Where a contextual "Ask UNDX" came from. The drill-in pushes this screen on
  // top of the asset screen, so `goBack()` normally does the right thing on its
  // own; this is the answer for the cases where the stack cannot answer — a
  // deep link, a restored session, a future caller that resets. It is read
  // only as a fallback, never in preference to the real stack, because the
  // stack knows about screens the member visited in between and this does not.
  const undxReturn = route.params.undxReturn;
  // The rule itself lives in `goBackFromUndxChat` (real stack first, recorded
  // origin second, dashboard as the guaranteed floor) so the rendered
  // navigation regression test exercises exactly what this screen runs.
  const goBackFromChat = useCallback(() => {
    goBackFromUndxChat(navigation, undxReturn);
  }, [navigation, undxReturn]);
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
  }, [navigation, t]);

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
      ? t("messaging:chat.headerReconnecting")
      : t("messaging:chat.headerUnavailable")
    : usingCachedMessages
      ? t("messaging:chat.headerCachedHistory")
      : t("messaging:chat.headerLiveChannel");
  // Presence beats connection state, and live presence beats the navigation
  // snapshot. When the server has told us nothing about the peer we show
  // connection status instead of guessing.
  const presenceSubtitle = peerPresenceSubtitle(peerPresence);
  const headerSubtitle = assistantConversation
    ? typing || (error ? t("messaging:chat.assistantReconnecting") : usingCachedMessages ? t("messaging:chat.headerCachedHistory") : t("messaging:chat.assistantAlwaysAvailable"))
    : typing || presenceSubtitle || headerStatus;
  const peerIsOnline = Boolean(peerPresence?.online);

  const mergeMessages = useCallback(
    (current: MessengerMessage[], incoming: MessengerMessage[]) => mergeConversationMessages(current, incoming),
    []
  );

  /**
   * Record the server's acknowledgement of a local bubble.
   *
   * This used to delete the local row by id and then insert the server row, which
   * meant ChatScreen was deciding for itself that those two rows were the same
   * message -- a second opinion on the one question `messengerReconciler` exists to
   * own. It also broke on a retry, whose optimistic row carries a fresh negative id
   * that no longer matches the row actually on screen.
   *
   * The only thing this layer knows that the reconciler cannot infer is the mapping
   * itself, so that is all it contributes: stamp the client id onto the server row
   * so both halves of the identity travel together, and let the reconciler collapse
   * them. A server response that omits the client id would otherwise arrive as an
   * unrelated message and render as a duplicate.
   */
  const acknowledgeLocalMessage = useCallback((local: MessengerMessage, next: MessengerMessage) => {
    const acked: MessengerMessage = {
      ...next,
      client_message_id: next.client_message_id || local.client_message_id
    };
    setMessages((current) => mergeMessages(current, [acked]));
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
        setStatusMessage(t("messaging:chat.showingCached"));
      } else {
        setUsingCachedMessages(false);
        setError(loadError instanceof Error ? loadError.message : t("messaging:chat.loadFailed"));
      }
    } finally {
      setInitialFetchComplete(true);
      setRefreshing(false);
      setLoading(false);
    }
  }, [assistantConversation, conversationId, selfUserId, t]);

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
      setError(loadError instanceof Error ? loadError.message : t("messaging:chat.loadOlderFailed"));
    } finally {
      setLoadingOlder(false);
    }
  }, [assistantConversation, conversationId, loadingOlder, mergeMessages, oldestMessageId, t]);

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
        if (messages.length) setStatusMessage(t("messaging:chat.undxReconnecting"));
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
        setStatusMessage(t("messaging:chat.messagesReconnected"));
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
      if (messages.length) setStatusMessage(t("messaging:chat.realtimeReconnecting"));
    }
  }, [assistantConversation, conversationId, mergeMessages, messages.length, newestMessageId, selfUserId, t]);

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
    client_message_id?: string;
  }) => {
    if (assistantConversation) {
      if ((payload.message_type || "text") !== "text" || payload.media_url || payload.attachment_ids?.length || payload.media_ids?.length) {
        setStatusMessage("UNDX can chat by text right now.");
        return "failed" as const;
      }
      const body = (payload.body || "").trim();
      if (!body) return "failed" as const;
      const local = createLocalMessage(conversationId, body, "text", payload.client_message_id);
      setMessages((current) => mergeMessages(current, [local]));
      setTyping(t("messaging:chat.undxTyping"));
      setStatusMessage(t("messaging:chat.undxThinking"));
      try {
        // Market Pulse → UNDX bridge. Two things can be true of one send, and
        // exactly one module decides which: the envelope parked by an asset
        // screen rides along on the first send only (the server persists it per
        // conversation, so later turns inherit it without a resend — and a
        // resend would falsely re-stamp a minutes-old snapshot as fresh), or
        // the member dismissed the chip and the request carries the news that
        // the topic is over. The second case matters because the server holds
        // its own copy: forgetting the envelope on this device alone would
        // leave "how is it doing?" still resolving to a coin the member said
        // they were finished with.
        const data = await sendPulseAiMessage({
          body,
          client_message_id: local.client_message_id,
          ui_context: {
            ...(await collectUndxUiContext(navigation, conversationId, route.params.undxTaskId)),
            ...buildUndxSendContext()
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
          local_error: sendError instanceof Error ? sendError.message : t("messaging:chat.undxNoResponse")
        }]);
        setMessages(failedMessages);
        await cacheMessages(conversationId, failedMessages);
        setStatusMessage(sendError instanceof Error ? sendError.message : t("messaging:chat.undxUnavailable"));
        throw sendError;
      }
    }
    const payloadType = normalizedMessageType(payload.message_type || "text");
    const label = payload.body?.trim() || mediaPreviewLabel(payloadType, Boolean(payload.media_url));
    const local = createLocalMessage(conversationId, payload.body || "", payload.message_type || "text", payload.client_message_id);
    local.media_url = payload.media_url;
    // attachment_ids come from /api/messages/media/init, whose contract is the
    // FOUNDATION message_attachments table — so this is a foundation media id
    // and belongs in media_upload_id, not attachment_id.
    local.media_upload_id = Number(payload.attachment_ids?.[0] || 0) || undefined;
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
      acknowledgeLocalMessage(local, serverMessage);
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
        // Routed through the reconciler rather than an `id === local.id` scan: on a
        // retry the row on screen still carries the FIRST attempt's negative id, so
        // matching on this attempt's id would silently update nothing. Identity is
        // the client id, and only the reconciler knows that.
        setMessages((current) => {
          const queuedMessages = mergeMessages(current, [{
            ...local,
            delivery_status: "queued",
            local_status: "queued",
            local_error: undefined
          }]);
          cacheMessages(conversationId, queuedMessages).catch(() => undefined);
          return queuedMessages;
        });
        return "queued" as const;
      }
      setMessages((current) => {
        const failedMessages = mergeMessages(current, [{
          ...local,
          delivery_status: "failed",
          local_status: "failed",
          local_error: sendError instanceof Error ? sendError.message : "Message could not be sent."
        }]);
        cacheMessages(conversationId, failedMessages).catch(() => undefined);
        return failedMessages;
      });
      throw sendError;
    }
  }, [assistantConversation, conversationId, acknowledgeLocalMessage, mergeMessages, messages, navigation, route.params.undxTaskId, sync]);

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
    // A retry is the SAME logical message, so it must carry the same identity.
    // Minting a fresh one here is what turned "the response was lost in flight"
    // into a guaranteed duplicate: the first attempt may well have reached the
    // server and only its acknowledgement went missing. Reusing the id lets the
    // server recognise the resend and hand back the original row instead of
    // writing a second one.
    //
    // The failed row is NOT removed first. It used to be, and the delete-then-
    // reinsert was ChatScreen quietly making the same identity decision the
    // reconciler owns -- with a worse answer, because the two rows were only
    // "the same message" by virtue of running one line apart. Now the retry's
    // optimistic bubble carries the same client id, so the reconciler folds it
    // onto the row already on screen: the failure text and retry affordance
    // clear in place and the message keeps its position. One bubble throughout.
    await sendPayload({
      body: isVoiceLikeMessage(message) ? "" : message.body || "",
      message_type: message.message_type || "text",
      media_url: message.media_url,
      thumbnail_url: message.thumbnail_url,
      file_size: message.file_size,
      duration_seconds: message.duration_seconds,
      reply_to_message_id: message.reply_to_message_id,
      reply_preview: message.reply_preview,
      client_message_id: message.client_message_id || undefined
    });
  }, [sendPayload]);

  const react = useCallback(async (message: MessengerMessage, reactionType = "pulse") => {
    if (message.id <= 0) {
      setStatusMessage(t("messaging:chat.reactPending"));
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
      setStatusMessage(reactionError instanceof Error ? reactionError.message : t("messaging:chat.reactionFailed"));
    }
  }, [t]);

  const dropGalleryMedia = mediaGallery.dropMessage;
  const removeMessage = useCallback(async (message: MessengerMessage, scope: "self" | "everyone" = "self") => {
    if (message.id <= 0) {
      setMessages((current) => current.filter((item) => item.id !== message.id));
      setStatusMessage(t("messaging:chat.pendingRemoved"));
      return;
    }
    try {
      await deleteMessage(message.id, scope);
      // The viewer may be open on exactly this photo. Dropping it from the
      // collection lands the viewer on the next item, or closes it when there is
      // no next item — rather than leaving a black frame over a deleted file.
      dropGalleryMedia(message.id);
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? { ...item, body: "", deleted_at: new Date().toISOString(), delivery_status: "deleted", message_type: "system" }
            : item
        )
      );
      setStatusMessage(scope === "everyone" ? t("messaging:chat.deleteRequested") : t("messaging:chat.deleteHidden"));
    } catch (deleteError) {
      setStatusMessage(deleteError instanceof Error ? deleteError.message : t("messaging:chat.deleteFailed"));
    }
  }, [dropGalleryMedia, t]);

  const report = useCallback(async (message: MessengerMessage) => {
    if (message.id <= 0) {
      setStatusMessage(t("messaging:chat.reportPending"));
      return;
    }
    try {
      const result = await reportMessage(message.id, "Reported from the PulseSoc app");
      setStatusMessage(result.message || "Message report sent to Trust & Safety.");
    } catch (reportError) {
      setStatusMessage(reportError instanceof Error ? reportError.message : t("messaging:chat.reportFailed"));
    }
  }, [t]);

  const uploadAndSend = useCallback(async (input: { uri: string; name: string; mimeType: string; sizeBytes?: number; voice?: boolean; durationSeconds?: number }) => {
    if (assistantConversation) {
      setStatusMessage("UNDX can chat in text for now. Attachments work in chats with people, but not yet with UNDX.");
      return;
    }
    if (uploading) return;
    setUploading(true);
    setStatusMessage(input.voice ? t("messaging:chat.sendingVoice") : t("messaging:chat.uploadingAttachment"));
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
        throw new Error(t("messaging:chat.attachmentNotDurable"));
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
            ? t("messaging:chat.voiceQueued")
            : t("messaging:chat.attachmentQueued")
          : input.voice
            ? t("messaging:chat.voiceSent")
            : t("messaging:chat.attachmentSent")
      );
    } catch (uploadError) {
      const message = uploadError instanceof Error ? uploadError.message : t("messaging:chat.attachmentSendFailed");
      setStatusMessage(message);
      Alert.alert(input.voice ? t("messaging:chat.voiceFailedTitle") : t("messaging:chat.attachmentFailedTitle"), message);
    } finally {
      setUploading(false);
      reportPresenceActivity("idle", "").catch(() => undefined);
    }
  }, [assistantConversation, conversationId, sendPayload, t, uploading]);

  const attachImage = useCallback(async () => {
    try {
      const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!permission.granted) {
        Alert.alert(t("messaging:chat.photosUnavailableTitle"), t("messaging:chat.photosUnavailableBody"));
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
      const message = imageError instanceof Error ? imageError.message : t("messaging:chat.imagePickerFailed");
      setStatusMessage(message);
      Alert.alert(t("messaging:chat.imageSharingUnavailableTitle"), message);
    }
  }, [t, uploadAndSend]);

  const attachVideo = useCallback(async () => {
    try {
      const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!permission.granted) {
        Alert.alert(t("messaging:chat.videosUnavailableTitle"), t("messaging:chat.videosUnavailableBody"));
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Videos,
        allowsEditing: false,
        videoMaxDuration: maxDurationSeconds("messenger"),
        videoQuality: ImagePicker.UIImagePickerControllerQualityType.Medium
      });
      if (result.canceled || !result.assets[0]) return;
      const asset = result.assets[0];
      // expo-image-picker reports duration in milliseconds. Sending it lets the
      // server refuse an overlong clip during init, before the bytes move.
      const durationSeconds = asset.duration ? asset.duration / 1000 : undefined;
      if (exceedsLimit("messenger", durationSeconds)) {
        const message = limitMessage("messenger");
        setStatusMessage(message);
        Alert.alert(t("messaging:chat.videoSharingUnavailableTitle"), message);
        return;
      }
      await uploadAndSend({
        uri: asset.uri,
        name: asset.fileName || `pulsesoc-video-${Date.now()}.mov`,
        mimeType: asset.mimeType || "video/quicktime",
        sizeBytes: asset.fileSize || 0,
        durationSeconds
      });
    } catch (videoError) {
      const message = videoError instanceof Error ? videoError.message : t("messaging:chat.videoPickerFailed");
      setStatusMessage(message);
      Alert.alert(t("messaging:chat.videoSharingUnavailableTitle"), message);
    }
  }, [t, uploadAndSend]);

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
      const message = fileError instanceof Error ? fileError.message : t("messaging:chat.filePickerFailed");
      setStatusMessage(message);
      Alert.alert(t("messaging:chat.fileSharingUnavailableTitle"), message);
    }
  }, [t, uploadAndSend]);

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
        if (!uri) throw new Error(t("messaging:chat.recordingNoFile"));
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
        Alert.alert(t("messaging:chat.microphoneUnavailableTitle"), t("messaging:chat.microphoneUnavailableBody"));
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
      setStatusMessage(t("messaging:chat.recordingStarted"));
      reportPresenceActivity("recording_voice", String(conversationId)).catch(() => undefined);
    } catch (recordingError) {
      setRecording(null);
      reportPresenceActivity("idle", "").catch(() => undefined);
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
      const message = recordingError instanceof Error ? recordingError.message : t("messaging:chat.recordingFailed");
      setStatusMessage(message);
      Alert.alert(t("messaging:chat.voiceUnavailableTitle"), message);
    }
  }, [recording, recordingStartedAt, t, uploadAndSend]);

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
      setStatusMessage(t("messaging:chat.recordingDiscarded"));
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(() => undefined);
    }
  }, [recording, t]);

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
    <ConversationGalleryProvider gallery={mediaGallery}>
    <View style={styles.root}>
      <LogiNexusScreenShell bottomDock={false} contentStyle={styles.shellContent}>
      {/* The atmosphere is the first paint layer. Keeping it after the header
          lets its opaque deep-space gradient cover the identity and call
          controls even though it cannot receive touches. */}
      <GalacticAtmosphere variant="messages" testID="messages-galactic-atmosphere" />
      <View style={[styles.header, { paddingTop: Math.max(insets.top, 10) }]}>
        <View style={styles.threadHeader}>
          <Pressable accessibilityRole="button" accessibilityLabel={undxReturn ? `Back to ${undxReturn.params.name || undxReturn.params.symbol}` : "Back to conversations"} style={styles.backButton} onPress={goBackFromChat}><Text style={styles.backButtonText}>‹</Text></Pressable>
          <PulseCommandAvatar label={assistantConversation ? PULSE_AI_DISPLAY_NAME : route.params.title || "Chat"} imageUrl={assistantConversation ? undefined : route.params.avatarUrl} active={assistantConversation || peerIsOnline} size={48} tone={assistantConversation ? "intelligence" : "default"} />
          <View style={styles.threadIdentity}>
            <Text style={styles.threadTitle} numberOfLines={1}>{threadTitle}</Text>
            <View style={styles.threadStatusRow}><LiveStatusDot warning={Boolean(error)} /><Text style={styles.threadSubtitle} numberOfLines={1}>{headerSubtitle}</Text></View>
          </View>
          <View style={styles.callActions}>
            {!assistantConversation ? <SignalIconButton accessibilityLabel={t("messaging:chat.a11yStartAudioCall")} icon="call-outline" onPress={() => navigation.navigate("Call", { conversationId, callType: "audio", direction: "outgoing", title: threadTitle })} /> : null}
            {!assistantConversation ? <SignalIconButton accessibilityLabel={t("messaging:chat.a11yStartVideoCall")} icon="videocam-outline" tone="intelligence" onPress={() => navigation.navigate("Call", { conversationId, callType: "video", direction: "outgoing", title: threadTitle })} /> : null}
            <SignalIconButton accessibilityLabel={t("messaging:chat.a11yOpenControls")} icon="ellipsis-vertical" onPress={() => setControlCenterOpen(true)} />
          </View>
        </View>
      </View>
      {error && hasMessages ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Retry loading messages" style={styles.errorBanner} onPress={() => retryLoad()}>
          <Text style={styles.error}>{error}</Text>
        </Pressable>
      ) : null}
      {showInitialLoading ? (
        <LogiNexusStatePanel state="loading" title={t("messaging:chat.openingTitle")} body={t("messaging:chat.openingBody")} loading style={styles.loadingPanel} />
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
          ListFooterComponent={loadingOlder ? <Text style={styles.loadingOlder}>{t("messaging:chat.loadingOlder")}</Text> : null}
          ListEmptyComponent={showEmptyConversation ? <LogiNexusStatePanel state="empty" title={assistantConversation ? t("messaging:chat.undxReadyTitle") : t("errors:empty.messages")} body={assistantConversation ? t("messaging:chat.undxReadyBody") : t("messaging:chat.emptyThreadBody")} style={styles.emptyMessages} /> : null}
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
          <View style={styles.composerMetaIdentity}><LiveStatusDot warning={Boolean(error)} /><Text style={styles.composerKicker}>{t("messaging:chat.composerKicker")}</Text></View>
          <Text style={[styles.composerState, showVoiceCapture && styles.composerStateRecording]}>{showVoiceCapture ? t("messaging:chat.stateRecording") : uploading ? t("messaging:chat.stateSendingMedia") : error ? t("messaging:chat.stateReconnecting") : assistantConversation ? t("messaging:chat.stateUndxReady") : t("messaging:chat.stateSecureReady")}</Text>
        </View>
        {assistantConversation && marketChip ? (
          <View style={styles.marketContextChip}>
            {/* Read aloud with a comma rather than the interpunct, which most
                screen readers announce as "middle dot" or skip entirely — the
                separator is a visual device and the name and ticker are the
                content. `numberOfLines` truncates the sighted label on a long
                name; the spoken one is never truncated. */}
            <Text
              accessibilityLabel={`Discussing ${marketChip.asset.name}, ${marketChip.asset.symbol}`}
              style={styles.marketContextChipText}
              numberOfLines={1}
            >
              Discussing {marketChip.asset.name} · {marketChip.asset.symbol}
            </Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Stop discussing ${marketChip.asset.name}`}
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
          <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yDismissStatus")} style={styles.statusBanner} onPress={() => setStatusMessage("")}>
            <Text style={styles.statusBannerText}>{statusMessage}</Text>
          </Pressable>
        ) : null}
        {replyTo ? (
          <View style={styles.replyComposer}>
            <View style={styles.replyCopy}>
              <Text style={styles.replyTitle}>{t("messaging:chat.replyingTo", { name: replyTo.is_mine ? t("messaging:chat.yourMessage") : replyTo.sender_display_name || t("messaging:chat.unknownSender") })}</Text>
              <Text style={styles.replyPreview} numberOfLines={1}>{messagePreview(replyTo)}</Text>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yCancelReply")} style={styles.replyCancel} onPress={() => setReplyTo(null)}>
              <Text style={styles.replyCancelText}>{t("common:actions.cancel")}</Text>
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
            placeholder={assistantConversation ? t("messaging:chat.composerPlaceholderUndx") : t("messaging:chat.composerPlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={draft}
            onChangeText={notifyTyping}
            accessibilityLabel={assistantConversation ? t("messaging:chat.a11yComposerUndx") : t("messaging:chat.a11yComposer")}
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
          navigation.navigate("SafetyHub", { section: "blocks", title: t("common:screens.safetyHub") });
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
        title={assistantConversation ? PULSE_AI_DISPLAY_NAME : route.params.title || t("messaging:chat.defaultConversationTitle")}
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
          navigation.navigate("SafetyHub", { section, title: section === "reports" ? t("messaging:chat.reportConversationTitle") : t("messaging:chat.blockedUsersTitle") });
        }}
      />
      </LogiNexusScreenShell>
      {/* One viewer for the whole conversation, outside the shell so it is not
          affected by the list's layout, and mounted unconditionally so that
          opening it is a state change rather than a screen push — requirement
          §6: swiping between photos must never push a new screen. */}
      <ConversationMediaGalleryViewer gallery={mediaGallery} online={!usingCachedMessages} />
    </View>
    </ConversationGalleryProvider>
  );
}

function VoiceCaptureDock({ elapsed, levels, disabled, onCancel, onSend }: { elapsed: number; levels: number[]; disabled: boolean; onCancel: () => void; onSend: () => void }) {
  const { t } = useTranslation();
  return (
    <View accessibilityLabel={t("messaging:chat.a11yRecordingVoice", { duration: formatDuration(elapsed) })} style={styles.voiceCaptureDock}>
      <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yDiscardRecording")} disabled={disabled} style={({ pressed }) => [styles.voiceCaptureCancel, pressed && styles.pressed]} onPress={onCancel}>
        <Ionicons name="trash-outline" size={20} color="#ff6685" />
      </Pressable>
      <View style={styles.voiceCaptureBody}>
        <View style={styles.voiceCaptureHeader}>
          <View style={styles.voiceCaptureLive}><View style={styles.voiceCaptureLiveDot} /><Text style={styles.voiceCaptureKicker}>{t("messaging:chat.liveVoiceKicker")}</Text></View>
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
      <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yStopSendVoice")} disabled={disabled} style={({ pressed }) => [styles.voiceCaptureSend, pressed && styles.pressed, disabled && styles.disabled]} onPress={onSend}>
        <Ionicons name="send" size={21} color="#03120f" />
      </Pressable>
    </View>
  );
}

function AttachmentActionSheet({ visible, recording, onClose, onImage, onVideo, onCamera, onFile, onVoice }: { visible: boolean; recording: boolean; onClose: () => void; onImage: () => void; onVideo: () => void; onCamera: () => void; onFile: () => void; onVoice: () => void }) {
  const { t } = useTranslation();
  return (
    <Modal transparent animationType="slide" visible={visible} onRequestClose={onClose}>
      <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yCloseAttachmentSheet")} style={styles.sheetBackdrop} onPress={onClose}>
        <PulseCommandPanel style={styles.attachmentSheet}>
          <View style={styles.sheetHandle} />
          <Text style={styles.sheetTitle}>{t("messaging:chat.attachmentSheetTitle")}</Text>
          <Text style={styles.sheetPreview}>{t("messaging:chat.attachmentSheetBody")}</Text>
          <View style={styles.sheetGrid}>
            <MediaSheetAction icon="images-outline" label={t("messaging:chat.attachPhoto")} detail={t("messaging:chat.attachPhotoDetail")} onPress={onImage} />
            <MediaSheetAction icon="videocam-outline" label={t("messaging:chat.attachVideo")} detail={t("messaging:chat.attachVideoDetail")} tone="intelligence" onPress={onVideo} />
            <MediaSheetAction icon="camera-outline" label={t("messaging:chat.attachCamera")} detail={t("messaging:chat.attachCameraDetail")} onPress={onCamera} />
            <MediaSheetAction icon="document-text-outline" label={t("messaging:chat.attachDocument")} detail={t("messaging:chat.attachDocumentDetail")} tone="intelligence" onPress={onFile} />
            <MediaSheetAction icon={recording ? "stop" : "mic-outline"} label={recording ? t("messaging:chat.attachStopSend") : t("messaging:chat.attachVoiceNote")} detail={recording ? t("messaging:chat.attachRecordingNow") : t("messaging:chat.attachVoiceNoteDetail")} tone={recording ? "danger" : "signal"} onPress={onVoice} />
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
  const { t } = useTranslation();
  const mine = Boolean(message.is_mine);
  const status = message.local_status || message.delivery_status || "sent";
  const deleted = Boolean(message.deleted_at || status === "deleted");
  const moderated = Boolean(message.moderated_at || message.moderation_state);
  const body = deleted ? t("messaging:chat.deletedMessageBody") : moderated ? t("messaging:chat.moderatedMessageBody") : displayMessageBody(message);
  const voiceMessage = isVoiceLikeMessage(message);
  /**
   * A photo or video sent with no caption gets a slimmer bubble.
   *
   * The 12/10 padding exists so a sentence is not pressed against a rounded
   * edge. A picture is not a sentence: the same padding draws a visible frame of
   * bubble colour around the image on all four sides, and with the media card
   * also carrying its own radius the result is a rounded rectangle inside a
   * rounded rectangle — §2, exactly. Caption messages keep the text padding,
   * because there the padding is doing its job.
   */
  const mediaOnly = !deleted && !moderated && !body && isVisualMediaMessage(message);
  return (
    <View style={[styles.bubbleWrap, mine ? styles.mineWrap : styles.theirWrap]} accessible={!voiceMessage} accessibilityLabel={messageAccessibilityLabel(message)}>
      <Pressable onLongPress={onLongPress} style={[styles.bubble, mine ? styles.mineBubble : styles.theirBubble, mediaOnly && styles.mediaBubble, moderated && styles.moderatedBubble]}>
        {!mine ? <Text style={styles.senderLabel}>{message.sender_display_name || (message.sender_trust_state === "intelligence" ? "UNDX" : t("common:identity.member"))}</Text> : null}
        {message.reply_preview ? (
          <View style={styles.replyBlock}>
            <Text style={styles.replyTitle}>{t("common:actions.reply")}</Text>
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
        {message.forwarded ? <Text style={styles.forwarded}>{t("messaging:chat.forwardedSignal")}</Text> : null}
        <View style={styles.metaRow}>
          <Text style={styles.meta}>{formatShortTime(message.created_at)}</Text>
          {message.edited_at ? <Text style={styles.meta}>{t("messaging:chat.editedLabel")}</Text> : null}
          {mine ? <Text style={styles.meta}>{messageDeliveryLabel(status, message.seen_at)}</Text> : null}
        </View>
        <ReactionRow reactions={message.reactions} viewerReaction={message.viewer_reaction} onReact={onReact} />
        {status === "failed" ? (
          <Pressable style={styles.retry} onPress={onRetry}>
            <Text style={styles.retryText}>{t("messaging:chat.retryFailedSend")}</Text>
          </Pressable>
        ) : null}
      </Pressable>
    </View>
  );
}

function ReactionRow({ reactions, viewerReaction, onReact }: { reactions?: Record<string, number>; viewerReaction?: string; onReact: () => void }) {
  const { t } = useTranslation();
  const entries = Object.entries(reactions || {}).filter(([, count]) => Number(count || 0) > 0).slice(0, 4);
  if (!entries.length && !viewerReaction) return null;
  return (
    <View style={styles.reactionRow}>
      {entries.map(([reaction, count]) => (
        <Pressable key={reaction} accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yReact", { reaction })} style={[styles.reactionPill, viewerReaction === reaction && styles.reactionActive]} onPress={onReact}>
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
  const { t } = useTranslation();
  if (!message) return null;
  const actions = messageActionRules(message);
  const canReact = actions.find((action) => action.key === "react")?.available;
  const actionIsAvailable = (key: ReturnType<typeof messageActionRules>[number]["key"]) => actions.find((action) => action.key === key)?.available;
  return (
    <Modal transparent animationType="fade" visible onRequestClose={onClose}>
      <Pressable style={styles.sheetBackdrop} onPress={onClose}>
        <PulseCommandPanel style={styles.sheet}>
          <Text style={styles.sheetTitle}>{t("messaging:chat.messageControlsTitle")}</Text>
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
            {actionIsAvailable("reply") ? <SheetAction label={t("common:actions.reply")} onPress={() => onReply(message)} /> : null}
            {actionIsAvailable("retry") ? <SheetAction label={t("messaging:chat.retry")} tone="warning" onPress={() => onRetry(message)} /> : null}
            {actionIsAvailable("report") ? <SheetAction label={t("common:actions.report")} tone="warning" onPress={() => onReport(message)} /> : null}
            {actionIsAvailable("safety") ? <SheetAction label={t("messaging:chat.muteBlock")} tone="safety" onPress={onSafety} /> : null}
            {actionIsAvailable("deleteSelf") ? <SheetAction label={t("messaging:chat.deleteForMe")} tone="danger" onPress={() => onDelete(message, "self")} /> : null}
            {actionIsAvailable("deleteEveryone") ? <SheetAction label={t("messaging:chat.deleteForEveryone")} tone="danger" onPress={() => onDelete(message, "everyone")} /> : null}
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

/**
 * Media card geometry.
 *
 * A share of the window, not a pixel count, so the card is proportionate on a
 * phone and on a tablet. `MEDIA_MAX_HEIGHT` is what stops a tall portrait clip
 * from filling the thread; past that bound the card crops and the viewer shows
 * the whole frame.
 */
const MEDIA_WIDTH_RATIO = 0.78;
const MEDIA_MAX_WIDTH = 420;
const MEDIA_MAX_HEIGHT = 380;
/** Portrait 9:16 through landscape 16:9, the range real camera media lives in. */
const MEDIA_MIN_RATIO = 0.5625;
const MEDIA_MAX_RATIO = 1.7778;
/** Used only when the grant reported no dimensions at all. */
const MEDIA_DEFAULT_RATIO = 1.25;

function MessageMedia({ message }: { message: MessengerMessage }) {
  const { t } = useTranslation();
  const gallery = useConversationGallery();
  const type = (message.message_type || "text").toLowerCase();
  // The message carries media identity; the renderer gets a short-lived access
  // URL for it. Handing the platform image loader a protected API path is what
  // made image loads run session refresh on the server and sign people out; see
  // media/messengerMediaAccess.
  //
  // The identity is handed over labelled rather than as a bare integer. A
  // Comm-v2 message carries two different ids for one picture, and only
  // media_upload_id (the foundation message_attachments row) addresses the
  // access endpoint. attachment_id is passed for completeness and is
  // deliberately NOT marked as proven foundation media.
  //
  // ONE grant per bubble, carrying both the original and its preview. This used
  // to be two calls to the same hook with the same identity, which resolved to
  // the same attachment id and therefore returned the same `/download` URL
  // twice: the thumbnail slot was handed the full asset, so a video bubble fed
  // an entire movie to `<Image>` and a thread of photos downloaded every
  // original at full size to paint cards a few hundred pixels wide.
  const mediaIdentity = { mediaUploadId: message.media_upload_id, attachmentId: message.attachment_id };
  const mediaAccess = useMessengerMediaAccessUrl(mediaIdentity, String(message.media_url || ""));
  // One bounded re-grant when the platform loader rejects a URL we handed it —
  // an expired grant is the ordinary cause. Single-shot per identity.
  const retryMedia = mediaAccess.retry;
  const mediaUrl = absoluteMediaUrl(mediaAccess.url);
  const thumbnailUrl = absoluteMediaUrl(mediaAccess.thumbnailUrl);
  /**
   * §21. The message's own attachments, split back into tiles.
   *
   * `firstAttachment` in api/messenger.ts flattens a message down to its first
   * attachment, which is lossless for anything the mobile composer sends — it
   * sends exactly one — and quietly drops media for anything the web composer
   * sends, which may send several. A three-photo message rendered as one photo
   * with the other two reachable from nowhere.
   *
   * Memoised because it feeds a child component's props; the function itself is
   * pure and cheap, but a fresh array every render re-renders the whole grid.
   */
  const mediaTiles = useMemo(
    () => messageMediaTiles(Number(message.id || message.message_id || 0), message.attachments),
    [message.attachments, message.id, message.message_id]
  );
  /**
   * The grid decision is made before the single-media grant is consulted at all.
   *
   * Every branch below this point reads `mediaUrl`, which is the grant for
   * attachment *one*. A multi-photo message must not be gated on it: each tile
   * carries its own identity and fetches its own grant, so a message whose first
   * photo failed still renders the other two.
   */
  if (isMultiMediaMessage(mediaTiles)) {
    return <MessageMediaGrid message={message} tiles={mediaTiles} />;
  }
  if ((type === "image" || type === "gif") && mediaAccess.failed && !mediaUrl) {
    return (
      <View accessible accessibilityRole="text" accessibilityLabel={`${messageAccessibilityLabel(message)}. Image unavailable.`} style={styles.voiceUnavailable}>
        <Ionicons name="alert-circle-outline" size={18} color={colors.danger} />
        <Text style={styles.voiceUnavailableText}>Image unavailable</Text>
      </View>
    );
  }
  if (isVoiceType(type) && !mediaUrl) {
    return (
      <View accessible accessibilityRole="text" accessibilityLabel={t("messaging:chat.a11yVoiceUnavailable", { label: messageAccessibilityLabel(message) })} style={styles.voiceUnavailable}>
        <Ionicons name="alert-circle-outline" size={18} color={colors.danger} />
        <Text style={styles.voiceUnavailableText}>{t("messaging:chat.voiceUnavailable")}</Text>
      </View>
    );
  }
  if (!mediaUrl) return null;
  /**
   * Open the conversation gallery *on this item*.
   *
   * The seed is everything this bubble knows: which photo it is, and the URL it
   * already decoded. The host puts that seed into the real collection and pages
   * the rest of the conversation in around it, which is why the viewer opens on
   * the tapped photo instantly and still ends up holding all 43.
   *
   * With no host — a preview, a harness — there is nothing to open, and a photo
   * that does not expand is a better outcome than a crash.
   */
  function openInGallery() {
    gallery?.open(gallerySeedFromMessage({
      messageId: Number(message.id || message.message_id || 0),
      attachmentId: Number(message.attachment_id || message.media_upload_id || message.id || 0),
      mediaUploadId: Number(message.media_upload_id || 0),
      kind: type === "video" ? "video" : "image",
      url: mediaUrl,
      thumbnailUrl,
      mimeType: String(message.mime_type || ""),
      width: mediaAccess.meta.width,
      height: mediaAccess.meta.height,
      durationSeconds: Number(message.duration_seconds || message.duration || 0),
      senderId: Number(message.sender_user_id || message.sender_id || 0),
      senderName: String(message.sender_display_name || ""),
      createdAt: String(message.created_at || "")
    }));
  }
  if (type === "image" || type === "gif") {
    const photoPreviewUrl = thumbnailUrl || (isPreviewTerminal(mediaAccess.meta.processingStatus) ? mediaUrl : "");
    return (
      <>
        <Pressable
          accessibilityRole="imagebutton"
          accessibilityLabel={type === "gif"
            ? t("messaging:chat.a11yGifAttachment", { label: messageAccessibilityLabel(message) })
            : t("messaging:chat.a11yImageAttachment", { label: messageAccessibilityLabel(message) })}
          accessibilityHint={t("messaging:chat.a11yOpensViewer")}
          onPress={openInGallery}
        >
          {/* The derived rendition first, always. A photo may fall back to the
              original because its size is bounded by the photo limit and the
              viewer is about to need those bytes anyway — but only once the
              rendition is known not to be coming, otherwise every bubble in a
              thread pulls a full-resolution image down to paint a card a few
              hundred points wide. Video deliberately does not fall back at all:
              there is no bound worth falling back through. */}
          <MediaSurface meta={mediaAccess.meta} message={message}>
            {photoPreviewUrl ? (
              <MediaPreviewImage
                uri={photoPreviewUrl}
                fallbackUri={isPreviewTerminal(mediaAccess.meta.processingStatus) ? mediaUrl : ""}
                onRetry={retryMedia}
              />
            ) : (
              <View style={[styles.mediaFill, styles.mediaPlaceholder, styles.mediaSkeleton]}>
                <ActivityIndicator color={colors.muted} size="small" />
              </View>
            )}
          </MediaSurface>
        </Pressable>
      </>
    );
  }
  if (isVoiceType(type)) {
    return <VoiceMessageCard message={message} url={mediaUrl} />;
  }
  if (type === "video") {
    return <VideoMessageCard message={message} access={mediaAccess} onOpen={openInGallery} />;
  }
  return <DocumentAttachmentCard message={message} url={mediaUrl} />;
}

/** Gap between tiles. Small enough that the grid reads as one object. */
const MEDIA_TILE_GAP = 3;

/**
 * §21: several photos in one message, as a grid of individually tappable tiles.
 *
 * The grid is laid out at the same bubble width as a single photo so a thread
 * containing both does not visibly change column. Each tile is square — a grid
 * of mixed aspect ratios reads as a broken layout rather than a deliberate one,
 * and the true ratio is one tap away in the viewer.
 *
 * What makes this more than a layout: every tile opens the *conversation*
 * gallery seeded with its own item. Tile 3 therefore lands on the third photo
 * of the message wherever that photo sits among the conversation's 43, because
 * the tile and the collection entry are the same object with the same key.
 */
function MessageMediaGrid({ message, tiles }: { message: MessengerMessage; tiles: ConversationMediaItem[] }) {
  const { width: windowWidth } = useWindowDimensions();
  const gallery = useConversationGallery();
  const columns = mediaTileColumns(tiles.length);
  const gridWidth = Math.min(MEDIA_MAX_WIDTH, Math.round(windowWidth * MEDIA_WIDTH_RATIO));
  const tileSize = Math.floor((gridWidth - MEDIA_TILE_GAP * (columns - 1)) / columns);
  return (
    <View style={[styles.mediaGrid, { width: gridWidth }]}>
      {tiles.map((tile, position) => (
        <MediaGridTile
          key={tile.key}
          message={message}
          tile={tile}
          size={tileSize}
          position={position + 1}
          total={tiles.length}
          // The tile hands its *granted* identity up rather than the grid
          // reaching for `tile` directly: `tile.url` is the protected API path
          // off the attachment payload, and the gallery shows a seeded URL until
          // its own resolve lands (`grant?.url || item.url` in the host). Seeding
          // the raw path would both lose the instant open and hand the platform
          // image loader a protected path — the thing that made image loads run
          // session refresh on the server.
          onOpen={(granted) => gallery?.open(gallerySeedFromMessage(granted))}
        />
      ))}
    </View>
  );
}

/**
 * One tile.
 *
 * This is a component rather than a loop body because it needs a hook per tile —
 * `useMessengerMediaAccessUrl` grants a short-lived URL for one identity, and
 * hooks cannot be called in a loop over a variable-length array. A tile failing
 * to load is therefore local to that tile: a three-photo message with one dead
 * object shows two photos and one legible failure, not an empty bubble.
 *
 * The preview is the *thumbnail*, and falls back to the original only once the
 * rendition is known not to be coming. Three tiles pulling three full-resolution
 * originals to paint squares a hundred points wide is the same mistake the
 * single-photo card already learned not to make, multiplied.
 */
function MediaGridTile({
  message,
  tile,
  size,
  position,
  total,
  onOpen
}: {
  message: MessengerMessage;
  tile: ConversationMediaItem;
  size: number;
  position: number;
  total: number;
  onOpen: (granted: ConversationMediaItem) => void;
}) {
  const { t } = useTranslation();
  const access = useMessengerMediaAccessUrl(
    { mediaUploadId: tile.mediaUploadId, attachmentId: tile.attachmentId },
    tile.url
  );
  const url = absoluteMediaUrl(access.url);
  const thumbnail = absoluteMediaUrl(access.thumbnailUrl);
  const previewUrl = thumbnail || (isPreviewTerminal(access.meta.processingStatus) ? url : "");
  const label = tile.kind === "video"
    ? t("messaging:chat.a11yGridVideoTile", { position, total, label: messageAccessibilityLabel(message) })
    : t("messaging:chat.a11yGridPhotoTile", { position, total, label: messageAccessibilityLabel(message) });
  return (
    <Pressable
      accessibilityRole="imagebutton"
      accessibilityLabel={label}
      accessibilityHint={t("messaging:chat.a11yOpensViewer")}
      // The key is deliberately left alone: it is what makes this tile and the
      // same photo in the server-paged collection one entry rather than two.
      onPress={() => onOpen({ ...tile, url, thumbnailUrl: thumbnail })}
      style={[styles.mediaTile, { height: size, width: size }]}
    >
      {previewUrl ? (
        <MediaPreviewImage
          uri={previewUrl}
          fallbackUri={isPreviewTerminal(access.meta.processingStatus) ? url : ""}
          onRetry={access.retry}
        />
      ) : (
        <View style={[styles.mediaFill, styles.mediaPlaceholder, styles.mediaSkeleton]}>
          <ActivityIndicator color={colors.muted} size="small" />
        </View>
      )}
      {/* A video tile has to say it is a video before it is opened — a poster
          frame alone is indistinguishable from a photo. The badge is scaled
          down from the single-card one so it does not swallow a small tile. */}
      {tile.kind === "video" ? (
        <View pointerEvents="none" style={styles.mediaTilePlayBadge}>
          <Ionicons name="play" size={14} color="#04110c" />
        </View>
      ) : null}
    </Pressable>
  );
}

/**
 * The bubble-width media frame every photo and video poster is drawn into.
 *
 * Width is a share of the window rather than the 200-220pt constants this
 * replaced, which made a photo read as a chip regardless of screen size. Height
 * comes from the media's own dimensions when the grant reported them, so a
 * portrait video stops being letterboxed into a 1.6 landscape box — bounded,
 * because one tall photo must not take the whole thread.
 *
 * A square placeholder is NOT a neutral default: it reflows the row the moment
 * the real ratio arrives. Absent dimensions keep the previous card shape.
 */
function MediaSurface({ meta, message, children }: { meta: MessengerMediaMeta; message: MessengerMessage; children: React.ReactNode }) {
  const { width: windowWidth } = useWindowDimensions();
  const cardWidth = Math.min(MEDIA_MAX_WIDTH, Math.round(windowWidth * MEDIA_WIDTH_RATIO));
  const declaredWidth = meta.width;
  const declaredHeight = meta.height;
  const ratio = declaredWidth > 0 && declaredHeight > 0
    ? clamp(declaredWidth / declaredHeight, MEDIA_MIN_RATIO, MEDIA_MAX_RATIO)
    : MEDIA_DEFAULT_RATIO;
  const height = Math.min(Math.round(cardWidth / ratio), MEDIA_MAX_HEIGHT);
  return <View style={[styles.mediaSurface, { width: cardWidth, height }]}>{children}</View>;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

/**
 * The bitmap inside a media frame, with the two states a frame is allowed to be
 * in while it has no bitmap.
 *
 * Handing `<Image>` a URL and drawing nothing else is what made a photo read as
 * a blank card: the frame is sized from the grant's dimensions and painted
 * immediately, so between layout and the first byte there is a correctly-shaped
 * rectangle of surface colour with nothing in it, and a photo that fails to
 * decode leaves that rectangle up forever. Neither state says anything, so both
 * look like the same bug.
 *
 * So a load in flight gets a skeleton, and a load that ended badly gets a
 * legible failure with a retry on it. `onError` re-grants once through the
 * access layer before this gives up, because an expired signature is the
 * ordinary cause and it is invisible from here.
 */
/**
 * `fallbackUri` covers the case the dispatch above cannot see: a rendition that
 * the record says exists and does not.
 *
 * The caller only falls back to the original when `thumbnailUrl` is *absent*.
 * But an attachment can sit at processing_status='ready' with a thumbnail_key
 * pointing at an object that was never written — the row claims a preview, the
 * access endpoint signs a perfectly valid URL for it, and the object 404s. That
 * is indistinguishable from a good preview until the image actually fails to
 * load, so the recovery has to live here, at the point of failure, rather than
 * in the branch that picks the URL. Falling through to the full-size photo is
 * the same trade the dispatch already makes when the rendition is known to be
 * missing, and it is bounded by the photo upload limit.
 */
function MediaPreviewImage({ uri, fallbackUri, onRetry, onLoad }: { uri: string; fallbackUri?: string; onRetry?: () => void; onLoad?: () => void }) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<"loading" | "ready" | "failed">("loading");
  const [source, setSource] = useState(uri);
  // Keyed on the URL: a re-grant hands over a new signature for the same
  // picture, and leaving the previous attempt's `failed` up would make the
  // retry look like it did nothing.
  useEffect(() => { setSource(uri); setPhase(uri ? "loading" : "failed"); }, [uri]);
  const handleError = useCallback(() => {
    // One step down, and only ever one: the fallback is a different object, so
    // if it fails too there is nothing further to try and the card must say so.
    if (fallbackUri && fallbackUri !== source) {
      setSource(fallbackUri);
      setPhase("loading");
      return;
    }
    setPhase("failed");
  }, [fallbackUri, source]);
  const retry = useCallback(() => {
    setSource(uri);
    setPhase("loading");
    onRetry?.();
  }, [onRetry, uri]);
  if (phase === "failed") {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${t("messaging:chat.videoPosterFailed")}. ${t("messaging:chat.tapToRetry")}`}
        style={[styles.mediaFill, styles.mediaPlaceholder, styles.mediaFailed]}
        onPress={retry}
      >
        <Ionicons name="alert-circle-outline" size={22} color={colors.danger} />
        <Text style={styles.mediaFailedTitle}>{t("messaging:chat.videoPosterFailed")}</Text>
        <Text style={styles.mediaFailedHint}>{t("messaging:chat.tapToRetry")}</Text>
      </Pressable>
    );
  }
  return (
    <>
      <Image
        source={{ uri: source }}
        style={styles.mediaFill}
        resizeMode="cover"
        onLoad={() => { setPhase("ready"); onLoad?.(); }}
        onError={handleError}
      />
      {phase === "loading" ? (
        <View pointerEvents="none" style={[styles.mediaFill, styles.mediaPlaceholder, styles.mediaSkeleton]}>
          <ActivityIndicator color={colors.muted} size="small" />
        </View>
      ) : null}
    </>
  );
}

/**
 * Whether a preview that is absent is absent for good.
 *
 * A photo falls back to its original only here. While the derived rendition is
 * still coming the frame shows a skeleton instead, so the ordinary path never
 * pulls a full-resolution image down to paint a card a few hundred points wide.
 */
function isPreviewTerminal(status: string) {
  const value = String(status || "").toLowerCase();
  return !isPosterPending(value);
}

/**
 * A video message, as a poster with a play control over it.
 *
 * What this replaced drew the words "Video attachment" and "Open viewer" over a
 * 200pt box and put the generated filename in the bubble underneath, so an
 * iPhone `.MOV` arrived looking like a file attachment with a UUID for a name.
 * The poster was already being granted and was already being rendered — it was
 * just small, unlabelled, and optional, and the text stayed regardless.
 *
 * The three states below are distinguishable on purpose. A poster that has not
 * been generated yet and one that never will are both an empty `thumbnailUrl`,
 * and collapsing them is how a permanently broken card ends up claiming it is
 * still working.
 */
function VideoMessageCard({ message, access, onOpen }: {
  message: MessengerMessage;
  access: MessengerMediaAccessState;
  /** Open the conversation gallery on this video. The card no longer owns a viewer. */
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const poster = absoluteMediaUrl(access.thumbnailUrl);
  const durationMs = access.meta.durationMs || Number(message.duration_seconds || 0) * 1000;
  const duration = formatMediaDuration(durationMs);
  const processing = !poster && isPosterPending(access.meta.processingStatus);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={duration
        ? t("messaging:chat.a11yVideoAttachmentDuration", { duration: spokenDuration(durationMs) })
        : t("messaging:chat.a11yVideoAttachment")}
      accessibilityHint={t("messaging:chat.a11yOpensViewer")}
      onPress={onOpen}
    >
      <MediaSurface meta={access.meta} message={message}>
        {poster ? (
          <MediaPreviewImage uri={poster} onRetry={access.retry} />
        ) : processing ? (
          // Still being cut. A skeleton, not a dark block: the frame has to read
          // as "coming" rather than as the finished article.
          <View style={[styles.mediaFill, styles.mediaPlaceholder, styles.mediaSkeleton]}>
            <ActivityIndicator color={colors.muted} size="small" />
          </View>
        ) : (
          // No poster and nothing left to wait for. Says so, and offers the one
          // re-grant that fixes the ordinary cause.
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${t("messaging:chat.videoPosterFailed")}. ${t("messaging:chat.tapToRetry")}`}
            style={[styles.mediaFill, styles.mediaPlaceholder, styles.mediaFailed]}
            onPress={access.retry}
          >
            <Ionicons name="alert-circle-outline" size={22} color={colors.danger} />
            <Text style={styles.mediaFailedTitle}>{t("messaging:chat.videoPosterFailed")}</Text>
            <Text style={styles.mediaFailedHint}>{t("messaging:chat.tapToRetry")}</Text>
          </Pressable>
        )}
        {/* The play affordance stays up in every state: the asset is playable
            even when its poster is not ready, so hiding it would make a
            perfectly good video look broken while a frame is being cut. */}
        <View pointerEvents="none" style={styles.videoPlayBadge}>
          <Ionicons name="play" size={24} color="#04121c" />
        </View>
        {duration ? (
          <View pointerEvents="none" style={styles.videoDurationBadge}>
            <Text style={styles.videoDurationText}>{duration}</Text>
          </View>
        ) : null}
        {processing ? (
          <View pointerEvents="none" style={styles.videoStatusBadge}>
            <Text style={styles.videoStatusText}>{t("messaging:chat.videoProcessing")}</Text>
          </View>
        ) : null}
      </MediaSurface>
    </Pressable>
  );
}

/** Statuses that mean a poster is still coming. `queued` included: the job row exists. */
function isPosterPending(status: string) {
  return ["queued", "processing"].includes(String(status || "").toLowerCase());
}

/**
 * `0:45`, `13:42`, `1:12:08` — hours only once there are hours.
 *
 * Duration is read off the attachment row, which the processing worker filled in
 * from the container itself. Nothing here measures the file, and a 90-minute
 * video formats by the same rule as a 10-second one.
 */
function formatMediaDuration(durationMs: number): string {
  const total = Math.round(Math.max(0, Number(durationMs) || 0) / 1000);
  if (total <= 0) return "";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

/** The same length, said rather than shown, for VoiceOver. */
function spokenDuration(durationMs: number): string {
  const total = Math.round(Math.max(0, Number(durationMs) || 0) / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes && seconds) return translate("messaging:chat.durationMinutesSeconds", { minutes, seconds });
  if (minutes) return translate("messaging:chat.durationMinutes", { minutes });
  return translate("messaging:chat.durationSeconds", { seconds });
}

/**
 * A document attachment that actually opens.
 *
 * The card this replaced rendered a filename and a byte count under an
 * `onPress` that evaluated to `undefined` for every non-video attachment, so a
 * PDF arrived, said "Sent", and did nothing when tapped for the life of the
 * conversation. Opening goes through the shared `openDocument` action rather
 * than a Messenger-local implementation, so the access grant, the retry policy
 * and the on-disk cache are the same ones every other surface uses.
 */
function DocumentAttachmentCard({ message, url }: { message: MessengerMessage; url: string }) {
  const { t } = useTranslation();
  const [opening, setOpening] = useState(false);
  const [failure, setFailure] = useState("");
  const filename = String(message.body || "").trim() || t("messaging:chat.fileAttachment");

  const open = useCallback(async () => {
    if (opening) return;
    setOpening(true);
    setFailure("");
    const result = await openDocument({
      url,
      mediaId: messengerMediaCacheIdentity({ mediaUploadId: message.media_upload_id, attachmentId: message.attachment_id }),
      mimeType: message.mime_type || undefined,
      expectedBytes: Number(message.file_size || 0) || undefined,
      surface: "messenger",
      title: filename
    });
    setOpening(false);
    if (result.status !== "opened") setFailure(result.message);
  }, [filename, message.attachment_id, message.file_size, message.media_upload_id, message.mime_type, opening, url]);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={filename}
      accessibilityHint={t("messaging:chat.a11yOpensDocument")}
      accessibilityState={{ busy: opening }}
      style={styles.attachment}
      onPress={open}
    >
      <Text style={styles.attachmentTitle}>{filename}</Text>
      <Text style={styles.attachmentMeta}>
        {opening
          ? t("messaging:chat.openingDocument")
          : `${formatFileSize(message.file_size)} · ${t("messaging:chat.openDocument")}`}
      </Text>
      {failure ? <Text style={styles.voiceError}>{failure}</Text> : null}
    </Pressable>
  );
}

const VoiceMessageCard = memo(function VoiceMessageCard({ message, url }: { message: MessengerMessage; url: string }) {
  const { t } = useTranslation();
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
    AccessibilityInfo.announceForAccessibility(t("messaging:chat.a11yPlaybackSpeed", { rate: next }));
  };
  return (
    <View style={styles.voiceCard}>
      <View accessible accessibilityRole="text" accessibilityLabel={messageAccessibilityLabel(message)} style={styles.voiceSemanticSummary} />
      <View style={styles.voiceControls}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={failed ? t("messaging:chat.a11yRetryVoice") : playing ? t("messaging:chat.a11yPauseVoice") : t("messaging:chat.a11yPlayVoice")}
          accessibilityState={{ busy: loading }}
          hitSlop={4}
          style={({ pressed }) => [styles.voicePlay, failed && styles.voicePlayError, pressed && styles.voicePressed]}
          onPress={() => toggle().catch(() => undefined)}
        >
          {loading ? <ActivityIndicator color="#03120f" size="small" /> : <Ionicons name={failed ? "refresh" : playing ? "pause" : "play"} size={19} color="#03120f" />}
        </Pressable>
        <Pressable
          accessibilityRole="adjustable"
          accessibilityLabel={t("messaging:chat.a11yVoiceProgress")}
          accessibilityValue={{ min: 0, max: Math.max(1, Math.round(durationMillis / 1000)), now: Math.round(snapshot.positionMillis / 1000), text: t("messaging:chat.voiceProgressValue", { position: formatDuration(snapshot.positionMillis / 1000), duration: formatDuration(durationMillis / 1000) }) }}
          accessibilityActions={[{ name: "increment", label: t("messaging:chat.a11yForwardFive") }, { name: "decrement", label: t("messaging:chat.a11yBackFive") }]}
          style={styles.voiceTimeline}
          onAccessibilityAction={(event) => seekVoicePlaybackBy(messageId, event.nativeEvent.actionName === "increment" ? 5000 : -5000).catch(() => undefined)}
          onLayout={(event) => { timelineWidth.current = Math.max(1, event.nativeEvent.layout.width); }}
          onPress={(event) => seekVoicePlayback(messageId, event.nativeEvent.locationX / timelineWidth.current).catch(() => undefined)}
        >
          {failed ? <Text numberOfLines={1} style={styles.voiceError}>{t("messaging:chat.voicePlaybackFailed")}</Text> : (
            <View style={styles.waveform}>
              {waveform.map((level, index) => (
                <View key={index} style={[styles.waveBar, index % 4 === 2 && styles.waveBarPurple, index / waveform.length <= progress ? styles.waveBarPlayed : styles.waveBarPending, { height: 7 + level * 14 }]} />
              ))}
            </View>
          )}
        </Pressable>
        <Text accessibilityLabel={t("messaging:chat.a11yVoiceDuration", { duration: formatDuration(durationMillis / 1000) })} style={styles.voiceDuration}>{formatDuration(durationMillis / 1000)}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:chat.a11yPlaybackSpeed", { rate: snapshot.rate })} hitSlop={5} style={({ pressed }) => [styles.voiceRate, pressed && styles.voicePressed]} onPress={() => changeRate().catch(() => undefined)}>
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

/**
 * Photo or video — the two things that belong in the swipeable gallery.
 *
 * Voice notes and documents are deliberately excluded (§28): a waveform player
 * and a document card are not things you can swipe onto, and they keep their own
 * chrome. This is the client half of the same classification the media-history
 * endpoint applies server-side, so the inline thread and the gallery agree on
 * what "media" means.
 */
function isVisualMediaMessage(message: MessengerMessage) {
  return ["image", "gif", "video"].includes(normalizedMessageType(message.message_type || message.type));
}

/**
 * The text of a bubble, which for an attachment the user never captioned is
 * nothing.
 *
 * Messenger sends the picked file's name as the message body (`body:
 * input.name` in the attach flow) because there is no caption field — so the
 * bubble was printing `81084427942__310C6CDB-....MOV` under the media as if the
 * user had typed it. A document's name is its content, but the card already
 * prints it as the card title, so a bubble body said it a second time.
 *
 * Only a body that reads as a filename is dropped, and the two kinds of
 * attachment need different rules for that — see the helpers below. Flows that
 * do carry a typed caption keep it, and the filename itself survives on the
 * attachment row for the card, the viewer and downloads.
 */
function displayMessageBody(message: MessengerMessage) {
  const body = message.body || "";
  if (!body) return "";
  const type = (message.message_type || "text").toLowerCase();
  // A voice note has no body, whatever the row says. The card already carries
  // everything it means — play, waveform, length, speed — so a line of text
  // above it is either the word "Voice message", which repeats the card, or the
  // recorder's generated filename, which is an implementation detail and is
  // deliberately never shown. There is no caption field on this flow, so
  // nothing a person typed can be lost here.
  if (isVoiceType(type)) return "";
  if (["image", "gif", "video"].includes(type)) return looksLikeFilename(body) ? "" : body;
  if (["file", "document"].includes(type)) return looksLikeDocumentName(body) ? "" : body;
  return body;
}

/** A bare filename: one token, no spaces, with an extension on the end. */
function looksLikeFilename(value: string) {
  return /^[^\s/]+\.[A-Za-z0-9]{2,5}$/.test(value.trim());
}

/**
 * A document name, which unlike camera media routinely has spaces in it —
 * "Deployment gear list.pdf" is a filename and the single-token rule above
 * would call it a caption. The extension on the end carries the signal: a
 * sentence someone typed does not finish in `.pdf`.
 */
function looksLikeDocumentName(value: string) {
  const trimmed = value.trim();
  return !trimmed.includes("\n") && /\.[A-Za-z0-9]{2,5}$/.test(trimmed);
}

function mediaPreviewLabel(type: string, hasMedia: boolean) {
  if (isVoiceType(type)) return translate("messaging:chat.attachVoice");
  if (type === "image" || type === "gif") return translate("messaging:chat.attachPhoto");
  if (type === "video") return translate("messaging:chat.attachVideo");
  if (type === "file" || type === "document") return translate("messaging:chat.fileAttachment");
  return hasMedia ? translate("messaging:chat.previewAttachment") : translate("messaging:chat.previewMessage");
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
    return activity ? translate("messaging:chat.presenceActivityDirect", { activity }) : translate("messaging:chat.presenceOnlineDirect");
  }
  return presence.last_seen_text || translate("messaging:chat.presenceOffline");
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
    return presenceActivityText(presence.activity) || translate("messaging:chat.presenceOnline");
  }
  return presence.last_seen_text || translate("messaging:chat.presenceOffline");
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
  mediaBubble: {
    gap: 4,
    minWidth: 0,
    paddingHorizontal: 6,
    paddingVertical: 6
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
  // No border. The card sits inside a bubble that already has an edge, and a
  // second hairline outline around the photo is the "nested rounded rectangle"
  // §2 rules out. The radius stays so the image corners follow the bubble's.
  mediaSurface: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 14,
    overflow: "hidden",
    position: "relative"
  },
  mediaFill: { height: "100%", width: "100%" },
  // §21. `gap` rather than per-tile margins so the grid's outer edge lines up
  // with a single photo's — a margin-based grid is inset by half a gap on every
  // side and reads as a narrower card sitting inside the bubble.
  mediaGrid: { borderRadius: 14, flexDirection: "row", flexWrap: "wrap", gap: MEDIA_TILE_GAP, overflow: "hidden" },
  mediaTile: { backgroundColor: colors.surfaceRaised, overflow: "hidden", position: "relative" },
  mediaTilePlayBadge: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
    borderRadius: 13,
    bottom: 6,
    height: 26,
    justifyContent: "center",
    left: 6,
    position: "absolute",
    width: 26
  },
  mediaPlaceholder: { alignItems: "center", justifyContent: "center" },
  // Absolute so it sits over the `<Image>` it is covering rather than pushing it
  // out of the frame.
  mediaSkeleton: { backgroundColor: "rgba(255,255,255,0.04)", bottom: 0, left: 0, position: "absolute", right: 0, top: 0 },
  mediaFailed: { backgroundColor: "rgba(255,255,255,0.04)", bottom: 0, gap: 3, left: 0, padding: 12, position: "absolute", right: 0, top: 0 },
  mediaFailedTitle: { color: colors.text, fontSize: 12, fontWeight: "800", textAlign: "center" },
  mediaFailedHint: { color: colors.muted, fontSize: 11, fontWeight: "700", textAlign: "center" },
  videoPlayBadge: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
    borderRadius: 26,
    height: 52,
    justifyContent: "center",
    left: "50%",
    marginLeft: -26,
    marginTop: -26,
    paddingLeft: 3,
    position: "absolute",
    top: "50%",
    width: 52
  },
  videoDurationBadge: {
    backgroundColor: "rgba(4,18,28,0.78)",
    borderRadius: 6,
    bottom: 8,
    left: 8,
    paddingHorizontal: 7,
    paddingVertical: 3,
    position: "absolute"
  },
  videoDurationText: { color: "#ffffff", fontSize: 12, fontWeight: "700" },
  videoStatusBadge: {
    backgroundColor: "rgba(4,18,28,0.78)",
    borderRadius: 6,
    bottom: 8,
    paddingHorizontal: 7,
    paddingVertical: 3,
    position: "absolute",
    right: 8
  },
  videoStatusText: { color: "#ffffff", fontSize: 11, fontWeight: "600" },
  attachment: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderColor: "rgba(97,216,255,0.24)",
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 3,
    minWidth: 190,
    padding: 10
  },
  videoPoster: {
    aspectRatio: 1.6,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 10,
    marginBottom: 4,
    width: 200
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
