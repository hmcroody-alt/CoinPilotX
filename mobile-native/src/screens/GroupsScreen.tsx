import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
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
  PulseGroupAsset,
  PulseGroupInvitation,
  PulseGroupMember,
  PulseGroupPost,
  PulseRoom,
  PulseRoomParticipant,
  reportGroup
} from "../api/groups";
import { PulseCommandAction, PulseCommandHeader, PulseCommandPanel, PulseCommandSearch } from "../components/PulseCommand";
import { LogiNexusStatePanel } from "../components/Screen";
import { useTranslation } from "../i18n";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
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

type Props = Partial<NativeStackScreenProps<RootStackParamList, "GroupDetail">>;

export function GroupsScreen({ route, navigation }: Props) {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const { t } = useTranslation();
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
        setError(loadError instanceof Error ? loadError.message : t("social:groups.loadError"));
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
      else setError(detailError instanceof Error ? detailError.message : t("social:groups.detailLoadError"));
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [initialSlug]);

  useEffect(() => {
    const timer = setTimeout(() => load("search", query).catch(() => undefined), 320);
    return () => clearTimeout(timer);
  }, [query]);

  const categories = useMemo(() => Array.from(new Set(groups.map((group) => group.category || t("social:groups.categoryFallback")))).slice(0, 8), [groups, t]);

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
      setError(joinError instanceof Error ? joinError.message : t("social:groups.membershipActionFailed"));
    } finally {
      setBusyKey("");
    }
  }

  async function handleOpenChat(group: PulseGroup) {
    setBusyKey(`chat-${group.slug}`);
    setError("");
    try {
      const result = await openGroupChat(group.slug);
      if (result.conversation_id && navigation) navigation.navigate("Chat", { conversationId: result.conversation_id, title: t("social:groups.chatTitle", { name: group.name }) });
      else setError(result.message || t("social:groups.chatUnavailable"));
    } catch (chatError) {
      setError(chatError instanceof Error ? chatError.message : t("social:groups.chatOpenError"));
    } finally {
      setBusyKey("");
    }
  }

  async function handleReport(group: PulseGroup) {
    setBusyKey(`report-${group.slug}`);
    try {
      const result = await reportGroup(group.slug, "Needs review");
      setError(result.message || t("social:groups.reportSent"));
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : t("social:groups.reportFailed"));
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
      else setError(t("social:groups.roomChatUnavailable"));
    } catch (roomError) {
      setError(roomError instanceof Error ? roomError.message : t("social:groups.roomOpenError"));
    } finally {
      setBusyKey("");
    }
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
        <LogiNexusStatePanel state="loading" title={t("social:groups.loadingTitle")} body={t("social:groups.loadingBody")} loading style={styles.statePanel} />
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
              title={t("social:groups.title")}
              subtitle={offline ? t("social:groups.offlineSubtitle") : t("social:groups.subtitle")}
              status={offline ? t("social:groups.statusCached") : t("social:groups.statusLiveSync")}
              tone={offline ? "warning" : "safety"}
              actions={navigation ? <PulseCommandAction compact label={t("social:groups.safetyAction")} tone="safety" onPress={() => navigation.navigate("SafetyHub", { section: "reports", title: t("social:groups.safetyHubTitle") })} /> : null}
            />
            <PulseCommandSearch value={query} onChangeText={setQuery} placeholder={t("social:groups.searchPlaceholder")} />
            {categories.length ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
                {categories.map((category) => (
                  <Pressable accessibilityRole="button" key={category} style={styles.filter} onPress={() => setQuery(category)}>
                    <Text style={styles.filterText}>{category}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            ) : null}
            <Text style={styles.sectionTitle}>{t("social:groups.roomsSection")}</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.roomRow}>
              {rooms.map((room) => (
                <RoomCard key={room.id} room={room} busy={busyKey === `room-${room.id}`} onOpen={openRoomDetail} />
              ))}
              {!rooms.length ? (
                <PulseCommandPanel style={styles.roomEmpty}>
                  <Text style={styles.roomTitle}>{t("social:groups.noRoomSignalsTitle")}</Text>
                  <Text style={styles.roomText}>{t("social:groups.noRoomSignalsBody")}</Text>
                </PulseCommandPanel>
              ) : null}
            </ScrollView>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <LogiNexusStatePanel state={error ? "error" : "empty"} title={error ? t("social:groups.errorTitle") : t("social:groups.emptyTitle")} body={error || t("social:groups.emptyBody")} style={styles.statePanel} />
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
        ListFooterComponent={loadingMore ? <Text style={styles.footer}>{t("social:groups.loadingMore")}</Text> : null}
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
      {selectedRoom ? (
        <RoomDetail
          room={selectedRoom}
          busyKey={busyKey}
          onClose={() => setSelectedRoom(null)}
          onOpen={handleOpenRoom}
          onReport={(room) => setError(t("social:groups.roomReportBoundary", { room: roomDisplayTitle(room) }))}
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
  const { t } = useTranslation();
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
            <Pressable accessibilityRole="button" accessibilityLabel={actions[0]?.accessibilityLabel || t("social:groups.membershipActionLabel")} style={styles.smallButton} disabled={busy} onPress={() => onJoin(group)}>
              <Text style={styles.smallButtonText}>{actions[0]?.label || (group.joined ? t("social:groups.leave") : t("social:groups.join"))}</Text>
            </Pressable>
          ) : null}
          {actionIsAvailable("openChat") ? <Pressable accessibilityRole="button" accessibilityLabel={t("social:groups.openChatLabel", { group: groupDisplayTitle(group) })} style={styles.smallButton} disabled={busy} onPress={() => onChat(group)}>
            <Text style={styles.smallButtonText}>{t("social:groups.chat")}</Text>
          </Pressable> : null}
          {actionIsAvailable("reportGroup") ? <Pressable accessibilityRole="button" accessibilityLabel={t("social:groups.reportLabel", { group: groupDisplayTitle(group) })} style={styles.smallButton} disabled={busy} onPress={() => onReport(group)}>
            <Text style={styles.smallButtonText}>{t("social:groups.report")}</Text>
          </Pressable> : null}
        </View>
      </View>
    </Pressable>
  );
}

