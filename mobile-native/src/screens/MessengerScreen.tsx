import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  loadCachedConversations,
  listConversations,
  MessengerConversation,
  MessengerUserSearchResult,
  openDirectConversation,
  searchMessenger,
  subscribeConversationUpdates
} from "../api/messenger";
import { PulseApiError } from "../api/pulseApi";
import { PulseCommandAvatar, PulseCommandPanel, PulseCommandSearch, PulseCommandSegmentRail } from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
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

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const insets = useSafeAreaInsets();
  const { authState, requestReauthentication } = useAuth();
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const [searchUsers, setSearchUsers] = useState<MessengerUserSearchResult[]>([]);
  const [openingUserId, setOpeningUserId] = useState(0);
  const qaFilter = String(process.env.EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER || "").toLowerCase();
  const validQaFilter = ["all", "direct", "groups", "rooms", "ai", "unread"].includes(qaFilter)
    ? qaFilter as ConversationFilter
    : null;
  const [selectedFilter, setSelectedFilter] = useState<ConversationFilter>(validQaFilter || "all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const openNewChat = useCallback((initialQuery = "") => {
    navigation.navigate("NewChat", initialQuery ? { initialQuery } : undefined);
  }, [navigation]);

  async function openConversationControlCenter() {
    const savedId = Number(await AsyncStorage.getItem(LAST_CONVERSATION_KEY));
    const active = conversations.find((item) => item.id === savedId) || conversations[0];
    if (!active) {
      setError("Open a conversation before using conversation settings.");
      return;
    }
    navigation.navigate("Chat", { conversationId: active.id, title: conversationDisplayTitle(active), openControlCenter: true });
  }

  async function load({ refresh = false } = {}) {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      if (query.trim()) {
        const data = await searchMessenger(query.trim());
        setConversations(data.conversations);
        setSearchUsers(data.users);
      } else {
        setConversations(await listConversations());
        setSearchUsers([]);
      }
    } catch (loadError) {
      setSearchUsers([]);
      if (loadError instanceof PulseApiError && loadError.status === 401) {
        requestReauthentication("/pulse/messages");
      }
      const cached = await loadCachedConversations();
      setConversations(cached);
      setError(loadError instanceof Error ? loadError.message : "Messenger could not load.");
    } finally {
      setRefreshing(false);
      setLoading(false);
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
    const timer = setTimeout(() => load().catch(() => undefined), 350);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    AsyncStorage.setItem(FILTER_KEY, selectedFilter).catch(() => undefined);
  }, [selectedFilter]);

  const unreadTotal = useMemo(() => conversations.reduce((total, item) => total + Number(item.unread_count || 0), 0), [conversations]);
  const filteredConversations = useMemo(
    () => conversations.filter((item) => conversationMatchesFilter(item, selectedFilter)),
    [conversations, selectedFilter]
  );
  const activeConversations = useMemo(
    () => conversations.filter((item) => isActivePresence(item.presence) || item.typing).slice(0, 8),
    [conversations]
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

  async function openSearchUser(user: MessengerUserSearchResult) {
    if (openingUserId) return;
    setOpeningUserId(user.user_id);
    setError("");
    try {
      const result = await openDirectConversation(user);
      navigation.navigate("Chat", { conversationId: result.conversation_id, title: user.display_name, avatarUrl: user.avatar_url });
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "This conversation could not be opened.");
    } finally {
      setOpeningUserId(0);
    }
  }

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
      {loading && conversations.length === 0 ? (
        <LogiNexusStatePanel state="loading" title="Loading conversations" body="Connecting to PulseSoc Messenger." loading />
      ) : (
        <FlatList
          data={filteredConversations}
          keyExtractor={(item) => `chat-${item.id}`}
          contentContainerStyle={[styles.list, { paddingTop: Math.max(insets.top, 8) }]}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          ListHeaderComponent={
            <View style={styles.headerStack}>
              <View style={styles.searchRow}>
                <View style={styles.searchField}><PulseCommandSearch value={query} onChangeText={setQuery} placeholder="Search people, rooms, messages..." /></View>
                <Pressable accessibilityRole="button" accessibilityLabel="Open Conversation Control Center" style={styles.gearButton} onPress={() => openConversationControlCenter().catch(() => setError("Conversation settings could not open."))}><Text style={styles.gearButtonText}>⚙</Text></Pressable>
              </View>
              <PulseCommandSegmentRail items={filters} selected={selectedFilter} onSelect={(key) => setSelectedFilter(key as ConversationFilter)} />
              {activeConversations.length ? (
                <ScrollView horizontal contentContainerStyle={styles.presenceRail} showsHorizontalScrollIndicator={false} accessibilityLabel="Active PulseSoc conversations">
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
              ) : null}
              <PulseCommandPanel style={styles.quickActions}>
                <QuickAction icon="＋" title="New Chat" subtitle="Direct message" primary onPress={() => openNewChat()} />
                <QuickAction icon="◎" title="Create Group" subtitle="Invite members" onPress={() => navigation.navigate("Tabs", { screen: "Groups" })} />
                <QuickAction icon="◉" title="Start Room" subtitle="Public or private" onPress={() => navigation.navigate("Tabs", { screen: "Groups" })} />
              </PulseCommandPanel>
              {query.trim() && searchUsers.length ? (
                <View style={styles.peopleResults}>
                  <Text style={styles.sectionLabel}>People</Text>
                  {searchUsers.map((user) => (
                    <Pressable key={`people-${user.user_id}`} accessibilityRole="button" accessibilityLabel={`Message ${user.display_name}`} disabled={openingUserId > 0} onPress={() => openSearchUser(user)} style={({ pressed }) => [styles.personRow, pressed && styles.rowPressed]}>
                      <PulseCommandAvatar label={user.display_name} imageUrl={user.avatar_url} active={false} />
                      <View style={styles.personCopy}><Text style={styles.title} numberOfLines={1}>{user.display_name}</Text><Text style={styles.muted} numberOfLines={1}>{user.public_pulse_id || "PulseSoc member"}</Text></View>
                      <Text style={styles.personAction}>{openingUserId === user.user_id ? "Opening…" : "Message"}</Text>
                    </Pressable>
                  ))}
                </View>
              ) : null}
              {error && conversations.length ? <Text accessibilityLiveRegion="polite" style={styles.error}>Showing cached conversations while Messenger reconnects.</Text> : null}
              <Text style={styles.sectionLabel}>Recent conversations</Text>
            </View>
          }
          ListEmptyComponent={error ? (
            <LogiNexusStatePanel state="error" title="Messenger could not load" body={error}>
              <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => load()}><Text style={styles.retryText}>Retry</Text></Pressable>
            </LogiNexusStatePanel>
          ) : <LogiNexusStatePanel state="empty" title={emptyTitle(selectedFilter, query)} body={emptyBody(selectedFilter, query)} />}
          renderItem={({ item }) => <ConversationRow item={item} navigation={navigation} />}
        />
      )}
    </LogiNexusScreenShell>
  );
}

