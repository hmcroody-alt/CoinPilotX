/**
 * The one discovery row — §2.
 *
 * ## Why one component instead of one per module
 *
 * The header, the carousel, the dismiss control, the skeleton, the empty state
 * and the impression accounting are identical for every module. Seven copies of
 * them is seven places for a missing `accessibilityRole`, a duplicated
 * impression, or a dismiss button that quietly does nothing — and none of those
 * show up in a diff, because each copy looks correct on its own. So the shell is
 * written once here and the only thing that varies per kind is the card, which
 * is where the differences actually are.
 *
 * ## Why every card is built from an explicit destination
 *
 * Each card takes its navigation payload from a field the adapter has already
 * proven non-empty (`sources.ts` drops anything without one). A card therefore
 * cannot render with a tap that resolves to nothing: the failure is an absent
 * card, not a dead one. `onOpen*` callbacks receive the *exact* identifier — the
 * reel id in the thumbnail, the status the avatar belongs to, that group's slug,
 * that person's profile key — never an index into a list the parent might have
 * re-sorted.
 *
 * ## Impressions
 *
 * A module impression fires once when the row mounts, and a card impression once
 * per card that actually becomes visible in the carousel. Both go through
 * `trackDiscoveryEvent`, which dedupes on `(event, kind, slot, target)`, because
 * FlatList unmounts and remounts rows freely while scrolling and a raw mount
 * hook would report one carousel as a dozen.
 *
 * ## Why the row is full-bleed and the cards are computed
 *
 * The row used to be a bordered panel with a 16pt margin, containing a carousel
 * with another 16pt of padding, containing 148pt thumbnails. Thirty-two points
 * of nothing before any content, and a card that was barely a third of the
 * screen: the suggestion read as a widget *about* content rather than as the
 * content. The panel is gone, the media now runs to within 12pt of the screen
 * edge, and every dimension comes from `discoveryCardMetrics` so the card is a
 * proportion of the device rather than a number that happened to look right on
 * one simulator.
 *
 * ## The single active card
 *
 * Exactly one card in the row may be playing a preview, and it is the first one
 * the carousel reports as viewable. That is decided here rather than in the
 * card, because "which of my siblings is the primary one" is not a question a
 * card can answer about itself — every card would answer yes. The row is also
 * gated on `isRowVisible`, which Home computes from the *vertical* feed's own
 * viewability and its focus state, so a carousel three screens down is not
 * quietly decoding video, and leaving the feed stops playback without this
 * component knowing what navigation is.
 */
import { Ionicons } from "@expo/vector-icons";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
  ViewToken,
  useWindowDimensions
} from "react-native";
import { useTranslation } from "../i18n/I18nContext";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import { trackDiscoveryEvent } from "./analytics";
import { DiscoveryCardEnergy, DiscoveryCardFrame } from "./DiscoveryCardFrame";
import { DiscoveryPreviewMedia } from "./DiscoveryPreviewMedia";
import {
  DISCOVERY_EDGE_INSET,
  DiscoveryCardMetrics,
  discoveryCardMetrics
} from "./discoveryCardMetrics";
import {
  DiscoveryModule,
  DiscoveryModuleKind,
  GroupSuggestion,
  PersonSuggestion,
  ReelSuggestion,
  StatusSuggestion
} from "./discoveryRows";

/** Corner radius of the media itself. The frame adds its ring outside this. */
const CARD_RADIUS = logiNexus.radius.card;

const MODULE_ICON: Record<DiscoveryModuleKind, keyof typeof Ionicons.glyphMap> = {
  reels: "play-circle",
  people: "person-add",
  statuses: "radio",
  groups: "people-circle",
  creators: "sparkles",
  topics: "pricetag",
  sponsored: "megaphone"
};

