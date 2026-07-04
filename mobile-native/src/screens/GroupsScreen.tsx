import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  getGroupDetail,
  joinGroup,
  joinRoom,
  leaveGroup,
  listGroups,
  listRooms,
  loadCachedGroupDetail,
  loadCachedGroups,
  openGroupChat,
  PulseGroup,
  PulseGroupPost,
  PulseRoom,
  reportGroup
} from "../api/groups";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "GroupDetail">>;

export function GroupsScreen({ route, navigation }: Props) {
  const initialSlug = route?.params?.groupSlug || "";
  const [groups, setGroups] = useState<PulseGroup[]>([]);
  const [rooms, setRooms] = useState<PulseRoom[]>([]);
  const [selected, setSelected] = useState<PulseGroup | null>(null);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");

  async function load(mode: "initial" | "refresh" | "more" | "search" = "initial", nextQuery = query) {
    if (mode === "more" && (!hasMore || loadingMore)) return;
    const nextOffset = mode === "more" ? offset : 0;
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    if (mode === "more") setLoadingMore(true);
    try {
      const data = await listGroups({ query: nextQuery, limit: 40, offset: nextOffset });
      const nextGroups = mode === "more" ? mergeGroups(groups, data.groups || []) : data.groups || [];
      setGroups(nextGroups);
      setRooms(data.rooms || []);
      setOffset(Number(data.next_offset || nextOffset + (data.groups?.length || 0)));
      setHasMore(Boolean(data.has_more));
      if (initialSlug && !selected) {
        const focused = nextGroups.find((group) => group.slug === initialSlug);
        if (focused) openDetail(focused).catch(() => undefined);
      }
    } catch (loadError) {
      const cached = await loadCachedGroups();
      if (cached) {
        setGroups(cached.groups || []);
        setRooms(cached.rooms || []);
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Groups could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
      setLoadingMore(false);
    }
  }

  async function openDetail(group: PulseGroup) {
    setSelected(group);
    try {
      const detail = await getGroupDetail(group.slug);
      if (detail.group) setSelected(detail.group);
    } catch (detailError) {
      const cached = await loadCachedGroupDetail(group.slug);
      if (cached?.group) setSelected(cached.group);
      else setError(detailError instanceof Error ? detailError.message : "Group detail could not load.");
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [initialSlug]);

  useEffect(() => {
    const timer = setTimeout(() => load("search", query).catch(() => undefined), 320);
    return () => clearTimeout(timer);
  }, [query]);

  const categories = useMemo(() => Array.from(new Set(groups.map((group) => group.category || "Community"))).slice(0, 8), [groups]);

  async function handleJoin(group: PulseGroup) {
    setBusyKey(`group-${group.slug}`);
    setError("");
    try {
      const result = group.joined ? await leaveGroup(group.slug) : await joinGroup(group.slug);
      const action = result as { joined?: boolean; left?: boolean; member_count?: number };
      const nextJoined = group.joined ? !Boolean(action.left) : Boolean(action.joined);
      updateGroup(group.slug, {
        joined: nextJoined,
        member_count: Number(action.member_count ?? group.member_count ?? 0),
        viewer_role: nextJoined ? group.viewer_role || "member" : ""
      });
      if (selected?.slug === group.slug) setSelected((current) => current ? { ...current, joined: nextJoined, member_count: Number(action.member_count ?? current.member_count ?? 0) } : current);
    } catch (joinError) {
      setError(joinError instanceof Error ? joinError.message : "Membership action failed.");
    } finally {
      setBusyKey("");
    }
  }

  async function handleOpenChat(group: PulseGroup) {
    setBusyKey(`chat-${group.slug}`);
    setError("");
    try {
      const result = await openGroupChat(group.slug);
      if (result.conversation_id && navigation) navigation.navigate("Chat", { conversationId: result.conversation_id, title: `${group.name} Chat` });
      else setError(result.message || "Group chat is not available yet.");
    } catch (chatError) {
      setError(chatError instanceof Error ? chatError.message : "Group chat could not be opened.");
    } finally {
      setBusyKey("");
    }
  }

  async function handleReport(group: PulseGroup) {
    setBusyKey(`report-${group.slug}`);
    try {
      const result = await reportGroup(group.slug, "Needs review");
      setError(result.message || "Group report sent.");
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : "Group report failed.");
    } finally {
      setBusyKey("");
    }
  }

  async function handleOpenRoom(room: PulseRoom) {
    setBusyKey(`room-${room.id}`);
    setError("");
    try {
      let conversationId = Number(room.conversation_id || 0);
      if (!conversationId) {
        const result = await joinRoom(room.room_id || room.id);
        conversationId = Number(result.conversation_id || 0);
      }
      if (conversationId && navigation) navigation.navigate("Chat", { conversationId, title: room.title || room.name });
      else setError("Room chat is not available yet.");
    } catch (roomError) {
      setError(roomError instanceof Error ? roomError.message : "Room could not be opened.");
    } finally {
      setBusyKey("");
    }
  }

  async function refreshRooms() {
    try {
      setRooms(await listRooms());
    } catch {
      // The main groups load already handles offline state; room refresh is a secondary enhancement.
    }
  }

  function updateGroup(slug: string, next: Partial<PulseGroup>) {
    setGroups((current) => current.map((group) => (group.slug === slug ? { ...group, ...next } : group)));
  }

  if (loading && !groups.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Communities</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        data={groups}
        keyExtractor={(item) => item.slug}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => {
          refreshRooms().catch(() => undefined);
          load("refresh").catch(() => undefined);
        }} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>Communities</Text>
            <Text style={styles.subtitle}>{offline ? "Showing saved communities" : "Groups, communities, and PulseSoc rooms"}</Text>
            <TextInput
              style={styles.searchInput}
              value={query}
              onChangeText={setQuery}
              placeholder="Search communities"
              placeholderTextColor={colors.muted}
              returnKeyType="search"
            />
            {categories.length ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
                {categories.map((category) => (
                  <Pressable key={category} style={styles.filter} onPress={() => setQuery(category)}>
                    <Text style={styles.filterText}>{category}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            ) : null}
            <Text style={styles.sectionTitle}>Rooms</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.roomRow}>
              {rooms.map((room) => (
                <RoomCard key={room.id} room={room} busy={busyKey === `room-${room.id}`} onOpen={handleOpenRoom} />
              ))}
            </ScrollView>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{error ? "Communities unavailable" : "No communities found"}</Text>
            <Text style={styles.emptyText}>{error || "PulseSoc communities will appear here when the existing backend returns them."}</Text>
          </View>
        }
        renderItem={({ item }) => (
          <GroupCard
            group={item}
            busy={busyKey.endsWith(item.slug)}
            onOpen={openDetail}
            onJoin={handleJoin}
            onChat={handleOpenChat}
            onReport={handleReport}
          />
        )}
        onEndReached={() => load("more").catch(() => undefined)}
        onEndReachedThreshold={0.35}
        ListFooterComponent={loadingMore ? <ActivityIndicator style={styles.footer} color={colors.accent} /> : null}
      />
      {selected ? (
        <GroupDetail
          group={selected}
          busyKey={busyKey}
          onClose={() => setSelected(null)}
          onJoin={handleJoin}
          onChat={handleOpenChat}
          onReport={handleReport}
        />
      ) : null}
    </View>
  );
}

