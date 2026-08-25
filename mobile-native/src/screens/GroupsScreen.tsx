import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useFocusEffect } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  FlatList,
  Image,
  KeyboardAvoidingView,
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
import {
  archiveGroup,
  createGroup,
  createRoom,
  deleteGroup,
  deleteRoom,
  getGroupDetail,
  joinGroup,
  joinRoom,
  leaveGroup,
  listGroups,
  listRooms,
  manageRoom,
  removeGroupMember,
  loadCachedGroupDetail,
  loadCachedGroups,
  openGroupChat,
  PulseGroup,
  PulseGroupAsset,
  PulseGroupInvitation,
  PulseGroupMember,
  PulseGroupPost,
  PulseRoom,
  PulseRoomParticipant,
  reportGroup,
  resolveRoomConversation,
  setGroupMemberRole
} from "../api/groups";
import { CommunityCreateIntent, takeCommunityCreateIntent } from "../community/communityCreateIntent";
import { PulseCommandAction, PulseCommandHeader, PulseCommandPanel, PulseCommandSearch } from "../components/PulseCommand";
import { LogiNexusStatePanel } from "../components/Screen";
import { useBottomNavSurface, useBottomNavVisibility } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import {
  groupAccessibilityLabel,
  groupActionRules,
  groupAssetCategoryLabel,
  groupDisplayTitle,
  groupInvitationAccessibilityLabel,
  groupInvitationStateLabel,
  groupMemberAccessibilityLabel,
  groupMemberActionRules,
  groupMemberRoleLabel,
  groupNotificationLabel,
  groupRoleLabel,
  groupSignalBadges,
  groupSummary,
  groupTypeLabel,
  roomAccessibilityLabel,
  roomActionRules,
  roomDisplayTitle,
  roomParticipantAccessibilityLabel,
  roomParticipantRoleLabel,
  roomProviderStateLabel,
  roomSignalBadges,
  roomSummary
} from "../pulseCommand/domain";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { formatShortTime } from "../utils/format";
import { createThemedStyles } from "../theme/themedStyles";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "GroupDetail">>;