export type DiscoveryRowActions = {
  /** Opens the exact reel in the thumbnail — never an index into a list. */
  onOpenReel: (reel: ReelSuggestion) => void;
  onOpenStatus: (status: StatusSuggestion) => void;
  onOpenGroup: (group: GroupSuggestion) => void;
  onOpenPerson: (person: PersonSuggestion) => void;
  onAddFriend: (person: PersonSuggestion) => void;
  onJoinGroup: (group: GroupSuggestion) => void;
  /**
   * Omitted for modules with no "all of these" screen.
   *
   * People You May Know is the case that matters: the mobile app has no friends
   * or suggestions screen, only `/api/pulse/friends` behind this row. A "See all"
   * that navigates nowhere is the same defect §9 names for topics, so the control
   * is absent rather than inert.
   */
  onSeeAll?: (kind: DiscoveryModuleKind) => void;
  onDismiss: (kind: DiscoveryModuleKind) => void;
};

export type DiscoveryRowViewProps = DiscoveryRowActions & {
  module: DiscoveryModule;
  slot: number;
  /** Profile keys with a request already sent this session — §5 relationship state. */
  pendingFriendKeys?: ReadonlySet<string>;
  /** Group slugs joined this session, so the button reflects the tap immediately. */
  joinedGroupSlugs?: ReadonlySet<string>;
  /**
   * Whether this row is meaningfully on screen in the *vertical* feed.
   *
   * Defaults to false, which means a caller that forgets to wire it gets a
   * silent, still row rather than a carousel autoplaying somewhere off screen.
   * Wrong-but-quiet beats wrong-but-decoding-video.
   */
  isRowVisible?: boolean;
};

/* ------------------------------------------------------------------ *
 * Cards
 * ------------------------------------------------------------------ */

/**
 * The pressable card body, wrapped in its living border.
 *
 * `pressed` feeds the frame rather than dimming the whole card: at this size a
 * global opacity change reads as the media glitching, whereas a brightened edge
 * reads as the card acknowledging the touch — §8's "brief responsive highlight".
 */
function CardShell({
  onPress,
  accessibilityLabel,
  testID,
  energy,
  metrics,
  frameHeight,
  children
}: {
  onPress: () => void;
  accessibilityLabel: string;
  testID: string;
  energy: DiscoveryCardEnergy;
  metrics: DiscoveryCardMetrics;
  frameHeight: number;
  children: React.ReactNode;
}) {
  const [pressed, setPressed] = useState(false);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      style={{ width: metrics.cardWidth }}
      onPress={onPress}
      onPressIn={() => setPressed(true)}
      onPressOut={() => setPressed(false)}
    >
      <DiscoveryCardFrame
        testID={`${testID}-frame`}
        energy={pressed && energy === "idle" ? "focused" : energy}
        width={metrics.cardWidth}
        height={frameHeight}
        radius={CARD_RADIUS}
      >
        {children}
      </DiscoveryCardFrame>
    </Pressable>
  );
}

/**
 * A still, full-bleed image for the kinds that have no video.
 *
 * Same contract as `DiscoveryPreviewMedia`: `cover` so the media fills the card
 * without letterboxing, and a text-first panel rather than an empty rectangle
 * when there is nothing to show (§11).
 */
function CardStill({
  uri,
  fallback,
  width,
  height,
  testID
}: {
  uri?: string | null;
  fallback: string;
  width: number;
  height: number;
  testID?: string;
}) {
  return (
    <DiscoveryPreviewMedia
      videoUrl={null}
      posterUrl={uri}
      fallbackLabel={fallback}
      width={width}
      height={height}
      active={false}
      testID={testID}
    />
  );
}

/**
 * Title, metadata and any in-card action, laid *over* the bottom of the media.
 *
 * Under the media they would add height the media does not own, which is how the
 * old row ended up with a 148pt thumbnail inside a much taller box. Over it, the
 * card is exactly the media, and the scrim `DiscoveryPreviewMedia` already
 * paints is what keeps the text legible.
 */
