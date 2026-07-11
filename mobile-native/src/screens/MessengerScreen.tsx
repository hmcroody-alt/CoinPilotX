import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { loadCachedConversations, listConversations, MessengerConversation, searchMessenger } from "../api/messenger";
import { PulseCall, getActiveCalls } from "../api/calls";
import { joinRoom, listGroups, listRooms, openGroupChat, PulseGroup, PulseRoom } from "../api/groups";
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
import { formatShortTime } from "../utils/format";

type PulseCommandTab = "chats" | "calls" | "groups" | "rooms";
type PulseCommandListItem = MessengerConversation | PulseCall | PulseGroup | PulseRoom;

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const [activeCalls, setActiveCalls] = useState<PulseCall[]>([]);
  const [groups, setGroups] = useState<PulseGroup[]>([]);
  const [rooms, setRooms] = useState<PulseRoom[]>([]);
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
      const [callData, groupData, roomData] = await Promise.allSettled([
        getActiveCalls(),
        listGroups({ query: query.trim(), limit: 20 }),
        listRooms()
      ]);
      if (callData.status === "fulfilled") setActiveCalls(callData.value.calls || []);
      if (groupData.status === "fulfilled") {
        setGroups(groupData.value.groups || []);
        if (groupData.value.rooms?.length) setRooms(groupData.value.rooms || []);
      }
      if (roomData.status === "fulfilled") setRooms(roomData.value || []);
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
  const activeSignal = useMemo(() => conversations.filter((item) => isActivePresence(item.presence)).slice(0, 12), [conversations]);
  const tabItems = useMemo(
    () => [
      { key: "chats", label: "Chats", count: unreadTotal },
      { key: "calls", label: "Calls", count: activeCalls.length },
      { key: "groups", label: "Groups", count: groups.length },
      { key: "rooms", label: "Rooms", count: rooms.reduce((total, room) => total + Number(room.unread_count || 0), 0) }
    ],
    [activeCalls.length, groups.length, rooms, unreadTotal]
  );
  const listData = useMemo<PulseCommandListItem[]>(() => {
    if (selectedTab === "calls") return activeCalls;
    if (selectedTab === "groups") return groups;
    if (selectedTab === "rooms") return rooms;
    return conversations;
  }, [activeCalls, conversations, groups, rooms, selectedTab]);

  function handleTabSelect(next: string) {
    setSelectedTab(next as PulseCommandTab);
  }

  async function openGroup(item: PulseGroup) {
    try {
      const result = await openGroupChat(item.slug);
      if (result.conversation_id) navigation.navigate("Chat", { conversationId: result.conversation_id, title: `${item.name} Chat` });
      else navigation.navigate("GroupDetail", { groupSlug: item.slug, title: item.name });
    } catch {
      navigation.navigate("GroupDetail", { groupSlug: item.slug, title: item.name });
    }
  }

  async function openRoom(item: PulseRoom) {
    try {
      let conversationId = Number(item.conversation_id || 0);
      if (!conversationId) {
        const result = await joinRoom(item.room_id || item.id);
        conversationId = Number(result.conversation_id || 0);
      }
      if (conversationId) navigation.navigate("Chat", { conversationId, title: item.title || item.name });
      else navigation.navigate("Tabs", { screen: "Groups" });
    } catch {
      navigation.navigate("Tabs", { screen: "Groups" });
    }
  }

  return (
    <LogiNexusScreenShell>
      {loading && conversations.length === 0 ? (
        <LogiNexusStatePanel state="loading" title="Loading Pulse Command" body="Synchronizing conversations, calls, and unread signals." loading />
      ) : (
        <FlatList<PulseCommandListItem>
          data={listData}
          keyExtractor={(item) => itemKey(selectedTab, item)}
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
                    <Pressable accessibilityRole="button" accessibilityLabel={`Open ${conversationDisplayTitle(item)}`} style={styles.signalItem} onPress={() => navigation.navigate("Chat", { conversationId: item.id, title: conversationDisplayTitle(item) })}>
                      <PulseCommandAvatar label={conversationDisplayTitle(item)} active={isActivePresence(item.presence)} />
                      <Text style={styles.signalName} numberOfLines={1}>{conversationDisplayTitle(item)}</Text>
                    </Pressable>
                  )}
                />
              </PulseCommandPanel>
              {error ? <Text style={styles.error}>{error}</Text> : null}
            </View>
          }
          ListEmptyComponent={<LogiNexusStatePanel state="empty" title={emptyTitle(selectedTab, query)} body={emptyBody(selectedTab, query)} />}
          renderItem={({ item }) => {
            if (selectedTab === "calls") return <CallRow item={item as PulseCall} onOpen={() => navigation.navigate("Call", { callId: (item as PulseCall).call_id, title: "PulseSoc Call" })} />;
            if (selectedTab === "groups") return <GroupRow item={item as unknown as PulseGroup} onOpen={() => openGroup(item as unknown as PulseGroup)} onDetail={() => navigation.navigate("GroupDetail", { groupSlug: (item as unknown as PulseGroup).slug, title: (item as unknown as PulseGroup).name })} />;
            if (selectedTab === "rooms") return <RoomRow item={item as unknown as PulseRoom} onOpen={() => openRoom(item as unknown as PulseRoom)} />;
            return <ConversationRow item={item as unknown as MessengerConversation} navigation={navigation} />;
          }}
        />
      )}
    </LogiNexusScreenShell>
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
        <Text style={styles.muted} numberOfLines={1}>
          {conversationPreview(item)}
        </Text>
        <View style={styles.rowSignals}>
          {conversationSignalBadges(item).map((badge) => (
            <Text key={badge} style={styles.signalPill}>{badge}</Text>
          ))}
        </View>
      </View>
      {item.other_public_player_id || item.public_player_id ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Open profile for ${title}`}
          style={styles.profileButton}
          onPress={() =>
            navigation.navigate("ProfileDetail", {
              profileKey: item.other_public_player_id || item.public_player_id,
              title
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
  );
}

function CallRow({ item, onOpen }: { item: PulseCall; onOpen: () => void }) {
  const title = item.call_type === "video" ? "Video call" : "Voice call";
  return (
    <Pressable style={({ pressed }) => [styles.row, pressed && styles.rowPressed]} accessibilityRole="button" accessibilityLabel={`Open ${title}, ${item.status || "active"}`} onPress={onOpen}>
      <PulseCommandAvatar label={item.call_type === "video" ? "VC" : "AC"} active={!["ended", "missed", "declined", "failed"].includes(String(item.status || "").toLowerCase())} tone={item.status === "missed" ? "danger" : "default"} />
      <View style={styles.rowBody}>
        <View style={styles.rowTop}>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.time}>{formatShortTime(item.started_at || item.created_at)}</Text>
        </View>
        <Text style={styles.muted}>{item.room_name || item.public_id || item.call_id}</Text>
        <View style={styles.rowSignals}>
          <Text style={styles.signalPill}>{item.status || "ready"}</Text>
          {item.duration_seconds ? <Text style={styles.signalPill}>{item.duration_seconds}s</Text> : null}
        </View>
      </View>
    </Pressable>
  );
}

function GroupRow({ item, onOpen, onDetail }: { item: PulseGroup; onOpen: () => void; onDetail: () => void }) {
  return (
    <Pressable style={({ pressed }) => [styles.row, pressed && styles.rowPressed]} accessibilityRole="button" accessibilityLabel={`Open group ${item.name}`} onPress={onOpen}>
      <PulseCommandAvatar label={item.name} active={item.joined} tone="safety" />
      <View style={styles.rowBody}>
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={1}>{item.name}</Text>
          <Text style={styles.time}>{item.member_count || 0} members</Text>
        </View>
        <Text style={styles.muted} numberOfLines={1}>{item.description || "Community channel"}</Text>
        <View style={styles.rowSignals}>
          <Text style={styles.signalPill}>{item.category || "community"}</Text>
          {item.viewer_role ? <Text style={styles.signalPill}>{item.viewer_role}</Text> : null}
          {item.trust_level ? <Text style={styles.signalPill}>{item.trust_level}</Text> : null}
        </View>
      </View>
      <Pressable accessibilityRole="button" accessibilityLabel={`Open details for ${item.name}`} style={styles.profileButton} onPress={onDetail}>
        <Text style={styles.profileButtonText}>Details</Text>
      </Pressable>
    </Pressable>
  );
}

function RoomRow({ item, onOpen }: { item: PulseRoom; onOpen: () => void }) {
  return (
    <Pressable style={({ pressed }) => [styles.row, pressed && styles.rowPressed]} accessibilityRole="button" accessibilityLabel={`Open room ${item.title || item.name}`} onPress={onOpen}>
      <PulseCommandAvatar label={item.title || item.name} active={Number(item.online_count || 0) > 0} tone="intelligence" />
      <View style={styles.rowBody}>
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={1}>{item.title || item.name}</Text>
          <Text style={styles.time}>{item.online_count || 0} online</Text>
        </View>
        <Text style={styles.muted} numberOfLines={1}>{item.last_message || item.description || item.pinned_notice || "Room signal"}</Text>
        <View style={styles.rowSignals}>
          <Text style={styles.signalPill}>room</Text>
          {item.unread_count ? <Text style={styles.signalPill}>{item.unread_count} unread</Text> : null}
          {item.partial ? <Text style={styles.signalPill}>provider</Text> : null}
        </View>
      </View>
    </Pressable>
  );
}

function itemKey(tab: PulseCommandTab, item: MessengerConversation | PulseCall | PulseGroup | PulseRoom) {
  if (tab === "calls") return `call-${(item as PulseCall).call_id}`;
  if (tab === "groups") return `group-${(item as PulseGroup).slug}`;
  if (tab === "rooms") return `room-${(item as PulseRoom).id}`;
  return `chat-${(item as MessengerConversation).id}`;
}

function emptyTitle(tab: PulseCommandTab, query: string) {
  if (query) return "No matching transmissions";
  if (tab === "calls") return "No call signals";
  if (tab === "groups") return "No groups loaded";
  if (tab === "rooms") return "No rooms loaded";
  return "No conversations loaded yet";
}

function emptyBody(tab: PulseCommandTab, query: string) {
  if (query) return "Try a different name, group, room, or message signal.";
  if (tab === "calls") return "Active and recent call states appear here when the call engine returns them.";
  if (tab === "groups") return "Groups are server-authoritative and appear when your account can access them.";
  if (tab === "rooms") return "Rooms appear when existing PulseSoc room contracts expose them.";
  return "New conversations will appear here as soon as the server has them.";
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
  pinnedRow: {
    borderColor: "rgba(189, 132, 255, 0.48)"
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