export function GroupsScreen({ route, navigation }: Props) {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const { setBottomNavHidden } = useBottomNavVisibility();
  const initialSlug = route?.params?.groupSlug || "";
  const [groups, setGroups] = useState<PulseGroup[]>([]);
  const [rooms, setRooms] = useState<PulseRoom[]>([]);
  const [selected, setSelected] = useState<PulseGroup | null>(null);
  const [selectedRoom, setSelectedRoom] = useState<PulseRoom | null>(null);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [createKind, setCreateKind] = useState<CommunityCreateIntent | null>(null);

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

  useFocusEffect(useCallback(() => {
    const intent = takeCommunityCreateIntent();
    if (intent) setCreateKind(intent);
  }, []));

  useEffect(() => {
    if (!createKind) return;
    setBottomNavHidden(true);
    return () => setBottomNavHidden(false);
  }, [createKind, setBottomNavHidden]);

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
      const roomId = room.room_id || room.id;
      const result = room.current_user_role
        ? await resolveRoomConversation(roomId)
        : await joinRoom(roomId);
      const conversationId = Number(result.conversation_id || 0);
      if (conversationId && navigation) navigation.navigate("Chat", { conversationId, roomId, title: room.title || room.name });
      else setError("Room chat is not available yet.");
    } catch (roomError) {
      setError(roomError instanceof Error ? roomError.message : "Room could not be opened.");
    } finally {
      setBusyKey("");
    }
  }

  function confirmDeleteGroup(group: PulseGroup) {
    Alert.alert("Delete group?", "This archives its community conversation and removes it from active discovery.", [
      { text: "Cancel", style: "cancel" },
      { text: "Delete", style: "destructive", onPress: () => {
        setBusyKey(`delete-${group.slug}`);
        deleteGroup(group.slug).then(() => { setSelected(null); return load("refresh"); }).catch((cause) => setError(cause instanceof Error ? cause.message : "Group could not be deleted.")).finally(() => setBusyKey(""));
      } }
    ]);
  }

  async function handleArchiveGroup(group: PulseGroup) {
    setBusyKey(`archive-${group.slug}`);
    try { await archiveGroup(group.slug); setSelected(null); await load("refresh"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Group could not be archived."); }
    finally { setBusyKey(""); }
  }

  async function handleGroupMemberAction(group: PulseGroup, member: PulseGroupMember, action: "promote" | "demote" | "removeMember") {
    if (!member.user_id) return;
    const execute = async () => {
      setBusyKey(`member-${member.user_id}`);
      try {
        if (action === "removeMember") await removeGroupMember(group.slug, member.user_id!);
        else await setGroupMemberRole(group.slug, member.user_id!, action === "promote" ? "moderator" : "member");
        const detail = await getGroupDetail(group.slug);
        if (detail.group) setSelected(detail.group);
      } catch (cause) { setError(cause instanceof Error ? cause.message : "Member action failed."); }
      finally { setBusyKey(""); }
    };
    if (action === "removeMember") Alert.alert("Remove member?", `${member.display_name} will lose access to this group.`, [{ text: "Cancel", style: "cancel" }, { text: "Remove", style: "destructive", onPress: () => execute() }]);
    else await execute();
  }

  async function handleRoomLifecycle(room: PulseRoom, action: "archive" | "delete") {
    setBusyKey(`${action}-${room.id}`);
    try {
      if (action === "delete") await deleteRoom(room.room_id || room.id);
      else await manageRoom(room.room_id || room.id, "archive");
      setSelectedRoom(null);
      await refreshRooms();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Room could not be updated.");
    } finally { setBusyKey(""); }
  }

  function openRoomDetail(room: PulseRoom) {
    setSelectedRoom(room);
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
      <View style={styles.root}>
        <LogiNexusStatePanel state="loading" title="Loading community channels" body="Synchronizing groups, rooms, and permissions." loading style={styles.statePanel} />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        data={groups}
        keyExtractor={(item) => item.slug}
        {...dock.handlers}
        contentContainerStyle={[styles.content, dock.contentPadding]}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => {
          refreshRooms().catch(() => undefined);
          load("refresh").catch(() => undefined);
        }} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <PulseCommandHeader
              title="Groups & Rooms"
              subtitle={offline ? "Showing saved communities from local cache." : "Community channels, rooms, and moderated group spaces."}
              status={offline ? "Cached" : "Live sync"}
              tone={offline ? "warning" : "safety"}
              actions={navigation ? <PulseCommandAction compact label="Safety" tone="safety" onPress={() => navigation.navigate("SafetyHub", { section: "reports", title: "Safety Hub" })} /> : null}
            />
            <PulseCommandSearch value={query} onChangeText={setQuery} placeholder="Search communities and rooms" />
            <View style={styles.actionRow}>
              <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => setCreateKind("group")}><Text style={styles.primaryText}>Create Group</Text></Pressable>
              <Pressable accessibilityRole="button" style={styles.smallButton} onPress={() => setCreateKind("room")}><Text style={styles.smallButtonText}>Start Room</Text></Pressable>
            </View>
            {categories.length ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
                {categories.map((category) => (
                  <Pressable accessibilityRole="button" key={category} style={styles.filter} onPress={() => setQuery(category)}>
                    <Text style={styles.filterText}>{category}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            ) : null}
            <Text style={styles.sectionTitle}>Rooms</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.roomRow}>
              {rooms.map((room) => (
                <RoomCard key={room.id} room={room} busy={busyKey === `room-${room.id}`} onOpen={openRoomDetail} />
              ))}
              {!rooms.length ? (
                <PulseCommandPanel style={styles.roomEmpty}>
                  <Text style={styles.roomTitle}>No rooms right now</Text>
                  <Text style={styles.roomText}>Rooms appear here when one of your communities starts one.</Text>
                </PulseCommandPanel>
              ) : null}
            </ScrollView>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <LogiNexusStatePanel state={error ? "error" : "empty"} title={error ? "Communities unavailable" : "No communities found"} body={error || "Your communities will appear here once you join or create one."} style={styles.statePanel} />
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
        ListFooterComponent={loadingMore ? <Text style={styles.footer}>Loading more community signals...</Text> : null}
      />
      {selected ? (
        <GroupDetail
          group={selected}
          busyKey={busyKey}
          onClose={() => setSelected(null)}
          onJoin={handleJoin}
          onChat={handleOpenChat}
          onReport={handleReport}
          onDelete={confirmDeleteGroup}
          onArchive={handleArchiveGroup}
          onMemberAction={handleGroupMemberAction}
        />
      ) : null}
      {selectedRoom ? (
        <RoomDetail
          room={selectedRoom}
          busyKey={busyKey}
          onClose={() => setSelectedRoom(null)}
          onOpen={handleOpenRoom}
          onReport={(room) => setError(`Reporting for ${roomDisplayTitle(room)} is not available in the app yet. You can report it on the PulseSoc website.`)}
          onLifecycle={handleRoomLifecycle}
        />
      ) : null}
      {createKind ? (
        <CommunityCreateSheet
          kind={createKind}
          onClose={() => setCreateKind(null)}
          onCreated={async (result) => {
            setCreateKind(null);
            await load("refresh");
            if (result.conversationId && navigation) navigation.navigate("Chat", { conversationId: result.conversationId, roomId: result.roomId, title: result.title });
          }}
        />
      ) : null}
    </View>
  );
}

