/**
 * Everything Home needs to show discovery rows, in one hook.
 *
 * ## Why a hook rather than more of `HomeScreen`
 *
 * `HomeScreen.tsx` is already three thousand lines and owns the feed, the status
 * rail, the composer, ads, the drawer, live polling and the spatial pager. Adding
 * module loading, dismissal persistence, rotation, per-tap relationship state and
 * eight navigation handlers to it would make the discovery feature unreviewable
 * and would make every future Home change touch discovery by accident. Home ends
 * up with three edits: call this, compose the rows, render the branch.
 *
 * ## What "off" means here
 *
 * With `homeDiscoveryEnabled()` false, this hook fetches nothing, subscribes to
 * nothing and returns an empty module list, so `injectDiscoveryRows` returns the
 * input array and Home renders precisely the rows `injectAds` produced. That is
 * the §15 rollback guarantee, and it holds without Home knowing anything about
 * it — the flag check lives here and in `sources.ts`, not at the call site.
 *
 * ## Where the destinations come from
 *
 * Every handler navigates with an identifier the adapter already proved
 * non-empty, and every route is one that exists today:
 *
 *   - a reel goes to the Reels **tab** with its payload staged in the transfer
 *     slot, so the player's first frame is the reel in the thumbnail (§4);
 *   - a status goes to `StatusDetail` with that status's id (§6);
 *   - a group goes to `GroupDetail` with that group's slug (§7);
 *   - a person goes to `ProfileDetail` through `resolveProfileTarget`, which is
 *     what the friend payload can actually address (§5).
 *
 * "See all" is deliberately absent for People: there is no friends screen in the
 * mobile app, and §9's rule against inventing inactive destinations is a rule
 * about taps, not about topics specifically.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { sendFriendRequest } from "../api/friends";
import { joinGroup } from "../api/groups";
import { profileNavigationParams, resolveProfileTarget } from "../api/profileTarget";
import type { RootStackParamList } from "../navigation/types";
import { trackDiscoveryEvent, resetDiscoveryImpressions } from "./analytics";
import type {
  DiscoveryModule,
  DiscoveryModuleKind,
  GroupSuggestion,
  PersonSuggestion,
  ReelSuggestion,
  StatusSuggestion
} from "./discoveryRows";
import { dismissModule, dismissPerson, loadDismissedModules, loadDismissedPeople } from "./dismissals";
import { homeDiscoveryEnabled } from "./flags";
import { stageReelTransfer } from "./reelTransfer";
import { loadDiscoveryModules } from "./sources";

type Navigation = NativeStackNavigationProp<RootStackParamList>;

export type UseHomeDiscoveryOptions = {
  navigation: Navigation;
  /** Reel ids already on Home, so a suggestion never duplicates a visible card. */
  excludeReelIds?: ReadonlySet<number>;
  /** Status ids already on Home's rail — §3 says do not re-suggest what is on screen. */
  excludeStatusIds?: ReadonlySet<number>;
  /** False while signed out: §13 requires suggestions respect auth state. */
  enabled?: boolean;
  /** Bumped by Home on pull-to-refresh. */
  refreshToken?: number;
};