function RoomCard({ room, busy, onOpen }: { room: PulseRoom; busy?: boolean; onOpen: (room: PulseRoom) => void }) {
  const { t } = useTranslation();
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
      <Text style={styles.cardMeta}>{primaryAction?.label || t("social:groups.openRoom")}</Text>
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
  const { t } = useTranslation();
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
            <Text style={styles.subtitle}>{t("social:groups.detailSubtitle", { count: Number(group.member_count || 0), type: group.group_type || t("social:groups.values.public"), role: groupRoleLabel(group) })}</Text>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={t("social:groups.closeDetailLabel", { title: groupDisplayTitle(group) })} style={styles.smallButton} onPress={onClose}>
            <Text style={styles.smallButtonText}>{t("social:groups.close")}</Text>
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
                <Text style={[styles.sectionChipText, section === item && styles.sectionChipTextActive]}>{t(groupDetailSectionLabelKey(item))}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <View style={styles.actionRow}>
            {actionIsAvailable("join") || actionIsAvailable("leave") ? <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.primaryButton} disabled={Boolean(busyKey)} onPress={() => onJoin(group)}>
              <Text style={styles.primaryText}>{groupActions[0]?.label || (group.joined ? t("social:groups.leave") : t("social:groups.join"))}</Text>
            </Pressable> : null}
            {actionIsAvailable("openChat") ? <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onChat(group)}>
              <Text style={styles.smallButtonText}>{t("social:groups.openChat")}</Text>
            </Pressable> : null}
            {actionIsAvailable("reportGroup") ? <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(busyKey) }} style={styles.smallButton} disabled={Boolean(busyKey)} onPress={() => onReport(group)}>
              <Text style={styles.smallButtonText}>{t("social:groups.report")}</Text>
            </Pressable> : null}
          </View>
          <GroupDetailSectionView group={group} section={section} />
        </ScrollView>
      </View>
    </View>
  );
}

type GroupDetailSection = "overview" | "members" | "invitations" | "media" | "files" | "links" | "settings";

/**
 * Returns a catalog key rather than display text, so the rail re-labels itself
 * when the language changes instead of freezing the language that was active
 * when this module loaded.
 */
function groupDetailSectionLabelKey(section: GroupDetailSection) {
  return {
    overview: "social:groups.sections.overview",
    members: "social:groups.sections.members",
    invitations: "social:groups.sections.invitations",
    media: "social:groups.sections.media",
    files: "social:groups.sections.files",
    links: "social:groups.sections.links",
    settings: "social:groups.sections.settings"
  }[section];
}

