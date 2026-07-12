import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useNavigation } from "@react-navigation/native";
import { useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { loadCachedConversations, listConversations, MessengerConversation, searchMessenger } from "../api/messenger";
import { PulseCommandAction, PulseCommandAvatar, PulseCommandPanel, PulseCommandSearch, PulseCommandSegmentRail } from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
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

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const qaFilter = String(process.env.EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER || "").toLowerCase();
  const validQaFilter = ["all", "direct", "groups", "rooms", "ai", "unread"].includes(qaFilter)
    ? qaFilter as ConversationFilter
    : null;
  const [selectedFilter, setSelectedFilter] = useState<ConversationFilter>(validQaFilter || "all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function load({ refresh = false } = {}) {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = query.trim() ? (await searchMessenger(query.trim())).conversations : await listConversations();
      setConversations(data);
    } catch (loadError) {
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
    load().catch(() => undefined);
  }, []);

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

  return (
    <LogiNexusScreenShell>
      {loading && conversations.length === 0 ? (
        <LogiNexusStatePanel state="loading" title="Loading conversations" body="Connecting to PulseSoc Messenger." loading />
      ) : (
        <FlatList
          data={filteredConversations}
          keyExtractor={(item) => `chat-${item.id}`}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          ListHeaderComponent={
            <View style={styles.headerStack}>
              <View style={styles.productionHeader}>
                <View style={styles.headerCopy}>
                  <Text style={styles.commandTitle}>Pulse Command</Text>
                  <Text style={styles.commandVersion}>Messenger V3</Text>
                </View>
                <View style={[styles.connectionDot, error && styles.connectionDotWarning]} accessibilityLabel={error ? "Messenger reconnecting" : "Messenger connected"} />
                <PulseCommandAction compact label="New chat" onPress={() => navigation.navigate("Search", { title: "New conversation" })} />
              </View>
              <PulseCommandSearch value={query} onChangeText={setQuery} placeholder="Search people, rooms, and messages" />
              <PulseCommandSegmentRail items={filters} selected={selectedFilter} onSelect={(key) => setSelectedFilter(key as ConversationFilter)} />
              <PulseCommandPanel style={styles.quickActions}>
                <QuickAction title="New Chat" subtitle="Direct message" onPress={() => navigation.navigate("Search", { title: "New conversation" })} />
                <QuickAction title="Create Group" subtitle="Invite members" onPress={() => navigation.navigate("Tabs", { screen: "Groups" })} />
                <QuickAction title="Start Room" subtitle="Public or private" onPress={() => navigation.navigate("Tabs", { screen: "Groups" })} />
              </PulseCommandPanel>
              {error ? <Text accessibilityLiveRegion="polite" style={styles.error}>Showing cached conversations while Messenger reconnects.</Text> : null}
              <Text style={styles.sectionLabel}>Recent conversations</Text>
            </View>
          }
          ListEmptyComponent={
            <LogiNexusStatePanel state="empty" title={emptyTitle(selectedFilter, query)} body={emptyBody(selectedFilter, query)} />
          }
          renderItem={({ item }) => <ConversationRow item={item} navigation={navigation} />}
        />
      )}
    </LogiNexusScreenShell>
  );
}

function QuickAction({ title, subtitle, onPress }: { title: string; subtitle: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${title}, ${subtitle}`} style={({ pressed }) => [styles.quickAction, pressed && styles.rowPressed]} onPress={onPress}>
      <Text style={styles.quickActionTitle}>{title}</Text>
      <Text style={styles.quickActionSubtitle}>{subtitle}</Text>
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
      onPress={() => navigation.navigate("Chat", { conversationId: item.id, title })}
    >
      <PulseCommandAvatar label={title} active={active} tone={item.trust_state === "founder" ? "intelligence" : "default"} />
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
  list: { gap: 6, padding: 10, paddingBottom: 116 },
  headerStack: { gap: 10 },
  productionHeader: { alignItems: "center", flexDirection: "row", gap: 10, paddingHorizontal: 4 },
  headerCopy: { flex: 1 },
  commandTitle: { color: colors.text, fontSize: 22, fontWeight: "900" },
  commandVersion: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  connectionDot: { backgroundColor: colors.safety, borderRadius: 6, height: 10, width: 10 },
  connectionDotWarning: { backgroundColor: colors.warning },
  quickActions: { flexDirection: "row", gap: 6, padding: 6 },
  quickAction: { borderColor: colors.border, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, flex: 1, minHeight: 56, padding: 8 },
  quickActionTitle: { color: colors.text, fontSize: 11, fontWeight: "900" },
  quickActionSubtitle: { color: colors.muted, fontSize: 9, marginTop: 3 },
  sectionLabel: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 0.7, textTransform: "uppercase" },
  row: { alignItems: "center", backgroundColor: "rgba(255,255,255,0.028)", borderColor: "rgba(255,255,255,0.06)", borderRadius: 12, borderWidth: 1, flexDirection: "row", gap: 10, minHeight: 70, padding: 10 },
  rowPressed: { backgroundColor: "rgba(105,218,240,0.06)", borderColor: "rgba(105,218,240,0.25)" },
  pinnedRow: { borderColor: "rgba(189,132,255,0.48)" },
  rowBody: { flex: 1, gap: 3, minWidth: 0 },
  rowTop: { alignItems: "center", flexDirection: "row", gap: 6 },
  rowSignals: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  title: { color: colors.text, flex: 1, fontSize: 14, fontWeight: "900" },
  muted: { color: colors.muted, fontSize: 12, lineHeight: 16 },
  time: { color: colors.muted, fontSize: 10 },
  signalPill: { borderColor: colors.border, borderRadius: logiNexus.radius.capsule, borderWidth: StyleSheet.hairlineWidth, color: colors.muted, fontSize: 9, fontWeight: "800", paddingHorizontal: 6, paddingVertical: 2, textTransform: "uppercase" },
  badge: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 12, minHeight: 23, minWidth: 23, paddingHorizontal: 6, paddingVertical: 2 },
  badgeText: { color: "#08110f", fontSize: 11, fontWeight: "900" },
  error: { color: colors.warning, fontSize: 12 }
});
