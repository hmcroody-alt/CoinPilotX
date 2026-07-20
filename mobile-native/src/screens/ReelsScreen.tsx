import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  Animated,
  AppState,
  Dimensions,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  Vibration,
  View,
  ViewToken
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PULSESOC_QA_REELS_FIXTURES } from "../api/config";
import { PulseComment } from "../api/feed";
import { liveWebUrl } from "../api/live";
import {
  addReelComment,
  clearReelCommentDraft,
  deleteReelComment,
  editReelComment,
  followReelCreator,
  getReelComments,
  listReels,
  loadReelCommentDraft,
  loadCachedReelsSnapshot,
  markReelNotInterested,
  PulseReel,
  reactToReel,
  reactToReelComment,
  reelWebUrl,
  reportReel,
  reportReelComment,
  repostReel,
  saveReel,
  saveReelCommentDraft,
  shareReel,
  trackReelView
} from "../api/reels";
import { PulseApiError } from "../api/pulseApi";
import { profileNavigationParams, profileTargetFromAuthor } from "../api/profileTarget";
import { ReelPlayerCard } from "../components/ReelPlayerCard";
import { registerSyncInvalidation } from "../core/eventSync";
import { configureReelsAudioSession } from "../core/reelsAudioSession";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { useAuth } from "../session/auth";

type Props = NativeStackScreenProps<RootStackParamList, "Reels"> | NativeStackScreenProps<RootStackParamList, "ReelDetail">;

const PAGE_SIZE = 8;
const QA_REELS_STATE = PULSESOC_QA_REELS_FIXTURES ? String(process.env.EXPO_PUBLIC_PULSESOC_QA_REELS_STATE || "").trim().toLowerCase() : "";
type ReelLane = "for_you" | "following" | "trending" | "music" | "live";
const REEL_LANES: Array<{ key: ReelLane; label: string }> = [{ key: "for_you", label: "For You" }, { key: "following", label: "Following" }, { key: "trending", label: "Trending" }, { key: "music", label: "Music" }, { key: "live", label: "Live" }];
type ConnectionState = "loading" | "connecting" | "ready" | "cached" | "offline" | "server_busy" | "maintenance" | "rate_limited" | "auth_expired" | "account_restricted" | "empty";
const RETRY_DELAYS = [1_000, 2_000, 5_000, 10_000];
const QA_RECOVERY_STATES = new Set<ConnectionState>(["loading", "connecting", "offline", "server_busy", "maintenance", "rate_limited", "auth_expired", "empty"]);