function CardCaption({
  title,
  meta,
  children
}: {
  title: string;
  meta?: string | null;
  children?: React.ReactNode;
}) {
  if (!title && !meta && !children) return null;
  return (
    // `box-none` so the caption itself is inert but the action inside it is not.
    <View style={styles.caption} pointerEvents="box-none">
      {title ? (
        <Text style={styles.captionTitle} numberOfLines={2}>
          {title}
        </Text>
      ) : null}
      {meta ? (
        <Text style={styles.captionMeta} numberOfLines={1}>
          {meta}
        </Text>
      ) : null}
      {children}
    </View>
  );
}

const ReelCard = memo(function ReelCard({
  reel,
  onPress,
  formatViews,
  metrics,
  active
}: {
  reel: ReelSuggestion;
  onPress: () => void;
  formatViews: (count: number) => string;
  metrics: DiscoveryCardMetrics;
  active: boolean;
}) {
  const [playing, setPlaying] = useState(false);
  const testID = `discovery-card-reels-${reel.reelId}`;
  const title = reel.title || reel.authorName || "";
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={title || String(reel.reelId)}
      testID={testID}
      energy={playing ? "playing" : active ? "focused" : "idle"}
      metrics={metrics}
      frameHeight={metrics.mediaHeight}
    >
      <DiscoveryPreviewMedia
        videoUrl={reel.previewVideoUrl}
        posterUrl={reel.posterUrl}
        fallbackLabel={title || String(reel.reelId)}
        width={metrics.cardWidth}
        height={metrics.mediaHeight}
        active={active}
        testID={`${testID}-media`}
        onPlayingChange={setPlaying}
      />
      <CardCaption
        title={title}
        meta={typeof reel.viewCount === "number" ? formatViews(reel.viewCount) : null}
      />
      {playing ? (
        <View style={styles.mutedBadge} pointerEvents="none" testID={`${testID}-muted-badge`}>
          <Ionicons name="volume-mute" size={13} color={colors.text} />
        </View>
      ) : null}
    </CardShell>
  );
});

