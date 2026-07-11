import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { loadCachedConversations, listConversations, MessengerConversation, searchMessenger } from "../api/messenger";
import { PulseCall, getActiveCalls } from "../api/calls";
import {
  PulseCommandAction,
  PulseCommandAvatar,
  PulseCommandHeader,
  PulseCommandMetric,
  PulseCommandPanel,
  PulseCommandSearch,
  PulseCommandSegmentRail
} from "../components/PulseCommand";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { compactPreview, formatShortTime } from "../utils/format";

type PulseCommandTab = "chats" | "calls" | "groups" | "rooms";

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const [activeCalls, setActiveCalls] = useState<PulseCall[]>([]);
  const [selectedTab, setSelectedTab] = useState<PulseCommandTab>("chats");
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
      getActiveCalls().then((data) => setActiveCalls(data.calls || [])).catch(() => undefined);
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
    loadCachedConversations().then((cached) => {
      if (cached.length) setConversations(cached);
    });
    load().catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      load().catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [query]);

  const unreadTotal = useMemo(() => conversations.reduce((total, item) => total + Number(item.unread_count || 0), 0), [conversations]);
  const activeSignal = useMemo(() => conversations.filter((item) => ["online", "active", "live"].includes(String(item.presence || "").toLowerCase())).slice(0, 12), [conversations]);
  const tabItems = useMemo(
    () => [
      { key: "chats", label: "Chats", count: unreadTotal },
      { key: "calls", label: "Calls", count: activeCalls.length },
      { key: "groups", label: "Groups" },
      { key: "rooms", label: "Rooms" }
    ],
    [activeCalls.length, unreadTotal]
  );

  function handleTabSelect(next: string) {
    const tab = next as PulseCommandTab;
    setSelectedTab(tab);
    if (tab === "groups" || tab === "rooms") navigation.navigate("Tabs", { screen: "Groups" });
    if (tab === "calls") navigation.navigate("Call", { title: "PulseSoc Calls" });
  }

  return (
    <LogiNexusScreenShell>
      {loading && conversations.length === 0 ? (
        <LogiNexusStatePanel state="loading" title="Loading Pulse Command" body="Synchronizing conversations, calls, and unread signals." loading />
      ) : (
        <FlatList
          data={conversations}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          ListHeaderComponent={
            <View style={styles.headerStack}>
              <PulseCommandHeader
                title="Pulse Command"
                subtitle="Messages, calls, groups, rooms, and UNDX in one secure nexus."
                status={error ? "Cached link" : "Live sync"}
                tone={error ? "warning" : "intelligence"}
                actions={
                  <View style={styles.headerActions}>
                    <PulseCommandAction compact label="New" onPress={() => navigation.navigate("Search", { title: "New conversation" })} />
                    <PulseCommandAction compact label="Safety" tone="safety" onPress={() => navigation.navigate("SafetyHub", { title: "Safety Hub", section: "blocks" })} />
                  </View>
                }
              />
              <View style={styles.metrics}>
                <PulseCommandMetric value={conversations.length} label="channels" />
                <PulseCommandMetric value={unreadTotal} label="unread" tone={unreadTotal ? "warning" : "default"} />
                <PulseCommandMetric value={activeCalls.length} label="active calls" tone={activeCalls.length ? "danger" : "default"} />
              </View>
              <PulseCommandSearch value={query} onChangeText={setQuery} placeholder="Search chats, groups, or messages" />
              <PulseCommandSegmentRail items={tabItems} selected={selectedTab} onSelect={handleTabSelect} />
              <PulseCommandPanel style={styles.signalRail}>
                <View style={styles.signalHeader}>
                  <Text style={styles.signalTitle}>Active signal rail</Text>
                  <Text style={styles.signalSubtitle}>{activeSignal.length ? "Authoritative presence when available" : "Presence appears when the server publishes it"}</Text>
                </View>
                <FlatList
                  horizontal
                  data={activeSignal.length ? activeSignal : conversations.slice(0, 8)}
                  keyExtractor={(item) => `signal-${item.id}`}
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.signalList}
                  renderItem={({ item }) => (
                    <Pressable accessibilityRole="button" accessibilityLabel={`Open ${item.title || item.name}`} style={styles.signalItem} onPress={() => navigation.navigate("Chat", { conversationId: item.id, title: item.title || item.name })}>
                      <PulseCommandAvatar label={item.title || item.name} active={["online", "active", "live"].includes(String(item.presence || "").toLowerCase())} />
                      <Text style={styles.signalName} numberOfLines={1}>{item.title || item.name || "Pulse"}</Text>
                    </Pressable>
                  )}
                />
              </PulseCommandPanel>
              {error ? <Text style={styles.error}>{error}</Text> : null}
            </View>
          }
          ListEmptyComponent={<LogiNexusStatePanel state="empty" title={query ? "No matching transmissions" : "No conversations loaded yet"} body={query ? "Try a different name, group, or message signal." : "New conversations will appear here as soon as the server has them."} />}
          renderItem={({ item }) => (
            <Pressable
              style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
              accessibilityRole="button"
              accessibilityLabel={`Open ${item.title || item.name || `conversation ${item.id}`}${Number(item.unread_count || 0) ? `, ${item.unread_count} unread` : ""}`}
              onPress={() => navigation.navigate("Chat", { conversationId: item.id, title: item.title || item.name })}
            >
              <PulseCommandAvatar label={item.title || item.name} active={["online", "active", "live"].includes(String(item.presence || "").toLowerCase())} />
              <View style={styles.rowBody}>
                <View style={styles.rowTop}>
                  <Text style={styles.title} numberOfLines={1}>{item.title || item.name || `Conversation ${item.id}`}</Text>
                  <Text style={styles.time}>{formatShortTime(item.last_activity_at || item.updated_at)}</Text>
                </View>
                <Text style={styles.muted} numberOfLines={1}>
                  {compactPreview(item.latest_message || item.last_message_preview, "Open chat")}
                </Text>
                <View style={styles.rowSignals}>
                  <Text style={styles.signalPill}>{item.conversation_type || "direct"}</Text>
                  {item.presence ? <Text style={styles.signalPill}>{item.presence}</Text> : null}
                </View>
              </View>
              {item.other_public_player_id || item.public_player_id ? (
                <Pressable
                  style={styles.profileButton}
                  onPress={() =>
                    navigation.navigate("ProfileDetail", {
                      profileKey: item.other_public_player_id || item.public_player_id,
                      title: item.title || "Profile"
                    })
                  }
                >
                  <Text style={styles.profileButtonText}>Profile</Text>
                </Pressable>
              ) : null}
              {Number(item.unread_count || 0) > 0 ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{item.unread_count}</Text>
                </View>
              ) : null}
            </Pressable>
          )}
        />
      )}
    </LogiNexusScreenShell>
  );
}

