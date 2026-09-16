import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { LinearGradient } from "expo-linear-gradient";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AccessibilityInfo, FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  ASSISTANT_PRESENCE,
  loadCachedConversations,
  listConversations,
  MessengerConversation,
  PULSE_AI_CONVERSATION_ID,
  PULSE_AI_DISPLAY_NAME,
  subscribeConversationUpdates
} from "../api/messenger";
import { conversationSplitEnabled } from "../api/conversationDomain";
import { PulseApiError } from "../api/pulseApi";
import { PulseCommandAvatar, PulseCommandPanel, PulseCommandSegmentRail } from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { setCommunityCreateIntent } from "../community/communityCreateIntent";
import { registerRefreshDestination } from "../navigation/refreshCoordinator";
import { RootStackParamList } from "../navigation/types";
import { useAuth } from "../session/auth";
import {
  conversationAccessibilityLabel,
  conversationDisplayTitle,
  conversationPreview,
  conversationSignalBadges,
  conversationTime,
  isActivePresence,
  isAssistantPresence
} from "../pulseCommand/domain";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import {
  messengerBackgroundGradient,
  messengerBackgroundOpacity,
  messengerBadgeTone,
  messengerPresenceDotColor,
  messengerTheme
} from "../theme/messengerTheme";
import { createThemedStyles } from "../theme/themedStyles";
import { messagesVisualRefreshEnabled } from "../spatial/flags";

