import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
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
  followReelCreator,
  listReelComments,
  listReels,
  loadCachedReels,
  markReelNotInterested,
  PulseReel,
  reactToReel,
  reactToReelComment,
  reelWebUrl,
  reportReel,
  repostReel,
  saveReel,
  shareReel,
  trackReelView
} from "../api/reels";
import { ReelPlayerCard } from "../components/ReelPlayerCard";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = NativeStackScreenProps<RootStackParamList, "Reels"> | NativeStackScreenProps<RootStackParamList, "ReelDetail">;

const PAGE_SIZE = 8;
const QA_REELS_STATE = PULSESOC_QA_REELS_FIXTURES ? String(process.env.EXPO_PUBLIC_PULSESOC_QA_REELS_STATE || "").trim().toLowerCase() : "";
type ReelLane = "for_you" | "following" | "trending" | "music" | "live";
const REEL_LANES: Array<{ key: ReelLane; label: string }> = [{ key: "for_you", label: "For You" }, { key: "following", label: "Following" }, { key: "trending", label: "Trending" }, { key: "music", label: "Music" }, { key: "live", label: "Live" }];

export function ReelsScreen({ route, navigation }: Props) {
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
  const [muted, setMuted] = useState(true);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [commentReel, setCommentReel] = useState<PulseReel | null>(null);
  const [comments, setComments] = useState<PulseComment[]>([]);
  const [commentBody, setCommentBody] = useState("");
  const [postingComment, setPostingComment] = useState(false);
  const [replyTo, setReplyTo] = useState<PulseComment | null>(null);
  const [reactionReel, setReactionReel] = useState<PulseReel | null>(null);
  const [musicReel, setMusicReel] = useState<PulseReel | null>(null);
  const [moreReel, setMoreReel] = useState<PulseReel | null>(null);
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  const [shareOpen, setShareOpen] = useState(false);
  const [viewportHeight, setViewportHeight] = useState(Dimensions.get("window").height);
  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 72 });
  const qaStateApplied = useRef(false);

  async function load(mode: "initial" | "refresh" | "more" = "initial") {
    if (mode === "more" && (!hasMore || loadingMore)) return;
    const nextOffset = mode === "more" ? offset : 0;
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    if (mode === "more") setLoadingMore(true);
    try {
      const data = await listReels({ lane, limit: PAGE_SIZE, offset: nextOffset, includeComments: false });
      const next = mode === "more" ? mergeReels(reels, data.reels || []) : focusInitialReel(data.reels || [], initialReelId);
      setReels(next);
      setOffset(Number(data.next_offset || nextOffset + (data.reels?.length || 0)));
      setHasMore(Boolean(data.has_more));
      if (initialReelId && mode !== "more") {
        const index = next.findIndex((item) => item.id === initialReelId);
        if (index >= 0) setActiveIndex(index);
      }
    } catch (loadError) {
      const cached = await loadCachedReels(lane);
      if (cached.length && mode !== "more") {
        setReels(focusInitialReel(cached, initialReelId));
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Reels could not load.");
      }
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
    if (typeof next === "number") setActiveIndex(next);
  });

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
    setCommentBody("");
    setReplyTo(null);
    setComments(reel.preview_comments || []);
    try {
      setComments(await listReelComments(reel.id));
    } catch {
      setComments(reel.preview_comments || []);
    }
  }

  async function submitComment() {
    if (!commentReel || !commentBody.trim() || postingComment) return;
    const body = commentBody.trim();
    setPostingComment(true);
    setCommentBody("");
    try {
      const comment = await addReelComment(commentReel.id, body, replyTo?.id || 0);
      setComments((current) => [comment, ...current]);
      updateReel(commentReel.id, { comments_count: Number(commentReel.comments_count || 0) + 1 });
    } catch {
      setCommentBody(body);
    } finally {
      setPostingComment(false);
      setReplyTo(null);
    }
  }

  function joinLiveReel(reel: PulseReel) {
    const liveId = Number(reel.live_session_id || reel.live?.live_session_id || 0);
    if (liveId) navigation.navigate("LiveDetail", { liveId, title: reel.title || "PulseSoc Live" });
  }

  if (loading && !reels.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Reels</Text>
      </View>
    );
  }

  return (
    <View style={styles.root} onLayout={(event) => setViewportHeight(event.nativeEvent.layout.height)}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.laneRailContent} style={[styles.laneRail, { top: insets.top + 6 }]} accessibilityRole="tablist">
        {REEL_LANES.map((item) => <Pressable key={item.key} accessibilityRole="tab" accessibilityState={{ selected: lane === item.key }} style={[styles.laneButton, lane === item.key && styles.laneButtonActive]} onPress={() => setLane(item.key)}><Text style={[styles.laneText, lane === item.key && styles.laneTextActive]}>{item.label}</Text></Pressable>)}
      </ScrollView>
      <Pressable accessibilityRole="button" accessibilityLabel="Create Reel" style={[styles.createButton, { top: insets.top + 6 }]} onPress={() => navigation.navigate("CameraStudio", { target: "reel", mode: "reel", title: "Create Reel" })}><Text style={styles.createText}>＋</Text></Pressable>
      {offline ? <Text style={[styles.statusPill, { top: insets.top + 52 }]}>Saved Reels · reconnecting</Text> : null}
      {error ? <Text style={[styles.errorPill, { top: insets.top + 52 }]}>{error}</Text> : null}
      <FlatList
        data={reels}
        keyExtractor={(item) => String(item.id)}
        renderItem={({ item, index }) => (
          <View style={[styles.page, { height: viewportHeight }]}>
            <ReelPlayerCard
              reel={item}
              active={index === activeIndex && appActive && !shareOpen && !commentReel && !reactionReel && !musicReel && !moreReel}
              muted={muted}
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
                const key = reel.author?.public_player_id || reel.author?.username || "";
                if (key) navigation.navigate("ProfileDetail", { profileKey: key, title: reel.author?.display_name || "Profile" });
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
        ListEmptyComponent={<Text style={styles.empty}>{error || "No Reels loaded yet."}</Text>}
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
        body={commentBody}
        posting={postingComment}
        onChangeBody={setCommentBody}
        onSubmit={submitComment}
        replyTo={replyTo}
        onReply={setReplyTo}
        onReact={(comment) => reactToReelComment(comment.id).catch(() => undefined)}
        onCancelReply={() => setReplyTo(null)}
        onClose={() => { setCommentReel(null); setReplyTo(null); }}
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
  body,
  posting,
  onChangeBody,
  onSubmit,
  replyTo,
  onReply,
  onReact,
  onCancelReply,
  onClose
}: {
  visible: boolean;
  reel: PulseReel | null;
  comments: PulseComment[];
  body: string;
  posting: boolean;
  onChangeBody: (value: string) => void;
  onSubmit: () => void;
  replyTo: PulseComment | null;
  onReply: (comment: PulseComment) => void;
  onReact: (comment: PulseComment) => void;
  onCancelReply: () => void;
  onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.modalWrap} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <Pressable style={styles.modalBackdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHeader}>
            <View><View style={styles.sheetHandle} /><Text style={styles.sheetTitle}>{reel?.comments_count || comments.length} Comments</Text><Text style={styles.sheetContext} numberOfLines={1}>{reel?.author?.display_name || "PulseSoc creator"} · {reel?.title || "Reel"}</Text></View>
            <Pressable onPress={onClose}><Text style={styles.closeText}>Close</Text></Pressable>
          </View>
          <FlatList
            data={comments}
            keyExtractor={(item, index) => `${item.id}-${index}`}
            ListEmptyComponent={<Text style={styles.empty}>No comments yet.</Text>}
            renderItem={({ item }) => (
              <View style={styles.comment}>
                <Text style={styles.commentAuthor}>{item.author?.display_name || item.author?.username || "PulseSoc"}</Text>
                <Text style={styles.commentBody}>{item.body}</Text>
                <View style={styles.commentActions}><Text style={styles.commentTime}>{formatShortTime(item.created_at)}</Text><Pressable accessibilityRole="button" onPress={() => onReply(item)}><Text style={styles.commentAction}>Reply</Text></Pressable><Pressable accessibilityRole="button" onPress={() => onReact(item)}><Text style={styles.commentAction}>Like</Text></Pressable></View>
              </View>
            )}
          />
          {reel?.comments_disabled ? (
            <Text style={styles.disabledText}>Comments are disabled for this Reel.</Text>
          ) : (
            <View>
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
  commentTime: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 5
  },
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
  errorPill: {
    backgroundColor: "rgba(255,107,107,0.18)",
    borderRadius: 8,
    color: colors.danger,
    left: 12,
    padding: 8,
    position: "absolute",
    right: 12,
    top: 8,
    zIndex: 20
  },
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
    backgroundColor: "#02050b",
    width: "100%"
  },
  root: {
    backgroundColor: "#02050b",
    flex: 1
  },
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
    backgroundColor: "rgba(243,185,78,0.18)",
    borderRadius: 8,
    color: colors.warning,
    left: 12,
    padding: 8,
    position: "absolute",
    top: 8,
    zIndex: 20
  }
});