export function ReelsScreen({ route, navigation }: Props) {
  const { authState, requestReauthentication } = useAuth();
  const insets = useSafeAreaInsets();
  const params = route.params || {};
  const initialReelId = "reelId" in params ? Number(params.reelId || 0) : 0;
  const [reels, setReels] = useState<PulseReel[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [lane, setLane] = useState<ReelLane>(QA_REELS_STATE === "live" ? "live" : QA_REELS_STATE === "music" ? "music" : "for_you");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [muted, setMuted] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("loading");
  const [retryCount, setRetryCount] = useState(0);
  const [cachedAt, setCachedAt] = useState(0);
  const [offline, setOffline] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [commentReel, setCommentReel] = useState<PulseReel | null>(null);
  const [comments, setComments] = useState<PulseComment[]>([]);
  const [commentBody, setCommentBody] = useState("");
  const [commentError, setCommentError] = useState("");
  const [commentTotal, setCommentTotal] = useState(0);
  const [postingComment, setPostingComment] = useState(false);
  const [replyTo, setReplyTo] = useState<PulseComment | null>(null);
  const [editingComment, setEditingComment] = useState<PulseComment | null>(null);
  const [editBody, setEditBody] = useState("");
  const [editingBusy, setEditingBusy] = useState(false);
  const [reactionReel, setReactionReel] = useState<PulseReel | null>(null);
  const [musicReel, setMusicReel] = useState<PulseReel | null>(null);
  const [moreReel, setMoreReel] = useState<PulseReel | null>(null);
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  const [shareOpen, setShareOpen] = useState(false);
  const [viewportHeight, setViewportHeight] = useState(Dimensions.get("window").height);
  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 72 });
  const qaStateApplied = useRef(false);
  const loadVersion = useRef(0);
  const activeReelId = useRef(0);

  async function load(mode: "initial" | "refresh" | "more" = "initial") {
    if (mode === "initial" && QA_REELS_STATE && QA_RECOVERY_STATES.has(QA_REELS_STATE as ConnectionState)) {
      setReels([]);
      setConnectionState(QA_REELS_STATE as ConnectionState);
      setLoading(QA_REELS_STATE === "loading");
      return;
    }
    if (mode === "more" && (!hasMore || loadingMore)) return;
    const nextOffset = mode === "more" ? offset : 0;
    setOffline(false);
    const version = ++loadVersion.current;
    if (mode === "initial") {
      setLoading(true);
      setConnectionState("loading");
      const snapshot = await loadCachedReelsSnapshot(lane);
      if (version !== loadVersion.current) return;
      if (snapshot.reels.length) {
        setReels(focusInitialReel(snapshot.reels, initialReelId));
        setCachedAt(snapshot.cachedAt);
        setOffline(true);
        setConnectionState("connecting");
        setLoading(false);
      }
    }
    if (mode === "refresh") setRefreshing(true);
    if (mode === "more") setLoadingMore(true);
    try {
      const data = await listReels({ lane, limit: PAGE_SIZE, offset: nextOffset, includeComments: false });
      if (version !== loadVersion.current) return;
      const next = mode === "more" ? mergeReels(reels, data.reels || []) : focusInitialReel(data.reels || [], initialReelId);
      setReels(next);
      setOffset(Number(data.next_offset || nextOffset + (data.reels?.length || 0)));
      setHasMore(Boolean(data.has_more));
      setOffline(false);
      setRetryCount(0);
      setConnectionState(next.length ? "ready" : "empty");
      if (mode !== "more") {
        const preserveReelId = initialReelId || activeReelId.current;
        const index = preserveReelId ? next.findIndex((item) => item.id === preserveReelId) : -1;
        if (index >= 0) setActiveIndex(index);
        else setActiveIndex((current) => Math.min(current, Math.max(0, next.length - 1)));
      }
    } catch (loadError) {
      if (version !== loadVersion.current) return;
      const snapshot = await loadCachedReelsSnapshot(lane);
      if (snapshot.reels.length && mode !== "more") {
        setReels(focusInitialReel(snapshot.reels, initialReelId));
        setCachedAt(snapshot.cachedAt);
        setOffline(true);
        setConnectionState("cached");
      } else {
        setConnectionState(classifyConnectionState(loadError));
      }
      setRetryCount((current) => Math.min(current + 1, RETRY_DELAYS.length));
      logReelsFailure(loadError, { lane, cacheCount: snapshot.reels.length, retryCount: retryCount + 1 });
    } finally {
      setLoading(false);
      setRefreshing(false);
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    setActiveIndex(0);
    setOffset(0);
    setHasMore(false);
    load("initial").catch(() => undefined);
  }, [initialReelId, lane]);

  useEffect(() => registerSyncInvalidation("reels", () => {
    load("refresh").catch(() => undefined);
    if (commentReel) refreshComments(commentReel).catch(() => undefined);
  }), [lane, initialReelId, commentReel?.id]);

  useEffect(() => {
    configureReelsAudioSession().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!appActive || connectionState === "ready" || connectionState === "empty" || connectionState === "auth_expired" || retryCount <= 0) return;
    const delay = RETRY_DELAYS[Math.min(retryCount - 1, RETRY_DELAYS.length - 1)];
    const timer = setTimeout(() => {
      setConnectionState((current) => current === "cached" ? "connecting" : current);
      load("refresh").catch(() => undefined);
    }, delay);
    return () => clearTimeout(timer);
  }, [appActive, connectionState, retryCount, lane]);

  useEffect(() => {
    const listener = AppState.addEventListener("change", (state) => setAppActive(state === "active"));
    return () => listener.remove();
  }, []);

  useEffect(() => {
    if (!QA_REELS_STATE || qaStateApplied.current || !reels.length) return;
    qaStateApplied.current = true;
    const normalReel = reels.find((item) => item.id > 0) || reels[0];
    if (QA_REELS_STATE === "comments") {
      setCommentReel(normalReel);
      setComments(normalReel.preview_comments || []);
    } else if (QA_REELS_STATE === "reaction") {
      setReactionReel(normalReel);
    } else if (QA_REELS_STATE === "music") {
      setMusicReel(reels.find((item) => item.audio?.title) || normalReel);
    } else if (QA_REELS_STATE === "more") {
      setMoreReel(normalReel);
    }
  }, [reels]);

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    const next = viewableItems[0]?.index;
    const item = viewableItems[0]?.item as PulseReel | undefined;
    if (item?.id) activeReelId.current = item.id;
    if (typeof next === "number") setActiveIndex(next);
  });

  useEffect(() => {
    if (!commentReel) return;
    const timer = setTimeout(() => {
      saveReelCommentDraft(commentReel.id, commentBody, replyTo?.id || 0).catch(() => undefined);
    }, 180);
    return () => clearTimeout(timer);
  }, [commentBody, replyTo?.id, commentReel?.id]);

  const updateReel = useCallback((reelId: number, next: Partial<PulseReel>) => {
    setReels((current) => current.map((item) => (item.id === reelId ? { ...item, ...next } : item)));
  }, []);

  async function handleReact(reel: PulseReel, reactionType = "fire") {
    if (reel.live_session_id || reel.live?.live_session_id) return;
    if (reel.reactions_disabled) return;
    setBusyId(reel.id);
    const previousReaction = reel.viewer_reaction || "";
    const previousCount = Number(reel.reactions_count || 0);
    updateReel(reel.id, { viewer_reaction: reactionType, reactions_count: previousCount + (previousReaction ? 0 : 1) });
    try {
      const result = await reactToReel(reel.id, reactionType);
      updateReel(reel.id, {
        viewer_reaction: result.removed ? "" : result.reaction_type || reactionType,
        reaction_counts: result.reaction_counts || reel.reaction_counts,
        reactions_count: result.removed ? Math.max(0, previousCount - 1) : previousCount + (previousReaction ? 0 : 1)
      });
      Vibration.vibrate(8);
    } catch {
      updateReel(reel.id, { viewer_reaction: reel.viewer_reaction, reactions_count: reel.reactions_count || 0 });
    } finally {
      setBusyId(null);
    }
  }

  async function handleSave(reel: PulseReel) {
    if (reel.live_session_id || reel.live?.live_session_id) return;
    setBusyId(reel.id);
    updateReel(reel.id, { saved: !reel.saved });
    try {
      const result = await saveReel(reel.id);
      updateReel(reel.id, { saved: Boolean(result.saved) });
    } catch {
      updateReel(reel.id, { saved: reel.saved });
    } finally {
      setBusyId(null);
    }
  }

  async function handleRepost(reel: PulseReel) {
    setBusyId(reel.id);
    updateReel(reel.id, { reposted: true });
    try {
      await repostReel(reel.id);
    } catch {
      updateReel(reel.id, { reposted: reel.reposted });
    } finally {
      setBusyId(null);
    }
  }

  async function handleShare(reel: PulseReel) {
    setShareOpen(true);
    try {
      const liveId = Number(reel.live_session_id || reel.live?.live_session_id || 0);
      if (liveId) {
        await Share.share({ message: reel.live?.live_url || liveWebUrl(liveId) });
        return;
      }
      const result = await shareReel(reel.id);
      await Share.share({ message: result.share_url || reelWebUrl(reel.id) });
    } catch {
      await Share.share({ message: reelWebUrl(reel.id) }).catch(() => undefined);
    } finally {
      setShareOpen(false);
    }
  }

  async function handleNotInterested(reel: PulseReel) {
    setBusyId(reel.id);
    try {
      await markReelNotInterested(reel.id);
      setReels((current) => current.filter((item) => item.id !== reel.id));
    } finally {
      setBusyId(null);
    }
  }

  async function handleFollowCreator(reel: PulseReel) {
    setBusyId(reel.id);
    const original = Boolean(reel.viewer_follows_author);
    updateReel(reel.id, { viewer_follows_author: !original });
    const result = await followReelCreator(reel.id).catch(() => null);
    updateReel(reel.id, { viewer_follows_author: result ? Boolean(result.following) : original });
    setBusyId(null);
  }

  async function handleReport(reel: PulseReel) {
    setBusyId(reel.id);
    await reportReel(reel.id).catch(() => undefined);
    setBusyId(null);
  }

  async function openComments(reel: PulseReel) {
    setCommentReel(reel);
    setCommentError("");
    setCommentTotal(Number(reel.comments_count || 0));
    setReplyTo(null);
    setEditingComment(null);
    setComments(reel.preview_comments || []);
    const draft = await loadReelCommentDraft(reel.id);
    setCommentBody(draft?.body || "");
    try {
      const result = await getReelComments(reel.id);
      setComments(result.comments);
      setCommentTotal(result.commentsCount);
      if (draft?.replyToCommentId) setReplyTo(findComment(result.comments, draft.replyToCommentId));
    } catch {
      setComments(reel.preview_comments || []);
    }
  }

  async function refreshComments(reel: PulseReel) {
    const result = await getReelComments(reel.id);
    setComments(result.comments);
    setCommentTotal(result.commentsCount);
    updateReel(reel.id, { comments_count: result.commentsCount });
  }

  async function submitComment() {
    if (!commentReel || !commentBody.trim() || postingComment) return;
    const body = commentBody.trim();
    setPostingComment(true);
    setCommentError("");
    setCommentBody("");
    try {
      const comment = await addReelComment(commentReel.id, body, replyTo?.id || 0);
      setComments((current) => replyTo ? insertReply(current, replyTo.id, comment) : [comment, ...current]);
      setCommentTotal((current) => current + 1);
      updateReel(commentReel.id, { comments_count: commentTotal + 1 });
      await clearReelCommentDraft(commentReel.id);
      setReplyTo(null);
    } catch {
      setCommentBody(body);
      setCommentError("Saved as a private draft on this device. Posting requires a connection.");
    } finally {
      setPostingComment(false);
    }
  }

  function beginEditComment(comment: PulseComment) {
    if (!comment.can_edit && Number(comment.user_id || comment.author?.user_id || 0) !== Number(authState.user?.user_id || 0)) return;
    setEditingComment(comment);
    setEditBody(comment.body);
    setCommentError("");
  }

  async function submitEditComment() {
    if (!editingComment || !editBody.trim() || editingBusy) return;
    setEditingBusy(true);
    setCommentError("");
    try {
      const result = await editReelComment(editingComment.id, editBody.trim());
      const updated = result.comment ? { ...editingComment, ...result.comment, body: result.comment.body || editBody.trim(), edited_at: result.comment.edited_at || new Date().toISOString() } : { ...editingComment, body: editBody.trim(), edited_at: new Date().toISOString() };
      setComments((current) => updateCommentTree(current, editingComment.id, updated));
      setEditingComment(null);
      setEditBody("");
    } catch {
      setCommentError("That edit was not accepted. Check your connection and ownership, then retry.");
    } finally {
      setEditingBusy(false);
    }
  }

  async function handleDeleteComment(comment: PulseComment) {
    if (!comment.can_delete && Number(comment.user_id || comment.author?.user_id || 0) !== Number(authState.user?.user_id || 0)) return;
    const previous = comments;
    setComments((current) => removeCommentFromTree(current, comment.id));
    if (commentReel) updateReel(commentReel.id, { comments_count: Math.max(0, Number(commentReel.comments_count || 0) - 1) });
    try {
      await deleteReelComment(comment.id);
      setCommentTotal((current) => Math.max(0, current - 1));
    } catch {
      setComments(previous);
      if (commentReel) updateReel(commentReel.id, { comments_count: Number(commentReel.comments_count || 0) });
      setCommentError("Delete was not authorized or the network is unavailable.");
    }
  }

  async function handleReactToComment(comment: PulseComment) {
    const previous = comments;
    const wasActive = Boolean(comment.viewer_reaction);
    setComments((current) => updateCommentTree(current, comment.id, { ...comment, viewer_reaction: wasActive ? "" : "like", like_count: Math.max(0, Number(comment.like_count || 0) + (wasActive ? -1 : 1)) }));
    try {
      const result = await reactToReelComment(comment.id);
      setComments((current) => updateCommentTree(current, comment.id, { ...comment, viewer_reaction: result.removed ? "" : result.reaction_type || "like", like_count: Number(result.reaction_counts?.like || 0), reaction_counts: result.reaction_counts }));
    } catch {
      setComments(previous);
      setCommentError("Comment reaction needs a connection. No change was saved.");
    }
  }

  function joinLiveReel(reel: PulseReel) {
    const liveId = Number(reel.live_session_id || reel.live?.live_session_id || 0);
    if (liveId) navigation.navigate("LiveDetail", { liveId, title: reel.title || "PulseSoc Live" });
  }

  return (
    <View style={styles.root} onLayout={(event) => setViewportHeight(event.nativeEvent.layout.height)}>
      <GalaxyField />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.laneRailContent} style={[styles.laneRail, { top: insets.top + 6 }]} accessibilityRole="tablist">
        {REEL_LANES.map((item) => <Pressable key={item.key} accessibilityRole="tab" accessibilityState={{ selected: lane === item.key }} style={[styles.laneButton, lane === item.key && styles.laneButtonActive]} onPress={() => setLane(item.key)}><Text style={[styles.laneText, lane === item.key && styles.laneTextActive]}>{item.label}</Text></Pressable>)}
      </ScrollView>
      <Pressable accessibilityRole="button" accessibilityLabel="Create Reel" style={[styles.createButton, { top: insets.top + 6 }]} onPress={() => navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true, composerMode: "reel" } })}><Text style={styles.createText}>＋</Text></Pressable>
      {offline && reels.length ? <View style={[styles.statusPill, { top: insets.top + 52 }]}><Text style={styles.statusPillText}>Saved Reels · {connectionState === "connecting" ? "refreshing" : cacheAge(cachedAt)}</Text></View> : null}
      <FlatList
        data={reels}
        keyExtractor={(item) => String(item.id)}
        renderItem={({ item, index }) => (
          <View style={[styles.page, { height: viewportHeight }]}>
            <ReelPlayerCard
              reel={item}
              active={index === activeIndex && appActive && !shareOpen && !commentReel && !reactionReel && !musicReel && !moreReel}
              muted={muted}
              offline={offline}
              contentTop={insets.top + 56}
              busy={busyId === item.id}
              onToggleMuted={() => setMuted((current) => !current)}
              onReact={handleReact}
              onOpenReactions={setReactionReel}
              onOpenComments={(reel) => reel.live_session_id || reel.live?.live_session_id ? joinLiveReel(reel) : openComments(reel)}
              onSave={handleSave}
              onRepost={handleRepost}
              onPromote={(reel) => navigation.navigate("GrowthCenter", { contentType: "reel", contentId: reel.id, title: "Promote Reel" })}
              onShare={handleShare}
              onNotInterested={handleNotInterested}
              onReport={handleReport}
              onFollowCreator={handleFollowCreator}
              onAuthorPress={(reel) => {
                const target = profileTargetFromAuthor(reel.author as Record<string, unknown> | undefined, reel as unknown as Record<string, unknown>);
                const params = profileNavigationParams(target, reel.author?.display_name || "Profile");
                if (params) navigation.navigate("ProfileDetail", params);
              }}
              onOpenMusic={setMusicReel}
              onOpenMore={setMoreReel}
              onJoinLive={joinLiveReel}
              onViewable={(reel, watchMs) => reel.id > 0 ? trackReelView(reel.id, watchMs).catch(() => undefined) : undefined}
            />
          </View>
        )}
        pagingEnabled
        showsVerticalScrollIndicator={false}
        snapToInterval={viewportHeight}
        decelerationRate="fast"
        viewabilityConfig={viewabilityConfig.current}
        onViewableItemsChanged={onViewableItemsChanged.current}
        onEndReached={() => load("more").catch(() => undefined)}
        onEndReachedThreshold={0.5}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListEmptyComponent={<ReelsRecovery state={connectionState} loading={loading} onRetry={() => load("refresh").catch(() => undefined)} onAuthenticate={() => requestReauthentication("/pulse/reels")} onExplore={(nextLane) => setLane(nextLane)} />}
        ListFooterComponent={loadingMore ? <ActivityIndicator style={styles.footer} color={colors.accent} /> : null}
        initialNumToRender={2}
        maxToRenderPerBatch={2}
        windowSize={3}
        removeClippedSubviews
      />
      <CommentsModal
        visible={Boolean(commentReel)}
        reel={commentReel}
        comments={comments}
        total={commentTotal}
        body={commentBody}
        error={commentError}
        posting={postingComment}
        onChangeBody={setCommentBody}
        onSubmit={submitComment}
        replyTo={replyTo}
        onReply={setReplyTo}
        onReact={(comment) => handleReactToComment(comment).catch(() => undefined)}
        editingComment={editingComment}
        editBody={editBody}
        editingBusy={editingBusy}
        onBeginEdit={beginEditComment}
        onChangeEditBody={setEditBody}
        onSubmitEdit={() => submitEditComment().catch(() => undefined)}
        onCancelEdit={() => { setEditingComment(null); setEditBody(""); }}
        currentUserId={Number(authState.user?.user_id || 0)}
        onDelete={(comment) => handleDeleteComment(comment).catch(() => undefined)}
        onReport={(comment) => reportReelComment(comment.id).catch(() => undefined)}
        onCancelReply={() => setReplyTo(null)}
        onClose={() => { setCommentReel(null); setReplyTo(null); setEditingComment(null); }}
      />
      <ReactionPicker reel={reactionReel} onSelect={(reaction) => { if (reactionReel) handleReact(reactionReel, reaction).catch(() => undefined); setReactionReel(null); }} onClose={() => setReactionReel(null)} />
      <MusicDetail reel={musicReel} onClose={() => setMusicReel(null)} />
      <ReelMoreMenu reel={moreReel} onClose={() => setMoreReel(null)} onRepost={(reel) => { setMoreReel(null); handleRepost(reel).catch(() => undefined); }} onLess={(reel) => { setMoreReel(null); handleNotInterested(reel).catch(() => undefined); }} onReport={(reel) => { setMoreReel(null); handleReport(reel).catch(() => undefined); }} onPromote={(reel) => { setMoreReel(null); navigation.navigate("GrowthCenter", { contentType: "reel", contentId: reel.id, title: "Promote Reel" }); }} />
    </View>
  );
}