const PersonCard = memo(function PersonCard({
  person,
  pending,
  onPress,
  onAdd,
  addLabel,
  pendingLabel,
  metrics,
  active
}: {
  person: PersonSuggestion;
  pending: boolean;
  onPress: () => void;
  onAdd: () => void;
  addLabel: string;
  pendingLabel: string;
  metrics: DiscoveryCardMetrics;
  active: boolean;
}) {
  const name = person.displayName || person.username;
  const testID = `discovery-card-people-${person.profileKey}`;
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={name}
      testID={testID}
      energy={active ? "focused" : "idle"}
      metrics={metrics}
      frameHeight={metrics.mediaHeight}
    >
      {/* A portrait, not a video frame (§10): people never get a player, so the
          avatar simply fills the card the way a poster would. */}
      <CardStill
        uri={person.avatarUrl}
        fallback={name}
        width={metrics.cardWidth}
        height={metrics.mediaHeight}
        testID={`${testID}-media`}
      />
      <CardCaption title={name} meta={person.rank}>
        {/* A nested Pressable: RN does not bubble a handled press to the card, so
            "Add friend" cannot also navigate — §11. */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${pending ? pendingLabel : addLabel}, ${name}`}
          accessibilityState={{ disabled: pending }}
          testID={`discovery-add-friend-${person.profileKey}`}
          disabled={pending}
          style={[styles.cardAction, pending && styles.cardActionMuted]}
          onPress={onAdd}
        >
          <Text style={[styles.cardActionText, pending && styles.cardActionTextMuted]} numberOfLines={1}>
            {pending ? pendingLabel : addLabel}
          </Text>
        </Pressable>
      </CardCaption>
    </CardShell>
  );
});

const StatusCard = memo(function StatusCard({
  status,
  onPress,
  newLabel,
  seenLabel,
  metrics,
  active
}: {
  status: StatusSuggestion;
  onPress: () => void;
  newLabel: string;
  seenLabel: string;
  metrics: DiscoveryCardMetrics;
  active: boolean;
}) {
  const title = status.authorName || status.title || String(status.statusId);
  const testID = `discovery-card-statuses-${status.statusId}`;
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={title}
      testID={testID}
      // An unseen status is worth pointing at even when it is not the card in
      // focus, so it borrows the focused border rather than the idle one.
      energy={active || !status.seen ? "focused" : "idle"}
      metrics={metrics}
      frameHeight={metrics.mediaHeight}
    >
      <CardStill
        uri={status.previewUrl}
        fallback={title}
        width={metrics.cardWidth}
        height={metrics.mediaHeight}
        testID={`${testID}-media`}
      />
      <CardCaption title={title} meta={status.seen ? seenLabel : newLabel} />
    </CardShell>
  );
});

const GroupCard = memo(function GroupCard({
  group,
  joined,
  onPress,
  onJoin,
  joinLabel,
  joinedLabel,
  formatMembers,
  metrics,
  active
}: {
  group: GroupSuggestion;
  joined: boolean;
  onPress: () => void;
  onJoin: () => void;
  joinLabel: string;
  joinedLabel: string;
  formatMembers: (count: number) => string;
  metrics: DiscoveryCardMetrics;
  active: boolean;
}) {
  const testID = `discovery-card-groups-${group.slug}`;
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={group.name}
      testID={testID}
      energy={active ? "focused" : "idle"}
      metrics={metrics}
      frameHeight={metrics.mediaHeight}
    >
      <CardStill
        uri={group.coverUrl}
        fallback={group.name}
        width={metrics.cardWidth}
        height={metrics.mediaHeight}
        testID={`${testID}-media`}
      />
      <CardCaption
        title={group.name}
        meta={typeof group.memberCount === "number" ? formatMembers(group.memberCount) : null}
      >
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${joined ? joinedLabel : joinLabel}, ${group.name}`}
          accessibilityState={{ disabled: joined }}
          testID={`discovery-join-group-${group.slug}`}
          disabled={joined}
          style={[styles.cardAction, joined && styles.cardActionMuted]}
          onPress={onJoin}
        >
          <Text style={[styles.cardActionText, joined && styles.cardActionTextMuted]} numberOfLines={1}>
            {joined ? joinedLabel : joinLabel}
          </Text>
        </Pressable>
      </CardCaption>
    </CardShell>
  );
});

/* ------------------------------------------------------------------ *
 * The row
 * ------------------------------------------------------------------ */

/** Identity of a card, for keys, impressions and analytics targets. */
function cardTarget(module: DiscoveryModule, item: unknown): string {
  switch (module.kind) {
    case "reels":
      return String((item as ReelSuggestion).reelId);
    case "people":
    case "creators":
      return (item as PersonSuggestion).profileKey;
    case "statuses":
      return String((item as StatusSuggestion).statusId);
    case "groups":
      return (item as GroupSuggestion).slug;
    default:
      return "";
  }
}