function GroupDetailSectionView({ group, section }: { group: PulseGroup; section: GroupDetailSection }) {
  const { t } = useTranslation();
  if (section === "overview") return <GroupOverview group={group} />;
  if (section === "members") return <GroupMembers group={group} />;
  if (section === "invitations") return <GroupInvitations group={group} />;
  if (section === "media") return <GroupAssets title={t("social:groups.sections.media")} assets={group.media || []} emptyTitle={t("social:groups.assets.mediaEmptyTitle")} emptyBody={t("social:groups.assets.mediaEmptyBody")} />;
  if (section === "files") return <GroupAssets title={t("social:groups.sections.files")} assets={group.files || []} emptyTitle={t("social:groups.assets.filesEmptyTitle")} emptyBody={t("social:groups.assets.filesEmptyBody")} />;
  if (section === "links") return <GroupAssets title={t("social:groups.sections.links")} assets={group.links || []} emptyTitle={t("social:groups.assets.linksEmptyTitle")} emptyBody={t("social:groups.assets.linksEmptyBody")} />;
  return <GroupSettings group={group} />;
}

function GroupOverview({ group }: { group: PulseGroup }) {
  const { t } = useTranslation();
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.sections.overview")}</Text>
      <Text style={styles.cardText}>{groupSummary(group)}</Text>
      <View style={styles.metricGrid}>
        <Metric label={t("social:groups.metrics.members")} value={String(Number(group.member_count || 0))} />
        <Metric label={t("social:groups.metrics.posts")} value={String(Number(group.post_count || 0))} />
        <Metric label={t("social:groups.metrics.role")} value={groupRoleLabel(group)} />
        <Metric label={t("social:groups.metrics.notify")} value={groupNotificationLabel(group)} />
      </View>
      {group.owner_name ? <Text style={styles.cardMeta}>{t("social:groups.ownerLine", { name: group.owner_name })}</Text> : null}
      {group.rules ? (
        <View style={styles.rulesBox}>
          <Text style={styles.sectionTitle}>{t("social:groups.rulesHeading")}</Text>
          <Text style={styles.cardText}>{group.rules}</Text>
        </View>
      ) : (
        <BoundaryPanel title={t("social:groups.rulesUnavailableTitle")} body={t("social:groups.rulesUnavailableBody")} />
      )}
      <Text style={styles.sectionTitle}>{t("social:groups.communityFeedHeading")}</Text>
      {(group.posts || []).length ? group.posts?.map((post) => <GroupPostCard key={post.id} post={post} />) : <Text style={styles.emptyText}>{t("social:groups.feedEmpty")}</Text>}
    </View>
  );
}

function GroupMembers({ group }: { group: PulseGroup }) {
  const { t } = useTranslation();
  const members = group.members || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.membersHeading")}</Text>
      {members.length ? members.map((member) => <GroupMemberRow key={member.id} group={group} member={member} />) : (
        <BoundaryPanel
          title={t("social:groups.memberBoundaryTitle")}
          body={t("social:groups.memberBoundaryBody", { role: groupRoleLabel(group) })}
        />
      )}
    </View>
  );
}