function CommunityCreateSheet({ kind, onClose, onCreated }: {
  kind: CommunityCreateIntent;
  onClose: () => void;
  onCreated: (result: { conversationId?: number; roomId?: string; title: string }) => Promise<void>;
}) {
  const insets = useSafeAreaInsets();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [privacy, setPrivacy] = useState<"public" | "private">("public");
  const [inviteIds, setInviteIds] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const label = kind === "group" ? "Create Group" : "Start Room";

  async function submit() {
    if (title.trim().length < 2) { setError(`Add a ${kind} name.`); return; }
    const inviteeUserIds = inviteIds.split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0);
    setBusy(true); setError("");
    try {
      if (kind === "group") {
        await createGroup({ name: title.trim(), description: description.trim(), privacy, inviteeUserIds });
        await onCreated({ title: title.trim() });
      } else {
        const result = await createRoom({ title: title.trim(), description: description.trim(), privacy, inviteeUserIds });
        await onCreated({ conversationId: Number(result.conversation_id || 0) || undefined, roomId: result.room_id, title: title.trim() });
      }
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : `${label} failed.`);
    } finally { setBusy(false); }
  }

  return (
    <View style={styles.detailOverlay}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.createSheetKeyboardAvoider}
      >
        <View style={styles.createSheet}>
          <View style={styles.detailHeader}>
            <View style={styles.detailTitleWrap}><Text style={styles.title}>{label}</Text><Text style={styles.subtitle}>Simple, text-based PulseSoc community space.</Text></View>
            <Pressable accessibilityRole="button" style={styles.smallButton} onPress={onClose}><Text style={styles.smallButtonText}>Close</Text></Pressable>
          </View>
          <ScrollView
            contentContainerStyle={[styles.detailContent, { paddingBottom: Math.max(insets.bottom, 16) + 20 }]}
            keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
            keyboardShouldPersistTaps="handled"
          >
            <Text style={styles.inputLabel}>{kind === "group" ? "Group name" : "Room title"}</Text>
            <TextInput accessibilityLabel={kind === "group" ? "Group name" : "Room title"} value={title} onChangeText={setTitle} placeholder={kind === "group" ? "Name your group" : "Name your room"} placeholderTextColor={colors.muted} style={styles.input} maxLength={140} />
            <Text style={styles.inputLabel}>Description</Text>
            <TextInput accessibilityLabel="Description" value={description} onChangeText={setDescription} placeholder="What is this community for?" placeholderTextColor={colors.muted} style={[styles.input, styles.multilineInput]} multiline maxLength={500} />
            <Text style={styles.inputLabel}>Privacy</Text>
            <View style={styles.actionRow}>{(["public", "private"] as const).map((value) => <Pressable key={value} accessibilityRole="button" accessibilityState={{ selected: privacy === value }} style={[styles.sectionChip, privacy === value && styles.sectionChipActive]} onPress={() => setPrivacy(value)}><Text style={[styles.sectionChipText, privacy === value && styles.sectionChipTextActive]}>{value === "public" ? "Public" : "Private"}</Text></Pressable>)}</View>
            <Text style={styles.inputLabel}>Optional invite Pulse IDs</Text>
            <TextInput accessibilityLabel="Optional invite Pulse IDs" value={inviteIds} onChangeText={setInviteIds} placeholder="Numeric IDs, separated by commas" placeholderTextColor={colors.muted} style={styles.input} keyboardType="numbers-and-punctuation" />
            <Text style={styles.helperText}>Only people you invite can join. Private rooms are visible only to invited participants.</Text>
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} disabled={busy} style={styles.primaryButton} onPress={() => submit()}><Text style={styles.primaryText}>{busy ? "Working…" : label}</Text></Pressable>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
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
  const actions = groupActionRules(group);
  const actionIsAvailable = (key: ReturnType<typeof groupActionRules>[number]["key"]) => actions.find((action) => action.key === key)?.available;
  return (
    <Pressable style={styles.card} accessibilityRole="button" accessibilityLabel={groupAccessibilityLabel(group)} onPress={() => onOpen(group)}>
      {group.cover_image_url ? <Image source={{ uri: group.cover_image_url }} style={styles.cover} /> : <View style={styles.coverFallback}><Text style={styles.coverText}>{groupDisplayTitle(group).slice(0, 1)}</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardType}>{groupTypeLabel(group)}</Text>
        <Text style={styles.cardTitle} numberOfLines={1}>{groupDisplayTitle(group)}</Text>
        <Text style={styles.cardText} numberOfLines={2}>{groupSummary(group)}</Text>
        <View style={styles.pillRow}>
          {groupSignalBadges(group).map((badge) => (
            <Text key={badge} style={styles.pill}>{badge}</Text>
          ))}
        </View>
        <View style={styles.actionRow}>
          {actionIsAvailable("join") || actionIsAvailable("leave") ? (
            <Pressable accessibilityRole="button" accessibilityLabel={actions[0]?.accessibilityLabel || "Membership action"} style={styles.smallButton} disabled={busy} onPress={() => onJoin(group)}>
              <Text style={styles.smallButtonText}>{actions[0]?.label || (group.joined ? "Leave" : "Join")}</Text>
            </Pressable>
          ) : null}
          {actionIsAvailable("openChat") ? <Pressable accessibilityRole="button" accessibilityLabel={`Open chat for ${groupDisplayTitle(group)}`} style={styles.smallButton} disabled={busy} onPress={() => onChat(group)}>
            <Text style={styles.smallButtonText}>Chat</Text>
          </Pressable> : null}
          {actionIsAvailable("reportGroup") ? <Pressable accessibilityRole="button" accessibilityLabel={`Report ${groupDisplayTitle(group)}`} style={styles.smallButton} disabled={busy} onPress={() => onReport(group)}>
            <Text style={styles.smallButtonText}>Report</Text>
          </Pressable> : null}
        </View>
      </View>
    </Pressable>
  );
}