function CommentsModal({
  visible,
  reel,
  comments,
  total,
  body,
  error,
  posting,
  onChangeBody,
  onSubmit,
  replyTo,
  onReply,
  onReact,
  editingComment,
  editBody,
  editingBusy,
  onBeginEdit,
  onChangeEditBody,
  onSubmitEdit,
  onCancelEdit,
  currentUserId,
  onDelete,
  onReport,
  onCancelReply,
  onClose
}: {
  visible: boolean;
  reel: PulseReel | null;
  comments: PulseComment[];
  total: number;
  body: string;
  error: string;
  posting: boolean;
  onChangeBody: (value: string) => void;
  onSubmit: () => void;
  replyTo: PulseComment | null;
  onReply: (comment: PulseComment) => void;
  onReact: (comment: PulseComment) => void;
  editingComment: PulseComment | null;
  editBody: string;
  editingBusy: boolean;
  onBeginEdit: (comment: PulseComment) => void;
  onChangeEditBody: (value: string) => void;
  onSubmitEdit: () => void;
  onCancelEdit: () => void;
  currentUserId: number;
  onDelete: (comment: PulseComment) => void;
  onReport: (comment: PulseComment) => void;
  onCancelReply: () => void;
  onClose: () => void;
}) {
  const [visibleCount, setVisibleCount] = useState(20);
  const [expandedReplies, setExpandedReplies] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (visible) setVisibleCount(20);
  }, [visible, reel?.id]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.modalWrap} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <Pressable style={styles.modalBackdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHeader}>
            <View><View style={styles.sheetHandle} /><Text style={styles.sheetTitle}>{total || comments.length} Comments</Text><Text style={styles.sheetContext} numberOfLines={1}>{reel?.author?.display_name || "PulseSoc creator"} · {reel?.title || "Reel"}</Text></View>
            <Pressable onPress={onClose}><Text style={styles.closeText}>Close</Text></Pressable>
          </View>
          <FlatList
            data={comments.slice(0, visibleCount)}
            keyExtractor={(item, index) => `${item.id}-${index}`}
            ListEmptyComponent={<Text style={styles.empty}>No comments yet.</Text>}
            renderItem={({ item }) => <CommentThread comment={item} currentUserId={currentUserId} expanded={expandedReplies.has(item.id)} editingComment={editingComment} editBody={editBody} editingBusy={editingBusy} onToggleReplies={() => setExpandedReplies((current) => toggleSetValue(current, item.id))} onReply={onReply} onReact={onReact} onBeginEdit={onBeginEdit} onChangeEditBody={onChangeEditBody} onSubmitEdit={onSubmitEdit} onCancelEdit={onCancelEdit} onDelete={onDelete} onReport={onReport} />}
            ListFooterComponent={comments.length > visibleCount ? <Pressable accessibilityRole="button" style={styles.loadMoreComments} onPress={() => setVisibleCount((current) => current + 20)}><Text style={styles.commentAction}>Load more comments</Text></Pressable> : null}
          />
          {reel?.comments_disabled ? (
            <Text style={styles.disabledText}>Comments are disabled for this Reel.</Text>
          ) : (
            <View>
              {error ? <Text accessibilityLiveRegion="polite" style={styles.commentError}>{error}</Text> : null}
              {replyTo ? <View style={styles.replyBanner}><Text style={styles.replyText}>Replying to {replyTo.author?.display_name || replyTo.author?.username || "comment"}</Text><Pressable onPress={onCancelReply}><Text style={styles.closeText}>Cancel</Text></Pressable></View> : null}
              <View style={styles.composer}>
              <TextInput
                style={styles.input}
                value={body}
                onChangeText={onChangeBody}
                placeholder={replyTo ? "Write a reply" : "Add a comment"}
                placeholderTextColor={colors.muted}
              />
              <Pressable style={[styles.sendButton, (!body.trim() || posting) && styles.sendDisabled]} disabled={!body.trim() || posting} onPress={onSubmit}>
                <Text style={styles.sendText}>{posting ? "Sending" : "Post"}</Text>
              </Pressable>
              </View>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function CommentThread({ comment, currentUserId, expanded, editingComment, editBody, editingBusy, onToggleReplies, onReply, onReact, onBeginEdit, onChangeEditBody, onSubmitEdit, onCancelEdit, onDelete, onReport, depth = 0 }: {
  comment: PulseComment;
  currentUserId: number;
  expanded: boolean;
  editingComment: PulseComment | null;
  editBody: string;
  editingBusy: boolean;
  onToggleReplies: () => void;
  onReply: (comment: PulseComment) => void;
  onReact: (comment: PulseComment) => void;
  onBeginEdit: (comment: PulseComment) => void;
  onChangeEditBody: (value: string) => void;
  onSubmitEdit: () => void;
  onCancelEdit: () => void;
  onDelete: (comment: PulseComment) => void;
  onReport: (comment: PulseComment) => void;
  depth?: number;
}) {
  const owned = Boolean(comment.can_edit || Number(comment.user_id || comment.author?.user_id || 0) === currentUserId);
  const deletable = Boolean(comment.can_delete || owned);
  const replies = comment.replies || [];
  const editing = editingComment?.id === comment.id;
  return (
    <View style={[styles.comment, depth > 0 && styles.replyComment]}>
      <Text style={styles.commentAuthor}>{comment.author?.display_name || comment.author?.username || "PulseSoc"}</Text>
      {editing ? (
        <View style={styles.editComposer}>
          <TextInput accessibilityLabel="Edit comment" style={styles.editInput} value={editBody} onChangeText={onChangeEditBody} multiline />
          <View style={styles.commentActions}><Pressable accessibilityRole="button" disabled={!editBody.trim() || editingBusy} onPress={onSubmitEdit}><Text style={styles.commentAction}>{editingBusy ? "Saving" : "Save edit"}</Text></Pressable><Pressable accessibilityRole="button" onPress={onCancelEdit}><Text style={styles.commentAction}>Cancel</Text></Pressable></View>
        </View>
      ) : (
        <Text style={styles.commentBody}>{comment.body}{comment.edited_at ? <Text style={styles.editedLabel}> · edited</Text> : null}</Text>
      )}
      <View style={styles.commentActions}>
        <Text style={styles.commentTime}>{formatShortTime(comment.created_at)}</Text>
        <Pressable accessibilityRole="button" onPress={() => onReply(comment)}><Text style={styles.commentAction}>Reply</Text></Pressable>
        <Pressable accessibilityRole="button" accessibilityState={{ selected: Boolean(comment.viewer_reaction) }} onPress={() => onReact(comment)}><Text style={styles.commentAction}>{comment.viewer_reaction ? "Liked" : "Like"}{comment.like_count ? ` ${comment.like_count}` : ""}</Text></Pressable>
        {owned ? <Pressable accessibilityRole="button" onPress={() => onBeginEdit(comment)}><Text style={styles.commentAction}>Edit</Text></Pressable> : null}
        {deletable ? <Pressable accessibilityRole="button" accessibilityLabel="Delete comment" onPress={() => onDelete(comment)}><Text style={styles.commentDanger}>Delete</Text></Pressable> : <Pressable accessibilityRole="button" accessibilityLabel="Report comment" onPress={() => onReport(comment)}><Text style={styles.commentAction}>Report</Text></Pressable>}
      </View>
      {replies.length ? <Pressable accessibilityRole="button" accessibilityState={{ expanded }} onPress={onToggleReplies}><Text style={styles.replyToggle}>{expanded ? "Hide replies" : `View ${replies.length} repl${replies.length === 1 ? "y" : "ies"}`}</Text></Pressable> : null}
      {expanded ? replies.map((reply) => <CommentThread key={reply.id} comment={reply} currentUserId={currentUserId} expanded onToggleReplies={() => undefined} editingComment={editingComment} editBody={editBody} editingBusy={editingBusy} onReply={onReply} onReact={onReact} onBeginEdit={onBeginEdit} onChangeEditBody={onChangeEditBody} onSubmitEdit={onSubmitEdit} onCancelEdit={onCancelEdit} onDelete={onDelete} onReport={onReport} depth={depth + 1} />) : null}
    </View>
  );
}

const REACTIONS = [{ key: "like", icon: "♥", label: "Like" }, { key: "love", icon: "💚", label: "Love" }, { key: "fire", icon: "🔥", label: "Fire" }, { key: "funny", icon: "☺", label: "Funny" }, { key: "wow", icon: "✦", label: "Wow" }, { key: "smart", icon: "◇", label: "Smart" }];

function ReactionPicker({ reel, onSelect, onClose }: { reel: PulseReel | null; onSelect: (reaction: string) => void; onClose: () => void }) {
  return <Modal visible={Boolean(reel)} transparent animationType="fade" onRequestClose={onClose}><Pressable style={styles.pickerBackdrop} onPress={onClose}><View style={styles.reactionPicker}>{REACTIONS.map((reaction) => <Pressable key={reaction.key} accessibilityRole="button" accessibilityLabel={`${reaction.label} reaction`} accessibilityState={{ selected: reel?.viewer_reaction === reaction.key }} style={({ pressed }) => [styles.reactionChoice, reel?.viewer_reaction === reaction.key && styles.reactionChoiceActive, pressed && styles.reactionPressed]} onPress={() => onSelect(reaction.key)}><Text style={styles.reactionEmoji}>{reaction.icon}</Text><Text style={styles.reactionLabel}>{reaction.label}</Text></Pressable>)}</View></Pressable></Modal>;
}

function MusicDetail({ reel, onClose }: { reel: PulseReel | null; onClose: () => void }) {
  const audio = reel?.audio;
  return <Modal visible={Boolean(reel)} transparent animationType="slide" onRequestClose={onClose}><Pressable style={styles.modalWrap} onPress={onClose}><Pressable style={styles.compactSheet} onPress={(event) => event.stopPropagation()}><View style={styles.sheetHandle} /><Text style={styles.musicGlyph}>♪</Text><Text style={styles.sheetTitle}>{audio?.title || "Original audio"}</Text><Text style={styles.sheetContext}>{audio?.artist || "PulseSoc creator audio"}</Text><Text style={styles.boundaryText}>PulseSoc keeps this sound synced to the Reel while the video remains the focus.</Text><Pressable style={styles.sheetPrimary} onPress={onClose}><Text style={styles.sheetPrimaryText}>Done</Text></Pressable></Pressable></Pressable></Modal>;
}

function ReelMoreMenu({ reel, onClose, onRepost, onLess, onReport, onPromote }: { reel: PulseReel | null; onClose: () => void; onRepost: (reel: PulseReel) => void; onLess: (reel: PulseReel) => void; onReport: (reel: PulseReel) => void; onPromote: (reel: PulseReel) => void }) {
  if (!reel) return null;
  const live = Boolean(reel.live_session_id || reel.live?.live_session_id);
  return <Modal visible transparent animationType="slide" onRequestClose={onClose}><Pressable style={styles.modalWrap} onPress={onClose}><Pressable style={styles.compactSheet} onPress={(event) => event.stopPropagation()}><View style={styles.sheetHandle} /><Text style={styles.sheetTitle}>{live ? "Live options" : "Reel options"}</Text>{live ? <Text style={styles.boundaryText}>Join Live to use the existing Live viewer, chat, moderation, and co-host controls.</Text> : <><MenuAction label={reel.reposted ? "Reposted" : "Repost"} onPress={() => onRepost(reel)} />{reel.can_manage ? <MenuAction label="Promote Reel" onPress={() => onPromote(reel)} /> : null}<MenuAction label="Not interested" onPress={() => onLess(reel)} /><MenuAction label="Report Reel" danger onPress={() => onReport(reel)} /></>}<MenuAction label="Cancel" onPress={onClose} /></Pressable></Pressable></Modal>;
}

function MenuAction({ label, danger, onPress }: { label: string; danger?: boolean; onPress: () => void }) { return <Pressable accessibilityRole="button" style={styles.menuAction} onPress={onPress}><Text style={[styles.menuActionText, danger && styles.menuDanger]}>{label}</Text><Text style={styles.menuChevron}>›</Text></Pressable>; }

function mergeReels(current: PulseReel[], incoming: PulseReel[]) {
  const seen = new Set(current.map((item) => item.id));
  return [...current, ...incoming.filter((item) => !seen.has(item.id))];
}

function focusInitialReel(reels: PulseReel[], reelId: number) {
  if (!reelId) return reels;
  const index = reels.findIndex((item) => item.id === reelId);
  if (index <= 0) return reels;
  return [reels[index], ...reels.slice(0, index), ...reels.slice(index + 1)];
}

function findComment(comments: PulseComment[], commentId: number): PulseComment | null {
  for (const comment of comments) {
    if (comment.id === commentId) return comment;
    const nested = findComment(comment.replies || [], commentId);
    if (nested) return nested;
  }
  return null;
}

function insertReply(comments: PulseComment[], parentId: number, reply: PulseComment): PulseComment[] {
  return comments.map((comment) => comment.id === parentId
    ? { ...comment, replies: [...(comment.replies || []), reply], reply_count: Number(comment.reply_count || comment.replies?.length || 0) + 1 }
    : { ...comment, replies: insertReply(comment.replies || [], parentId, reply) });
}

function updateCommentTree(comments: PulseComment[], commentId: number, next: PulseComment): PulseComment[] {
  return comments.map((comment) => comment.id === commentId ? { ...comment, ...next } : { ...comment, replies: updateCommentTree(comment.replies || [], commentId, next) });
}

function removeCommentFromTree(comments: PulseComment[], commentId: number): PulseComment[] {
  return comments.filter((comment) => comment.id !== commentId).map((comment) => ({ ...comment, replies: removeCommentFromTree(comment.replies || [], commentId) }));
}

function toggleSetValue(values: Set<number>, value: number) {
  const next = new Set(values);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

function classifyConnectionState(error: unknown): ConnectionState {
  if (!(error instanceof PulseApiError)) return "offline";
  if (error.status === 401 || error.code === "session_expired") return "auth_expired";
  if (error.status === 403) return "account_restricted";
  if (error.status === 429) return "rate_limited";
  if (error.status === 503 || error.code === "request_unreachable") return "offline";
  if (error.status === 502 || error.status === 504) return "server_busy";
  if (error.status >= 500) return "server_busy";
  return "offline";
}

function logReelsFailure(error: unknown, context: { lane: ReelLane; cacheCount: number; retryCount: number }) {
  const apiError = error instanceof PulseApiError ? error : null;
  console.warn("PULSESOC_REELS_RECOVERY", {
    endpoint: "/api/pulse/reels/feed",
    status: apiError?.status || 0,
    code: apiError?.code || "unknown",
    lane: context.lane,
    cache_state: context.cacheCount ? "available" : "empty",
    retry_count: context.retryCount,
    platform: Platform.OS
  });
}

function cacheAge(cachedAt: number) {
  if (!cachedAt) return "reconnecting";
  const minutes = Math.max(0, Math.floor((Date.now() - cachedAt) / 60_000));
  return minutes < 1 ? "updated just now" : `updated ${minutes}m ago`;
}

function GalaxyField() {
  const pulse = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (reduceMotion) {
      pulse.setValue(0.35);
      return;
    }
    const animation = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 1, duration: 2_800, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 0, duration: 2_800, useNativeDriver: true })
    ]));
    animation.start();
    return () => animation.stop();
  }, [pulse, reduceMotion]);

  return (
    <View pointerEvents="none" style={styles.galaxy} accessibilityElementsHidden>
      <Animated.View style={[styles.nebula, styles.nebulaOne, { opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.22, 0.5] }), transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.08] }) }] }]} />
      <Animated.View style={[styles.nebula, styles.nebulaTwo, { opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.34, 0.14] }) }]} />
      {GALAXY_STARS.map((star, index) => <View key={index} style={[styles.star, { left: star.left, top: star.top, width: star.size, height: star.size, opacity: star.opacity }]} />)}
    </View>
  );
}

