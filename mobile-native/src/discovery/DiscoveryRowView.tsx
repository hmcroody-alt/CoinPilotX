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
 */
import { Ionicons } from "@expo/vector-icons";
import { memo, useCallback, useEffect, useRef } from "react";
import { ActivityIndicator, FlatList, Image, Pressable, Text, View, ViewToken } from "react-native";
import { useTranslation } from "../i18n/I18nContext";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import { trackDiscoveryEvent } from "./analytics";
import {
  DiscoveryModule,
  DiscoveryModuleKind,
  GroupSuggestion,
  PersonSuggestion,
  ReelSuggestion,
  StatusSuggestion
} from "./discoveryRows";

/** Fixed so `getItemLayout` can skip measurement — §16. */
const CARD_WIDTH = 148;
const CARD_GAP = logiNexus.spacing.md;
const CARD_STRIDE = CARD_WIDTH + CARD_GAP;

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
};

/* ------------------------------------------------------------------ *
 * Cards
 * ------------------------------------------------------------------ */

function CardShell({
  onPress,
  accessibilityLabel,
  testID,
  children
}: {
  onPress: () => void;
  accessibilityLabel: string;
  testID: string;
  children: React.ReactNode;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      style={styles.card}
      onPress={onPress}
    >
      {children}
    </Pressable>
  );
}

/** A poster, or the first two letters of whatever the card is about. */
function CardMedia({ uri, fallback, round }: { uri?: string | null; fallback: string; round?: boolean }) {
  const shape = round ? styles.mediaRound : styles.mediaSquare;
  if (uri) return <Image source={{ uri }} style={[styles.media, shape]} accessibilityIgnoresInvertColors />;
  return (
    <View style={[styles.media, shape, styles.mediaEmpty]}>
      <Text style={styles.mediaFallback}>{fallback.slice(0, 2).toUpperCase() || "PS"}</Text>
    </View>
  );
}