type ConversationFilter = "all" | "direct" | "groups" | "rooms" | "ai" | "unread";
const FILTER_KEY = "pulsesoc.native.messenger.filter";
const LAST_CONVERSATION_KEY = "pulsesoc.native.messenger.last_conversation";
const DEFAULT_UNDX_AI_CONVERSATION: MessengerConversation = {
  id: PULSE_AI_CONVERSATION_ID,
  conversation_id: PULSE_AI_CONVERSATION_ID,
  conversation_domain: "SOCIAL",
  title: PULSE_AI_DISPLAY_NAME,
  conversation_type: "ai",
  latest_message: "Message UNDX",
  last_message_preview: "Message UNDX",
  presence: ASSISTANT_PRESENCE,
  pinned: true,
  trust_state: "intelligence",
  verified: true
};

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const insets = useSafeAreaInsets();
  const dock = useBottomNavSurface();
  const { authState, requestReauthentication } = useAuth();
  const listRef = useRef<FlatList<MessengerConversation>>(null);
  const loadSequence = useRef(0);
  const refreshingRef = useRef(false);
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const qaFilter = String(process.env.EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER || "").toLowerCase();
  const validQaFilter = ["all", "direct", "groups", "rooms", "ai", "unread"].includes(qaFilter)
    ? qaFilter as ConversationFilter
    : null;
  const [selectedFilter, setSelectedFilter] = useState<ConversationFilter>(validQaFilter || "all");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  // ---- Messages visual refresh (flag-gated; inert when OFF) ---------------
  // Approved refinements only (mission §17): screen title, inbox search,
  // unread clarity, floating compose. The inbox stays vertical and every
  // conversation behavior is untouched. Tilt is never wired to this screen.
  const visualRefresh = messagesVisualRefreshEnabled();
  const [searchQuery, setSearchQuery] = useState("");
  // -------------------------------------------------------------------------

  const openNewChat = useCallback((initialQuery = "") => {
    navigation.navigate("NewChat", initialQuery ? { initialQuery } : undefined);
  }, [navigation]);

  async function load({ refresh = false } = {}) {
    const sequence = ++loadSequence.current;
    if (refresh) {
      if (refreshingRef.current) return;
      refreshingRef.current = true;
      setRefreshing(true);
    }
    else setLoading(true);
    setError("");
    try {
      // "social" is not a display filter — it is the scope the query runs in, so
      // a marketplace or dispute thread is never in the result to begin with.
      const nextConversations = await listConversations("social");
      if (sequence !== loadSequence.current) return;
      setConversations(nextConversations);
    } catch (loadError) {
      if (sequence !== loadSequence.current) return;
      if (loadError instanceof PulseApiError && loadError.status === 401) {
        requestReauthentication("/pulse/messages");
      }
      const cached = await loadCachedConversations("social");
      setConversations(cached);
      setError(loadError instanceof Error ? loadError.message : "Messenger could not load.");
    } finally {
      if (sequence === loadSequence.current) {
        setRefreshing(false);
        setLoading(false);
        if (refresh) refreshingRef.current = false;
      }
    }
  }

  useEffect(() => {
    if (!validQaFilter) AsyncStorage.getItem(FILTER_KEY).then((value) => {
      if (["all", "direct", "groups", "rooms", "ai", "unread"].includes(value || "")) setSelectedFilter(value as ConversationFilter);
    }).catch(() => undefined);
    loadCachedConversations("social").then((cached) => cached.length && setConversations(cached));
    return subscribeConversationUpdates((conversation) => {
      // The live listener is shared plumbing (see the Tier 0.4 shared-infra rule),
      // so the social list has to reject a commerce thread on arrival rather than
      // trust that only social threads are broadcast.
      if (conversationSplitEnabled() && conversation.conversation_domain !== "SOCIAL") return;
      setConversations((current) => [conversation, ...current.filter((item) => item.id !== conversation.id)]);
    });
  }, []);

  useFocusEffect(useCallback(() => {
    loadCachedConversations("social").then((cached) => cached.length && setConversations(cached)).catch(() => undefined);
    load().catch(() => undefined);
  }, []));

  useEffect(() => {
    AsyncStorage.setItem(FILTER_KEY, selectedFilter).catch(() => undefined);
  }, [selectedFilter]);

  useEffect(() => registerRefreshDestination("social-messages", {
    scrollToTop: () => listRef.current?.scrollToOffset({ offset: 0, animated: true }),
    refresh: async () => {
      await load({ refresh: true });
      AccessibilityInfo.announceForAccessibility?.("Messages refreshed");
    },
    isRefreshing: () => refreshingRef.current
  }), []);

  const unreadTotal = useMemo(() => conversations.reduce((total, item) => total + Number(item.unread_count || 0), 0), [conversations]);
  const conversationsWithUndxAi = useMemo(
    () => withDefaultUndxAiConversation(conversations),
    [conversations]
  );
  const filteredConversations = useMemo(() => {
    const byFilter = conversationsWithUndxAi.filter((item) => conversationMatchesFilter(item, selectedFilter));
    const query = visualRefresh ? searchQuery.trim().toLowerCase() : "";
    if (!query) return byFilter;
    return byFilter.filter((item) =>
      `${conversationDisplayTitle(item)} ${conversationPreview(item)}`.toLowerCase().includes(query)
    );
  }, [conversationsWithUndxAi, selectedFilter, visualRefresh, searchQuery]);
  const activeConversations = useMemo(
    () => conversationsWithUndxAi.filter((item) => isActivePresence(item.presence) || isAssistantPresence(item.presence) || item.typing).slice(0, 8),
    [conversationsWithUndxAi]
  );
  const filters = useMemo(
    () => [
      { key: "all", label: "All" },
      { key: "direct", label: "Direct" },
      { key: "groups", label: "Groups" },
      { key: "rooms", label: "Rooms" },
      { key: "ai", label: "AI" },
      { key: "unread", label: "Unread", count: unreadTotal }
    ],
    [unreadTotal]
  );

  if (authState.status !== "signedIn") {
    return (
      <LogiNexusScreenShell>
        {/* The signed-out branch gets the field too — a dark flash on the way to
            the signed-in page is still a dark flash. */}
        <NeonDuskField />
        <View style={styles.permissionPage}>
          <LogiNexusStatePanel state="permission" title="Sign in to open Messenger" body="Pulse Command uses your existing PulseSoc identity and conversations.">
            <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => requestReauthentication("/pulse/messages")}><Text style={styles.retryText}>Sign in</Text></Pressable>
          </LogiNexusStatePanel>
        </View>
      </LogiNexusScreenShell>
    );
  }

  return (
    <LogiNexusScreenShell>
      <NeonDuskField />
      <FlatList
        ref={listRef}
        data={filteredConversations}
        keyExtractor={(item) => `chat-${item.id}`}
        contentContainerStyle={[styles.list, { paddingTop: Math.max(insets.top + 4, 36) }, dock.contentPadding]}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={messengerTheme.tealAccent} onRefresh={() => load({ refresh: true })} />}
        initialNumToRender={10}
        maxToRenderPerBatch={8}
        windowSize={7}
        removeClippedSubviews
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          <View style={styles.headerStack}>
            {visualRefresh ? (
              <>
                <Text accessibilityRole="header" style={styles.screenTitle} testID="messenger-refresh-title">Messages</Text>
                <View style={styles.searchShell}>
                  <Text style={styles.searchIcon}>⌕</Text>
                  <TextInput
                    testID="messenger-search-input"
                    accessibilityLabel="Search conversations"
                    style={styles.searchInput}
                    placeholder="Search conversations"
                    placeholderTextColor={messengerTheme.tertiaryText}
                    value={searchQuery}
                    onChangeText={setSearchQuery}
                    autoCapitalize="none"
                    autoCorrect={false}
                    returnKeyType="search"
                  />
                  {searchQuery ? (
                    <Pressable accessibilityRole="button" accessibilityLabel="Clear search" onPress={() => setSearchQuery("")}>
                      <Text style={styles.searchClear}>✕</Text>
                    </Pressable>
                  ) : null}
                </View>
              </>
            ) : null}
            <PulseCommandSegmentRail items={filters} selected={selectedFilter} onSelect={(key) => setSelectedFilter(key as ConversationFilter)} />
            <ScrollView horizontal style={styles.presenceRailShell} contentContainerStyle={styles.presenceRail} showsHorizontalScrollIndicator={false} accessibilityLabel="Active PulseSoc conversations" testID="messenger-active-rail">
              <Pressable accessibilityRole="button" accessibilityLabel="Start a new direct conversation" style={styles.presenceItem} onPress={() => openNewChat()}>
                <View style={styles.addPresenceAvatar}><Text style={styles.addPresenceText}>＋</Text></View>
                <Text style={styles.presenceName} numberOfLines={1}>Add</Text>
              </Pressable>
              {activeConversations.map((item) => {
                const title = conversationDisplayTitle(item);
                return (
                  <Pressable key={`active-${item.id}`} accessibilityRole="button" accessibilityLabel={`Open ${title}, active now`} style={styles.presenceItem} onPress={() => navigation.navigate("Chat", { conversationId: item.id, title, avatarUrl: item.avatar_url, presence: item.presence })}>
                    <PulseCommandAvatar label={title} imageUrl={item.avatar_url} active size={50} tone={item.trust_state === "founder" ? "intelligence" : "default"} signalColor={messengerPresenceDotColor(item.presence)} />
                    <Text style={styles.presenceName} numberOfLines={1}>{title}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>
            <PulseCommandPanel style={styles.quickActions}>
              <QuickAction icon="＋" title="New Chat" subtitle="Direct message" accent="chat" primary onPress={() => openNewChat()} />
              <QuickAction icon="◎" title="Create Group" subtitle="Invite members" accent="group" onPress={() => { setCommunityCreateIntent("group"); navigation.navigate("Tabs", { screen: "Groups" }); }} />
              <QuickAction icon="◉" title="Start Room" subtitle="Public or private" accent="room" onPress={() => { setCommunityCreateIntent("room"); navigation.navigate("Tabs", { screen: "Groups" }); }} />
            </PulseCommandPanel>
            {error && conversations.length ? <Text accessibilityLiveRegion="polite" style={styles.error}>Showing cached conversations while Messenger reconnects.</Text> : null}
            <Text style={styles.sectionLabel} testID="messenger-recent-heading">Recent conversations</Text>
          </View>
        }
        ListEmptyComponent={loading ? <ConversationSkeletonList /> : error ? (
          <LogiNexusStatePanel state="error" title="Messenger could not load" body={error}>
            <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => load()}><Text style={styles.retryText}>Retry</Text></Pressable>
          </LogiNexusStatePanel>
        ) : <LogiNexusStatePanel state="empty" title={emptyTitle(selectedFilter)} body={emptyBody(selectedFilter)} />}
        renderItem={({ item }) => <ConversationRow item={item} navigation={navigation} />}
        {...dock.handlers}
      />
      {visualRefresh ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Compose new message"
          testID="messenger-compose-fab"
          style={({ pressed }) => [
            styles.composeFab,
            { bottom: insets.bottom + 86 },
            pressed && styles.composeFabPressed
          ]}
          onPress={() => openNewChat()}
        >
          <Text style={styles.composeFabText}>✎</Text>
        </Pressable>
      ) : null}
    </LogiNexusScreenShell>
  );
}