function QuickAction({ icon, title, subtitle, primary, onPress }: { icon: string; title: string; subtitle: string; primary?: boolean; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${title}, ${subtitle}`} style={({ pressed }) => [styles.quickAction, primary && styles.quickActionPrimary, pressed && styles.rowPressed]} onPress={onPress}>
      <View style={styles.quickActionIcon}><Text style={styles.quickActionIconText}>{icon}</Text></View>
      <View style={styles.quickActionCopy}><Text style={[styles.quickActionTitle, primary && styles.quickActionPrimaryText]}>{title}</Text><Text style={[styles.quickActionSubtitle, primary && styles.quickActionPrimarySubtitle]}>{subtitle}</Text></View>
    </Pressable>
  );
}

function ConversationRow({ item, navigation }: { item: MessengerConversation; navigation: NativeStackNavigationProp<RootStackParamList> }) {
  const active = isActivePresence(item.presence);
  const title = conversationDisplayTitle(item);
  return (
    <Pressable
      style={({ pressed }) => [styles.row, item.pinned && styles.pinnedRow, pressed && styles.rowPressed]}
      accessibilityRole="button"
      accessibilityLabel={conversationAccessibilityLabel(item)}
      onPress={() => {
        AsyncStorage.setItem(LAST_CONVERSATION_KEY, String(item.id)).catch(() => undefined);
        navigation.navigate("Chat", { conversationId: item.id, title, avatarUrl: item.avatar_url, presence: item.presence });
      }}
    >
      <PulseCommandAvatar label={title} imageUrl={item.avatar_url} active={active} tone={item.trust_state === "founder" ? "intelligence" : "default"} size={48} />
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

function emptyTitle(filter: ConversationFilter, query: string) {
  if (query) return "No matching conversations";
  if (filter === "unread") return "You're all caught up";
  if (filter === "all") return "Choose a chat";
  return `No ${filter} conversations`;
}

function emptyBody(filter: ConversationFilter, query: string) {
  if (query) return "Try a different person, room, or message.";
  if (filter === "unread") return "New unread conversations will appear here.";
  return "Your conversations and composer open instantly here.";
}

const styles = StyleSheet.create({
  permissionPage: { flex: 1, justifyContent: "center", padding: 16 },
  list: { gap: 4, padding: 8, paddingBottom: 108 },
  headerStack: { gap: 6 },
  searchRow: { alignItems: "center", flexDirection: "row", gap: 6 },
  searchField: { flex: 1 },
  gearButton: { alignItems: "center", backgroundColor: "#0c1830", borderColor: "#24546b", borderRadius: 12, borderWidth: 1, height: 44, justifyContent: "center", width: 44 },
  gearButtonText: { color: "#61e9f6", fontSize: 21 },
  presenceRail: { gap: 10, paddingHorizontal: 1, paddingVertical: 2 },
  presenceItem: { alignItems: "center", gap: 3, width: 58 },
  presenceName: { color: colors.muted, fontSize: 10, maxWidth: 58 },
  quickActions: { flexDirection: "row", gap: 6, padding: 5 },
  quickAction: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: StyleSheet.hairlineWidth, flex: 1, flexDirection: "row", gap: 6, minHeight: 56, padding: 6 },
  quickActionPrimary: { backgroundColor: "rgba(77,228,196,0.9)", borderColor: "rgba(132,255,228,0.96)", shadowColor: colors.accent, shadowOpacity: 0.28, shadowRadius: 12 },
  quickActionIcon: { alignItems: "center", backgroundColor: "rgba(97,233,246,0.08)", borderColor: "rgba(97,233,246,0.22)", borderRadius: 9, borderWidth: 1, height: 30, justifyContent: "center", width: 30 },
  quickActionIconText: { color: colors.accentStrong, fontSize: 16, fontWeight: "900" },
  quickActionCopy: { flex: 1, minWidth: 0 },
  quickActionTitle: { color: colors.text, fontSize: 11, fontWeight: "900" },
  quickActionPrimaryText: { color: "#061410" },
  quickActionSubtitle: { color: colors.muted, fontSize: 9, marginTop: 1 },
  quickActionPrimarySubtitle: { color: "rgba(6,20,16,0.68)" },
  sectionLabel: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 0.7, textTransform: "uppercase" },
  peopleResults: { gap: 4 },
  personRow: { alignItems: "center", backgroundColor: "rgba(105,218,240,0.045)", borderColor: "rgba(105,218,240,0.18)", borderRadius: 11, borderWidth: 1, flexDirection: "row", gap: 8, minHeight: 56, padding: 7 },
  personCopy: { flex: 1, minWidth: 0 },
  personAction: { color: colors.accent, fontSize: 11, fontWeight: "900" },
  row: { alignItems: "center", backgroundColor: "rgba(9,18,34,0.9)", borderColor: "rgba(105,218,240,0.11)", borderRadius: 13, borderWidth: 1, flexDirection: "row", gap: 9, minHeight: 64, padding: 9 },
  rowPressed: { backgroundColor: "rgba(105,218,240,0.06)", borderColor: "rgba(105,218,240,0.25)" },
  pinnedRow: { borderColor: "rgba(77,228,196,0.56)", shadowColor: colors.accent, shadowOpacity: 0.1, shadowRadius: 10 },
  rowBody: { flex: 1, gap: 2, minWidth: 0 },
  rowTop: { alignItems: "center", flexDirection: "row", gap: 6 },
  rowSignals: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  title: { color: colors.text, flex: 1, fontSize: 14, fontWeight: "900" },
  muted: { color: colors.muted, fontSize: 12, lineHeight: 16 },
  time: { color: colors.muted, fontSize: 10 },
  signalPill: { borderColor: colors.border, borderRadius: logiNexus.radius.capsule, borderWidth: StyleSheet.hairlineWidth, color: colors.muted, fontSize: 9, fontWeight: "800", paddingHorizontal: 6, paddingVertical: 2, textTransform: "uppercase" },
  badge: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 12, minHeight: 23, minWidth: 23, paddingHorizontal: 6, paddingVertical: 2 },
  badgeText: { color: "#08110f", fontSize: 11, fontWeight: "900" },
  error: { color: colors.warning, fontSize: 12 },
  retryButton: { alignSelf: "center", backgroundColor: colors.signalDim, borderColor: colors.accent, borderRadius: 10, borderWidth: 1, marginTop: 8, paddingHorizontal: 14, paddingVertical: 9 },
  retryText: { color: colors.accent, fontSize: 12, fontWeight: "900" }
});