function GroupCard({ group, busy, onOpen, onJoin, onChat, onReport }: {
  group: PulseGroup;
  busy?: boolean;
  onOpen: (group: PulseGroup) => void;
  onJoin: (group: PulseGroup) => void;
  onChat: (group: PulseGroup) => void;
  onReport: (group: PulseGroup) => void;
}) {
  return (
    <Pressable style={styles.card} onPress={() => onOpen(group)}>
      {group.cover_image_url ? <Image source={{ uri: group.cover_image_url }} style={styles.cover} /> : <View style={styles.coverFallback}><Text style={styles.coverText}>{(group.name || "P").slice(0, 1)}</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardType}>{group.category || "Community"} · {group.group_type || "public"}</Text>
        <Text style={styles.cardTitle} numberOfLines={1}>{group.name}</Text>
        <Text style={styles.cardText} numberOfLines={2}>{group.description || "PulseSoc community"}</Text>
        <View style={styles.pillRow}>
          <Text style={styles.pill}>{group.member_count || 0} members</Text>
          <Text style={styles.pill}>{group.post_count || 0} posts</Text>
          <Text style={styles.pill}>{group.trust_level || "standard"}</Text>
          {group.viewer_role ? <Text style={styles.pill}>{group.viewer_role}</Text> : null}
        </View>
        <View style={styles.actionRow}>
          <Pressable style={styles.smallButton} disabled={busy} onPress={() => onJoin(group)}>
            <Text style={styles.smallButtonText}>{group.joined ? "Leave" : "Join"}</Text>
          </Pressable>
          <Pressable style={styles.smallButton} disabled={busy} onPress={() => onChat(group)}>
            <Text style={styles.smallButtonText}>Chat</Text>
          </Pressable>
          <Pressable style={styles.smallButton} disabled={busy} onPress={() => onReport(group)}>
            <Text style={styles.smallButtonText}>Report</Text>
          </Pressable>
        </View>
      </View>
    </Pressable>
  );
}

function RoomCard({ room, busy, onOpen }: { room: PulseRoom; busy?: boolean; onOpen: (room: PulseRoom) => void }) {
  return (
    <Pressable style={styles.roomCard} disabled={busy} onPress={() => onOpen(room)}>
      <Text style={styles.roomTitle} numberOfLines={1}>{room.title || room.name}</Text>
      <Text style={styles.roomText} numberOfLines={2}>{room.description || room.pinned_notice || "PulseSoc room"}</Text>
      <Text style={styles.cardMeta}>{room.online_count || 0} active · {room.unread_count || 0} unread</Text>
    </Pressable>
  );
}

