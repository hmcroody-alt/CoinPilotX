import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Animated, Image, Modal, PanResponder, Pressable, ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View } from "react-native";
import { Audio, ResizeMode, Video } from "expo-av";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@expo/vector-icons";
import { feedRenderableMedia, mediaDisplayUrl, mediaKind, PulseMedia, PulsePost, pulsePostUrl, savablePostId } from "../api/feed";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "./NativeMediaViewer";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { AttachedMusicPolicy, resolvePostAudioPolicy } from "../core/attachedMusicAudioPolicy";
import { configureReelsAudioSession } from "../core/reelsAudioSession";
import { canonicalMediaPlaybackUrl, refreshCanonicalMediaAccess } from "../media/mediaAccess";
import { useSavedState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { formatShortTime } from "../utils/format";
import { sharePulseObject } from "../sharing/nativeShare";
import { ContentTranslation } from "./ContentTranslation";
import { createThemedStyles } from "../theme/themedStyles";
import { EmbeddedLiveViewerSurface } from "./reels/ReelLiveViewerSurface";
import { EmojiPicker } from "../emoji";

const MEDIA_ASPECT_MIN = 0.55;
const MEDIA_ASPECT_MAX = 1.91;
const COLLAPSED_BODY_LINES = 4;

// Shared media layout contract for feed posts. Feed photo/video default to
// fullBleed so media reaches the true feed-viewport edges regardless of the
// per-screen list padding. Only media escapes the text content column.
export type PostMediaLayout = "fullBleed" | "inset";

// Derive the horizontal bleed from the measured card width and the actual
// window width instead of a per-screen magic inset. When fullBleed, the media
// container is pulled out symmetrically so it spans the full viewport width.
export function computeMediaBleedStyle(
  layout: PostMediaLayout,
  windowWidth: number,
  cardWidth: number
): { marginHorizontal: number; width?: number } {
  if (layout !== "fullBleed") return { marginHorizontal: 0 };
  if (!(windowWidth > 0) || !(cardWidth > 0)) return { marginHorizontal: 0 };
  const bleed = Math.max(0, (windowWidth - cardWidth) / 2);
  if (bleed <= 0) return { marginHorizontal: 0 };
  return { marginHorizontal: -bleed, width: windowWidth };
}

function sharePostFromCard(post: PulsePost) {
  const author = post.author || post.user || {};
  return sharePulseObject({
    kind: "post",
    url: pulsePostUrl(post.id),
    title: post.title || "PulseSoc post",
    description: post.body || post.text || post.content,
    author: author.display_name || author.name || author.username || post.author_name,
    previewImageUrl: post.thumbnail_url || post.image_url
  });
}

const VISIBILITY_ICON: Record<string, keyof typeof Ionicons.glyphMap> = {
  public: "globe-outline",
  followers: "people-outline",
  friends: "people-outline",
  private: "lock-closed-outline"
};

type PostCardProps = {
  post: PulsePost;
  detail?: boolean;
  busy?: boolean;
  active?: boolean;
  motionEnabled?: boolean;
  mediaLayout?: PostMediaLayout;
  onOpen?: (post: PulsePost) => void;
  onReact?: (post: PulsePost, reactionType: string) => void;
  onSave?: (post: PulsePost) => void;
  onRepost?: (post: PulsePost) => void;
  onPromote?: (post: PulsePost) => void;
  onShare?: (post: PulsePost) => void;
  onComment?: (post: PulsePost) => void;
  onSubmitComment?: (post: PulsePost, body: string) => Promise<void> | void;
  onFollow?: (post: PulsePost) => void;
  onReport?: (post: PulsePost) => void;
  onHide?: (post: PulsePost) => void;
  onBlock?: (post: PulsePost) => void;
  onMute?: (post: PulsePost) => void;
  onDelete?: (post: PulsePost) => void;
  onAuthorPress?: (post: PulsePost) => void;
  onOpenLive?: (post: PulsePost) => void;
};

/**
 * The card body. Exported below wrapped in `React.memo` — see `PostCard`.
 *
 * This component reads ~138 `styles.*` entries and mounts a media strip, a
 * translation subscriber and an emoji picker per row, so re-rendering it for
 * every feed-level state change (scroll viewability, a badge poll, a composer
 * keystroke) is the single most expensive thing the Home feed does. The memo is
 * only load-bearing because the callers pass stable callback identities; see
 * the `useCallback` block in `HomeScreen`.
 */
function PostCardBody({
  post,
  detail,
  busy,
  active = false,
  motionEnabled = true,
  mediaLayout = "fullBleed",
  onOpen,
  onReact,
  onSave,
  onRepost,
  onPromote,
  onShare,
  onComment,
  onSubmitComment,
  onFollow,
  onReport,
  onHide,
  onBlock,
  onMute,
  onDelete,
  onAuthorPress,
  onOpenLive
}: PostCardProps) {
  const { width: windowWidth } = useWindowDimensions();
  const [cardWidth, setCardWidth] = useState(0);
  const mediaBleedStyle = computeMediaBleedStyle(mediaLayout, windowWidth, cardWidth);
  const commentInputRef = useRef<TextInput>(null);
  const [commentBody, setCommentBody] = useState("");
  const [commentEmojiOpen, setCommentEmojiOpen] = useState(false);
  const [commentPosting, setCommentPosting] = useState(false);
  const [commentNotice, setCommentNotice] = useState("");
  const [commentComposerOpen, setCommentComposerOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // Swipe-to-dismiss lives on the grip alone. Claiming the responder across the
  // whole sheet would swallow taps that are on their way to an action row.
  const menuSwipe = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_event, gesture) => gesture.dy > 8,
        onPanResponderRelease: (_event, gesture) => {
          if (gesture.dy > 40) setMenuOpen(false);
        }
      }),
    []
  );
  const [reactionsOpen, setReactionsOpen] = useState(false);
  // Feed/Profile Live is a user-initiated authenticated surface, so receive
  // remote Agora audio by default. The viewer remains receive-only and can
  // still mute playback locally with the control below.
  const [liveMuted, setLiveMuted] = useState(false);
  const [bodyExpanded, setBodyExpanded] = useState(Boolean(detail));
  const [bodyTruncated, setBodyTruncated] = useState(false);
  // Lifted so a media load failure collapses the entire media region -- wrapper,
  // bleed margin and all -- not just the inner strip. A null MediaStrip left the
  // outer bleed View mounted, so the invariant "no drawable image => no reserved
  // media height" was still violated by the wrapper's margin. Keyed to post.id so
  // a recycled row never inherits a stale failure.
  const [mediaFailed, setMediaFailed] = useState(false);
  useEffect(() => {
    setMediaFailed(false);
  }, [post.id]);
  const likeScale = useRef(new Animated.Value(1)).current;
  const author = post.author || {};
  const displayName = author.display_name || author.name || post.author_name || "PulseSoc";
  const handle = author.username || author.handle || post.author_username || "";
  const body = post.body || "";
  const showReadMore = !detail && bodyTruncated;
  const visibilityKey = String(post.visibility || "public").toLowerCase();
  const visibilityIcon = VISIBILITY_ICON[visibilityKey] || "globe-outline";
  const visibilityLabel = visibilityKey.charAt(0).toUpperCase() + visibilityKey.slice(1);
  const commentCount = Number(post.comment_count || 0);
  const reactionTotal = Object.values(post.reaction_counts || {}).reduce((sum, count) => sum + Number(count || 0), 0);
  const creatorLabel = author.premium_verified || author.verified ? "Pulse Creator" : "";
  const automated = author.automated === true || author.account_type === "PULSESOC_AUTOMATED";
  const viewerLiked = Boolean(post.viewer_reaction);
  /**
   * A repost is a wrapper row around an original, and both can be on screen at
   * once. Saving is about the content, not the wrapper, so the card asks about
   * — and writes — the original's id. The server collapses to the same id, so
   * the two cards agree without either knowing about the other.
   */
  const savableId = savablePostId(post);
  const saveState = useSavedState("post", savableId, typeof (post.saved ?? post.is_saved) === "boolean" ? Boolean(post.saved ?? post.is_saved) : undefined);
  const viewerSaved = saveState.saved;
  const liveId = Number(post.live?.live_session_id || 0);
  const liveStatus = String(post.live?.status || "").toLowerCase();
  const isLivePost = String(post.content_type || post.post_type || "").toLowerCase() === "live" || liveId > 0;
  const isRealtimeLive = isLivePost && ["starting", "live", "broadcasting", "active", "connecting", "connected", "reconnecting"].includes(liveStatus);
  const isReplayProcessing = isLivePost && liveStatus === "processing";
  const isReplayUnavailable = isLivePost && ["unavailable", "failed"].includes(liveStatus);

  async function submitInlineComment() {
    const bodyText = commentBody.trim();
    if (!bodyText || commentPosting || !onSubmitComment) return;
    setCommentPosting(true);
    setCommentNotice("");
    try {
      await onSubmitComment(post, bodyText);
      setCommentBody("");
      setCommentNotice("Comment posted.");
    } catch (err) {
      setCommentNotice(err instanceof Error ? err.message : "Comment failed. Try again.");
    } finally {
      setCommentPosting(false);
    }
  }

  function toggleCommentComposer() {
    setCommentComposerOpen((open) => {
      const next = !open;
      if (next) requestAnimationFrame(() => commentInputRef.current?.focus());
      return next;
    });
  }

  function pulseReaction() {
    if (!motionEnabled) return;
    likeScale.setValue(0.55);
    Animated.spring(likeScale, { toValue: 1, useNativeDriver: true, friction: 3, tension: 140 }).start();
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
  }

  return (
    <Pressable
      testID={`home-feed-post-${post.id}`}
      style={({ pressed }) => [pressed && onOpen ? styles.cardPressed : undefined]}
      onPress={() => onOpen?.(post)}
      disabled={!onOpen}
    >
      <View style={styles.card} onLayout={(event) => setCardWidth(event.nativeEvent.layout.width)}>
      <View style={styles.cardInset}>
        <View style={styles.cardHeader}>
          <Pressable
            testID={`home-feed-author-${post.id}`}
            accessibilityRole="button"
            accessibilityLabel={`Open ${displayName} profile`}
            style={styles.authorRow}
            onPress={(event) => {
              event.stopPropagation();
              onAuthorPress?.(post);
            }}
            disabled={!onAuthorPress}
          >
            {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
            <View style={styles.authorText}>
              <View style={styles.authorNameRow}>
                <Text style={styles.authorName} numberOfLines={1}>
                  {displayName}
                </Text>
                {automated ? (
                  <View accessibilityLabel="Automated PulseSoc account" style={styles.automatedPill}>
                    <Text style={styles.automatedPillText}>AUTOMATED</Text>
                  </View>
                ) : null}
                {/*
                  Verification and Premium are different claims and must not
                  share the checkmark. This used to render `checkmark-circle`
                  for `verified || premium_verified`, so a paid subscription
                  looked identical to a verified identity. The checkmark is now
                  reserved for actual verification; Premium gets its own star.
                */}
                {author.verified ? (
                  <Ionicons name="checkmark-circle" size={15} color={colors.accent} style={styles.verifiedMark} />
                ) : author.premium_verified ? (
                  <Ionicons name="star" size={13} color={colors.accent} style={styles.verifiedMark} />
                ) : null}
              </View>
              <View style={styles.metaRow}>
                <Text style={styles.meta} numberOfLines={1}>
                  {handle ? `@${handle} · ` : ""}
                  {formatShortTime(post.created_at)} · {visibilityLabel}
                </Text>
                <Ionicons name={visibilityIcon} size={11} color={colors.muted} style={styles.metaVisibilityIcon} />
              </View>
            </View>
          </Pressable>
          <View style={styles.headerActions}>
            {onFollow && !automated ? (
              <Pressable
                testID={`home-feed-follow-${post.id}`}
                accessibilityRole="button"
                accessibilityLabel={`${post.viewer_follows_author ? "Following" : "Follow"} ${displayName}`}
                style={[styles.followPill, post.viewer_follows_author && styles.followPillActive]}
                disabled={busy}
                onPress={(event) => {
                  event.stopPropagation();
                  onFollow(post);
                }}
              >
                <Text style={[styles.followPillText, post.viewer_follows_author && styles.followPillTextActive]}>
                  {post.viewer_follows_author ? "Following" : "Follow"}
                </Text>
              </Pressable>
            ) : null}
            <Pressable
              testID={`home-feed-overflow-${post.id}`}
              accessibilityRole="button"
              accessibilityLabel={`Open post options for ${displayName}`}
              accessibilityState={{ expanded: menuOpen }}
              style={styles.overflowButton}
              onPress={(event) => {
                event.stopPropagation();
                setMenuOpen((open) => !open);
              }}
            >
              <Text style={styles.overflowText}>•••</Text>
            </Pressable>
          </View>
        </View>

      {creatorLabel ? (
        <View style={styles.badgeRow}>
          <View style={styles.creatorPill}><Text style={styles.creatorPillText}>✦ {creatorLabel}</Text></View>
        </View>
      ) : null}

      {isRealtimeLive ? (
        <View style={styles.liveMetaRow}>
          <View style={styles.liveBadge}><Text style={styles.liveBadgeText}>LIVE</Text></View>
          <Text style={styles.liveViewerCount}>{Number(post.live?.viewer_count || 0)} watching</Text>
        </View>
      ) : null}

      {post.title ? (
        <Text style={styles.title} numberOfLines={detail ? undefined : 2}>
          {post.title}
        </Text>
      ) : null}
      {body && !detail ? (
        <Text
          style={[styles.body, styles.bodyMeasure]}
          onTextLayout={(event) => setBodyTruncated(event.nativeEvent.lines.length > COLLAPSED_BODY_LINES)}
        >
          {body}
        </Text>
      ) : null}
      {body ? (
        <ContentTranslation
          contentType="post"
          contentRef={post.id}
          text={body}
          textStyle={styles.body}
          numberOfLines={detail || bodyExpanded ? undefined : COLLAPSED_BODY_LINES}
        />
      ) : null}
      {showReadMore ? (
        <Pressable accessibilityRole="button" accessibilityLabel={bodyExpanded ? "Collapse post" : "Read full post"} onPress={(event) => { event.stopPropagation(); setBodyExpanded((value) => !value); }}>
          <Text style={styles.readMore}>{bodyExpanded ? "Show less" : "Read more"}</Text>
        </Pressable>
      ) : null}
      </View>

      {isRealtimeLive && liveId > 0 ? (
        <View style={[styles.liveStage, mediaBleedStyle]}>
          <EmbeddedLiveViewerSurface
            liveId={liveId}
            hlsUrl={String(post.live?.playback_url || "")}
            active={active}
            muted={liveMuted}
            poster={post.live?.preview_url}
            identity={post.id}
            agoraPresentation="cover"
          />
          <View style={styles.liveStageActions} pointerEvents="box-none">
            <Pressable accessibilityRole="button" accessibilityLabel={liveMuted ? "Unmute Live" : "Mute Live"} style={styles.liveStageButton} onPress={(event) => { event.stopPropagation(); setLiveMuted((value) => !value); }}>
              <Text style={styles.liveStageButtonText}>{liveMuted ? "Unmute" : "Mute"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Open full Live" style={[styles.liveStageButton, styles.liveJoinButton]} onPress={(event) => { event.stopPropagation(); onOpenLive?.(post); }}>
              <Text style={styles.liveStageButtonText}>Join Live</Text>
            </Pressable>
          </View>
        </View>
      ) : isReplayProcessing ? (
        <View style={[styles.liveProcessing, mediaBleedStyle]}>
          {post.live?.preview_url ? <Image source={{ uri: post.live.preview_url }} style={StyleSheet.absoluteFill} blurRadius={8} /> : null}
          <View style={styles.liveProcessingShade} />
          <View style={styles.liveProcessingBadge}>
            <Ionicons name="time-outline" size={14} color={colors.accent} />
            <Text style={styles.liveProcessingBadgeText}>Background processing</Text>
          </View>
          <Text style={styles.liveProcessingTitle}>Live ended</Text>
          <Text style={styles.liveProcessingBody}>Replay is processing in the background. You can keep using PulseSoc.</Text>
        </View>
      ) : isReplayUnavailable ? (
        <View style={[styles.liveProcessing, mediaBleedStyle]}>
          {post.live?.preview_url ? <Image source={{ uri: post.live.preview_url }} style={StyleSheet.absoluteFill} blurRadius={8} /> : null}
          <View style={styles.liveProcessingShade} />
          <Text style={styles.liveProcessingTitle}>Live ended</Text>
          <Text style={styles.liveProcessingBody}>Replay unavailable</Text>
        </View>
      ) : null}

      {/* Renderable, not merely present. A post can carry an attached media row
          whose URL never materialized; that used to satisfy `post.media.length`
          and reserve a full-bleed 4:5 box around nothing. And once an image that
          did have a URL 404s, `mediaFailed` drops the whole wrapper so not even
          the bleed margin survives. */}
      {feedRenderableMedia(post.media).length && !mediaFailed ? (
        <View style={[styles.mediaBleed, mediaBleedStyle]}>
          <MediaStrip
            post={post}
            active={active}
            motionEnabled={motionEnabled}
            onReact={onReact}
            onMediaError={() => setMediaFailed(true)}
          />
        </View>
      ) : null}

      <View style={styles.cardInset}>
      <View style={styles.socialContextRow}>
        <Text style={styles.reactionSummary} accessibilityLabel={`${reactionTotal} reactions`}>{reactionSummary(post.reaction_counts || {})}</Text>
        <Text style={styles.socialContextText} numberOfLines={1}>
          {reactionTotal ? `${compactCount(reactionTotal)} reactions` : "Be the first to react"}
        </Text>
        {commentCount ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`View all ${commentCount} comments`}
            onPress={(event) => {
              event.stopPropagation();
              onComment?.(post);
            }}
          >
            <Text style={styles.viewCommentsText}>View all {commentCount} comments ›</Text>
          </Pressable>
        ) : null}
      </View>

      <View style={styles.actionRow}>
        <Pressable
          testID={`home-feed-like-${post.id}`}
          accessibilityRole="button"
          accessibilityLabel={`${viewerLiked ? "Liked" : "Like"} post ${post.id}`}
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
          disabled={busy}
          accessibilityState={{ selected: viewerLiked, busy: Boolean(busy) }}
          onLongPress={(event) => {
            event.stopPropagation();
            setReactionsOpen((open) => !open);
          }}
          onPress={(event) => {
            event.stopPropagation();
            pulseReaction();
            onReact?.(post, post.viewer_reaction || "love");
          }}
        >
          <Animated.Text style={[styles.actionIcon, viewerLiked && styles.actionIconActive, { transform: [{ scale: likeScale }] }]}>♥</Animated.Text>
          <Text style={[styles.actionText, viewerLiked && styles.actionTextActive]}>{reactionTotal ? compactCount(reactionTotal) : "Like"}</Text>
        </Pressable>
        {onComment ? (
          <Pressable
            testID={`home-feed-comment-${post.id}`}
            accessibilityRole="button"
            accessibilityLabel={`Comment on post ${post.id}`}
            style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
            disabled={busy}
            onPress={(event) => {
              event.stopPropagation();
              if (onSubmitComment) toggleCommentComposer();
              else onComment(post);
            }}
          >
            <Text style={[styles.actionIcon, commentComposerOpen && styles.actionIconActive]}>◯</Text>
            <Text style={[styles.actionText, commentComposerOpen && styles.actionTextActive]}>{commentCount ? compactCount(commentCount) : "Comment"}</Text>
          </Pressable>
        ) : null}
        <Pressable
          testID={`home-feed-repost-${post.id}`}
          accessibilityRole="button"
          accessibilityLabel={`${post.reposted ? "Reposted" : "Repost"} post ${post.id}`}
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
          disabled={busy}
          onPress={(event) => {
            event.stopPropagation();
            onRepost?.(post);
          }}
        >
          <Text style={[styles.actionIcon, post.reposted && styles.actionIconActive]}>↻</Text>
          <Text style={[styles.actionText, post.reposted && styles.actionTextActive]}>
            {post.repost_count ? compactCount(post.repost_count) : "Repost"}
          </Text>
        </Pressable>
        <Pressable
          testID={`home-feed-share-${post.id}`}
          accessibilityRole="button"
          accessibilityLabel={`Share post ${post.id}`}
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
          onPress={(event) => {
            event.stopPropagation();
            onShare ? onShare(post) : sharePostFromCard(post).catch(() => undefined);
          }}
        >
          <Text style={styles.actionIcon}>↗</Text>
          <Text style={styles.actionText}>{post.share_count ? compactCount(post.share_count) : "Share"}</Text>
        </Pressable>
        {/*
          Rendered unconditionally. It used to appear only when a screen passed
          `onSave`, which is why the same post offered Save in the feed and no
          Save at all in search results or an activity card — the control was a
          property of the screen rather than of the content. The prop is still
          honoured where a screen wants to observe the action, but the card can
          now perform the save itself, so a surface cannot omit it by omission.
        */}
        <Pressable
          testID={`home-feed-save-${post.id}`}
          accessibilityRole="button"
          accessibilityLabel={`${viewerSaved ? "Saved" : "Save"} post ${savableId}`}
          accessibilityHint={viewerSaved ? "Removes this post from your Saved collection" : "Adds this post to your Saved collection"}
          accessibilityState={{ selected: viewerSaved, busy: saveState.pending || Boolean(busy) }}
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
          disabled={saveState.pending || busy}
          onPress={(event) => {
            event.stopPropagation();
            if (onSave) {
              onSave(post);
              return;
            }
            setSaved({ type: "post", id: savableId }, !viewerSaved).catch(() => undefined);
          }}
        >
          <Ionicons name={viewerSaved ? "bookmark" : "bookmark-outline"} size={16} color={viewerSaved ? colors.accent : colors.muted} />
          <Text style={[styles.actionText, viewerSaved && styles.actionTextActive]}>
            {saveState.pending ? (viewerSaved ? "Saving" : "Removing") : viewerSaved ? "Saved" : "Save"}
          </Text>
        </Pressable>
      </View>

      {reactionsOpen ? (
        <View testID={`home-feed-reaction-selector-${post.id}`} accessibilityRole="toolbar" accessibilityLabel="Choose a reaction" style={styles.reactionSelector}>
          {REACTIONS.map((reaction) => (
            <Pressable
              key={reaction.key}
              accessibilityRole="button"
              accessibilityLabel={reaction.label}
              accessibilityState={{ selected: post.viewer_reaction === reaction.key }}
              style={[styles.reactionChoice, post.viewer_reaction === reaction.key && styles.reactionChoiceActive]}
              onPress={(event) => {
                event.stopPropagation();
                setReactionsOpen(false);
                onReact?.(post, reaction.key);
              }}
            >
              <Text style={styles.reactionChoiceEmoji}>{reaction.emoji}</Text>
              <Text style={styles.reactionChoiceLabel}>{reaction.label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      <Modal
        visible={menuOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setMenuOpen(false)}
      >
        <View style={styles.overflowBackdrop}>
          <Pressable
            testID={`home-feed-overflow-dismiss-${post.id}`}
            style={styles.overflowBackdropFill}
            accessibilityLabel="Close post options"
            onPress={() => setMenuOpen(false)}
          />
          <View style={styles.overflowMenu}>
            <View style={styles.overflowGrip} {...menuSwipe.panHandlers}>
              <View style={styles.overflowHandle} />
            </View>
            <ScrollView
              style={styles.overflowScroll}
              contentContainerStyle={styles.overflowScrollBody}
              showsVerticalScrollIndicator={false}
            >
              {onPromote ? (
                <Pressable
                  testID={`home-feed-promote-${post.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Promote post ${post.id}`}
                  style={styles.menuAction}
                  disabled={busy}
                  onPress={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onPromote(post);
                  }}
                >
                  <Text style={styles.menuActionText}>Promote</Text>
                </Pressable>
              ) : null}
              {onReport ? (
                <Pressable
                  testID={`home-feed-report-${post.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Report post ${post.id}`}
                  style={styles.menuAction}
                  disabled={busy}
                  onPress={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onReport(post);
                  }}
                >
                  <Text style={styles.menuActionText}>Report</Text>
                </Pressable>
              ) : null}
              {onHide ? (
                <Pressable
                  testID={`home-feed-hide-${post.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Hide post ${post.id}`}
                  style={styles.menuAction}
                  disabled={busy}
                  onPress={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onHide(post);
                  }}
                >
                  <Text style={styles.menuActionText}>Hide</Text>
                </Pressable>
              ) : null}
              {onBlock ? (
                <Pressable
                  testID={`home-feed-block-${post.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Block ${displayName}`}
                  style={styles.menuAction}
                  disabled={busy}
                  onPress={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onBlock(post);
                  }}
                >
                  <Text style={styles.menuActionText}>Block</Text>
                </Pressable>
              ) : null}
              {onMute ? (
                <Pressable
                  testID={`home-feed-mute-${post.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Mute ${displayName}`}
                  style={styles.menuAction}
                  disabled={busy}
                  onPress={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onMute(post);
                  }}
                >
                  <Text style={styles.menuActionText}>Mute</Text>
                </Pressable>
              ) : null}
              {onDelete ? (
                <Pressable
                  testID={`home-feed-delete-${post.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Delete post ${post.id}`}
                  style={styles.menuAction}
                  disabled={busy}
                  onPress={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    Alert.alert(
                      "Delete post?",
                      "This removes the post for everyone. This cannot be undone.",
                      [
                        { text: "Cancel", style: "cancel" },
                        {
                          text: "Delete",
                          style: "destructive",
                          onPress: () => onDelete(post)
                        }
                      ]
                    );
                  }}
                >
                  <Text style={styles.menuActionDangerText}>Delete</Text>
                </Pressable>
              ) : null}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {!detail && post.preview_comments?.length ? (
        <View style={styles.previewComments}>
          {post.preview_comments.slice(0, 2).map((comment) => (
            <Text key={`${comment.id}-${comment.created_at || ""}`} style={styles.previewComment} numberOfLines={2}>
              <Text style={styles.previewAuthor}>{comment.author?.display_name || comment.author?.username || "PulseSoc"}: </Text>
              {comment.body}
            </Text>
          ))}
        </View>
      ) : null}

      {!detail && onSubmitComment && commentComposerOpen ? (
        <Pressable
          testID={`home-feed-inline-comment-${post.id}`}
          style={styles.inlineCommentComposer}
          onPress={(event) => event.stopPropagation()}
        >
          <View style={styles.inlineCommentAvatar}>
            <Text style={styles.inlineCommentAvatarText}>PS</Text>
          </View>
          <TextInput
            ref={commentInputRef}
            accessibilityLabel="Write a comment"
            testID={`home-feed-comment-input-${post.id}`}
            style={styles.inlineCommentInput}
            value={commentBody}
            onChangeText={(next) => {
              setCommentBody(next);
              if (commentNotice) setCommentNotice("");
            }}
            onSubmitEditing={submitInlineComment}
            placeholder="Write a comment..."
            placeholderTextColor={colors.muted}
            multiline
            returnKeyType="send"
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Add emoji"
            testID={`home-feed-comment-emoji-${post.id}`}
            style={styles.inlineCommentTool}
            onPress={(event) => {
              event.stopPropagation();
              setCommentEmojiOpen(true);
            }}
          >
            <Text style={styles.inlineCommentToolText}>☺</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Send comment"
            accessibilityState={{ disabled: !commentBody.trim() || commentPosting, busy: commentPosting }}
            testID={`home-feed-comment-submit-${post.id}`}
            style={[styles.inlineCommentSend, (!commentBody.trim() || commentPosting) && styles.inlineCommentSendDisabled]}
            disabled={!commentBody.trim() || commentPosting}
            onPress={(event) => {
              event.stopPropagation();
              submitInlineComment();
            }}
          >
            <Text style={styles.inlineCommentSendText}>{commentPosting ? "…" : "➤"}</Text>
          </Pressable>
        </Pressable>
      ) : null}
      {commentNotice ? <Text style={styles.commentNotice}>{commentNotice}</Text> : null}
      <EmojiPicker
        visible={commentEmojiOpen}
        stayOpenOnSelect
        onClose={() => setCommentEmojiOpen(false)}
        onSelect={(emoji) => setCommentBody((current) => `${current}${emoji}`)}
      />
      </View>
      </View>
    </Pressable>
  );
}