function RoomCard({ room, busy, onOpen }: { room: PulseRoom; busy?: boolean; onOpen: (room: PulseRoom) => void }) {
  const primaryAction = roomActionRules(room).find((action) => action.key === "openRoom");
  return (
    <Pressable style={styles.roomCard} accessibilityRole="button" accessibilityLabel={roomAccessibilityLabel(room)} disabled={busy} onPress={() => onOpen(room)}>
      <Text style={styles.roomTitle} numberOfLines={1}>{roomDisplayTitle(room)}</Text>
      <Text style={styles.roomText} numberOfLines={2}>{roomSummary(room)}</Text>
      <View style={styles.pillRow}>
        {roomSignalBadges(room).map((badge) => (
          <Text key={badge} style={styles.pill}>{badge}</Text>
        ))}
      </View>
      <Text style={styles.cardMeta}>{primaryAction?.label || "Open Room"}</Text>
    </Pressable>
  );
}

function GroupDetail({ group, busyKey, onClose, onJoin, onChat, onReport, onDelete, onArchive, onMemberAction }: {
  group: PulseGroup;
  busyKey: string;
  onClose: () => void;
  onJoin: (group: PulseGroup) => void;
  onChat: (group: PulseGroup) => void;
  onReport: (group: PulseGroup) => void;
  onDelete: (group: PulseGroup) => void;
  onArchive: (group: PulseGroup) => void;
  onMemberAction: (group: PulseGroup, member: PulseGroupMember, action: "promote" | "demote" | "removeMember") => void;
}) {
  const sections: GroupDetailSection[] = ["overview", "members", "invitations", "media", "files", "links", "settings"];
  const [section, setSection] = useState<GroupDetailSection>("overview");
  const groupActions = groupActionRules(group);
  const actionIsAvailable = (key: ReturnType<typeof groupActionRules>[number]["key"]) => groupActions.find((action) => action.key === key)?.available;
  return (
    <View style={styles.detailOverlay}>
      <View style={styles.detail}>
        <View style={styles.detailHeader}>
          <View style={styles.detailTitleWrap}>
            <Text style={styles.title} numberOfLines={1}>{groupDisplayTitle(group)}</Text>
            <Text style={styles.subtitle}>{Number(group.member_count || 0)} members · {group.group_type || "public"} · {groupRoleLabel(group)}</Text>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={`Close ${groupDisplayTitle(group)} detail`} style={styles.smallButton} onPress={onClose}>
            <Text style={styles.smallButtonText}>Close</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.detailContent}>
          {group.cover_image_url ? <Image source={{ uri: group.cover_image_url }} style={styles.detailCover} /> : null}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sectionRail}>
            {sections.map((item) => (
              <Pressable
                key={item}
                accessibilityRole="tab"
                accessibilityState={{ selected: section === item }}
                style={[styles.sectionChip, section === item && styles.sectionChipActive]}
                onPress={() => setSection(item)}
              >
                <Text style={[styles.sectionChipText, section === item && styles.sectionChipTextActive]}>{groupDetailSectionLabel(item)}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <View style={styles.actionRow}>
            {actionIsAvailable("join") || actionIsAvailable("leave") ? <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.primaryButton} disabled={Boolean(busyKey)} onPress={() => onJoin(group)}>
              <Text style={styles.primaryText}>{groupActions[0]?.label || (group.joined ? "Leave" : "Join")}</Text>
            </Pressable> : null}
            {actionIsAvailable("openChat") ? <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onChat(group)}>
              <Text style={styles.smallButtonText}>Open Chat</Text>
            </Pressable> : null}
            {actionIsAvailable("reportGroup") ? <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onReport(group)}>
              <Text style={styles.smallButtonText}>Report</Text>
            </Pressable> : null}
          </View>
          <GroupDetailSectionView group={group} section={section} onDelete={onDelete} onArchive={onArchive} onMemberAction={onMemberAction} />
        </ScrollView>
      </View>
    </View>
  );
}

type GroupDetailSection = "overview" | "members" | "invitations" | "media" | "files" | "links" | "settings";

function groupDetailSectionLabel(section: GroupDetailSection) {
  return {
    overview: "Overview",
    members: "Members",
    invitations: "Invitations",
    media: "Media",
    files: "Files",
    links: "Links",
    settings: "Settings"
  }[section];
}