function GroupDetail({ group, busyKey, onClose, onJoin, onChat, onReport }: {
  group: PulseGroup;
  busyKey: string;
  onClose: () => void;
  onJoin: (group: PulseGroup) => void;
  onChat: (group: PulseGroup) => void;
  onReport: (group: PulseGroup) => void;
}) {
  return (
    <View style={styles.detailOverlay}>
      <View style={styles.detail}>
        <View style={styles.detailHeader}>
          <View style={styles.detailTitleWrap}>
            <Text style={styles.title} numberOfLines={1}>{group.name}</Text>
            <Text style={styles.subtitle}>{group.member_count || 0} members · {group.group_type || "public"} · {group.viewer_role || "not joined"}</Text>
          </View>
          <Pressable style={styles.smallButton} onPress={onClose}>
            <Text style={styles.smallButtonText}>Close</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.detailContent}>
          {group.cover_image_url ? <Image source={{ uri: group.cover_image_url }} style={styles.detailCover} /> : null}
          <Text style={styles.cardText}>{group.description || "PulseSoc community"}</Text>
          {group.rules ? (
            <View style={styles.rulesBox}>
              <Text style={styles.sectionTitle}>Rules</Text>
              <Text style={styles.cardText}>{group.rules}</Text>
            </View>
          ) : null}
          <View style={styles.actionRow}>
            <Pressable style={styles.primaryButton} disabled={Boolean(busyKey)} onPress={() => onJoin(group)}>
              <Text style={styles.primaryText}>{group.joined ? "Leave" : "Join"}</Text>
            </Pressable>
            <Pressable style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onChat(group)}>
              <Text style={styles.smallButtonText}>Open Chat</Text>
            </Pressable>
            <Pressable style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onReport(group)}>
              <Text style={styles.smallButtonText}>Report</Text>
            </Pressable>
          </View>
          <Text style={styles.sectionTitle}>Community Feed</Text>
          {(group.posts || []).length ? group.posts?.map((post) => <GroupPostCard key={post.id} post={post} />) : <Text style={styles.emptyText}>Group posts will appear here when the existing backend returns them.</Text>}
        </ScrollView>
      </View>
    </View>
  );
}

function GroupPostCard({ post }: { post: PulseGroupPost }) {
  return (
    <View style={styles.postCard}>
      <Text style={styles.cardType}>{post.pinned ? "Pinned · " : ""}{post.author_name || "PulseSoc Member"} · {formatShortTime(post.created_at)}</Text>
      {post.title ? <Text style={styles.cardTitle}>{post.title}</Text> : null}
      <Text style={styles.cardText}>{post.body || "Group update"}</Text>
      {post.media_url ? <Text style={styles.cardMeta}>Media attached</Text> : null}
    </View>
  );
}

function mergeGroups(current: PulseGroup[], incoming: PulseGroup[]) {
  const seen = new Set(current.map((group) => group.id));
  return [...current, ...incoming.filter((group) => !seen.has(group.id))];
}

const styles = StyleSheet.create({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginBottom: 12,
    padding: 12
  },
  cardBody: {
    flex: 1
  },
  cardMeta: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "800",
    marginTop: 8
  },
  cardText: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 4
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  cardType: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    marginBottom: 4,
    textTransform: "uppercase"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  content: {
    padding: 16,
    paddingBottom: 32
  },
  cover: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    height: 96,
    width: 96
  },
  coverFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 96,
    justifyContent: "center",
    width: 96
  },
  coverText: {
    color: colors.accent,
    fontSize: 28,
    fontWeight: "900"
  },
  detail: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    margin: 12,
    maxHeight: "94%"
  },
  detailContent: {
    padding: 16,
    paddingBottom: 28
  },
  detailCover: {
    borderRadius: 8,
    height: 170,
    marginBottom: 12,
    width: "100%"
  },
  detailHeader: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: 10,
    padding: 12
  },
  detailOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.58)",
    justifyContent: "center",
    zIndex: 20
  },
  detailTitleWrap: {
    flex: 1
  },
  empty: {
    alignItems: "center",
    padding: 24
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 20,
    textAlign: "center"
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900",
    marginBottom: 6
  },
  error: {
    color: "#ff9f9f",
    marginTop: 10
  },
  filter: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 34,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  filterRow: {
    gap: 8,
    paddingTop: 12
  },
  filterText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  footer: {
    marginVertical: 12
  },
  header: {
    marginBottom: 14
  },
  pill: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    paddingHorizontal: 8,
    paddingVertical: 5
  },
  pillRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 10
  },
  postCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 10,
    padding: 12
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 14
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  roomCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 126,
    padding: 12,
    width: 230
  },
  roomRow: {
    gap: 10,
    paddingBottom: 4,
    paddingTop: 10
  },
  roomText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 6
  },
  roomTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  rulesBox: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 14,
    padding: 12
  },
  searchInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 16,
    marginTop: 14,
    minHeight: 46,
    paddingHorizontal: 12
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900",
    marginTop: 16
  },
  smallButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 36,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  smallButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 4
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  }
});