function GroupMemberRow({ group, member }: { group: PulseGroup; member: PulseGroupMember }) {
  const actions = groupMemberActionRules(group, member).filter((action) => action.available).slice(0, 3);
  return (
    <View style={styles.memberRow} accessibilityLabel={groupMemberAccessibilityLabel(member)}>
      <Avatar name={member.display_name} uri={member.avatar_url} />
      <View style={styles.memberMain}>
        <Text style={styles.cardTitle} numberOfLines={1}>{member.display_name}</Text>
        <Text style={styles.cardText} numberOfLines={1}>{member.username ? `@${member.username} · ` : ""}{groupMemberRoleLabel(member.role)}{member.presence ? ` · ${member.presence}` : ""}</Text>
        <View style={styles.actionRow}>
          {actions.map((action) => (
            <Pressable key={action.key} accessibilityRole="button" accessibilityLabel={action.accessibilityLabel} style={[styles.inlineAction, action.tone === "danger" && styles.inlineDanger]}>
              <Text style={styles.inlineActionText}>{action.label}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    </View>
  );
}

function GroupInvitations({ group }: { group: PulseGroup }) {
  const { t } = useTranslation();
  const invitations = group.invitations || [];
  const requests = group.membership_requests || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.sections.invitations")}</Text>
      {invitations.length ? invitations.map((invite) => <GroupInvitationRow key={invite.id} invitation={invite} />) : (
        <BoundaryPanel title={t("social:groups.invitationsBoundaryTitle")} body={t("social:groups.invitationsBoundaryBody")} />
      )}
      <Text style={styles.sectionTitle}>{t("social:groups.membershipRequestsHeading")}</Text>
      {requests.length ? requests.map((invite) => <GroupInvitationRow key={invite.id} invitation={invite} request />) : (
        <Text style={styles.emptyText}>{t("social:groups.membershipRequestsEmpty")}</Text>
      )}
    </View>
  );
}

function GroupInvitationRow({ invitation, request }: { invitation: PulseGroupInvitation; request?: boolean }) {
  const { t } = useTranslation();
  return (
    <View style={styles.memberRow} accessibilityLabel={groupInvitationAccessibilityLabel(invitation)}>
      <Avatar name={invitation.display_name} uri={invitation.avatar_url} />
      <View style={styles.memberMain}>
        <Text style={styles.cardTitle} numberOfLines={1}>{invitation.display_name}</Text>
        <Text style={styles.cardText} numberOfLines={1}>{t("social:groups.invitationMeta", {
          kind: request ? t("social:groups.invitationKindRequest") : t("social:groups.invitationKindInvite"),
          state: groupInvitationStateLabel(invitation),
          role: groupMemberRoleLabel(invitation.role)
        })}</Text>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.inlineAction}><Text style={styles.inlineActionText}>{request ? t("social:groups.approveBoundary") : t("social:groups.pending")}</Text></Pressable>
          <Pressable accessibilityRole="button" style={[styles.inlineAction, styles.inlineDanger]}><Text style={styles.inlineActionText}>{request ? t("social:groups.rejectBoundary") : t("social:groups.cancelBoundary")}</Text></Pressable>
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
  const { t } = useTranslation();
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
        <Text style={styles.cardText} numberOfLines={2}>{asset.url ? t("social:groups.assets.handoffReady") : t("social:groups.assets.noUrl")}</Text>
      </View>
    </View>
  );
}

function GroupSettings({ group }: { group: PulseGroup }) {
  const { t } = useTranslation();
  const actions = groupActionRules(group);
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.settingsHeading")}</Text>
      <View style={styles.metricGrid}>
        <Metric label={t("social:groups.metrics.privacy")} value={group.privacy || group.group_type || t("social:groups.values.public")} />
        <Metric label={t("social:groups.metrics.trust")} value={group.trust_level || t("social:groups.values.standard")} />
        <Metric label={t("social:groups.metrics.status")} value={group.status || t("social:groups.values.active")} />
        <Metric label={t("social:groups.metrics.manage")} value={group.can_manage ? t("social:groups.values.allowed") : t("social:groups.values.member")} />
      </View>
      {actions.map((action) => (
        <View key={action.key} style={styles.permissionRow}>
          <Text style={styles.cardTitle}>{action.label}</Text>
          <Text style={styles.cardText}>{action.available ? t("social:groups.permissionAvailable") : t("social:groups.permissionHidden")}</Text>
        </View>
      ))}
      {!group.can_manage ? <BoundaryPanel title={t("social:groups.adminGatedTitle")} body={t("social:groups.adminGatedBody")} /> : null}
    </View>
  );
}