function GroupDetailSectionView({ group, section, onDelete, onArchive, onMemberAction }: { group: PulseGroup; section: GroupDetailSection; onDelete: (group: PulseGroup) => void; onArchive: (group: PulseGroup) => void; onMemberAction: (group: PulseGroup, member: PulseGroupMember, action: "promote" | "demote" | "removeMember") => void }) {
  if (section === "overview") return <GroupOverview group={group} />;
  if (section === "members") return <GroupMembers group={group} onMemberAction={onMemberAction} />;
  if (section === "invitations") return <GroupInvitations group={group} />;
  if (section === "media") return <GroupAssets title="Media" assets={group.media || []} emptyTitle="No indexed group media" emptyBody="Photos and videos shared in this group appear here, including anything attached to group posts." />;
  if (section === "files") return <GroupAssets title="Files" assets={group.files || []} emptyTitle="No group files yet" emptyBody="Files shared in chat are not listed here. This app does not read private chat history to build a file list." />;
  if (section === "links") return <GroupAssets title="Links" assets={group.links || []} emptyTitle="No shared links yet" emptyBody="Links shared in this group are not collected yet. When they are, you will find them listed here." />;
  return <GroupSettings group={group} onDelete={onDelete} onArchive={onArchive} />;
}

function GroupOverview({ group }: { group: PulseGroup }) {
  return (
    <View>
      <Text style={styles.sectionTitle}>Overview</Text>
      <Text style={styles.cardText}>{groupSummary(group)}</Text>
      <View style={styles.metricGrid}>
        <Metric label="Members" value={String(Number(group.member_count || 0))} />
        <Metric label="Posts" value={String(Number(group.post_count || 0))} />
        <Metric label="Role" value={groupRoleLabel(group)} />
        <Metric label="Notify" value={groupNotificationLabel(group)} />
      </View>
      {group.owner_name ? <Text style={styles.cardMeta}>Owner: {group.owner_name}</Text> : null}
      {group.rules ? (
        <View style={styles.rulesBox}>
          <Text style={styles.sectionTitle}>Rules</Text>
          <Text style={styles.cardText}>{group.rules}</Text>
        </View>
      ) : (
        <BoundaryPanel title="Rules unavailable" body="The current group contract did not return rules for this community." />
      )}
      <Text style={styles.sectionTitle}>Community Feed</Text>
      {(group.posts || []).length ? group.posts?.map((post) => <GroupPostCard key={post.id} post={post} />) : <Text style={styles.emptyText}>This group has no posts yet. Be the first to share something with the community.</Text>}
    </View>
  );
}

function GroupMembers({ group, onMemberAction }: { group: PulseGroup; onMemberAction: (group: PulseGroup, member: PulseGroupMember, action: "promote" | "demote" | "removeMember") => void }) {
  const members = group.members || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>Members and Roles</Text>
      {members.length ? members.map((member) => <GroupMemberRow key={member.id} group={group} member={member} onMemberAction={onMemberAction} />) : (
        <BoundaryPanel
          title="Member roster boundary"
          body={`You can see your own role (${groupRoleLabel(group)}) and how many people are in this group. The full member list is not available yet, so role changes cannot be made here.`}
        />
      )}
    </View>
  );
}

