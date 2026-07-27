import { StyleSheet, View } from "react-native";
import { PostCard } from "../PostCard";
import { ReelPlayerCard } from "../ReelPlayerCard";
import { StatusViewerCard } from "../StatusViewerCard";
import { PreviewContent } from "../../create/draftToContentModel";

type Props = {
  content: PreviewContent;
  /** Whether media should play (false while the screen is backgrounded). */
  active: boolean;
  muted: boolean;
  onToggleMuted: () => void;
};

/**
 * The ONE canonical preview renderer. It renders draft content through the
 * EXACT production feed components (`PostCard` / `ReelPlayerCard` /
 * `StatusViewerCard`) — no bespoke preview markup — so layout, media rules,
 * badges, metadata, and audio behavior are identical to the published result.
 *
 * "Preview mode" means: every interactive callback is inert (`noop`). Taps on
 * reactions, comments, share, follow, report, promote, author, etc. do
 * nothing — the content is displayed but not actionable, which is the correct
 * disabled/non-published state. Media playback is still driven by the real
 * renderer via the `active` prop, and the attached-music audio policy runs
 * exactly as it does in the feed.
 */
export function ContentPreviewRenderer({ content, active, muted, onToggleMuted }: Props) {
  if (content.kind === "reel") {
    return (
      <View style={styles.fill}>
        <ReelPlayerCard
          reel={content.reel}
          active={active}
          muted={muted}
          onToggleMuted={onToggleMuted}
          onReact={noop}
          onOpenReactions={noop}
          onOpenComments={noop}
          onSave={noop}
          onRepost={noop}
          onShare={noop}
          onNotInterested={noop}
          onReport={noop}
          onFollowCreator={noop}
          onAuthorPress={noop}
          onOpenMusic={noop}
          onOpenMore={noop}
          onJoinLive={noop}
        />
      </View>
    );
  }

  if (content.kind === "status") {
    return (
      <View style={styles.fill}>
        <StatusViewerCard
          status={content.status}
          active={active}
          muted={muted}
          progress={1}
          onPrevious={noop}
          onNext={noop}
          onToggleMuted={onToggleMuted}
          onReact={noop}
          onReply={noop}
          onShare={noop}
          onMore={noop}
          onAuthorPress={noop}
        />
      </View>
    );
  }

  return (
    <View style={styles.postWrap}>
      <PostCard
        post={content.post}
        active={active}
        onReact={noop}
        onSave={noop}
        onRepost={noop}
        onShare={noop}
        onComment={noop}
        onFollow={noop}
        onReport={noop}
        onHide={noop}
        onBlock={noop}
        onMute={noop}
        onAuthorPress={noop}
      />
    </View>
  );
}

function noop() {
  // Preview is non-interactive by design.
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  postWrap: { paddingHorizontal: 12, paddingVertical: 8 }
});