const styles = StyleSheet.create({
  list: {
    gap: logiNexus.spacing.md,
    padding: logiNexus.spacing.lg,
    paddingBottom: 116
  },
  headerActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.sm
  },
  headerStack: {
    gap: logiNexus.spacing.md
  },
  metrics: {
    flexDirection: "row",
    gap: logiNexus.spacing.sm
  },
  row: {
    alignItems: "center",
    backgroundColor: colors.glass,
    borderColor: "rgba(97,216,255,0.24)",
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    minHeight: 82,
    padding: logiNexus.spacing.md
  },
  rowPressed: {
    backgroundColor: colors.surfaceRaised
  },
  rowBody: {
    flex: 1,
    gap: 5,
    minWidth: 0
  },
  rowSignals: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6
  },
  rowTop: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  title: {
    color: colors.text,
    flex: 1,
    fontSize: 16,
    fontWeight: "900"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  profileButton: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  profileButtonText: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900"
  },
  time: {
    color: colors.muted,
    fontSize: 12
  },
  signalHeader: {
    gap: 2
  },
  signalItem: {
    alignItems: "center",
    gap: 6,
    width: 74
  },
  signalList: {
    gap: logiNexus.spacing.md,
    paddingTop: logiNexus.spacing.md
  },
  signalName: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    maxWidth: 72,
    textAlign: "center"
  },
  signalPill: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    paddingHorizontal: 7,
    paddingVertical: 3,
    textTransform: "uppercase"
  },
  signalRail: {
    paddingBottom: logiNexus.spacing.md
  },
  signalSubtitle: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  signalTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  badge: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 11,
    minWidth: 22,
    paddingHorizontal: 6,
    paddingVertical: 3
  },
  badgeText: {
    color: "#08110f",
    fontSize: 12,
    fontWeight: "900"
  },
  error: {
    color: colors.warning,
    fontSize: 13
  }
});