function GroupMemberRow({ group, member, onMemberAction }: { group: PulseGroup; member: PulseGroupMember; onMemberAction: (group: PulseGroup, member: PulseGroupMember, action: "promote" | "demote" | "removeMember") => void }) {
  const actions = groupMemberActionRules(group, member).filter((action) => action.available && ["promote", "demote", "removeMember"].includes(action.key));
  return (
    <View style={styles.memberRow} accessibilityLabel={groupMemberAccessibilityLabel(member)}>
      <Avatar name={member.display_name} uri={member.avatar_url} />
      <View style={styles.memberMain}>
        <Text style={styles.cardTitle} numberOfLines={1}>{member.display_name}</Text>
        <Text style={styles.cardText} numberOfLines={1}>{member.username ? `@${member.username} · ` : ""}{groupMemberRoleLabel(member.role)}{member.presence ? ` · ${member.presence}` : ""}</Text>
        <View style={styles.actionRow}>
          {actions.map((action) => (
            <Pressable key={action.key} accessibilityRole="button" accessibilityLabel={action.accessibilityLabel} style={[styles.inlineAction, action.tone === "danger" && styles.inlineDanger]} onPress={() => onMemberAction(group, member, action.key as "promote" | "demote" | "removeMember")}>
              <Text style={styles.inlineActionText}>{action.label}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    </View>
  );
}

function GroupInvitations({ group }: { group: PulseGroup }) {
  const invitations = group.invitations || [];
  const requests = group.membership_requests || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>Invitations</Text>
      {invitations.length ? invitations.map((invite) => <GroupInvitationRow key={invite.id} invitation={invite} />) : (
        <BoundaryPanel title="No pending invitations exposed" body="Invitations you send appear here while they are waiting to be accepted." />
      )}
      <Text style={styles.sectionTitle}>Membership Requests</Text>
      {requests.length ? requests.map((invite) => <GroupInvitationRow key={invite.id} invitation={invite} request />) : (
        <Text style={styles.emptyText}>No one is waiting to join right now.</Text>
      )}
    </View>
  );
}

function GroupInvitationRow({ invitation, request }: { invitation: PulseGroupInvitation; request?: boolean }) {
  return (
    <View style={styles.memberRow} accessibilityLabel={groupInvitationAccessibilityLabel(invitation)}>
      <Avatar name={invitation.display_name} uri={invitation.avatar_url} />
      <View style={styles.memberMain}>
        <Text style={styles.cardTitle} numberOfLines={1}>{invitation.display_name}</Text>
        <Text style={styles.cardText} numberOfLines={1}>{request ? "Request" : "Invite"} · {groupInvitationStateLabel(invitation)} · {groupMemberRoleLabel(invitation.role)}</Text>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.inlineAction}><Text style={styles.inlineActionText}>{request ? "Approve boundary" : "Pending"}</Text></Pressable>
          <Pressable accessibilityRole="button" style={[styles.inlineAction, styles.inlineDanger]}><Text style={styles.inlineActionText}>{request ? "Reject boundary" : "Cancel boundary"}</Text></Pressable>
        </View>
      </View>
    </View>
  );
}

function GroupAssets({ title, assets, emptyTitle, emptyBody }: { title: string; assets: PulseGroupAsset[]; emptyTitle: string; emptyBody: string }) {
  return (
    <View>
      <Text style={styles.sectionTitle}>{title}</Text>
      {assets.length ? assets.map((asset) => <GroupAssetCard key={asset.id} asset={asset} />) : <BoundaryPanel title={emptyTitle} body={emptyBody} />}
    </View>
  );
}

function GroupAssetCard({ asset }: { asset: PulseGroupAsset }) {
  return (
    <View style={styles.assetCard}>
      {asset.thumbnail_url || asset.url ? (
        <Image source={{ uri: asset.thumbnail_url || asset.url }} style={styles.assetThumb} />
      ) : (
        <View style={styles.assetFallback}><Text style={styles.coverText}>{groupAssetCategoryLabel(asset).slice(0, 1)}</Text></View>
      )}
      <View style={styles.cardBody}>
        <Text style={styles.cardType}>{groupAssetCategoryLabel(asset)}</Text>
        <Text style={styles.cardTitle} numberOfLines={2}>{asset.title}</Text>
        <Text style={styles.cardText} numberOfLines={2}>{asset.url ? "NativeMediaViewer handoff ready where the media contract provides a safe URL." : "No safe media URL returned."}</Text>
      </View>
    </View>
  );
}

function GroupSettings({ group, onDelete, onArchive }: { group: PulseGroup; onDelete: (group: PulseGroup) => void; onArchive: (group: PulseGroup) => void }) {
  const actions = groupActionRules(group);
  return (
    <View>
      <Text style={styles.sectionTitle}>Settings and Safety</Text>
      <View style={styles.metricGrid}>
        <Metric label="Privacy" value={group.privacy || group.group_type || "public"} />
        <Metric label="Trust" value={group.trust_level || "standard"} />
        <Metric label="Status" value={group.status || "active"} />
        <Metric label="Manage" value={group.can_manage ? "allowed" : "member"} />
      </View>
      {actions.map((action) => (
        <View key={action.key} style={styles.permissionRow}>
          <Text style={styles.cardTitle}>{action.label}</Text>
          <Text style={styles.cardText}>{action.available ? "Available in this group." : "Hidden by current role or provider state."}</Text>
        </View>
      ))}
      {!group.can_manage ? <BoundaryPanel title="Admin settings gated" body="Editing the group, managing members, and deleting it are shown only to owners, admins, and moderators." /> : null}
      {group.viewer_role === "owner" ? <View style={styles.actionRow}><Pressable accessibilityRole="button" style={styles.smallButton} onPress={() => onArchive(group)}><Text style={styles.smallButtonText}>Archive Group</Text></Pressable><Pressable accessibilityRole="button" style={[styles.smallButton, styles.dangerButton]} onPress={() => onDelete(group)}><Text style={styles.smallButtonText}>Delete Group</Text></Pressable></View> : null}
    </View>
  );
}

function RoomDetail({ room, busyKey, onClose, onOpen, onReport, onLifecycle }: {
  room: PulseRoom;
  busyKey: string;
  onClose: () => void;
  onOpen: (room: PulseRoom) => void;
  onReport: (room: PulseRoom) => void;
  onLifecycle: (room: PulseRoom, action: "archive" | "delete") => void;
}) {
  const [section, setSection] = useState<RoomDetailSection>("overview");
  const sections: RoomDetailSection[] = ["overview", "participants", "activity", "provider"];
  const actions = roomActionRules(room);
  const primary = actions.find((action) => action.key === "openRoom");
  return (
    <View style={styles.detailOverlay}>
      <View style={styles.detail}>
        <View style={styles.detailHeader}>
          <View style={styles.detailTitleWrap}>
            <Text style={styles.title} numberOfLines={1}>{roomDisplayTitle(room)}</Text>
            <Text style={styles.subtitle}>{roomProviderStateLabel(room)} · {Number(room.online_count || 0)} active · {room.current_user_role || "participant boundary"}</Text>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={`Close ${roomDisplayTitle(room)} detail`} style={styles.smallButton} onPress={onClose}>
            <Text style={styles.smallButtonText}>Close</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.detailContent}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sectionRail}>
            {sections.map((item) => (
              <Pressable key={item} accessibilityRole="tab" accessibilityState={{ selected: section === item }} style={[styles.sectionChip, section === item && styles.sectionChipActive]} onPress={() => setSection(item)}>
                <Text style={[styles.sectionChipText, section === item && styles.sectionChipTextActive]}>{roomDetailSectionLabel(item)}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <View style={styles.actionRow}>
            {primary?.available ? (
              <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.primaryButton} disabled={Boolean(busyKey)} onPress={() => onOpen(room)}>
                <Text style={styles.primaryText}>{primary.label}</Text>
              </Pressable>
            ) : null}
            {actions.find((action) => action.key === "providerBoundary")?.available ? (
              <View style={styles.boundaryPill}><Text style={styles.boundaryPillText}>{roomProviderStateLabel(room)}</Text></View>
            ) : null}
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onReport(room)}>
              <Text style={styles.smallButtonText}>Report</Text>
            </Pressable>
            {room.can_manage ? <Pressable accessibilityRole="button" style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onLifecycle(room, "archive")}><Text style={styles.smallButtonText}>End Room</Text></Pressable> : null}
            {room.current_user_role === "owner" ? <Pressable accessibilityRole="button" style={[styles.smallButton, styles.dangerButton]} disabled={Boolean(busyKey)} onPress={() => Alert.alert("Delete room?", "This permanently removes the room from active community discovery.", [{ text: "Cancel", style: "cancel" }, { text: "Delete", style: "destructive", onPress: () => onLifecycle(room, "delete") }])}><Text style={styles.smallButtonText}>Delete</Text></Pressable> : null}
          </View>
          <RoomDetailSectionView room={room} section={section} />
        </ScrollView>
      </View>
    </View>
  );
}