/**
 * Default shallow prop comparison. Every prop is either a primitive, the row's
 * `post` object (which the feed reducers replace by identity when it changes)
 * or a callback the caller is expected to keep stable, so shallow equality is
 * exactly the right test — a custom comparator would only be able to get it
 * wrong.
 */
export const PostCard = memo(PostCardBody);

function clampedMediaAspect(media: PulseMedia) {
  const explicit = Number(media.aspect_ratio || 0);
  const width = Number(media.width || 0);
  const height = Number(media.height || 0);
  const raw = explicit > 0 ? explicit : width > 0 && height > 0 ? width / height : 0;
  if (!Number.isFinite(raw) || raw <= 0) return 4 / 5;
  return Math.min(MEDIA_ASPECT_MAX, Math.max(MEDIA_ASPECT_MIN, raw));
}

function mediaPosterUrl(media: PulseMedia) {
  return mediaDisplayUrl({
    ...media,
    media_url: media.thumbnail_url || media.poster_url || media.valid_url || media.media_url || media.url || ""
  });
}

function MediaStrip({ post, active, motionEnabled, onReact, onMediaError }: { post: PulsePost; active: boolean; motionEnabled: boolean; onReact?: (post: PulsePost, reactionType: string) => void; onMediaError?: () => void }) {
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  // A URL that 404s or times out gets us right back to the empty rectangle the
  // renderable-URL gate exists to prevent, so a load error collapses the strip
  // too. Keyed by post id so recycled rows don't inherit a stale failure.
  const [failedPostId, setFailedPostId] = useState<number | null>(null);
  const author = post.author || {};
  const likeMedia = onReact ? () => onReact(post, post.viewer_reaction || "love") : undefined;
  const renderable = useMemo(
    () => (failedPostId === post.id ? [] : feedRenderableMedia(post.media)),
    [failedPostId, post.id, post.media]
  );
  const viewerItems = useMemo(
    () =>
      feedRenderableMedia(post.media).map((media) =>
        mediaViewerItemFromPulseMedia(media, {
          title: post.title || "PulseSoc post media",
          subtitle: post.body || "PulseSoc media",
          author,
          sourceUrl: pulsePostUrl(post.id),
          // Carry the attached-music policy into the expanded/fullscreen viewer so
          // opening a video post keeps its selected soundtrack instead of falling
          // back to the original video audio. Resolved per-media so galleries with
          // mixed clips each honor their own metadata; images ignore it harmlessly.
          musicPolicy: resolvePostAudioPolicy(post, media)
        })
      ),
    [author, post, post.body, post.id, post.media, post.title]
  );
  if (!renderable.length) return null;
  const items = renderable.slice(0, 4);
  const gallery = items.length > 1;

  if (!gallery) {
    const media = items[0];
    const kind = mediaKind(media);
    const aspect = clampedMediaAspect(media);
    if (kind === "video") {
      return (
        <View>
          <FeedInlineVideo
            media={media}
            postId={post.id}
            aspect={aspect}
            active={active}
            motionEnabled={motionEnabled}
            musicPolicy={resolvePostAudioPolicy(post, media)}
            onOpenViewer={() => setViewerIndex(0)}
          />
          <NativeMediaViewer
            visible={viewerIndex !== null}
            items={viewerItems}
            initialIndex={viewerIndex || 0}
            title="Post media"
            onClose={() => setViewerIndex(null)}
            onShare={() => sharePostFromCard(post).catch(() => undefined)}
          />
        </View>
      );
    }
    const url = mediaDisplayUrl(media);
    // Defense in depth against the blank rectangle: even though the gate above is
    // now consistent with the resolver, never mount an <Image> around an empty
    // URI. An empty-string source reserves its aspect box and may never fire
    // onError, which is precisely the permanent blank the invariant forbids.
    // Collapsing to null here is enough; notifying the parent from render would
    // set state mid-render, and the consistent resolver makes this unreachable
    // for any record the gate already admitted.
    if (!url) return null;
    return (
      <View>
        <Pressable
          style={styles.mediaSingleWrap}
          onPress={(event) => {
            event.stopPropagation();
            setViewerIndex(0);
          }}
          testID={`home-feed-media-${post.id}-0`}
          accessibilityRole="button"
          accessibilityLabel={`Open media 1 for post ${post.id}`}
        >
          <Image
            source={{ uri: url }}
            style={[styles.mediaSingleImage, { aspectRatio: aspect }]}
            resizeMode="cover"
            onError={() => {
              setFailedPostId(post.id);
              onMediaError?.();
            }}
          />
        </Pressable>
        <NativeMediaViewer
          visible={viewerIndex !== null}
          items={viewerItems}
          initialIndex={viewerIndex || 0}
          title="Post media"
          onClose={() => setViewerIndex(null)}
          onShare={() => sharePostFromCard(post).catch(() => undefined)}
          onLike={likeMedia}
        />
      </View>
    );
  }

  return (
    <View>
      <View style={styles.mediaGrid}>
        {items.map((media, index) => {
          const url = mediaDisplayUrl(media);
          const poster = mediaPosterUrl(media);
          const kind = mediaKind(media);
          return (
            <Pressable
              style={styles.mediaTile}
              key={`${url}-${index}`}
              onPress={(event) => {
                event.stopPropagation();
                setViewerIndex(index);
              }}
              testID={`home-feed-media-${post.id}-${index}`}
              accessibilityRole="button"
              accessibilityLabel={`Open ${kind} media ${index + 1} for post ${post.id}`}
            >
              {kind === "image" ? (
                <Image source={{ uri: url }} style={styles.mediaTileImage} resizeMode="cover" />
              ) : (
                <View style={styles.mediaTileVideoFallback}>
                  {poster ? <Image source={{ uri: poster }} style={styles.mediaTileImage} resizeMode="cover" /> : null}
                  <View style={styles.mediaTilePlayBadge}>
                    <Text style={styles.mediaTilePlayIcon}>▶</Text>
                  </View>
                </View>
              )}
              {index === 3 && post.media!.length > 4 ? (
                <View style={styles.mediaMore}>
                  <Text style={styles.mediaMoreText}>+{post.media!.length - 4}</Text>
                </View>
              ) : null}
            </Pressable>
          );
        })}
      </View>
      <NativeMediaViewer
        visible={viewerIndex !== null}
        items={viewerItems}
        initialIndex={viewerIndex || 0}
        title="Post media"
        onClose={() => setViewerIndex(null)}
        onShare={() => sharePostFromCard(post).catch(() => undefined)}
        onLike={likeMedia}
      />
    </View>
  );
}