export function useHomeDiscovery({
  navigation,
  excludeReelIds,
  excludeStatusIds,
  enabled = true,
  refreshToken = 0
}: UseHomeDiscoveryOptions) {
  const active = enabled && homeDiscoveryEnabled();

  const [modules, setModules] = useState<DiscoveryModule[]>([]);
  const [dismissed, setDismissed] = useState<ReadonlySet<DiscoveryModuleKind>>(new Set());
  const [dismissedPeople, setDismissedPeople] = useState<ReadonlySet<string>>(new Set());
  const [pendingFriendKeys, setPendingFriendKeys] = useState<ReadonlySet<string>>(new Set());
  const [joinedGroupSlugs, setJoinedGroupSlugs] = useState<ReadonlySet<string>>(new Set());
  const [loading, setLoading] = useState(false);

  /**
   * Advanced on refresh so the same kind is not pinned to the same scroll depth
   * forever. Not randomised: §3 wants stable placement within a session, and a
   * random offset would reshuffle on every unrelated re-render.
   */
  const rotationOffset = refreshToken;

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    loadDismissedModules()
      .then((set) => {
        if (!cancelled) setDismissed(set);
      })
      .catch(() => undefined);
    loadDismissedPeople()
      .then((set) => {
        if (!cancelled) setDismissedPeople(set);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [active]);

  // Held in a ref so a changing exclusion set does not re-trigger the fetch on
  // every feed page — the sets change identity whenever Home appends posts.
  const excludeReelRef = useRef(excludeReelIds);
  const excludeStatusRef = useRef(excludeStatusIds);
  excludeReelRef.current = excludeReelIds;
  excludeStatusRef.current = excludeStatusIds;

  useEffect(() => {
    if (!active) {
      setModules([]);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    resetDiscoveryImpressions();
    loadDiscoveryModules({
      excludeReelIds: excludeReelRef.current,
      excludeStatusIds: excludeStatusRef.current
    })
      .then((result) => {
        if (cancelled) return;
        setModules(result.modules);
        // §14: a module that failed is a recommendation failure with a reason,
        // not silence.
        for (const [kind, reason] of Object.entries(result.failures)) {
          trackDiscoveryEvent({
            name: "module_impression",
            kind: kind as DiscoveryModuleKind,
            slot: -1,
            reason
          });
        }
      })
      .catch(() => {
        if (!cancelled) setModules([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      // §16: an obsolete load must not overwrite the current one.
      cancelled = true;
    };
  }, [active, refreshToken]);

  const handleDismiss = useCallback((kind: DiscoveryModuleKind) => {
    setDismissed(dismissModule(kind));
  }, []);

  const handleRemovePerson = useCallback((person: PersonSuggestion) => {
    setDismissedPeople(dismissPerson(person.profileKey));
  }, []);

  /**
   * Suggestions the viewer removed, taken out of the row without a refetch.
   *
   * Applied here rather than in `sources` because a removal must land on the next
   * render: re-running the fetch would leave the card on screen until the network
   * answered, and the graph would hand back the same person anyway — the server
   * has no record of a client-side removal. Identity is preserved when nothing is
   * dismissed so the common case does not rebuild the module array at all.
   */
  const visibleModules = useMemo(() => {
    if (!dismissedPeople.size) return modules;
    return modules
      .map((module) => {
        if (module.kind !== "people") return module;
        const items = module.items.filter((person) => !dismissedPeople.has(person.profileKey));
        if (items.length === module.items.length) return module;
        return { ...module, items };
      })
      // A row whose every suggestion was removed is an empty carousel under a
      // heading, so it goes away until the next load brings new people.
      .filter((module) => module.kind !== "people" || module.items.length > 0);
  }, [dismissedPeople, modules]);

  /**
   * Open the exact reel.
   *
   * Neighbours are not staged, and that is deliberate: `ReelsScreen` seeds
   * `[tappedReel]` before its first commit and then runs its normal lane load,
   * which appends the rest of the page behind it. So the reel under the thumb is
   * on screen in frame one and swiping works as soon as the page lands. Staging
   * this row's other suggestions instead would pin a *different* ordering into
   * the player than the lane it belongs to, which is how a reel the user did not
   * tap ends up one swipe away.
   */
  const handleOpenReel = useCallback(
    (reel: ReelSuggestion) => {
      const nonce = reel.source ? stageReelTransfer(reel.source) : null;
      // The **tab** instance, not the root-stack one: only the tab sits inside
      // `BottomNavVisibilityProvider`, and §4 makes the navigator's response to
      // swipes part of the requirement, not a detail. Opening the stack copy
      // plays the right reel under a dock that has stopped listening.
      navigation.navigate("Tabs", {
        screen: "Reels",
        params: {
          reelId: reel.reelId,
          title: reel.title || undefined,
          reelTransferNonce: nonce ?? undefined
        }
      });
    },
    [navigation]
  );

  const handleOpenStatus = useCallback(
    (status: StatusSuggestion) => {
      navigation.navigate("StatusDetail", { statusId: status.statusId, title: status.authorName || undefined });
    },
    [navigation]
  );

  const handleOpenGroup = useCallback(
    (group: GroupSuggestion) => {
      navigation.navigate("GroupDetail", { groupSlug: group.slug, title: group.name });
    },
    [navigation]
  );

  const handleOpenPerson = useCallback(
    (person: PersonSuggestion) => {
      // `profileNavigationParams` is the same builder every other profile tap in
      // Home uses, and it returns `undefined` for an unaddressable target. Doing
      // nothing is right here: the alternative is pushing `ProfileDetail` with a
      // key the resolver already rejected, which lands on "Profile not found".
      const params = profileNavigationParams(
        resolveProfileTarget({
          profileKey: person.profileKey,
          username: person.username,
          publicPlayerId: person.publicPlayerId || undefined,
          source: "HOME_DISCOVERY"
        }),
        person.displayName || person.username
      );
      if (params) navigation.navigate("ProfileDetail", params);
    },
    [navigation]
  );

  const handleAddFriend = useCallback((person: PersonSuggestion) => {
    // Optimistic, and never reverted on failure: the server owns duplicate
    // suppression, so the honest states are "asked" and "not asked yet". Flipping
    // the button back would invite a second request for one that may well have
    // landed.
    setPendingFriendKeys((current) => {
      if (current.has(person.profileKey)) return current;
      const next = new Set(current);
      next.add(person.profileKey);
      return next;
    });
    sendFriendRequest(person.profileKey).catch(() => undefined);
  }, []);

  const handleJoinGroup = useCallback((group: GroupSuggestion) => {
    setJoinedGroupSlugs((current) => {
      if (current.has(group.slug)) return current;
      const next = new Set(current);
      next.add(group.slug);
      return next;
    });
    joinGroup(group.slug).catch(() => undefined);
  }, []);

  /**
   * "See all" goes to the tab, because that is where these three surfaces live —
   * `Status` and `Groups` are only declared on `AppTabParamList`, and Reels wants
   * the tab for the same navigator reason as an exact-reel tap.
   */
  const handleSeeAll = useCallback(
    (kind: DiscoveryModuleKind) => {
      if (kind === "reels") navigation.navigate("Tabs", { screen: "Reels" });
      else if (kind === "statuses") navigation.navigate("Tabs", { screen: "Status" });
      else if (kind === "groups") navigation.navigate("Tabs", { screen: "Groups" });
    },
    [navigation]
  );

  /** `undefined` for kinds with no destination, so the control is not rendered. */
  const seeAllFor = useCallback(
    (kind: DiscoveryModuleKind) =>
      kind === "reels" || kind === "statuses" || kind === "groups" ? handleSeeAll : undefined,
    [handleSeeAll]
  );

  const actions = useMemo(
    () => ({
      onOpenReel: handleOpenReel,
      onOpenStatus: handleOpenStatus,
      onOpenGroup: handleOpenGroup,
      onOpenPerson: handleOpenPerson,
      onAddFriend: handleAddFriend,
      onRemovePerson: handleRemovePerson,
      onJoinGroup: handleJoinGroup,
      onDismiss: handleDismiss
    }),
    [
      handleAddFriend,
      handleDismiss,
      handleJoinGroup,
      handleOpenGroup,
      handleOpenPerson,
      handleOpenReel,
      handleOpenStatus,
      handleRemovePerson
    ]
  );

  return {
    /** Empty whenever the feature is off — Home needs no flag check of its own. */
    modules: active ? visibleModules : [],
    dismissed,
    rotationOffset,
    loading,
    pendingFriendKeys,
    joinedGroupSlugs,
    actions,
    seeAllFor
  };
}