type RoomDetailSection = "overview" | "participants" | "activity" | "provider";

function roomDetailSectionLabel(section: RoomDetailSection) {
  return {
    overview: "Overview",
    participants: "Participants",
    activity: "Activity",
    provider: "Provider"
  }[section];
}

function RoomDetailSectionView({ room, section }: { room: PulseRoom; section: RoomDetailSection }) {
  if (section === "participants") return <RoomParticipants room={room} />;
  if (section === "activity") return <RoomActivity room={room} />;
  if (section === "provider") return <RoomProviderBoundary room={room} />;
  return (
    <View>
      <Text style={styles.sectionTitle}>Room Overview</Text>
      <Text style={styles.cardText}>{roomSummary(room)}</Text>
      <View style={styles.metricGrid}>
        <Metric label="Active" value={String(Number(room.online_count || 0))} />
        <Metric label="Unread" value={String(Number(room.unread_count || 0))} />
        <Metric label="Privacy" value={room.privacy || "member"} />
        <Metric label="State" value={roomProviderStateLabel(room)} />
      </View>
      {room.pinned_notice ? <BoundaryPanel title="Pinned notice" body={room.pinned_notice} /> : null}
    </View>
  );
}

function RoomParticipants({ room }: { room: PulseRoom }) {
  const participants = room.participants || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>Participants and Presence</Text>
      {participants.length ? participants.map((participant) => <RoomParticipantRow key={participant.id} participant={participant} />) : (
        <BoundaryPanel title="Live presence boundary" body="You can see how many people are active in this room. Who they are, their roles, and their connection status are not available yet." />
      )}
    </View>
  );
}

function RoomParticipantRow({ participant }: { participant: PulseRoomParticipant }) {
  return (
    <View style={styles.memberRow} accessibilityLabel={roomParticipantAccessibilityLabel(participant)}>
      <Avatar name={participant.display_name} uri={participant.avatar_url} />
      <View style={styles.memberMain}>
        <Text style={styles.cardTitle} numberOfLines={1}>{participant.display_name}</Text>
        <Text style={styles.cardText} numberOfLines={1}>{roomParticipantRoleLabel(participant)}{participant.provider_state ? ` · ${participant.provider_state}` : ""}{participant.presence ? ` · ${participant.presence}` : ""}</Text>
      </View>
    </View>
  );
}

