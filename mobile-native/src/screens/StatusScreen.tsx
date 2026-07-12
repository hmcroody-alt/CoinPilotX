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
  deleteStatus,
  listStatuses,
  loadCachedStatuses,
  PulseStatus,
  pulseStatusUrl,
  reactToStatus,
  replyToStatus,
  shareStatus,
  statusMediaKind,
  statusMediaUrl,
  statusMusicLabel,
  trackStatusView,
  updateStatus as updateStatusOnServer
} from "../api/status";
import { StatusCreator } from "../components/StatusCreator";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "../components/NativeMediaViewer";
import { StatusViewerCard } from "../components/StatusViewerCard";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = {
  route: { params?: { statusId?: number; title?: string; openCreator?: boolean } };
  navigation: { navigate: (name: string, params?: Record<string, unknown>) => void };
};

const LANE = "for_you";

export function StatusScreen({ route, navigation }: Props) {
  const initialStatusId = Number(route.params?.statusId || 0);
  const [items, setItems] = useState<PulseStatus[]>([]);
  const [railItems, setRailItems] = useState<PulseStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [muted, setMuted] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [replyStatus, setReplyStatus] = useState<PulseStatus | null>(null);
  const [replyBody, setReplyBody] = useState("");
  const [postingReply, setPostingReply] = useState(false);
  const [manageStatus, setManageStatus] = useState<PulseStatus | null>(null);
  const viewed = useRef(new Set<number>());

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

  async function handleReact(status: PulseStatus, reactionType = "fire") {
    setBusyId(status.id);
    updateStatus(status.id, { reaction_count: Number(status.reaction_count || 0) + 1 });
    try {
      const result = await reactToStatus(status.id, reactionType);
      updateStatus(status.id, { reaction_count: Number(result.reaction_count || status.reaction_count || 0) });
    } catch {
      updateStatus(status.id, { reaction_count: status.reaction_count || 0 });
    } finally {
      setBusyId(null);
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
      setItems((current) => [status, ...current.filter((item) => item.id !== status.id)]);
      setRailItems((current) => [status, ...current.filter((item) => item.id !== status.id)].slice(0, 24));
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
                <Pressable style={styles.cameraButton} onPress={() => navigation.navigate("CameraStudio", { target: "status", mode: "status", title: "Status Camera" })}>
                  <Text style={styles.cameraText}>Camera</Text>
                </Pressable>
                <Pressable style={styles.createButton} onPress={() => setCreatorOpen(true)}>
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
              const key = status.author?.public_player_id || status.author?.username || "";
              if (key) navigation.navigate("ProfileDetail", { profileKey: key, title: status.author?.display_name || "Profile" });
            }}
            onViewed={handleViewed}
          />
        ) : null}
        <Pressable style={styles.close} onPress={() => setViewerIndex(null)}>
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
      />
    </View>
  );
}

function StatusRailBubble({ status, onPress }: { status: PulseStatus; onPress: (status: PulseStatus) => void }) {
  const author = status.author || {};
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Open ${author.display_name || "member"} Status, ${status.viewed ? "seen" : "unseen"}`} style={styles.bubble} onPress={() => onPress(status)}>
      <View style={[styles.bubbleRing, status.viewed ? styles.bubbleViewed : undefined, status.author_live && styles.bubbleLive]}>
        {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.bubbleAvatar} /> : <View style={styles.bubbleAvatarFallback} />}
      </View>
      {Number(status.story_count || 1) > 1 ? <View style={styles.storyCount}><Text style={styles.storyCountText}>{status.story_count}</Text></View> : null}
      <Text style={styles.bubbleName} numberOfLines={1}>{author.display_name || "Status"}</Text>
    </Pressable>
  );
}

function StatusManageModal({ status, onClose, onUpdated, onDeleted }: {
  status: PulseStatus | null;
  onClose: () => void;
  onUpdated: (status: PulseStatus) => void;
  onDeleted: (statusId: number) => void;
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
            <TextInput accessibilityLabel="Edit Status caption" style={styles.replyInput} value={body} onChangeText={setBody} multiline />
            <View style={styles.replyActions}>{(["public", "followers", "private"] as const).map((item) => <Pressable key={item} accessibilityRole="button" accessibilityState={{ selected: visibility === item }} style={[styles.visibilityOption, visibility === item && styles.visibilityOptionActive]} onPress={() => setVisibility(item)}><Text style={styles.secondaryText}>{item}</Text></Pressable>)}</View>
            <Pressable accessibilityRole="button" style={styles.primaryButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { const result = await updateStatusOnServer(status.id, { body: body.trim(), visibility }); if (result.status) onUpdated(result.status); onClose(); } catch { setError("Status could not be updated. Try again."); } finally { setBusy(false); } }}><Text style={styles.primaryText}>Save changes</Text></Pressable>
            <Pressable accessibilityRole="button" style={styles.deleteButton} disabled={busy} onPress={async () => { setBusy(true); setError(""); try { await deleteStatus(status.id); onDeleted(status.id); onClose(); } catch { setError("Status could not be deleted. Try again."); } finally { setBusy(false); } }}><Text style={styles.deleteText}>Delete Status</Text></Pressable>
          </> : <Text style={styles.emptyText}>Sharing and reporting follow the current Status privacy and safety rules.</Text>}
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
            style={styles.replyInput}
            value={body}
            onChangeText={onChangeBody}
            placeholder="Write a reply"
            placeholderTextColor={colors.muted}
            multiline
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