function RoomDetail({ room, busyKey, onClose, onOpen, onReport }: {
  room: PulseRoom;
  busyKey: string;
  onClose: () => void;
  onOpen: (room: PulseRoom) => void;
  onReport: (room: PulseRoom) => void;
}) {
  const { t } = useTranslation();
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
            <Text style={styles.subtitle}>{t("social:groups.roomDetailSubtitle", { state: roomProviderStateLabel(room), active: Number(room.online_count || 0), role: room.current_user_role || t("social:groups.values.participantBoundary") })}</Text>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={t("social:groups.closeDetailLabel", { title: roomDisplayTitle(room) })} style={styles.smallButton} onPress={onClose}>
            <Text style={styles.smallButtonText}>{t("social:groups.close")}</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.detailContent}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sectionRail}>
            {sections.map((item) => (
              <Pressable key={item} accessibilityRole="tab" accessibilityState={{ selected: section === item }} style={[styles.sectionChip, section === item && styles.sectionChipActive]} onPress={() => setSection(item)}>
                <Text style={[styles.sectionChipText, section === item && styles.sectionChipTextActive]}>{t(roomDetailSectionLabelKey(item))}</Text>
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
              <Text style={styles.smallButtonText}>{t("social:groups.report")}</Text>
            </Pressable>
          </View>
          <RoomDetailSectionView room={room} section={section} />
        </ScrollView>
      </View>
    </View>
  );
}

type RoomDetailSection = "overview" | "participants" | "activity" | "provider";

/** Catalog keys, resolved at render time — see `groupDetailSectionLabelKey`. */
function roomDetailSectionLabelKey(section: RoomDetailSection) {
  return {
    overview: "social:groups.roomSections.overview",
    participants: "social:groups.roomSections.participants",
    activity: "social:groups.roomSections.activity",
    provider: "social:groups.roomSections.provider"
  }[section];
}

function RoomDetailSectionView({ room, section }: { room: PulseRoom; section: RoomDetailSection }) {
  const { t } = useTranslation();
  if (section === "participants") return <RoomParticipants room={room} />;
  if (section === "activity") return <RoomActivity room={room} />;
  if (section === "provider") return <RoomProviderBoundary room={room} />;
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.roomOverviewHeading")}</Text>
      <Text style={styles.cardText}>{roomSummary(room)}</Text>
      <View style={styles.metricGrid}>
        <Metric label={t("social:groups.metrics.active")} value={String(Number(room.online_count || 0))} />
        <Metric label={t("social:groups.metrics.unread")} value={String(Number(room.unread_count || 0))} />
        <Metric label={t("social:groups.metrics.privacy")} value={room.privacy || t("social:groups.values.member")} />
        <Metric label={t("social:groups.metrics.state")} value={roomProviderStateLabel(room)} />
      </View>
      {room.pinned_notice ? <BoundaryPanel title={t("social:groups.pinnedNoticeTitle")} body={room.pinned_notice} /> : null}
    </View>
  );
}

function RoomParticipants({ room }: { room: PulseRoom }) {
  const { t } = useTranslation();
  const participants = room.participants || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.participantsHeading")}</Text>
      {participants.length ? participants.map((participant) => <RoomParticipantRow key={participant.id} participant={participant} />) : (
        <BoundaryPanel title={t("social:groups.presenceBoundaryTitle")} body={t("social:groups.presenceBoundaryBody")} />
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
  const { t } = useTranslation();
  const activity = room.activity || [];
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.roomActivityHeading")}</Text>
      {activity.length ? activity.map((asset) => <GroupAssetCard key={asset.id} asset={asset} />) : (
        <BoundaryPanel title={t("social:groups.roomActivityEmptyTitle")} body={t("social:groups.roomActivityEmptyBody")} />
      )}
    </View>
  );
}

function RoomProviderBoundary({ room }: { room: PulseRoom }) {
  const { t } = useTranslation();
  return (
    <View>
      <Text style={styles.sectionTitle}>{t("social:groups.providerBoundaryHeading")}</Text>
      <BoundaryPanel
        title={roomProviderStateLabel(room)}
        body={room.partial ? t("social:groups.providerPartialBody") : t("social:groups.providerReadyBody")}
      />
      <View style={styles.metricGrid}>
        <Metric label={t("social:groups.metrics.provider")} value={room.provider || "PulseSoc"} />
        <Metric label={t("social:groups.metrics.roomType")} value={room.room_type || t("social:groups.values.room")} />
        <Metric label={t("social:groups.metrics.role")} value={room.current_user_role || t("social:groups.values.member")} />
        <Metric label={t("social:groups.metrics.conversation")} value={room.conversation_id ? t("social:groups.values.available") : t("social:groups.values.joinRequired")} />
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
  const { t } = useTranslation();
  const metaParams = { author: post.author_name || t("social:groups.postAuthorFallback"), time: formatShortTime(post.created_at) };
  return (
    <View style={styles.postCard}>
      <Text style={styles.cardType}>{post.pinned ? t("social:groups.postMetaPinned", metaParams) : t("social:groups.postMeta", metaParams)}</Text>
      {post.title ? <Text style={styles.cardTitle}>{post.title}</Text> : null}
      <Text style={styles.cardText}>{post.body || t("social:groups.postBodyFallback")}</Text>
      {post.media_url ? <Text style={styles.cardMeta}>{t("social:groups.mediaAttached")}</Text> : null}
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
    paddingBottom: 0
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
    backgroundColor: colors.background,
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
});