function RoomActivity({ room }: { room: PulseRoom }) {
  const activity = room.activity || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>Room Activity</Text>
      {activity.length ? activity.map((asset) => <GroupAssetCard key={asset.id} asset={asset} />) : (
        <BoundaryPanel title="No persistent room activity returned" body="This room currently exposes summary data. Messages, shared media, announcements, and participant events remain tied to the existing chat/provider contracts." />
      )}
    </View>
  );
}

function RoomProviderBoundary({ room }: { room: PulseRoom }) {
  return (
    <View>
      <Text style={styles.sectionTitle}>Live Media</Text>
      <BoundaryPanel
        title={roomProviderStateLabel(room)}
        body={room.partial ? "Live audio and video cannot run in the Simulator. You can still open this room and check how it looks and which permissions it asks for; the microphone, camera, Bluetooth, and calls with other people need a real device." : "This room can open through the existing Pulse Command chat contract. Live media features remain provider and physical-device gated where applicable."}
      />
      <View style={styles.metricGrid}>
        <Metric label="Provider" value={room.provider || "PulseSoc"} />
        <Metric label="Room type" value={room.room_type || "room"} />
        <Metric label="Role" value={room.current_user_role || "member"} />
        <Metric label="Conversation" value={room.conversation_id ? "available" : "join required"} />
      </View>
    </View>
  );
}

function BoundaryPanel({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.boundaryPanel}>
      <Text style={styles.cardTitle}>{title}</Text>
      <Text style={styles.cardText}>{body}</Text>
    </View>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue} numberOfLines={1}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function Avatar({ name, uri }: { name: string; uri?: string }) {
  return uri ? <Image source={{ uri }} style={styles.avatar} /> : <View style={styles.avatarFallback}><Text style={styles.avatarText}>{name.slice(0, 1).toUpperCase()}</Text></View>;
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

const styles = createThemedStyles(() => ({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  assetCard: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginTop: 10,
    padding: 10
  },
  assetFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    height: 72,
    justifyContent: "center",
    width: 86
  },
  assetThumb: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 10,
    height: 72,
    width: 86
  },
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 22,
    height: 44,
    width: 44
  },
  avatarFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  avatarText: {
    color: colors.accent,
    fontSize: 17,
    fontWeight: "900"
  },
  boundaryPanel: {
    backgroundColor: "rgba(97,216,255,0.07)",
    borderColor: "rgba(97,216,255,0.22)",
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    marginTop: 12,
    padding: 12
  },
  boundaryPill: {
    alignItems: "center",
    borderColor: "rgba(255,209,102,0.4)",
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 12
  },
  boundaryPillText: {
    color: "#ffd166",
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  card: {
    backgroundColor: colors.glass,
    borderColor: "rgba(97,216,255,0.24)",
    borderRadius: logiNexus.radius.large,
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
    backgroundColor: "transparent",
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
    paddingBottom: 0
  },
  dangerButton: {
    borderColor: colors.danger
  },
  createSheet: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    maxHeight: "88%",
    width: "100%"
  },
  createSheetKeyboardAvoider: {
    alignSelf: "center",
    justifyContent: "center",
    width: "92%"
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
    borderRadius: logiNexus.radius.panel,
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
  inlineAction: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    minHeight: 32,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  inlineActionText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    color: colors.text,
    fontSize: 15,
    marginTop: 6,
    minHeight: 48,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  inputLabel: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 14
  },
  helperText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginBottom: 12,
    marginTop: 8
  },
  multilineInput: {
    minHeight: 92,
    textAlignVertical: "top"
  },
  inlineDanger: {
    borderColor: "rgba(255,107,122,0.34)"
  },
  memberMain: {
    flex: 1,
    minWidth: 0
  },
  memberRow: {
    alignItems: "flex-start",
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginTop: 10,
    padding: 12
  },
  metric: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flex: 1,
    minWidth: 126,
    padding: 10
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "900",
    marginTop: 4,
    textTransform: "uppercase"
  },
  metricValue: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  error: {
    color: "#ff9f9f",
    marginTop: 10
  },
  filter: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
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
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    marginVertical: 12,
    textAlign: "center"
  },
  header: {
    gap: logiNexus.spacing.md,
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
  permissionRow: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    marginTop: 10,
    padding: 12
  },
  postCard: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 10,
    padding: 12
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.medium,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 14
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  roomCard: {
    backgroundColor: colors.glass,
    borderColor: "rgba(63,240,160,0.34)",
    borderRadius: logiNexus.radius.large,
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
    backgroundColor: "transparent",
    flex: 1
  },
  roomEmpty: {
    minHeight: 126,
    width: 230
  },
  rulesBox: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 14,
    padding: 12
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900",
    marginTop: 16
  },
  sectionChip: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minHeight: 34,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  sectionChipActive: {
    backgroundColor: "rgba(63,240,160,0.16)",
    borderColor: colors.accent
  },
  sectionChipText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  sectionChipTextActive: {
    color: colors.text
  },
  sectionRail: {
    gap: 8,
    paddingBottom: 4
  },
  smallButton: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
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
  },
  statePanel: {
    margin: 16
  }
}));
