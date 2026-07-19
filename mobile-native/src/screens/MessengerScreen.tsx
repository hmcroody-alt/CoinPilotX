import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  loadCachedConversations,
  listConversations,
  MessengerConversation,
  PULSE_AI_CONVERSATION_ID,
  PULSE_AI_DISPLAY_NAME,
  subscribeConversationUpdates
} from "../api/messenger";
import { PulseApiError } from "../api/pulseApi";
import { PulseCommandAvatar, PulseCommandPanel, PulseCommandSegmentRail } from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { useBottomNavScrollVisibility } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { useAuth } from "../session/auth";
import {
  conversationAccessibilityLabel,
  conversationDisplayTitle,
  conversationPreview,
  conversationSignalBadges,
  conversationTime,
  isActivePresence
} from "../pulseCommand/domain";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type ConversationFilter = "all" | "direct" | "groups" | "rooms" | "ai" | "unread";
const FILTER_KEY = "pulsesoc.native.messenger.filter";
const LAST_CONVERSATION_KEY = "pulsesoc.native.messenger.last_conversation";
const DEFAULT_UNDX_AI_CONVERSATION: MessengerConversation = {
  id: PULSE_AI_CONVERSATION_ID,
  conversation_id: PULSE_AI_CONVERSATION_ID,
  title: PULSE_AI_DISPLAY_NAME,
  conversation_type: "ai",
  latest_message: "Message UNDX",
  last_message_preview: "Message UNDX",
  presence: "available",
  pinned: true,
  trust_state: "intelligence",
  verified: true
};

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const insets = useSafeAreaInsets();
  const bottomNavScroll = useBottomNavScrollVisibility();
  const { authState, requestReauthentication } = useAuth();
  const loadSequence = useRef(0);
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const qaFilter = String(process.env.EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER || "").toLowerCase();
  const validQaFilter = ["all", "direct", "groups", "rooms", "ai", "unread"].includes(qaFilter)
    ? qaFilter as ConversationFilter
    : null;
  const [selectedFilter, setSelectedFilter] = useState<ConversationFilter>(validQaFilter || "all");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const openNewChat = useCallback((initialQuery = "") => {
    navigation.navigate("NewChat", initialQuery ? { initialQuery } : undefined);
  }, [navigation]);

  async function load({ refresh = false } = {}) {
    const sequence = ++loadSequence.current;
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const nextConversations = await listConversations();
      if (sequence !== loadSequence.current) return;
      setConversations(nextConversations);
    } catch (loadError) {
      if (sequence !== loadSequence.current) return;
      if (loadError instanceof PulseApiError && loadError.status === 401) {
        requestReauthentication("/pulse/messages");
      }
      const cached = await loadCachedConversations();
      setConversations(cached);
      setError(loadError instanceof Error ? loadError.message : "Messenger could not load.");
    } finally {
      if (sequence === loadSequence.current) {
        setRefreshing(false);
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    if (!validQaFilter) AsyncStorage.getItem(FILTER_KEY).then((value) => {
      if (["all", "direct", "groups", "rooms", "ai", "unread"].includes(value || "")) setSelectedFilter(value as ConversationFilter);
    }).catch(() => undefined);
    loadCachedConversations().then((cached) => cached.length && setConversations(cached));
    return subscribeConversationUpdates((conversation) => {
      setConversations((current) => [conversation, ...current.filter((item) => item.id !== conversation.id)]);
    });
  }, []);

  useFocusEffect(useCallback(() => {
    loadCachedConversations().then((cached) => cached.length && setConversations(cached)).catch(() => undefined);
    load().catch(() => undefined);
  }, []));

  useEffect(() => {
    AsyncStorage.setItem(FILTER_KEY, selectedFilter).catch(() => undefined);
  }, [selectedFilter]);

  const unreadTotal = useMemo(() => conversations.reduce((total, item) => total + Number(item.unread_count || 0), 0), [conversations]);
  const conversationsWithUndxAi = useMemo(
    () => withDefaultUndxAiConversation(conversations),
    [conversations]
  );
  const filteredConversations = useMemo(
    () => conversationsWithUndxAi.filter((item) => conversationMatchesFilter(item, selectedFilter)),
    [conversationsWithUndxAi, selectedFilter]
  );
  const activeConversations = useMemo(
    () => conversationsWithUndxAi.filter((item) => isActivePresence(item.presence) || item.typing).slice(0, 8),
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
      <FlatList
        data={filteredConversations}
        keyExtractor={(item) => `chat-${item.id}`}
        contentContainerStyle={[styles.list, { paddingTop: Math.max(insets.top + 4, 36) }]}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
        initialNumToRender={10}
        maxToRenderPerBatch={8}
        windowSize={7}
        removeClippedSubviews
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          <View style={styles.headerStack}>
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
                    <PulseCommandAvatar label={title} imageUrl={item.avatar_url} active size={50} tone={item.trust_state === "founder" ? "intelligence" : "default"} />
                    <Text style={styles.presenceName} numberOfLines={1}>{title}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>
            <PulseCommandPanel style={styles.quickActions}>
              <QuickAction icon="＋" title="New Chat" subtitle="Direct message" accent="chat" primary onPress={() => openNewChat()} />
              <QuickAction icon="◎" title="Create Group" subtitle="Invite members" accent="group" onPress={() => navigation.navigate("Tabs", { screen: "Groups" })} />
              <QuickAction icon="◉" title="Start Room" subtitle="Public or private" accent="room" onPress={() => navigation.navigate("Tabs", { screen: "Groups" })} />
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
        onScroll={bottomNavScroll.onScroll}
        onScrollBeginDrag={bottomNavScroll.onScrollBeginDrag}
        scrollEventThrottle={bottomNavScroll.scrollEventThrottle}
      />
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

function quickActionAccentColor(accent: QuickActionAccent) {
  if (accent === "group") return "#73f27d";
  if (accent === "room") return "#a77cff";
  return "#3eeed1";
}

function ConversationRow({ item, navigation }: { item: MessengerConversation; navigation: NativeStackNavigationProp<RootStackParamList> }) {
  const active = isActivePresence(item.presence);
  const title = conversationDisplayTitle(item);
  const opensUndxAi = item.conversation_id === PULSE_AI_CONVERSATION_ID;
  return (
    <Pressable
      style={({ pressed }) => [styles.row, item.pinned && styles.pinnedRow, pressed && styles.rowPressed]}
      accessibilityRole="button"
      accessibilityLabel={conversationAccessibilityLabel(item)}
      onPress={() => {
        if (opensUndxAi) {
          AsyncStorage.setItem(LAST_CONVERSATION_KEY, String(PULSE_AI_CONVERSATION_ID)).catch(() => undefined);
          navigation.navigate("Chat", { conversationId: PULSE_AI_CONVERSATION_ID, title: PULSE_AI_DISPLAY_NAME, presence: "available" });
          return;
        }
        AsyncStorage.setItem(LAST_CONVERSATION_KEY, String(item.id)).catch(() => undefined);
        navigation.navigate("Chat", { conversationId: item.id, title, avatarUrl: item.avatar_url, presence: item.presence });
      }}
    >
      <PulseCommandAvatar label={title} imageUrl={item.avatar_url} active={active} tone={item.trust_state === "founder" || item.trust_state === "intelligence" ? "intelligence" : "default"} size={48} />
      <View style={styles.rowBody}>
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={1}>{title}</Text>
          <Text style={styles.time}>{conversationTime(item)}</Text>
        </View>
        <Text style={styles.muted} numberOfLines={1}>{conversationPreview(item)}</Text>
        <View style={styles.rowSignals}>
          {conversationSignalBadges(item).map((badge) => <Text key={badge} style={styles.signalPill}>{badge}</Text>)}
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

const styles = StyleSheet.create({
  permissionPage: { flex: 1, justifyContent: "center", padding: 16 },
  list: { gap: 4, padding: 8, paddingBottom: 108 },
  headerStack: { gap: 6 },
  presenceRailShell: { backgroundColor: "rgba(11,24,34,0.78)", borderColor: "rgba(97,216,255,0.18)", borderRadius: 15, borderWidth: 1 },
  presenceRail: { gap: 10, paddingHorizontal: 10, paddingVertical: 7 },
  addPresenceAvatar: { alignItems: "center", borderColor: "rgba(61,223,255,0.72)", borderRadius: 25, borderStyle: "dashed", borderWidth: 1, height: 50, justifyContent: "center", width: 50 },
  addPresenceText: { color: "#3bdfff", fontSize: 24, fontWeight: "900" },
  presenceItem: { alignItems: "center", gap: 3, width: 58 },
  presenceName: { color: colors.muted, fontSize: 10, maxWidth: 58 },
  quickActions: { backgroundColor: "rgba(6,16,28,0.88)", borderColor: "rgba(97,216,255,0.22)", flexDirection: "row", gap: 6, padding: 5 },
  quickAction: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: StyleSheet.hairlineWidth, flex: 1, flexDirection: "row", gap: 6, minHeight: 56, padding: 6 },
  quickActionPrimary: { backgroundColor: "rgba(77,228,196,0.86)", borderColor: "rgba(132,255,228,0.96)", shadowColor: colors.accent, shadowOpacity: 0.34, shadowRadius: 12 },
  quickActionIcon: { alignItems: "center", backgroundColor: "rgba(97,233,246,0.08)", borderColor: "rgba(97,233,246,0.22)", borderRadius: 9, borderWidth: 1, height: 30, justifyContent: "center", width: 30 },
  quickActionIconText: { color: colors.accentStrong, fontSize: 16, fontWeight: "900" },
  quickActionCopy: { flex: 1, minWidth: 0 },
  quickActionTitle: { color: colors.text, fontSize: 11, fontWeight: "900" },
  quickActionPrimaryText: { color: "#061410" },
  quickActionSubtitle: { color: colors.muted, fontSize: 9, marginTop: 1 },
  quickActionPrimarySubtitle: { color: "rgba(6,20,16,0.68)" },
  sectionLabel: { color: "#b7c5d8", fontSize: 11, fontWeight: "800", letterSpacing: 0.7, textTransform: "uppercase" },
  row: { alignItems: "center", backgroundColor: "rgba(9,20,36,0.94)", borderColor: "rgba(105,218,240,0.16)", borderRadius: 13, borderWidth: 1, flexDirection: "row", gap: 9, minHeight: 64, padding: 9, shadowColor: "#61d8ff", shadowOpacity: 0.08, shadowRadius: 8 },
  rowPressed: { backgroundColor: "rgba(105,218,240,0.06)", borderColor: "rgba(105,218,240,0.25)" },
  pinnedRow: { borderColor: "rgba(77,228,196,0.56)", shadowColor: colors.accent, shadowOpacity: 0.1, shadowRadius: 10 },
  rowBody: { flex: 1, gap: 2, minWidth: 0 },
  rowTop: { alignItems: "center", flexDirection: "row", gap: 6 },
  rowSignals: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  title: { color: colors.text, flex: 1, fontSize: 14, fontWeight: "900" },
  muted: { color: "#a9b7c9", fontSize: 12, lineHeight: 16 },
  time: { color: colors.muted, fontSize: 10 },
  signalPill: { backgroundColor: "rgba(63,240,160,0.11)", borderColor: "rgba(63,240,160,0.22)", borderRadius: logiNexus.radius.capsule, borderWidth: StyleSheet.hairlineWidth, color: "#94f6b1", fontSize: 9, fontWeight: "800", paddingHorizontal: 6, paddingVertical: 2, textTransform: "uppercase" },
  badge: { alignItems: "center", backgroundColor: "#3bdfff", borderRadius: 12, minHeight: 23, minWidth: 23, paddingHorizontal: 6, paddingVertical: 2, shadowColor: "#3bdfff", shadowOpacity: 0.38, shadowRadius: 8 },
  badgeText: { color: "#08110f", fontSize: 11, fontWeight: "900" },
  skeletonAvatar: { backgroundColor: "rgba(105,218,240,0.12)", borderRadius: 24, height: 48, width: 48 },
  skeletonBody: { flex: 1, gap: 7 },
  skeletonLine: { backgroundColor: "rgba(180,211,223,0.12)", borderRadius: 6, height: 10, width: "62%" },
  skeletonLineTitle: { height: 13, width: "46%" },
  skeletonList: { gap: 6 },
  skeletonRow: { alignItems: "center", backgroundColor: "rgba(9,20,36,0.72)", borderColor: "rgba(105,218,240,0.1)", borderRadius: 13, borderWidth: 1, flexDirection: "row", gap: 9, minHeight: 64, padding: 9 },
  error: { color: colors.warning, fontSize: 12 },
  retryButton: { alignSelf: "center", backgroundColor: colors.signalDim, borderColor: colors.accent, borderRadius: 10, borderWidth: 1, marginTop: 8, paddingHorizontal: 14, paddingVertical: 9 },
  retryText: { color: colors.accent, fontSize: 12, fontWeight: "900" }
});
