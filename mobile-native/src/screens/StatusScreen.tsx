import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  DEFAULT_STATUS_REACTION,
  deleteStatus,
  describeStatusReactionError,
  listStatuses,
  loadCachedStatuses,
  PulseStatus,
  pulseStatusUrl,
  reactToStatus,
  replyToStatus,
  reconcileStatusItems,
  shareStatus,
  StatusReactionType,
  statusMediaKind,
  statusMediaUrl,
  statusMusicLabel,
  trackStatusView,
  updateStatus as updateStatusOnServer
} from "../api/status";
import { mutePostAuthor } from "../api/feed";
import { profileNavigationParams, profileTargetFromAuthor } from "../api/profileTarget";
import { blockPulseUser, reportPulseTarget } from "../api/support";
import { registerSyncInvalidation } from "../core/eventSync";
import { StatusCreator } from "../components/StatusCreator";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "../components/NativeMediaViewer";
import { StatusViewerCard } from "../components/StatusViewerCard";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { useSafeAreaInsets } from "react-native-safe-area-context";

type Props = {
  route: { params?: { statusId?: number; title?: string; openCreator?: boolean } };
  navigation: { navigate: (name: string, params?: Record<string, unknown>) => void };
};

const LANE = "for_you";

export function StatusScreen({ route, navigation }: Props) {
  const insets = useSafeAreaInsets();
  const initialStatusId = Number(route.params?.statusId || 0);
  const [items, setItems] = useState<PulseStatus[]>([]);
  const [railItems, setRailItems] = useState<PulseStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [muted, setMuted] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [replyStatus, setReplyStatus] = useState<PulseStatus | null>(null);
  const [replyBody, setReplyBody] = useState("");
  const [postingReply, setPostingReply] = useState(false);
  const [manageStatus, setManageStatus] = useState<PulseStatus | null>(null);
  const [reactingIds, setReactingIds] = useState<Set<number>>(new Set());
  const [reactionError, setReactionError] = useState("");
  const reactionSeqRef = useRef(new Map<number, number>());
  const viewed = useRef(new Set<number>());

  useEffect(() => {
    if (!reactionError) return;
    const timer = setTimeout(() => setReactionError(""), 3200);
    return () => clearTimeout(timer);
  }, [reactionError]);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const data = await listStatuses({ lane: LANE });
      const nextItems = focusInitialStatus(data.items || [], initialStatusId);
      setItems(nextItems);
      setRailItems(data.rail_items || []);
      if (initialStatusId && nextItems.length) setViewerIndex(Math.max(0, nextItems.findIndex((item) => item.id === initialStatusId)));
    } catch (err) {
      const cached = await loadCachedStatuses(LANE);
      if (cached.items.length) {
        setItems(focusInitialStatus(cached.items, initialStatusId));
        setRailItems(cached.rail_items);
        setOffline(true);
        if (initialStatusId) setViewerIndex(0);
      } else {
        setError(err instanceof Error ? err.message : "PulseSoc Status could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [initialStatusId]);

  useEffect(() => {
    if (route.params?.openCreator) setCreatorOpen(true);
  }, [route.params?.openCreator]);

  useEffect(() => registerSyncInvalidation("status", () => load("refresh")), []);

  const activeStatus = useMemo(() => (viewerIndex === null ? null : items[viewerIndex] || null), [items, viewerIndex]);

  function openStatus(status: PulseStatus) {
    const index = items.findIndex((item) => item.id === status.id);
    setViewerIndex(index >= 0 ? index : 0);
  }

  function updateStatus(statusId: number, next: Partial<PulseStatus>) {
    setItems((current) => current.map((item) => (item.id === statusId ? { ...item, ...next } : item)));
    setRailItems((current) => current.map((item) => (item.id === statusId ? { ...item, ...next } : item)));
  }

  async function handleViewed(status: PulseStatus, watchMs: number, completed = false) {
    if (!completed && viewed.current.has(status.id)) return;
    viewed.current.add(status.id);
    try {
      const result = await trackStatusView(status.id, { completed, completionRatio: completed ? 1 : 0.35, watchMs });
      updateStatus(status.id, { viewed: true, view_count: Number(result.view_count || status.view_count || 0) });
    } catch {
      updateStatus(status.id, { viewed: true });
    }
  }

  /**
   * Optimistic, sequence-guarded reaction mutation. The production route
   * (`POST /api/pulse/status/<id>/react`) always REPLACES the caller's prior
   * reaction row and never supports removal, so this only ever sets a new
   * reaction — it never advertises or performs a "remove reaction" action.
   *
   * `reactionSeqRef` guards against rapid repeated taps / duplicate or
   * stale responses: each call bumps a per-status sequence number, and any
   * response (success or failure) that isn't for the latest sequence is
   * discarded so it can never stomp a newer optimistic update or roll back
   * to a stale count ("count drift").
   */
  async function handleReact(status: PulseStatus, reactionType: StatusReactionType = DEFAULT_STATUS_REACTION) {
    const statusId = status.id;
    const previousReaction = status.viewer_reaction;
    const previousCount = Number(status.reaction_count || 0);
    if (previousReaction === reactionType) return;

    const seq = (reactionSeqRef.current.get(statusId) || 0) + 1;
    reactionSeqRef.current.set(statusId, seq);
    setReactionError("");
    setReactingIds((current) => new Set(current).add(statusId));

    const optimisticCount = previousReaction ? previousCount : previousCount + 1;
    updateStatus(statusId, { viewer_reaction: reactionType, reaction_count: optimisticCount });

    try {
      const result = await reactToStatus(statusId, reactionType);
      if (reactionSeqRef.current.get(statusId) !== seq) return;
      updateStatus(statusId, { viewer_reaction: reactionType, reaction_count: Number(result.reaction_count ?? optimisticCount) });
    } catch (err) {
      if (reactionSeqRef.current.get(statusId) !== seq) return;
      updateStatus(statusId, { viewer_reaction: previousReaction, reaction_count: previousCount });
      setReactionError(describeStatusReactionError(err));
    } finally {
      if (reactionSeqRef.current.get(statusId) === seq) {
        setReactingIds((current) => {
          const next = new Set(current);
          next.delete(statusId);
          return next;
        });
      }
    }
  }

  async function handleShare(status: PulseStatus) {
    setBusyId(status.id);
    try {
      const result = await shareStatus(status.id);
      updateStatus(status.id, { share_count: Number(result.share_count || status.share_count || 0) });
    } finally {
      setBusyId(null);
    }
    await Share.share({ message: pulseStatusUrl(status.id) }).catch(() => undefined);
  }

  async function submitReply() {
    if (!replyStatus || !replyBody.trim() || postingReply) return;
    const body = replyBody.trim();
    setPostingReply(true);
    setReplyBody("");
    try {
      await replyToStatus(replyStatus.id, body);
      updateStatus(replyStatus.id, { reply_count: Number(replyStatus.reply_count || 0) + 1 });
      setReplyStatus(null);
    } catch {
      setReplyBody(body);
    } finally {
      setPostingReply(false);
    }
  }

  function handleCreatedStatus(status?: PulseStatus) {
    if (status?.id) {
      setItems((current) => reconcileStatusItems(current, [status]));
      setRailItems((current) => reconcileStatusItems(current, [status]).slice(0, 24));
    }
    load("refresh").catch(() => undefined);
  }

  if (loading && !items.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Status</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListHeaderComponent={
          <View>
            <View style={styles.headerRow}>
              <View>
                <Text style={styles.title}>Status</Text>
                <Text style={styles.subtitle}>{offline ? "Showing saved Status" : "PulseSoc native Status"}</Text>
              </View>
              <View style={styles.headerActions}>
                <Pressable accessibilityRole="button" accessibilityLabel="Open Status camera" style={styles.cameraButton} onPress={() => navigation.navigate("CameraStudio", { target: "status", mode: "status", title: "Status Camera" })}>
                  <Text style={styles.cameraText}>Camera</Text>
                </Pressable>
                <Pressable accessibilityRole="button" accessibilityLabel="Create Status" style={styles.createButton} onPress={() => setCreatorOpen(true)}>
                  <Text style={styles.createButtonText}>Create</Text>
                </Pressable>
              </View>
            </View>
            <FlatList
              horizontal
              data={railItems.length ? railItems : items.slice(0, 12)}
              keyExtractor={(item) => `rail-${item.id}`}
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.rail}
              ListHeaderComponent={<CreateStatusRailEntry onPress={() => setCreatorOpen(true)} />}
              renderItem={({ item }) => <StatusRailBubble status={item} onPress={openStatus} />}
            />
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{error ? "Status unavailable" : "No active Status"}</Text>
            <Text style={styles.emptyText}>{error || "New Status from people you follow will appear here. Create one to start the moment."}</Text>
          </View>
        }
        renderItem={({ item }) => <StatusListCard status={item} onPress={openStatus} />}
      />

      <Modal visible={Boolean(activeStatus)} animationType="slide" onRequestClose={() => setViewerIndex(null)}>
        {activeStatus ? (
          <StatusViewerCard
            status={activeStatus}
            active
            muted={muted}
            busy={busyId === activeStatus.id}
            reactionPending={reactingIds.has(activeStatus.id)}
            reactionError={reactionError}
            progress={(viewerIndex === null ? 0 : viewerIndex + 1) / Math.max(1, items.length)}
            onPrevious={() => setViewerIndex((current) => Math.max(0, Number(current || 0) - 1))}
            onNext={() => {
              if (viewerIndex !== null && viewerIndex < items.length - 1) setViewerIndex(viewerIndex + 1);
              else setViewerIndex(null);
            }}
            onToggleMuted={() => setMuted((current) => !current)}
            onReact={handleReact}
            onReply={(status) => setReplyStatus(status)}
            onShare={handleShare}
            onMore={(status) => setManageStatus(status)}
            onAuthorPress={(status) => {
              const target = profileTargetFromAuthor(status.author as Record<string, unknown> | undefined, status as unknown as Record<string, unknown>);
              const params = profileNavigationParams(target, status.author?.display_name || status.author_name || "Profile");
              if (params) navigation.navigate("ProfileDetail", params);
            }}
            onViewed={handleViewed}
          />
        ) : null}
        <Pressable accessibilityRole="button" accessibilityLabel="Close Status viewer" style={[styles.close, { top: insets.top + 64 }]} onPress={() => setViewerIndex(null)}>
          <Text style={styles.closeText}>Close</Text>
        </Pressable>
      </Modal>

      <ReplyModal
        visible={Boolean(replyStatus)}
        body={replyBody}
        posting={postingReply}
        onChangeBody={setReplyBody}
        onSubmit={submitReply}
        onClose={() => setReplyStatus(null)}
      />
      <StatusCreator visible={creatorOpen} onClose={() => setCreatorOpen(false)} onCreated={handleCreatedStatus} />
      <StatusManageModal
        status={manageStatus}
        onClose={() => setManageStatus(null)}
        onUpdated={(next) => updateStatus(next.id, next)}
        onDeleted={(statusId) => {
          setItems((current) => current.filter((item) => item.id !== statusId));
          setRailItems((current) => current.filter((item) => item.id !== statusId));
          setViewerIndex(null);
        }}
        onAuthorRemoved={(userId) => {
          setItems((current) => current.filter((item) => Number(item.user_id || item.author?.user_id || item.author?.id) !== userId));
          setRailItems((current) => current.filter((item) => Number(item.user_id || item.author?.user_id || item.author?.id) !== userId));
          setViewerIndex(null);
        }}
      />
    </View>
  );
}

function StatusRailBubble({ status, onPress }: { status: PulseStatus; onPress: (status: PulseStatus) => void }) {
  const author = status.author || {};
  const state = status.fixture_state === "uploading" ? "uploading" : status.fixture_state === "failed" ? "upload failed, retry available" : status.muted ? "muted" : status.author_live ? "live" : status.viewed ? "seen" : "unseen";
  const stories = Number(status.story_count || 1);
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Open ${author.display_name || "member"} Status, ${state}, ${stories} ${stories === 1 ? "story" : "stories"}`} style={styles.bubble} onPress={() => onPress(status)}>
      <View style={[styles.bubbleRing, status.viewed ? styles.bubbleViewed : undefined, status.author_live && styles.bubbleLive]}>
        {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.bubbleAvatar} /> : <View style={styles.bubbleAvatarFallback} />}
      </View>
      {Number(status.story_count || 1) > 1 ? <View style={styles.storyCount}><Text style={styles.storyCountText}>{status.story_count}</Text></View> : null}
      <Text style={styles.bubbleName} numberOfLines={1}>{author.display_name || "Status"}</Text>
      {status.muted ? <Text style={styles.railState}>Muted</Text> : status.fixture_state === "uploading" ? <Text style={styles.railState}>Uploading</Text> : status.fixture_state === "failed" ? <Text style={styles.railError}>Retry</Text> : status.author_live ? <Text style={styles.railLive}>Live</Text> : null}
    </Pressable>
  );
}

function CreateStatusRailEntry({ onPress }: { onPress: () => void }) {
  return <Pressable accessibilityRole="button" accessibilityLabel="Create a new Status" style={styles.bubble} onPress={onPress}><View style={styles.createRailRing}><Text style={styles.createRailPlus}>+</Text></View><Text style={styles.bubbleName}>Your Status</Text><Text style={styles.railState}>Create</Text></Pressable>;
}

function StatusManageModal({ status, onClose, onUpdated, onDeleted, onAuthorRemoved }: {
  status: PulseStatus | null;
  onClose: () => void;
  onUpdated: (status: PulseStatus) => void;
  onDeleted: (statusId: number) => void;
  onAuthorRemoved: (userId: number) => void;
}) {
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<"public" | "followers" | "private">("public");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setBody(status?.body || "");
    setVisibility(status?.visibility === "followers" || status?.visibility === "private" ? status.visibility : "public");
    setError("");
  }, [status]);
  if (!status) return null;
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.manageBackdrop}>
        <View style={styles.manageSheet}>
          <Text style={styles.replyTitle}>{status.can_manage ? "Manage Status" : "Status options"}</Text>
          {status.can_manage ? <>
            <View accessibilityLabel="Status owner analytics" style={styles.analyticsPanel}>
              <Text style={styles.analyticsTitle}>Status insights</Text>
              <Text style={styles.analyticsText}>{Number(status.owner_analytics?.views ?? status.view_count ?? 0)} views · {Math.round(Number(status.owner_analytics?.completion_rate ?? status.completion_rate ?? 0) * 100)}% completion</Text>
              <Text style={styles.analyticsText}>{Number(status.owner_analytics?.reactions ?? status.reaction_count ?? 0)} reactions · {Number(status.owner_analytics?.replies ?? status.reply_count ?? 0)} replies · {Number(status.owner_analytics?.shares ?? status.share_count ?? 0)} shares</Text>
            </View>
            <TextInput accessibilityLabel="Edit Status caption" style={styles.replyInput} value={body} onChangeText={setBody} multiline />
            <View style={styles.replyActions}>{(["public", "followers", "private"] as const).map((item) => <Pressable key={item} accessibilityRole="button" accessibilityState={{ selected: visibility === item }} style={[styles.visibilityOption, visibility === item && styles.visibilityOptionActive]} onPress={() => setVisibility(item)}><Text style={styles.secondaryText}>{item}</Text></Pressable>)}</View>
            <Pressable accessibilityRole="button" style={styles.primaryButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { const result = await updateStatusOnServer(status.id, { body: body.trim(), visibility }); if (result.status) onUpdated(result.status); onClose(); } catch { setError("Status could not be updated. Try again."); } finally { setBusy(false); } }}><Text style={styles.primaryText}>Save changes</Text></Pressable>
            <Pressable accessibilityRole="button" style={styles.deleteButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { await deleteStatus(status.id); onDeleted(status.id); onClose(); } catch { setError("Status could not be deleted. Try again."); } finally { setBusy(false); } }}><Text style={styles.deleteText}>Delete Status</Text></Pressable>
          </> : <>
            <Text style={styles.emptyText}>Safety actions are enforced by the existing PulseSoc report, mute, and block services.</Text>
            <Pressable accessibilityRole="button" accessibilityLabel="Report Status" style={styles.secondaryButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { await reportPulseTarget("status", status.id, "Reported from native Status"); onClose(); } catch { setError("Status report could not be submitted. Try again."); } finally { setBusy(false); } }}><Text style={styles.secondaryText}>Report Status</Text></Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Mute Status creator" style={styles.secondaryButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { await mutePostAuthor({ id: status.id, post_id: status.id, body: status.body || "", author: status.author }); onAuthorRemoved(Number(status.user_id || status.author?.user_id || status.author?.id || 0)); onClose(); } catch { setError("Creator could not be muted. Try again."); } finally { setBusy(false); } }}><Text style={styles.secondaryText}>Mute creator</Text></Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Block Status creator" style={styles.deleteButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { const userId = Number(status.user_id || status.author?.user_id || status.author?.id || 0); await blockPulseUser({ blockedUserId: userId, publicPlayerId: status.author?.public_player_id || status.author?.username || "", reason: "Blocked from native Status" }); onAuthorRemoved(userId); onClose(); } catch { setError("Creator could not be blocked. Try again."); } finally { setBusy(false); } }}><Text style={styles.deleteText}>Block creator</Text></Pressable>
          </>}
          {error ? <Text accessibilityLiveRegion="polite" style={styles.errorText}>{error}</Text> : null}
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={onClose}><Text style={styles.secondaryText}>Close</Text></Pressable>
        </View>
      </View>
    </Modal>
  );
}

function StatusListCard({ status, onPress }: { status: PulseStatus; onPress: (status: PulseStatus) => void }) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const kind = statusMediaKind(status);
  const url = statusMediaUrl(status);
  const music = statusMusicLabel(status);
  const viewerItems = (status.media || []).map((media) =>
    mediaViewerItemFromPulseMedia(media, {
      title: status.author?.display_name || status.author_name || "PulseSoc Status",
      subtitle: status.body || "Status media",
      author: status.author,
      sourceUrl: pulseStatusUrl(status.id)
    })
  );
  return (
    <Pressable style={styles.card} onPress={() => onPress(status)} onLongPress={() => (viewerItems.length ? setViewerOpen(true) : undefined)}>
      {kind === "image" && url ? <Image source={{ uri: url }} style={styles.cardMedia} /> : null}
      <View style={styles.cardScrim} />
      <Text style={styles.cardAuthor}>{status.author?.display_name || status.author_name || "PulseSoc member"}</Text>
      <Text style={styles.cardBody} numberOfLines={3}>{status.body || (kind === "video" ? "Video Status" : "PulseSoc Status")}</Text>
      {music ? <Text style={styles.cardMeta} numberOfLines={1}>{music}</Text> : null}
      <Text style={styles.cardMeta}>{formatShortTime(status.created_at)} · {status.reaction_count || 0} reactions · {status.reply_count || 0} replies</Text>
      <NativeMediaViewer visible={viewerOpen} items={viewerItems} title="Status media" onClose={() => setViewerOpen(false)} />
    </Pressable>
  );
}

function ReplyModal({ visible, body, posting, onChangeBody, onSubmit, onClose }: {
  visible: boolean;
  body: string;
  posting: boolean;
  onChangeBody: (value: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.replyWrap} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <Pressable style={styles.replyBackdrop} onPress={onClose} />
        <View style={styles.replySheet}>
          <Text style={styles.replyTitle}>Reply to Status</Text>
          <TextInput
            accessibilityLabel="Status reply"
            style={styles.replyInput}
            value={body}
            onChangeText={onChangeBody}
            placeholder="Write a reply"
            placeholderTextColor={colors.muted}
            multiline
            autoFocus
          />
          <View style={styles.replyActions}>
            <Pressable style={styles.secondaryButton} onPress={onClose}><Text style={styles.secondaryText}>Cancel</Text></Pressable>
            <Pressable style={[styles.primaryButton, (!body.trim() || posting) && styles.disabledButton]} disabled={!body.trim() || posting} onPress={onSubmit}>
              <Text style={styles.primaryText}>{posting ? "Sending" : "Send"}</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function focusInitialStatus(statuses: PulseStatus[], statusId: number) {
  if (!statusId) return statuses;
  const index = statuses.findIndex((item) => item.id === statusId);
  if (index <= 0) return statuses;
  return [statuses[index], ...statuses.slice(0, index), ...statuses.slice(index + 1)];
}

const styles = StyleSheet.create({
  bubble: {
    alignItems: "center",
    marginRight: 12,
    width: 70
  },
  bubbleAvatar: {
    borderRadius: 28,
    height: 56,
    width: 56
  },
  bubbleAvatarFallback: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 28,
    height: 56,
    width: 56
  },
  bubbleName: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 6,
    textAlign: "center",
    width: "100%"
  },
  bubbleRing: {
    borderColor: colors.accent,
    borderRadius: 34,
    borderWidth: 3,
    padding: 3
  },
  bubbleViewed: {
    borderColor: colors.border
  },
  bubbleLive: {
    borderColor: "#ff5fa8",
    shadowColor: "#ff5fa8",
    shadowOpacity: 0.42,
    shadowRadius: 12
  },
  createRailRing: { alignItems: "center", backgroundColor: "rgba(54,229,143,0.1)", borderColor: colors.accent, borderRadius: 34, borderStyle: "dashed", borderWidth: 2, height: 68, justifyContent: "center", width: 68 },
  createRailPlus: { color: colors.accent, fontSize: 32, fontWeight: "500" },
  railState: { color: colors.muted, fontSize: 9, fontWeight: "800", marginTop: 2 },
  railError: { color: colors.danger, fontSize: 9, fontWeight: "900", marginTop: 2 },
  railLive: { color: "#ff5fa8", fontSize: 9, fontWeight: "900", marginTop: 2 },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 12,
    minHeight: 180,
    overflow: "hidden",
    padding: 14
  },
  cardAuthor: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    zIndex: 2
  },
  cardBody: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900",
    lineHeight: 28,
    marginTop: 38,
    zIndex: 2
  },
  cardMedia: {
    ...StyleSheet.absoluteFillObject
  },
  cardMeta: {
    color: "rgba(244,247,251,0.72)",
    fontSize: 12,
    marginTop: 8,
    zIndex: 2
  },
  cardScrim: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.42)"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center"
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  cameraButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  cameraText: {
    color: colors.text,
    fontWeight: "900"
  },
  close: {
    backgroundColor: "rgba(8,15,28,0.72)",
    borderRadius: 16,
    left: 12,
    paddingHorizontal: 12,
    paddingVertical: 9,
    position: "absolute",
    top: 44,
    zIndex: 20
  },
  closeText: {
    color: colors.text,
    fontWeight: "900"
  },
  content: {
    padding: 16,
    paddingBottom: 36
  },
  disabledButton: {
    opacity: 0.5
  },
  empty: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 18
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 21,
    marginTop: 6
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  createButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 13,
    paddingVertical: 10
  },
  createButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  headerRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12
  },
  headerActions: {
    flexDirection: "row",
    gap: 8
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  rail: {
    paddingBottom: 14,
    paddingTop: 14
  },
  replyActions: {
    flexDirection: "row",
    gap: 10,
    justifyContent: "flex-end",
    marginTop: 12
  },
  replyBackdrop: {
    ...StyleSheet.absoluteFillObject
  },
  replyInput: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 96,
    padding: 12,
    textAlignVertical: "top"
  },
  replySheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    padding: 16
  },
  replyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginBottom: 12
  },
  replyWrap: {
    backgroundColor: "rgba(0,0,0,0.45)",
    flex: 1,
    justifyContent: "flex-end"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  storyCount: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 10, height: 20, justifyContent: "center", position: "absolute", right: 4, top: 42, width: 20 },
  storyCountText: { color: colors.background, fontSize: 10, fontWeight: "900" },
  analyticsPanel: { backgroundColor: colors.surfaceRaised, borderColor: colors.border, borderRadius: 14, borderWidth: 1, gap: 5, padding: 12 },
  analyticsTitle: { color: colors.text, fontSize: 15, fontWeight: "900" },
  analyticsText: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  manageBackdrop: { backgroundColor: "rgba(0,0,0,0.62)", flex: 1, justifyContent: "flex-end" },
  manageSheet: { backgroundColor: colors.surface, borderColor: colors.border, borderTopLeftRadius: 26, borderTopRightRadius: 26, borderWidth: 1, gap: 12, padding: 18, paddingBottom: 32 },
  visibilityOption: { borderColor: colors.border, borderRadius: 999, borderWidth: 1, flex: 1, padding: 10 },
  visibilityOptionActive: { backgroundColor: "rgba(54,229,143,0.18)", borderColor: colors.accent },
  deleteButton: { alignItems: "center", borderColor: colors.danger, borderRadius: 14, borderWidth: 1, padding: 12 },
  deleteText: { color: colors.danger, fontWeight: "900" },
  errorText: { color: colors.danger, fontWeight: "800" },
  secondaryButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 3
  },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  }
});