function FeedInlineVideo({
  media,
  postId,
  aspect,
  active,
  motionEnabled,
  musicPolicy,
  onOpenViewer
}: {
  media: PulseMedia;
  postId: number;
  aspect: number;
  active: boolean;
  motionEnabled: boolean;
  musicPolicy?: AttachedMusicPolicy;
  onOpenViewer: () => void;
}) {
  const videoRef = useRef<Video>(null);
  const attachedSoundRef = useRef<Audio.Sound | null>(null);
  const refreshAttempted = useRef(false);
  const [muted, setMuted] = useState(true);
  const [buffering, setBuffering] = useState(false);
  const [failed, setFailed] = useState(false);
  const [refreshingUrl, setRefreshingUrl] = useState(false);
  const [source, setSource] = useState(() => canonicalMediaPlaybackUrl(media));
  const poster = mediaPosterUrl(media);
  const playbackOwnerId = `feed:${postId}:${media.id || 0}`;
  const canAutoplay = active && motionEnabled;
  const audibleAutoplay = canAutoplay && !muted;
  // Attached music takes exclusive audio priority: the original video track is
  // silenced whenever a track is attached, and the music follows the same
  // audible state the viewer controls with the mute toggle.
  const hasAttachedMusic = Boolean(musicPolicy?.hasAttachedMusic && musicPolicy.musicUrl);
  const videoMuted = muted || Boolean(musicPolicy?.muteOriginalAudio);

  useEffect(() => {
    setSource(canonicalMediaPlaybackUrl(media));
    setFailed(false);
    refreshAttempted.current = false;
  }, [media]);

  useEffect(() => {
    if (audibleAutoplay) {
      claimMediaPlayback({
        id: playbackOwnerId,
        kind: "feed",
        pause: () => Promise.all([
          videoRef.current?.pauseAsync().catch(() => undefined),
          attachedSoundRef.current?.pauseAsync().catch(() => undefined)
        ]).then(() => undefined),
        stop: () => Promise.all([
          videoRef.current?.stopAsync().catch(() => undefined),
          attachedSoundRef.current?.stopAsync().catch(() => undefined)
        ]).then(() => undefined)
      })
        .then((granted) => (granted ? videoRef.current?.playAsync() : undefined))
        .catch(() => undefined);
    } else if (canAutoplay) {
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
      videoRef.current?.playAsync().catch(() => undefined);
    } else {
      videoRef.current?.pauseAsync().catch(() => undefined);
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
    }
    return () => {
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
    };
  }, [audibleAutoplay, canAutoplay, playbackOwnerId]);

  // Drive the attached music track alongside the (muted) video, mirroring the
  // Reels player so a video post published with music plays the music — not the
  // original mic audio. The track is audible only when the viewer has unmuted.
  useEffect(() => {
    let cancelled = false;
    async function syncAttachedAudio() {
      if (!hasAttachedMusic || !audibleAutoplay) {
        const existing = attachedSoundRef.current;
        attachedSoundRef.current = null;
        if (existing) await existing.unloadAsync().catch(() => undefined);
        return;
      }
      if (!attachedSoundRef.current) {
        // Match the Reels player: put the iOS audio session into playback mode so
        // the attached track is audible even when the ringer switch is silent and
        // even if the viewer opened the feed without visiting Reels/Music first.
        await configureReelsAudioSession().catch(() => undefined);
        const created = await Audio.Sound.createAsync(
          { uri: musicPolicy!.musicUrl! },
          {
            isLooping: musicPolicy!.isLooping,
            positionMillis: musicPolicy!.musicStartMs,
            shouldPlay: true,
            volume: musicPolicy!.musicVolume
          }
        );
        if (cancelled) return created.sound.unloadAsync();
        attachedSoundRef.current = created.sound;
      } else {
        await attachedSoundRef.current.setStatusAsync({ shouldPlay: true, isMuted: false });
      }
    }
    syncAttachedAudio().catch(() => undefined);
    return () => { cancelled = true; };
  }, [audibleAutoplay, hasAttachedMusic, musicPolicy]);

  useEffect(() => () => {
    attachedSoundRef.current?.unloadAsync().catch(() => undefined);
    attachedSoundRef.current = null;
  }, [musicPolicy?.musicUrl]);

  async function recover() {
    if (refreshAttempted.current) {
      setFailed(true);
      return;
    }
    refreshAttempted.current = true;
    setRefreshingUrl(true);
    try {
      const refreshed = await refreshCanonicalMediaAccess(media);
      if (!refreshed.url) throw new Error("unavailable");
      setSource(refreshed.url);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setRefreshingUrl(false);
    }
  }

  return (
    <Pressable
      style={[styles.mediaSingleWrap, { aspectRatio: aspect }]}
      onPress={(event) => {
        event.stopPropagation();
        onOpenViewer();
      }}
      accessibilityRole="button"
      accessibilityLabel="Open video"
    >
      {poster ? <Image source={{ uri: poster }} style={StyleSheet.absoluteFillObject} resizeMode="cover" /> : null}
      {!failed && source ? (
        <Video
          ref={videoRef}
          source={{ uri: source }}
          style={StyleSheet.absoluteFillObject}
          resizeMode={ResizeMode.COVER}
          shouldPlay={false}
          isMuted={videoMuted}
          isLooping
          progressUpdateIntervalMillis={400}
          onPlaybackStatusUpdate={(status) => {
            if (!status.isLoaded) {
              if (status.error) recover().catch(() => undefined);
              return;
            }
            setBuffering(Boolean(status.isBuffering));
          }}
          onError={() => recover().catch(() => undefined)}
        />
      ) : null}
      {buffering || refreshingUrl ? (
        <View style={styles.mediaVideoBuffering}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={muted ? "Unmute video" : "Mute video"}
        style={styles.mediaVideoMute}
        onPress={(event) => {
          event.stopPropagation();
          setMuted((value) => !value);
        }}
      >
        <Text style={styles.mediaVideoMuteText}>{muted ? "⌁" : "◖))"}</Text>
      </Pressable>
    </Pressable>
  );
}

function compactCount(value?: number) {
  const count = Number(value || 0);
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(count >= 10_000_000 ? 0 : 1)}M`;
  if (count >= 1000) return `${(count / 1000).toFixed(count >= 10_000 ? 0 : 1)}K`;
  return String(count);
}

const REACTIONS = [
  { key: "like", emoji: "👍", label: "Like" },
  { key: "love", emoji: "❤️", label: "Love" },
  { key: "fire", emoji: "🔥", label: "Fire" },
  { key: "funny", emoji: "😂", label: "Funny" },
  { key: "wow", emoji: "😮", label: "Wow" },
  { key: "rocket", emoji: "🚀", label: "Rocket" }
] as const;

function reactionSummary(counts: Record<string, number>) {
  const active = REACTIONS.filter((reaction) => Number(counts[reaction.key] || 0) > 0)
    .sort((left, right) => Number(counts[right.key] || 0) - Number(counts[left.key] || 0))
    .slice(0, 3)
    .map((reaction) => reaction.emoji);
  return active.length ? active.join("") : "♡";
}

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    borderRadius: logiNexus.radius.medium,
    flex: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: 38,
    paddingVertical: 9
  },
  actionButtonPressed: {
    backgroundColor: "rgba(255, 255, 255, 0.05)"
  },
  actionIcon: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "900",
    lineHeight: 18
  },
  actionIconActive: {
    color: colors.danger
  },
  actionRow: {
    alignItems: "center",
    borderTopColor: logiNexus.colors.home.borderSubtle,
    borderTopWidth: 1,
    flexDirection: "row",
    marginTop: 10,
    paddingTop: 4
  },
  actionText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "capitalize"
  },
  actionTextActive: {
    color: colors.accent
  },
  authorName: {
    color: colors.text,
    ...logiNexus.typography.home.cardAuthor,
    flexShrink: 1
  },
  authorNameRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  automatedPill: {
    backgroundColor: "rgba(244, 183, 64, 0.12)",
    borderColor: "#f4b740",
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  automatedPillText: {
    color: "#f4c96b",
    fontSize: 8,
    fontWeight: "900",
    letterSpacing: 0.5
  },
  authorRow: {
    alignItems: "center",
    flexDirection: "row",
    flex: 1,
    gap: 9,
    minWidth: 0
  },
  authorText: {
    flex: 1
  },
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accent,
    borderRadius: 24,
    borderWidth: 2,
    height: 48,
    width: 48
  },
  avatarFallback: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 24,
    borderWidth: 1,
    height: 48,
    width: 48
  },
  badgeRow: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 7,
    marginTop: 8
  },
  body: {
    color: colors.text,
    ...logiNexus.typography.home.cardBody,
    fontSize: 15,
    lineHeight: 22,
    marginTop: 10
  },
  bodyMeasure: {
    position: "absolute",
    left: 0,
    right: 0,
    opacity: 0,
    zIndex: -1
  },
  commentNotice: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 8
  },
  card: {
    borderBottomColor: logiNexus.colors.home.borderSubtle,
    borderBottomWidth: 1,
    paddingBottom: 14,
    paddingTop: 14
  },
  cardInset: {
    paddingHorizontal: 16
  },
  mediaBleed: {
    marginTop: 12
  },
  cardHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  cardPressed: {
    backgroundColor: "rgba(255, 255, 255, 0.03)"
  },
  countRow: {
    borderTopColor: logiNexus.colors.home.borderSubtle,
    borderTopWidth: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 14,
    marginTop: 14,
    paddingTop: 12
  },
  followPill: {
    alignItems: "center",
    borderColor: "rgba(100, 255, 188, 0.58)",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minHeight: 36,
    paddingHorizontal: 12
  },
  followPillActive: {
    backgroundColor: "rgba(50, 230, 179, 0.14)"
  },
  followPillText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    lineHeight: 18
  },
  followPillTextActive: {
    color: colors.accentStrong
  },
  headerActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  inlineCommentAvatar: {
    alignItems: "center",
    backgroundColor: "rgba(121, 210, 255, 0.13)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 14,
    borderWidth: 1,
    height: 28,
    justifyContent: "center",
    width: 28
  },
  inlineCommentAvatarText: {
    color: colors.accentStrong,
    fontSize: 10,
    fontWeight: "900"
  },
  inlineCommentComposer: {
    alignItems: "center",
    backgroundColor: "rgba(4, 11, 22, 0.62)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    marginTop: 10,
    minHeight: 46,
    paddingHorizontal: 8,
    paddingVertical: 7
  },
  inlineCommentInput: {
    color: colors.text,
    flex: 1,
    fontSize: 13,
    fontWeight: "800",
    lineHeight: 18,
    maxHeight: 84,
    minHeight: 28,
    minWidth: 0,
    padding: 0
  },
  inlineCommentSend: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 15,
    height: 30,
    justifyContent: "center",
    width: 30
  },
  inlineCommentSendDisabled: {
    opacity: 0.45
  },
  inlineCommentSendText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  inlineCommentTool: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderRadius: 14,
    height: 28,
    justifyContent: "center",
    width: 28
  },
  inlineCommentToolText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "900"
  },
  mediaSingleWrap: {
    backgroundColor: colors.surfaceRaised,
    overflow: "hidden",
    width: "100%"
  },
  mediaSingleImage: {
    backgroundColor: colors.surfaceRaised,
    width: "100%"
  },
  mediaGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 2
  },
  mediaTile: {
    aspectRatio: 1,
    flexBasis: "49.4%",
    flexGrow: 1,
    overflow: "hidden"
  },
  mediaTileImage: {
    height: "100%",
    width: "100%"
  },
  mediaTileVideoFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    height: "100%",
    justifyContent: "center",
    width: "100%"
  },
  mediaTilePlayBadge: {
    alignItems: "center",
    backgroundColor: "rgba(2, 8, 17, 0.55)",
    borderRadius: 20,
    height: 40,
    justifyContent: "center",
    position: "absolute",
    width: 40
  },
  mediaTilePlayIcon: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "900",
    marginLeft: 2
  },
  mediaVideoBuffering: {
    alignItems: "center",
    ...StyleSheet.absoluteFillObject,
    justifyContent: "center"
  },
  mediaVideoMute: {
    alignItems: "center",
    backgroundColor: "rgba(2, 10, 20, 0.62)",
    borderRadius: 15,
    bottom: 10,
    height: 30,
    justifyContent: "center",
    position: "absolute",
    right: 10,
    width: 34
  },
  mediaVideoMuteText: {
    color: "#68f3de",
    fontSize: 10,
    fontWeight: "900"
  },
  mediaMore: {
    alignItems: "center",
    backgroundColor: "rgba(2, 8, 17, 0.62)",
    borderRadius: 16,
    bottom: 0,
    justifyContent: "center",
    left: 0,
    position: "absolute",
    right: 0,
    top: 0
  },
  mediaMoreText: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  liveBadge: {
    backgroundColor: colors.danger,
    borderRadius: 7,
    paddingHorizontal: 8,
    paddingVertical: 4
  },
  liveBadgeText: {
    color: "#fff",
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.8
  },
  liveJoinButton: {
    backgroundColor: "rgba(235,32,88,0.9)",
    borderColor: "rgba(255,255,255,0.28)"
  },
  liveMetaRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    marginTop: 10
  },
  liveProcessing: {
    alignItems: "center",
    aspectRatio: 4 / 3,
    backgroundColor: "#030812",
    justifyContent: "center",
    overflow: "hidden",
    padding: 24
  },
  liveProcessingBadge: {
    alignItems: "center",
    backgroundColor: "rgba(8, 17, 30, 0.78)",
    borderColor: "rgba(77, 255, 216, 0.28)",
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    marginBottom: 12,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  liveProcessingBadgeText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.3
  },
  liveProcessingShade: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(3, 8, 18, 0.72)"
  },
  liveProcessingBody: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 8,
    maxWidth: 280,
    textAlign: "center"
  },
  liveProcessingTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  liveStage: {
    aspectRatio: 9 / 16,
    backgroundColor: "#02050b",
    overflow: "hidden",
    position: "relative"
  },
  liveStageActions: {
    bottom: 12,
    flexDirection: "row",
    gap: 8,
    left: 12,
    position: "absolute",
    right: 12
  },
  liveStageButton: {
    backgroundColor: "rgba(2,8,17,0.82)",
    borderColor: "rgba(104,243,222,0.4)",
    borderRadius: 18,
    borderWidth: 1,
    minHeight: 38,
    justifyContent: "center",
    paddingHorizontal: 16
  },
  liveStageButtonText: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "900"
  },
  liveViewerCount: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  menuAction: {
    alignItems: "flex-start",
    alignSelf: "stretch",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 12,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 52,
    paddingHorizontal: 16,
    paddingVertical: 12
  },
  menuActionText: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  menuActionDangerText: {
    color: colors.danger,
    fontSize: 16,
    fontWeight: "800"
  },
  meta: {
    color: colors.muted,
    ...logiNexus.typography.home.cardMetadata,
    flexShrink: 1
  },
  metaRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 4,
    marginTop: 2
  },
  metaVisibilityIcon: {
    marginTop: 1
  },
  overflowButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 18,
    borderWidth: 1,
    height: 36,
    justifyContent: "center",
    width: 36
  },
  overflowBackdrop: {
    backgroundColor: "rgba(0,0,0,0.55)",
    flex: 1,
    justifyContent: "flex-end"
  },
  overflowBackdropFill: {
    ...StyleSheet.absoluteFillObject
  },
  overflowGrip: {
    alignItems: "center",
    paddingBottom: 8,
    paddingTop: 10
  },
  overflowHandle: {
    backgroundColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 3,
    height: 5,
    width: 42
  },
  overflowMenu: {
    backgroundColor: "rgb(5, 13, 26)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    borderWidth: 1,
    // Capped rather than sized to content so a long menu on a short screen
    // scrolls instead of running off the top of the sheet.
    maxHeight: "70%",
    paddingBottom: 28,
    paddingHorizontal: 14
  },
  overflowScroll: {
    flexGrow: 0
  },
  overflowScrollBody: {
    gap: 8
  },
  overflowText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "900",
    letterSpacing: 1
  },
  previewAuthor: {
    color: colors.text,
    fontWeight: "800"
  },
  previewComment: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  previewComments: {
    borderTopColor: logiNexus.colors.home.borderSubtle,
    borderTopWidth: 1,
    gap: 6,
    marginTop: 10,
    paddingTop: 9
  },
  readMore: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900",
    marginTop: 5
  },
  reactionChoice: {
    alignItems: "center",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 14,
    borderWidth: 1,
    flex: 1,
    gap: 2,
    minHeight: 48,
    justifyContent: "center"
  },
  reactionChoiceActive: {
    backgroundColor: "rgba(47, 225, 180, 0.14)",
    borderColor: colors.accent
  },
  reactionChoiceEmoji: {
    fontSize: 16
  },
  reactionChoiceLabel: {
    color: colors.muted,
    fontSize: 8,
    fontWeight: "800"
  },
  reactionSelector: {
    backgroundColor: "rgba(3, 9, 18, 0.92)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 5,
    marginTop: 8,
    padding: 6
  },
  reactionSummary: {
    color: colors.text,
    fontSize: 13,
    letterSpacing: -2
  },
  socialContextRow: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 7,
    marginTop: 10
  },
  socialContextText: {
    color: colors.muted,
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "800"
  },
  safetyButton: {
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minHeight: 32,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  safetyRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 8
  },
  safetyText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  title: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "800",
    lineHeight: 24,
    marginTop: 12
  },
  utilityButton: {
    minHeight: 34,
    paddingVertical: 8
  },
  utilityRow: {
    flexDirection: "row",
    gap: 18,
    marginTop: 8
  },
  utilityText: {
    color: colors.accentStrong,
    fontSize: 13,
    fontWeight: "800"
  },
  viewCommentsText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  verifiedMark: {
    marginTop: 1
  },
  creatorPill: {
    backgroundColor: "rgba(159, 124, 255, 0.22)",
    borderColor: logiNexus.colors.home.borderIntelligence,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  creatorPillText: {
    color: "#d8c7ff",
    fontSize: 11,
    fontWeight: "900"
  }
}));