const GALAXY_STARS = [
  { left: "8%", top: "18%", size: 2, opacity: 0.7 }, { left: "21%", top: "34%", size: 3, opacity: 0.4 },
  { left: "78%", top: "23%", size: 2, opacity: 0.8 }, { left: "88%", top: "46%", size: 3, opacity: 0.35 },
  { left: "16%", top: "67%", size: 2, opacity: 0.5 }, { left: "72%", top: "76%", size: 2, opacity: 0.65 },
  { left: "43%", top: "12%", size: 2, opacity: 0.45 }, { left: "54%", top: "58%", size: 3, opacity: 0.3 }
] as const;

const RECOVERY_COPY: Record<Exclude<ConnectionState, "ready" | "cached">, { icon: string; title: string; body: string; action: string }> = {
  loading: { icon: "◎", title: "Your Galaxy is loading", body: "Finding the newest signals from across PulseSoc.", action: "Loading" },
  connecting: { icon: "◌", title: "Connecting to Pulse Network", body: "Refreshing your saved Reels quietly.", action: "Refresh now" },
  offline: { icon: "◇", title: "You're offline", body: "We'll reconnect automatically. Saved Reels appear whenever available.", action: "Try again" },
  server_busy: { icon: "◉", title: "Pulse Network is catching up", body: "Your Galaxy is still here. We'll retry in the background.", action: "Retry now" },
  maintenance: { icon: "✦", title: "Galaxy tune-up in progress", body: "Reels will return as soon as the network is ready.", action: "Check again" },
  rate_limited: { icon: "◷", title: "Taking a short orbit", body: "Reels will reconnect automatically in a moment.", action: "Try again" },
  auth_expired: { icon: "⌁", title: "Reconnect your account", body: "Your secure session ended. Sign in to restore this Reel and your existing PulseSoc account.", action: "Sign In" },
  account_restricted: { icon: "!", title: "Reels access is limited", body: "Your account is signed in, but Reels access is currently restricted. Review Account Health for details.", action: "Check again" },
  empty: { icon: "✧", title: "Welcome to Reels", body: "Discover creators from across the Pulse Galaxy.", action: "Explore Trending" }
};