const ReelCard = memo(function ReelCard({
  reel,
  onPress,
  formatViews
}: {
  reel: ReelSuggestion;
  onPress: () => void;
  formatViews: (count: number) => string;
}) {
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={reel.title || reel.authorName || String(reel.reelId)}
      testID={`discovery-card-reels-${reel.reelId}`}
    >
      <CardMedia uri={reel.posterUrl} fallback={reel.title || reel.authorName || "PS"} />
      <Text style={styles.cardTitle} numberOfLines={2}>
        {reel.title || reel.authorName || ""}
      </Text>
      {typeof reel.viewCount === "number" ? (
        <Text style={styles.cardMeta} numberOfLines={1}>
          {formatViews(reel.viewCount)}
        </Text>
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
  pendingLabel
}: {
  person: PersonSuggestion;
  pending: boolean;
  onPress: () => void;
  onAdd: () => void;
  addLabel: string;
  pendingLabel: string;
}) {
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={person.displayName || person.username}
      testID={`discovery-card-people-${person.profileKey}`}
    >
      <CardMedia uri={person.avatarUrl} fallback={person.displayName || person.username} round />
      <Text style={styles.cardTitle} numberOfLines={1}>
        {person.displayName || person.username}
      </Text>
      {person.rank ? (
        <Text style={styles.cardMeta} numberOfLines={1}>
          {person.rank}
        </Text>
      ) : null}
      {/* A nested Pressable: RN does not bubble a handled press to the card, so
          "Add friend" cannot also navigate — §11. */}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${pending ? pendingLabel : addLabel}, ${person.displayName || person.username}`}
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
    </CardShell>
  );
});

const StatusCard = memo(function StatusCard({
  status,
  onPress,
  newLabel,
  seenLabel
}: {
  status: StatusSuggestion;
  onPress: () => void;
  newLabel: string;
  seenLabel: string;
}) {
  return (
    <CardShell
      onPress={onPress}
      accessibilityLabel={status.authorName || status.title || String(status.statusId)}
      testID={`discovery-card-statuses-${status.statusId}`}
    >
      <View style={!status.seen ? styles.statusRing : undefined}>
        <CardMedia uri={status.previewUrl} fallback={status.authorName || status.title || "PS"} round />
      </View>
      <Text style={styles.cardTitle} numberOfLines={1}>
        {status.authorName || status.title}
      </Text>
      <Text style={styles.cardMeta} numberOfLines={1}>
        {status.seen ? seenLabel : newLabel}
      </Text>
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
  formatMembers
}: {
  group: GroupSuggestion;
  joined: boolean;
  onPress: () => void;
  onJoin: () => void;
  joinLabel: string;
  joinedLabel: string;
  formatMembers: (count: number) => string;
}) {
  return (
    <CardShell onPress={onPress} accessibilityLabel={group.name} testID={`discovery-card-groups-${group.slug}`}>
      <CardMedia uri={group.coverUrl} fallback={group.name} />
      <Text style={styles.cardTitle} numberOfLines={2}>
        {group.name}
      </Text>
      {typeof group.memberCount === "number" ? (
        <Text style={styles.cardMeta} numberOfLines={1}>
          {formatMembers(group.memberCount)}
        </Text>
      ) : null}
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

  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 60 }).current;
  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    viewableItems.forEach((token) => {
      if (token.index === null || token.index === undefined) return;
      trackDiscoveryEvent({
        name: "card_impression",
        kind: moduleRef.current.kind,
        slot: slotRef.current,
        target: cardTarget(moduleRef.current, token.item),
        position: token.index
      });
    });
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
      const tap = (name: "card_tap" | "reel_opened" | "status_opened") =>
        trackDiscoveryEvent({ name, kind: module.kind, slot, target, position: index });

      switch (module.kind) {
        case "reels": {
          const reel = item as ReelSuggestion;
          return (
            <ReelCard
              reel={reel}
              formatViews={formatViews}
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
      formatMembers,
      formatViews,
      joinedGroupSlugs,
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
        contentContainerStyle={styles.carousel}
        // §16: bounded work per frame, and no measurement pass — every card is
        // exactly CARD_STRIDE wide.
        initialNumToRender={4}
        windowSize={3}
        maxToRenderPerBatch={4}
        removeClippedSubviews
        getItemLayout={(_data, index) => ({ length: CARD_STRIDE, offset: CARD_STRIDE * index, index })}
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
      <View style={styles.carousel}>
        <ActivityIndicator color={colors.accent} />
        {label ? <Text style={styles.skeletonLabel}>{label}</Text> : null}
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  row: {
    marginHorizontal: logiNexus.spacing.lg,
    marginBottom: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md,
    borderRadius: logiNexus.radius.card,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: logiNexus.spacing.lg,
    marginBottom: logiNexus.spacing.md,
    gap: logiNexus.spacing.sm
  },
  headerIcon: { marginRight: logiNexus.spacing.xs },
  headerTitle: { ...logiNexus.typography.home.sectionLabel, color: colors.text, flex: 1 },
  headerAction: { ...logiNexus.typography.home.buttonSecondary, color: colors.accent },
  headerDismiss: { paddingLeft: logiNexus.spacing.xs },
  carousel: {
    paddingHorizontal: logiNexus.spacing.lg,
    gap: CARD_GAP,
    alignItems: "flex-start"
  },
  card: { width: CARD_WIDTH },
  media: { width: CARD_WIDTH, height: CARD_WIDTH, backgroundColor: colors.surfaceRaised },
  mediaSquare: { borderRadius: logiNexus.radius.medium },
  mediaRound: { borderRadius: logiNexus.radius.circular },
  mediaEmpty: { alignItems: "center", justifyContent: "center" },
  mediaFallback: { ...logiNexus.typography.title, color: colors.muted },
  statusRing: {
    borderRadius: logiNexus.radius.circular,
    borderWidth: 2,
    borderColor: colors.accent,
    padding: 2
  },
  cardTitle: {
    ...logiNexus.typography.home.cardMetric,
    color: colors.text,
    marginTop: logiNexus.spacing.sm
  },
  cardMeta: { ...logiNexus.typography.home.cardMetadata, color: colors.muted, marginTop: 2 },
  cardAction: {
    marginTop: logiNexus.spacing.sm,
    paddingVertical: logiNexus.spacing.sm,
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
  skeletonLabel: { ...logiNexus.typography.home.cardMetadata, color: colors.muted, marginTop: logiNexus.spacing.sm }
}));