function ConversationSkeletonList() {
  return (
    <View style={styles.skeletonList} testID="messenger-skeleton-list" accessibilityLabel="Loading recent conversations">
      {[0, 1, 2].map((item) => (
        <View key={item} style={styles.skeletonRow}>
          <View style={styles.skeletonAvatar} />
          <View style={styles.skeletonBody}>
            <View style={[styles.skeletonLine, styles.skeletonLineTitle]} />
            <View style={styles.skeletonLine} />
          </View>
        </View>
      ))}
    </View>
  );
}

type QuickActionAccent = "chat" | "group" | "room";

function QuickAction({ icon, title, subtitle, primary, accent, onPress }: { icon: string; title: string; subtitle: string; primary?: boolean; accent: QuickActionAccent; onPress: () => void }) {
  const accentColor = quickActionAccentColor(accent);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${title}, ${subtitle}`}
      style={({ pressed }) => [
        styles.quickAction,
        { borderColor: `${accentColor}44`, backgroundColor: `${accentColor}12` },
        primary && styles.quickActionPrimary,
        pressed && styles.rowPressed
      ]}
      onPress={onPress}
    >
      <View style={[styles.quickActionIcon, { borderColor: `${accentColor}55`, backgroundColor: `${accentColor}16` }]}><Text style={[styles.quickActionIconText, { color: accentColor }]}>{icon}</Text></View>
      <View style={styles.quickActionCopy}><Text style={[styles.quickActionTitle, primary && styles.quickActionPrimaryText]}>{title}</Text><Text style={[styles.quickActionSubtitle, primary && styles.quickActionPrimarySubtitle]}>{subtitle}</Text></View>
    </Pressable>
  );
}

/**
 * New Chat is teal, Create Group is blue, Start Room is violet.
 *
 * Create Group was `#73f27d` — a green, and the only green on the screen that
 * was not presence. Greens that do not mean "online" are exactly what makes the
 * ONLINE dot stop reading as a status.
 */
function quickActionAccentColor(accent: QuickActionAccent) {
  if (accent === "group") return messengerTheme.blueAccent;
  if (accent === "room") return messengerTheme.violetAccent;
  return messengerTheme.tealAccent;
}

function ConversationRow({ item, navigation }: { item: MessengerConversation; navigation: NativeStackNavigationProp<RootStackParamList> }) {
  // The avatar ring means "this person is online". An assistant lights it for
  // its own reason (the service is reachable), never by borrowing a human
  // presence value.
  const active = isActivePresence(item.presence) || isAssistantPresence(item.presence);
  const title = conversationDisplayTitle(item);
  const opensUndxAi = item.conversation_id === PULSE_AI_CONVERSATION_ID;
  // Unread clarity (visual refresh only): the preview line brightens so an
  // unread thread reads at a glance without changing row layout or behavior.
  const unread = messagesVisualRefreshEnabled() && Number(item.unread_count || 0) > 0;
  return (
    <Pressable
      style={({ pressed }) => [styles.row, item.pinned && styles.pinnedRow, pressed && styles.rowPressed]}
      accessibilityRole="button"
      accessibilityLabel={conversationAccessibilityLabel(item)}
      onPress={() => {
        if (opensUndxAi) {
          AsyncStorage.setItem(LAST_CONVERSATION_KEY, String(PULSE_AI_CONVERSATION_ID)).catch(() => undefined);
          navigation.navigate("Chat", { conversationId: PULSE_AI_CONVERSATION_ID, title: PULSE_AI_DISPLAY_NAME, presence: ASSISTANT_PRESENCE });
          return;
        }
        AsyncStorage.setItem(LAST_CONVERSATION_KEY, String(item.id)).catch(() => undefined);
        navigation.navigate("Chat", { conversationId: item.id, title, avatarUrl: item.avatar_url, presence: item.presence });
      }}
    >
      <PulseCommandAvatar label={title} imageUrl={item.avatar_url} active={active} tone={item.trust_state === "founder" || item.trust_state === "intelligence" ? "intelligence" : "default"} size={48} signalColor={messengerPresenceDotColor(item.presence)} />
      <View style={styles.rowBody}>
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={1}>{title}</Text>
          <Text style={styles.time}>{conversationTime(item)}</Text>
        </View>
        <Text style={[styles.muted, unread && styles.unreadPreview]} numberOfLines={1}>{conversationPreview(item)}</Text>
        <View style={styles.rowSignals}>
          {/* Each badge is toned by what it says. One shared pill style used to
              paint all of them the presence green, so OFFLINE, AI, ROOM, DIRECT
              and VERIFIED all arrived green and green stopped meaning online. */}
          {conversationSignalBadges(item).map((badge) => {
            const tone = messengerBadgeTone(badge);
            return (
              <Text
                key={badge}
                testID={`messenger-badge-${badge}`}
                style={[styles.signalPill, { backgroundColor: tone.background, borderColor: tone.border, color: tone.text }]}
              >
                {badge}
              </Text>
            );
          })}
        </View>
      </View>
      {Number(item.unread_count || 0) > 0 ? <View style={styles.badge}><Text style={styles.badgeText}>{item.unread_count}</Text></View> : null}
    </Pressable>
  );
}

function conversationMatchesFilter(item: MessengerConversation, filter: ConversationFilter) {
  const type = String(item.conversation_type || "direct").toLowerCase();
  if (filter === "all") return true;
  if (filter === "direct") return type === "direct";
  if (filter === "groups") return type === "group";
  if (filter === "rooms") return type === "room";
  if (filter === "ai") return ["ai", "intelligence", "undx"].includes(type);
  return Number(item.unread_count || 0) > 0;
}

/**
 * The Neon Dusk field.
 *
 * `PulseBackground` is the app's single shared backdrop, mounted once at the
 * root and pinned there by `navigation/__tests__/backgroundSurfaces.test.ts`.
 * Lightening *it* would lighten all fifteen tabs, so Messenger lifts its own
 * page instead — the same opt-in shape `Screen`'s `surface="business"` prop uses
 * for the commerce screens.
 *
 * It is a translucent veil rather than an opaque fill, which is the whole point
 * of the 0.93: the shared mesh and its nodes still read faintly through the
 * gradient, so the page gets lighter without going flat. One gradient for the
 * screen, drawn once and never animated — a backdrop inside `renderItem` would
 * be built and torn down per conversation in a virtualized list.
 */
function NeonDuskField() {
  return (
    <LinearGradient
      testID="messenger-neon-dusk-field"
      pointerEvents="none"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      colors={messengerBackgroundGradient}
      style={[StyleSheet.absoluteFill, { opacity: messengerBackgroundOpacity }]}
    />
  );
}

function withDefaultUndxAiConversation(items: MessengerConversation[]) {
  const undxIndex = items.findIndex(isUndxAiConversation);
  const undxConversation =
    undxIndex >= 0
      ? {
          ...DEFAULT_UNDX_AI_CONVERSATION,
          ...items[undxIndex],
          id: PULSE_AI_CONVERSATION_ID,
          conversation_id: PULSE_AI_CONVERSATION_ID,
          title: PULSE_AI_DISPLAY_NAME,
          name: PULSE_AI_DISPLAY_NAME,
          conversation_type: "ai",
          latest_message: items[undxIndex].latest_message || items[undxIndex].last_message_preview || DEFAULT_UNDX_AI_CONVERSATION.latest_message,
          pinned: true,
          trust_state: items[undxIndex].trust_state || "intelligence",
          verified: true
        }
      : DEFAULT_UNDX_AI_CONVERSATION;
  const rest = undxIndex >= 0 ? items.filter((_, index) => index !== undxIndex) : items;
  return [undxConversation, ...rest];
}

function isUndxAiConversation(item: MessengerConversation) {
  const title = `${item.title || ""} ${item.name || ""}`.toLowerCase();
  const type = String(item.conversation_type || "").toLowerCase();
  return item.conversation_id === PULSE_AI_CONVERSATION_ID || title.includes("undx") || ["ai", "intelligence", "undx", "assistant"].includes(type);
}

function emptyTitle(filter: ConversationFilter) {
  if (filter === "unread") return "You're all caught up";
  if (filter === "all") return "Choose a chat";
  return `No ${filter} conversations`;
}

function emptyBody(filter: ConversationFilter) {
  if (filter === "unread") return "New unread conversations will appear here.";
  return "Your conversations and composer open instantly here.";
}

const styles = createThemedStyles(() => ({
  permissionPage: { flex: 1, justifyContent: "center", padding: 16 },
  list: { gap: 4, padding: 8 },
  headerStack: { gap: 6 },
  // The story/status rail sits one step above a conversation card so the avatars
  // stay the brightest thing in it.
  presenceRailShell: { backgroundColor: messengerTheme.surfaceElevated, borderColor: messengerTheme.border, borderRadius: 15, borderWidth: 1 },
  presenceRail: { gap: 10, paddingHorizontal: 10, paddingVertical: 7 },
  addPresenceAvatar: { alignItems: "center", borderColor: messengerTheme.tealBorder, borderRadius: 25, borderStyle: "dashed", borderWidth: 1, height: 50, justifyContent: "center", width: 50 },
  addPresenceText: { color: messengerTheme.tealAccent, fontSize: 24, fontWeight: "900" },
  presenceItem: { alignItems: "center", gap: 3, width: 58 },
  presenceName: { color: messengerTheme.tertiaryText, fontSize: 10, maxWidth: 58 },
  quickActions: { backgroundColor: messengerTheme.surfaceElevated, borderColor: messengerTheme.border, flexDirection: "row", gap: 6, padding: 5 },
  quickAction: { alignItems: "center", borderColor: messengerTheme.border, borderRadius: 12, borderWidth: StyleSheet.hairlineWidth, flex: 1, flexDirection: "row", gap: 6, minHeight: 56, padding: 6 },
  quickActionPrimary: { backgroundColor: messengerTheme.tealAccent, borderColor: messengerTheme.tealAccent },
  quickActionIcon: { alignItems: "center", borderRadius: 9, borderWidth: 1, height: 30, justifyContent: "center", width: 30 },
  quickActionIconText: { fontSize: 16, fontWeight: "900" },
  quickActionCopy: { flex: 1, minWidth: 0 },
  quickActionTitle: { color: messengerTheme.primaryText, fontSize: 11, fontWeight: "900" },
  quickActionPrimaryText: { color: messengerTheme.onAccentText },
  quickActionSubtitle: { color: messengerTheme.secondaryText, fontSize: 9, marginTop: 1 },
  quickActionPrimarySubtitle: { color: "rgba(6, 32, 28, 0.72)" },
  sectionLabel: { color: messengerTheme.tertiaryText, fontSize: 11, fontWeight: "800", letterSpacing: 0.7, textTransform: "uppercase" },
  /**
   * A conversation card. Separation is carried by the fill and a hairline-soft
   * border, not by a glow: the old row stacked a `#61d8ff` shadow at radius 8 on
   * every row, and an offscreen-rendered shadow per row in a virtualized list is
   * the single most expensive thing this screen could do while scrolling.
   */
  row: { alignItems: "center", backgroundColor: messengerTheme.surface, borderColor: messengerTheme.border, borderRadius: 13, borderWidth: 1, flexDirection: "row", gap: 9, minHeight: 64, padding: 9 },
  rowPressed: { backgroundColor: messengerTheme.surfacePressed, borderColor: messengerTheme.borderStrong },
  // Pinned reads through its border and its own teal PINNED pill — no shadow.
  pinnedRow: { borderColor: messengerTheme.tealBorder },
  rowBody: { flex: 1, gap: 2, minWidth: 0 },
  rowTop: { alignItems: "center", flexDirection: "row", gap: 6 },
  rowSignals: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  title: { color: messengerTheme.primaryText, flex: 1, fontSize: 14, fontWeight: "900" },
  muted: { color: messengerTheme.secondaryText, fontSize: 12, lineHeight: 16 },
  time: { color: messengerTheme.tertiaryText, fontSize: 10 },
  // Colour comes from `messengerBadgeTone` at the call site; these three are the
  // neutral fallback so an untoned pill is gray rather than accidentally green.
  signalPill: { backgroundColor: messengerTheme.offlineSoft, borderColor: messengerTheme.offlineBorder, borderRadius: logiNexus.radius.capsule, borderWidth: StyleSheet.hairlineWidth, color: messengerTheme.offline, fontSize: 9, fontWeight: "800", paddingHorizontal: 6, paddingVertical: 2, textTransform: "uppercase" },
  badge: { alignItems: "center", backgroundColor: messengerTheme.tealAccent, borderRadius: 12, minHeight: 23, minWidth: 23, paddingHorizontal: 6, paddingVertical: 2 },
  badgeText: { color: messengerTheme.onAccentText, fontSize: 11, fontWeight: "900" },
  skeletonAvatar: { backgroundColor: "rgba(148, 182, 228, 0.16)", borderRadius: 24, height: 48, width: 48 },
  skeletonBody: { flex: 1, gap: 7 },
  skeletonLine: { backgroundColor: "rgba(148, 182, 228, 0.14)", borderRadius: 6, height: 10, width: "62%" },
  skeletonLineTitle: { height: 13, width: "46%" },
  skeletonList: { gap: 6 },
  skeletonRow: { alignItems: "center", backgroundColor: messengerTheme.surface, borderColor: messengerTheme.border, borderRadius: 13, borderWidth: 1, flexDirection: "row", gap: 9, minHeight: 64, padding: 9 },
  error: { color: colors.warning, fontSize: 12 },
  retryButton: { alignSelf: "center", backgroundColor: messengerTheme.tealSoft, borderColor: messengerTheme.tealBorder, borderRadius: 10, borderWidth: 1, marginTop: 8, paddingHorizontal: 14, paddingVertical: 9 },
  retryText: { color: messengerTheme.tealAccent, fontSize: 12, fontWeight: "900" },
  // ---- Messages visual refresh (only rendered when the flag is ON) --------
  screenTitle: { color: messengerTheme.primaryText, fontSize: 22, fontWeight: "900", letterSpacing: 0.3 },
  searchShell: { alignItems: "center", backgroundColor: messengerTheme.surfaceRecessed, borderColor: messengerTheme.border, borderRadius: 13, borderWidth: 1, flexDirection: "row", gap: 8, minHeight: 40, paddingHorizontal: 12 },
  searchIcon: { color: messengerTheme.tertiaryText, fontSize: 15, fontWeight: "800" },
  searchInput: { color: messengerTheme.primaryText, flex: 1, fontSize: 13, paddingVertical: 8 },
  searchClear: { color: messengerTheme.tertiaryText, fontSize: 13, fontWeight: "900", padding: 4 },
  unreadPreview: { color: messengerTheme.primaryText, fontWeight: "700" },
  composeFab: { alignItems: "center", backgroundColor: messengerTheme.tealAccent, borderColor: messengerTheme.tealAccent, borderRadius: 27, borderWidth: 1, height: 54, justifyContent: "center", position: "absolute", right: 16, shadowColor: messengerTheme.tealAccent, shadowOpacity: 0.3, shadowRadius: 12, width: 54 },
  composeFabPressed: { backgroundColor: messengerTheme.tealBorder },
  composeFabText: { color: messengerTheme.onAccentText, fontSize: 20, fontWeight: "900" }
}));
