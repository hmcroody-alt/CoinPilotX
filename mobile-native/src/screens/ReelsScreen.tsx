import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Dimensions,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
  ViewToken
} from "react-native";
import { PulseComment } from "../api/feed";
import {
  addReelComment,
  followReelCreator,
  listReelComments,
  listReels,
  loadCachedReels,
  markReelNotInterested,
  PulseReel,
  reactToReel,
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

export function ReelsScreen({ route, navigation }: Props) {
  const params = route.params || {};
  const initialReelId = "reelId" in params ? Number(params.reelId || 0) : 0;
  const [reels, setReels] = useState<PulseReel[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
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
  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 72 });
  const screenHeight = Dimensions.get("window").height;

  async function load(mode: "initial" | "refresh" | "more" = "initial") {
    if (mode === "more" && (!hasMore || loadingMore)) return;
    const nextOffset = mode === "more" ? offset : 0;
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    if (mode === "more") setLoadingMore(true);
    try {
      const data = await listReels({ limit: PAGE_SIZE, offset: nextOffset, includeComments: true });
      const next = mode === "more" ? mergeReels(reels, data.reels || []) : focusInitialReel(data.reels || [], initialReelId);
      setReels(next);
      setOffset(Number(data.next_offset || nextOffset + (data.reels?.length || 0)));
      setHasMore(Boolean(data.has_more));
      if (initialReelId && mode !== "more") {
        const index = next.findIndex((item) => item.id === initialReelId);
        if (index >= 0) setActiveIndex(index);
      }
    } catch (loadError) {
      const cached = await loadCachedReels();
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
    load("initial").catch(() => undefined);
  }, [initialReelId]);

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    const next = viewableItems[0]?.index;
    if (typeof next === "number") setActiveIndex(next);
  });

  const updateReel = useCallback((reelId: number, next: Partial<PulseReel>) => {
    setReels((current) => current.map((item) => (item.id === reelId ? { ...item, ...next } : item)));
  }, []);

  async function handleReact(reel: PulseReel, reactionType = "fire") {
    if (reel.reactions_disabled) return;
    setBusyId(reel.id);
    updateReel(reel.id, { viewer_reaction: reactionType, reactions_count: Number(reel.reactions_count || 0) + (reel.viewer_reaction ? 0 : 1) });
    try {
      const result = await reactToReel(reel.id, reactionType);
      updateReel(reel.id, {
        viewer_reaction: result.removed ? "" : result.reaction_type || reactionType,
        reaction_counts: result.reaction_counts || reel.reaction_counts,
        reactions_count: result.removed ? Math.max(0, Number(reel.reactions_count || 0) - 1) : Number(reel.reactions_count || 0) + 1
      });
    } catch {
      updateReel(reel.id, { viewer_reaction: reel.viewer_reaction, reactions_count: reel.reactions_count || 0 });
    } finally {
      setBusyId(null);
    }
  }

  async function handleSave(reel: PulseReel) {
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
    try {
      const result = await shareReel(reel.id);
      await Share.share({ message: result.share_url || reelWebUrl(reel.id) });
    } catch {
      await Share.share({ message: reelWebUrl(reel.id) }).catch(() => undefined);
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
    await followReelCreator(reel.id).catch(() => undefined);
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
      const comment = await addReelComment(commentReel.id, body);
      setComments((current) => [comment, ...current]);
      updateReel(commentReel.id, { comments_count: Number(commentReel.comments_count || 0) + 1 });
    } catch {
      setCommentBody(body);
    } finally {
      setPostingComment(false);
    }
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
    <View style={styles.root}>
      {offline ? <Text style={styles.statusPill}>Saved Reels</Text> : null}
      {error ? <Text style={styles.errorPill}>{error}</Text> : null}
      <FlatList
        data={reels}
        keyExtractor={(item) => String(item.id)}
        renderItem={({ item, index }) => (
          <View style={[styles.page, { height: screenHeight }]}>
            <ReelPlayerCard
              reel={item}
              active={index === activeIndex}
              muted={muted}
              busy={busyId === item.id}
              onToggleMuted={() => setMuted((current) => !current)}
              onReact={handleReact}
              onOpenComments={openComments}
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
              onViewable={(reel, watchMs) => trackReelView(reel.id, watchMs).catch(() => undefined)}
            />
          </View>
        )}
        pagingEnabled
        showsVerticalScrollIndicator={false}
        snapToInterval={screenHeight}
        decelerationRate="fast"
        viewabilityConfig={viewabilityConfig.current}
        onViewableItemsChanged={onViewableItemsChanged.current}
        onEndReached={() => load("more").catch(() => undefined)}
        onEndReachedThreshold={0.5}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListEmptyComponent={<Text style={styles.empty}>{error || "No Reels loaded yet."}</Text>}
        ListFooterComponent={loadingMore ? <ActivityIndicator style={styles.footer} color={colors.accent} /> : null}
        initialNumToRender={3}
        maxToRenderPerBatch={3}
        windowSize={5}
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
        onClose={() => setCommentReel(null)}
      />
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
  onClose
}: {
  visible: boolean;
  reel: PulseReel | null;
  comments: PulseComment[];
  body: string;
  posting: boolean;
  onChangeBody: (value: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.modalWrap} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <Pressable style={styles.modalBackdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>Comments</Text>
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
                <Text style={styles.commentTime}>{formatShortTime(item.created_at)}</Text>
              </View>
            )}
          />
          {reel?.comments_disabled ? (
            <Text style={styles.disabledText}>Comments are disabled for this Reel.</Text>
          ) : (
            <View style={styles.composer}>
              <TextInput
                style={styles.input}
                value={body}
                onChangeText={onChangeBody}
                placeholder="Add a comment"
                placeholderTextColor={colors.muted}
              />
              <Pressable style={[styles.sendButton, (!body.trim() || posting) && styles.sendDisabled]} disabled={!body.trim() || posting} onPress={onSubmit}>
                <Text style={styles.sendText}>{posting ? "Sending" : "Post"}</Text>
              </Pressable>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

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
  modalBackdrop: {
    ...StyleSheet.absoluteFillObject
  },
  modalWrap: {
    backgroundColor: "rgba(0,0,0,0.42)",
    flex: 1,
    justifyContent: "flex-end"
  },
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