export function DiscoveryRowView({
  module,
  slot,
  pendingFriendKeys,
  joinedGroupSlugs,
  isRowVisible = false,
  onOpenReel,
  onOpenStatus,
  onOpenGroup,
  onOpenPerson,
  onAddFriend,
  onJoinGroup,
  onSeeAll,
  onDismiss
}: DiscoveryRowViewProps) {
  const { t } = useTranslation();
  const { width: screenWidth } = useWindowDimensions();
  const metrics = useMemo(() => discoveryCardMetrics(screenWidth, module.kind), [module.kind, screenWidth]);

  useEffect(() => {
    trackDiscoveryEvent({ name: "module_impression", kind: module.kind, slot });
  }, [module.kind, slot]);

  // FlatList treats `onViewableItemsChanged` as immutable after mount — passing a
  // new function throws "Changing onViewableItemsChanged on the fly is not
  // supported" — so the handler reads the current props through refs rather than
  // closing over them.
  const moduleRef = useRef(module);
  const slotRef = useRef(slot);
  moduleRef.current = module;
  slotRef.current = slot;

  /**
   * The one card allowed to play. Null until the carousel reports something.
   *
   * §5's threshold lives in `viewabilityConfig` below and is deliberately the
   * same 60% that already governs impressions: a card counted as seen and a card
   * allowed to play should not be able to disagree about what "visible" means.
   */
  const [activeTarget, setActiveTarget] = useState<string | null>(null);

  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 60 }).current;
  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    let primary: string | null = null;
    viewableItems.forEach((token) => {
      if (token.index === null || token.index === undefined) return;
      const target = cardTarget(moduleRef.current, token.item);
      // First viewable wins: the leftmost card is the one the snap point puts
      // under the user's eye, and "first" is stable while "most visible" flips
      // back and forth across the midpoint of a drag.
      if (primary === null && token.isViewable !== false) primary = target;
      trackDiscoveryEvent({
        name: "card_impression",
        kind: moduleRef.current.kind,
        slot: slotRef.current,
        target,
        position: token.index
      });
    });
    setActiveTarget(primary);
  }).current;

  const title = t(module.titleKey);

  const formatViews = useCallback(
    (count: number) => t("social:feed.discovery.views", { count }),
    [t]
  );
  const formatMembers = useCallback(
    (count: number) => t("social:feed.discovery.members", { count }),
    [t]
  );

  const handleDismiss = useCallback(() => {
    trackDiscoveryEvent({ name: "module_dismissed", kind: module.kind, slot });
    onDismiss(module.kind);
  }, [module.kind, onDismiss, slot]);

  const handleSeeAll = useCallback(() => {
    if (!onSeeAll) return;
    trackDiscoveryEvent({ name: "see_all_tap", kind: module.kind, slot });
    onSeeAll(module.kind);
  }, [module.kind, onSeeAll, slot]);

  const renderItem = useCallback(
    ({ item, index }: { item: unknown; index: number }) => {
      const target = cardTarget(module, item);
      // Both halves must be true: the horizontal carousel says this is its
      // primary card, and Home says the row itself is on screen and focused.
      // Either alone would let a card three screens down decide it is active.
      const active = isRowVisible && activeTarget !== null && activeTarget === target;
      const tap = (name: "card_tap" | "reel_opened" | "status_opened") =>
        trackDiscoveryEvent({ name, kind: module.kind, slot, target, position: index });

      switch (module.kind) {
        case "reels": {
          const reel = item as ReelSuggestion;
          return (
            <ReelCard
              reel={reel}
              formatViews={formatViews}
              metrics={metrics}
              active={active}
              onPress={() => {
                tap("card_tap");
                tap("reel_opened");
                onOpenReel(reel);
              }}
            />
          );
        }
        case "people":
        case "creators": {
          const person = item as PersonSuggestion;
          const pending = Boolean(pendingFriendKeys?.has(person.profileKey));
          return (
            <PersonCard
              person={person}
              pending={pending}
              metrics={metrics}
              active={active}
              addLabel={t("social:feed.discovery.addFriend")}
              pendingLabel={t("social:feed.discovery.requestSent")}
              onPress={() => {
                tap("card_tap");
                onOpenPerson(person);
              }}
              onAdd={() => {
                trackDiscoveryEvent({ name: "friend_request_sent", kind: module.kind, slot, target, position: index });
                onAddFriend(person);
              }}
            />
          );
        }
        case "statuses": {
          const status = item as StatusSuggestion;
          return (
            <StatusCard
              status={status}
              metrics={metrics}
              active={active}
              newLabel={t("social:feed.discovery.statusNew")}
              seenLabel={t("social:feed.discovery.statusSeen")}
              onPress={() => {
                tap("card_tap");
                tap("status_opened");
                onOpenStatus(status);
              }}
            />
          );
        }
        case "groups": {
          const group = item as GroupSuggestion;
          const joined = Boolean(joinedGroupSlugs?.has(group.slug));
          return (
            <GroupCard
              group={group}
              joined={joined}
              metrics={metrics}
              active={active}
              formatMembers={formatMembers}
              joinLabel={t("social:feed.discovery.join")}
              joinedLabel={t("social:feed.discovery.joined")}
              onPress={() => {
                tap("card_tap");
                onOpenGroup(group);
              }}
              onJoin={() => {
                trackDiscoveryEvent({ name: "group_join_tap", kind: module.kind, slot, target, position: index });
                onJoinGroup(group);
              }}
            />
          );
        }
        default:
          // `topics` and `sponsored` have no approved source (§9, §10). They
          // cannot reach here — nothing builds them — and if that ever changes
          // this renders nothing rather than a card with no destination.
          return null;
      }
    },
    [
      activeTarget,
      formatMembers,
      formatViews,
      isRowVisible,
      joinedGroupSlugs,
      metrics,
      module,
      onAddFriend,
      onJoinGroup,
      onOpenGroup,
      onOpenPerson,
      onOpenReel,
      onOpenStatus,
      pendingFriendKeys,
      slot,
      t
    ]
  );

  return (
    <View style={styles.row} testID={`discovery-row-${module.kind}`} accessibilityLabel={title}>
      <View style={styles.header}>
        <Ionicons name={MODULE_ICON[module.kind]} size={16} color={colors.accent} style={styles.headerIcon} />
        <Text style={styles.headerTitle} numberOfLines={1}>
          {title}
        </Text>
        {onSeeAll ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("social:feed.discovery.seeAll")}
            testID={`discovery-see-all-${module.kind}`}
            onPress={handleSeeAll}
            hitSlop={8}
          >
            <Text style={styles.headerAction}>{t("social:feed.discovery.seeAll")}</Text>
          </Pressable>
        ) : null}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("social:feed.discovery.hideSection", { section: title })}
          testID={`discovery-dismiss-${module.kind}`}
          onPress={handleDismiss}
          hitSlop={8}
          style={styles.headerDismiss}
        >
          <Ionicons name="close" size={16} color={colors.muted} />
        </Pressable>
      </View>

      <FlatList
        horizontal
        data={module.items as readonly unknown[]}
        keyExtractor={(item) => `${module.kind}:${cardTarget(module, item)}`}
        renderItem={renderItem}
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={[
          styles.carousel,
          // From metrics, not from the stylesheet: the inset is what leaves the
          // next card peeking, and it is derived from the same numbers the cards
          // are sized with. A stylesheet constant here would drift from them.
          { paddingHorizontal: metrics.edgeInset, gap: metrics.gap }
        ]}
        // §16: bounded work per frame, and no measurement pass — every card is
        // exactly one stride wide. Three, not more: at this size the viewport
        // holds one card and a peek, so three is the visible pair plus a card of
        // runway. This bounds *layout*; the number of decoders is bounded
        // separately and much harder, at exactly one, by `active`.
        initialNumToRender={3}
        windowSize={3}
        maxToRenderPerBatch={3}
        removeClippedSubviews
        getItemLayout={(_data, index) => ({
          length: metrics.stride,
          offset: metrics.stride * index,
          index
        })}
        // §3: one card per swipe, landing with the next one peeking. `start`
        // alignment keeps the settled card flush against the edge inset, which is
        // what makes the peek a constant rather than something that drifts by a
        // few points every card.
        snapToInterval={metrics.stride}
        snapToAlignment="start"
        decelerationRate="fast"
        // Without this a fast flick carries past several cards and lands wherever
        // momentum ran out, which reads as the carousel ignoring the gesture.
        disableIntervalMomentum
        viewabilityConfig={viewabilityConfig}
        onViewableItemsChanged={onViewableItemsChanged}
        // §11: the vertical feed keeps the gesture unless the drag is clearly
        // horizontal, which is RN's default for a nested horizontal list. Stated
        // rather than assumed, so a later `nestedScrollEnabled` change is a
        // visible edit.
        directionalLockEnabled
      />
    </View>
  );
}