function ReelsRecovery({ state, loading, onRetry, onAuthenticate, onExplore }: { state: ConnectionState; loading: boolean; onRetry: () => void; onAuthenticate: () => void; onExplore: (lane: ReelLane) => void }) {
  const visibleState = state === "ready" || state === "cached" ? "loading" : state;
  const copy = RECOVERY_COPY[visibleState];
  return (
    <View style={styles.recoveryWrap} accessibilityRole="summary" accessibilityLiveRegion="polite">
      <View style={styles.skeletonCard}>
        <View style={styles.skeletonProfile} />
        <View style={styles.skeletonLines}><View style={[styles.skeletonLine, { width: "58%" }]} /><View style={[styles.skeletonLine, { width: "82%" }]} /></View>
        <View style={styles.skeletonRail}><View style={styles.skeletonAction} /><View style={styles.skeletonAction} /><View style={styles.skeletonAction} /></View>
      </View>
      <View style={styles.recoveryCard}>
        <Text style={styles.recoveryIcon}>{copy.icon}</Text>
        <ActivityIndicator animating={loading || ["loading", "connecting", "offline", "server_busy", "rate_limited"].includes(visibleState)} color={colors.accent} />
        <Text style={styles.recoveryTitle}>{copy.title}</Text>
        <Text style={styles.recoveryBody}>{copy.body}</Text>
        {visibleState === "empty" ? (
          <View style={styles.recoveryActions}><Pressable accessibilityRole="button" style={styles.recoveryButton} onPress={() => onExplore("trending")}><Text style={styles.recoveryButtonText}>Explore Trending</Text></Pressable><Pressable accessibilityRole="button" style={styles.recoverySecondary} onPress={() => onExplore("music")}><Text style={styles.recoverySecondaryText}>Music</Text></Pressable></View>
        ) : visibleState === "auth_expired" ? (
          <Pressable accessibilityRole="button" accessibilityLabel="Sign In" style={styles.recoveryButton} onPress={onAuthenticate}><Text style={styles.recoveryButtonText}>Sign In</Text></Pressable>
        ) : (
          <Pressable accessibilityRole="button" accessibilityLabel={copy.action} style={styles.recoveryButton} onPress={onRetry}><Text style={styles.recoveryButtonText}>{copy.action}</Text></Pressable>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
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
  closeText: {
    color: colors.accent,
    fontWeight: "900"
  },
  commentAction: { color: colors.accent, fontSize: 11, fontWeight: "800" },
  commentActions: { alignItems: "center", flexDirection: "row", gap: 14, marginTop: 6 },
  comment: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    paddingVertical: 10
  },
  commentAuthor: {
    color: colors.text,
    fontWeight: "900"
  },
  commentBody: {
    color: colors.text,
    lineHeight: 20,
    marginTop: 4
  },
  commentDanger: { color: colors.danger, fontSize: 11, fontWeight: "800" },
  commentError: { color: colors.warning, fontSize: 11, lineHeight: 16, paddingBottom: 6 },
  commentTime: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 5
  },
  editComposer: { gap: 6, marginTop: 6 },
  editInput: { backgroundColor: colors.surfaceRaised, borderColor: colors.accent, borderRadius: 8, borderWidth: 1, color: colors.text, minHeight: 42, paddingHorizontal: 10, paddingVertical: 8 },
  editedLabel: { color: colors.muted, fontSize: 10 },
  composer: {
    alignItems: "center",
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    paddingTop: 10
  },
  compactSheet: { backgroundColor: "#06111f", borderColor: "rgba(97,234,246,0.25)", borderRadius: 24, borderWidth: 1, gap: 8, padding: 18, width: "100%" },
  createButton: { alignItems: "center", backgroundColor: "rgba(5,13,26,0.56)", borderColor: "rgba(97,234,246,0.28)", borderRadius: 18, borderWidth: 1, height: 40, justifyContent: "center", position: "absolute", right: 10, top: 12, width: 40, zIndex: 30 },
  createText: { color: colors.text, fontSize: 22 },
  disabledText: {
    color: colors.warning,
    paddingTop: 10
  },
  empty: {
    color: colors.muted,
    padding: 20,
    textAlign: "center"
  },
  galaxy: { ...StyleSheet.absoluteFillObject, backgroundColor: "#02050b", overflow: "hidden" },
  nebula: { borderRadius: 999, position: "absolute" },
  nebulaOne: { backgroundColor: "rgba(36,218,193,0.22)", height: 330, right: -155, top: 120, width: 330 },
  nebulaTwo: { backgroundColor: "rgba(93,78,204,0.24)", bottom: 80, height: 390, left: -220, width: 390 },
  star: { backgroundColor: "#9df9ef", borderRadius: 4, position: "absolute" },
  footer: {
    padding: 20
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    minHeight: 42,
    paddingHorizontal: 12
  },
  loadMoreComments: { alignItems: "center", minHeight: 44, justifyContent: "center", paddingVertical: 8 },
  laneButton: { borderRadius: 16, minHeight: 32, justifyContent: "center", paddingHorizontal: 11 },
  laneButtonActive: { backgroundColor: "rgba(47,225,180,0.18)", borderColor: "rgba(97,234,246,0.36)", borderWidth: 1 },
  laneRail: { backgroundColor: "rgba(2,8,18,0.54)", borderColor: "rgba(255,255,255,0.10)", borderRadius: 20, borderWidth: 1, height: 40, left: 10, position: "absolute", right: 58, zIndex: 30 },
  laneRailContent: { alignItems: "center", gap: 1, paddingHorizontal: 3 },
  laneText: { color: "rgba(244,247,251,0.70)", fontSize: 10, fontWeight: "800" },
  laneTextActive: { color: colors.accent },
  menuAction: { alignItems: "center", borderTopColor: "rgba(255,255,255,0.08)", borderTopWidth: StyleSheet.hairlineWidth, flexDirection: "row", minHeight: 52 },
  menuActionText: { color: colors.text, flex: 1, fontSize: 14, fontWeight: "800" },
  menuChevron: { color: colors.muted, fontSize: 20 },
  menuDanger: { color: colors.danger },
  modalBackdrop: {
    ...StyleSheet.absoluteFillObject
  },
  modalWrap: {
    backgroundColor: "rgba(0,0,0,0.42)",
    flex: 1,
    justifyContent: "flex-end",
    padding: 10
  },
  musicGlyph: { color: colors.accent, fontSize: 28 },
  boundaryText: { color: colors.muted, fontSize: 11, lineHeight: 16, marginVertical: 4 },
  pickerBackdrop: { alignItems: "center", backgroundColor: "rgba(0,0,0,0.46)", flex: 1, justifyContent: "center", padding: 14 },
  reactionChoice: { alignItems: "center", borderColor: "transparent", borderRadius: 16, borderWidth: 1, minHeight: 68, justifyContent: "center", width: 52 },
  reactionChoiceActive: { backgroundColor: "rgba(47,225,180,0.16)", borderColor: colors.accent },
  reactionEmoji: { color: colors.text, fontSize: 25 },
  reactionLabel: { color: colors.muted, fontSize: 8, fontWeight: "700", marginTop: 4 },
  reactionPicker: { backgroundColor: "rgba(5,13,27,0.96)", borderColor: "rgba(97,234,246,0.26)", borderRadius: 23, borderWidth: 1, flexDirection: "row", gap: 2, padding: 8 },
  reactionPressed: { transform: [{ scale: 0.88 }] },
  replyBanner: { alignItems: "center", backgroundColor: "rgba(97,234,246,0.08)", borderRadius: 10, flexDirection: "row", justifyContent: "space-between", marginBottom: 6, padding: 8 },
  replyText: { color: colors.muted, flex: 1, fontSize: 11 },
  page: {
    backgroundColor: "transparent",
    width: "100%"
  },
  recoveryActions: { flexDirection: "row", gap: 8, marginTop: 6 },
  recoveryBody: { color: colors.muted, fontSize: 13, lineHeight: 19, maxWidth: 280, textAlign: "center" },
  recoveryButton: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 16, justifyContent: "center", marginTop: 8, minHeight: 44, paddingHorizontal: 20 },
  recoveryButtonText: { color: colors.background, fontSize: 13, fontWeight: "900" },
  recoveryCard: { alignItems: "center", backgroundColor: "rgba(5,17,31,0.90)", borderColor: "rgba(97,234,246,0.30)", borderRadius: 24, borderWidth: 1, gap: 8, marginHorizontal: 24, padding: 22 },
  recoveryIcon: { color: colors.accentStrong, fontSize: 30 },
  recoverySecondary: { alignItems: "center", borderColor: colors.border, borderRadius: 16, borderWidth: 1, justifyContent: "center", marginTop: 8, minHeight: 44, paddingHorizontal: 18 },
  recoverySecondaryText: { color: colors.text, fontSize: 13, fontWeight: "800" },
  recoveryTitle: { color: colors.text, fontSize: 20, fontWeight: "900", textAlign: "center" },
  recoveryWrap: { flex: 1, justifyContent: "center", minHeight: Dimensions.get("window").height - 120, paddingBottom: 60, paddingTop: 92 },
  replyComment: { borderLeftColor: "rgba(97,234,246,0.24)", borderLeftWidth: 2, marginLeft: 18, paddingLeft: 10 },
  replyToggle: { color: colors.accent, fontSize: 11, fontWeight: "800", marginTop: 8 },
  root: {
    backgroundColor: "#02050b",
    flex: 1
  },
  skeletonAction: { backgroundColor: "rgba(97,234,246,0.12)", borderRadius: 16, height: 32, width: 32 },
  skeletonCard: { backgroundColor: "rgba(10,24,39,0.44)", borderColor: "rgba(97,234,246,0.15)", borderRadius: 20, borderWidth: 1, height: 164, marginBottom: 14, marginHorizontal: 34, padding: 16 },
  skeletonLine: { backgroundColor: "rgba(180,211,223,0.13)", borderRadius: 5, height: 9, marginBottom: 8 },
  skeletonLines: { marginLeft: 52, marginTop: -38 },
  skeletonProfile: { backgroundColor: "rgba(50,230,179,0.16)", borderColor: "rgba(97,234,246,0.22)", borderRadius: 20, borderWidth: 1, height: 40, width: 40 },
  skeletonRail: { bottom: 14, gap: 10, position: "absolute", right: 14 },
  sendButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  sendDisabled: {
    opacity: 0.48
  },
  sendText: {
    color: colors.background,
    fontWeight: "900"
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    maxHeight: "72%",
    minHeight: "48%",
    padding: 16
  },
  sheetContext: { color: colors.muted, fontSize: 11, marginTop: 2 },
  sheetHandle: { alignSelf: "center", backgroundColor: colors.border, borderRadius: 3, height: 4, marginBottom: 8, width: 42 },
  sheetHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 8
  },
  sheetTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  sheetPrimary: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 14, minHeight: 44, justifyContent: "center", marginTop: 8 },
  sheetPrimaryText: { color: colors.background, fontWeight: "900" },
  statusPill: {
    backgroundColor: "rgba(5,17,31,0.90)",
    borderColor: "rgba(97,234,246,0.26)",
    borderRadius: 14,
    borderWidth: 1,
    left: 12,
    paddingHorizontal: 10,
    paddingVertical: 6,
    position: "absolute",
    top: 8,
    zIndex: 20
  },
  statusPillText: { color: colors.warning, fontSize: 11, fontWeight: "800" }
});