/**
 * The row while its module is still loading.
 *
 * Rendered instead of nothing so the feed does not reflow when suggestions
 * arrive — §3 requires stable placement, and a row that appears mid-scroll
 * moves everything under the user's thumb.
 */
export function DiscoveryRowSkeleton({ label }: { label?: string }) {
  return (
    <View style={styles.row} testID="discovery-row-skeleton" accessibilityRole="progressbar">
      <View style={styles.header}>
        <View style={styles.skeletonTitle} />
      </View>
      <View style={styles.skeletonBody}>
        <ActivityIndicator color={colors.accent} />
        {label ? <Text style={styles.skeletonLabel}>{label}</Text> : null}
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  // No horizontal margin, no border, no background: §2. The row is a heading and
  // a carousel, not a panel drawn around them — the surface behind the cards is
  // the feed's own, so the only thing between the screen edge and the media is
  // the carousel's edge inset.
  row: {
    marginBottom: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    // Matched to the carousel's edge inset so the title lines up with the left
    // edge of the first card instead of hanging off it.
    paddingHorizontal: DISCOVERY_EDGE_INSET,
    marginBottom: logiNexus.spacing.md,
    gap: logiNexus.spacing.sm
  },
  headerIcon: { marginRight: logiNexus.spacing.xs },
  headerTitle: { ...logiNexus.typography.home.sectionLabel, color: colors.text, flex: 1 },
  headerAction: { ...logiNexus.typography.home.buttonSecondary, color: colors.accent },
  headerDismiss: { paddingLeft: logiNexus.spacing.xs },
  // Padding and gap come from metrics at the call site; this holds only what
  // does not depend on the device.
  carousel: { alignItems: "flex-start" },
  caption: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    padding: logiNexus.spacing.md,
    gap: 2
  },
  captionTitle: { ...logiNexus.typography.home.cardMetric, color: colors.text },
  captionMeta: { ...logiNexus.typography.home.cardMetadata, color: colors.muted },
  // Sits over the media so the user can see the preview is silent by choice
  // rather than by failure. Decorative: the sound can never be turned on.
  mutedBadge: {
    position: "absolute",
    top: logiNexus.spacing.sm,
    right: logiNexus.spacing.sm,
    width: 24,
    height: 24,
    borderRadius: logiNexus.radius.circular,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(5, 9, 16, 0.55)"
  },
  cardAction: {
    marginTop: logiNexus.spacing.sm,
    paddingVertical: logiNexus.spacing.sm,
    paddingHorizontal: logiNexus.spacing.md,
    borderRadius: logiNexus.radius.capsule,
    backgroundColor: colors.signalDim,
    borderWidth: 1,
    borderColor: colors.accent,
    alignItems: "center"
  },
  cardActionMuted: { backgroundColor: "transparent", borderColor: colors.border },
  cardActionText: { ...logiNexus.typography.home.buttonSecondary, color: colors.accent },
  cardActionTextMuted: { color: colors.muted },
  skeletonTitle: {
    height: 12,
    width: 140,
    borderRadius: logiNexus.radius.small,
    backgroundColor: colors.surfaceRaised
  },
  skeletonBody: { paddingHorizontal: DISCOVERY_EDGE_INSET, alignItems: "flex-start" },
  skeletonLabel: { ...logiNexus.typography.home.cardMetadata, color: colors.muted, marginTop: logiNexus.spacing.sm }
}));
