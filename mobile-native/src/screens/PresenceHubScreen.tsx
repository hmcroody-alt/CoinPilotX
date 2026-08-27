import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View
} from "react-native";
import { listMyPages, pageTypeLabel, PulsePage } from "../api/pages";
import { ComingSoonSheet } from "../launch/ComingSoonSheet";
import { presenceModuleId, readinessOf } from "../launch/readiness";
import { useLaunchCopy, useLaunchGate, useLaunchMotionEnabled } from "../launch/useLaunchGate";
import { useLockedMotion } from "../launch/lockedMotion";
import { useIsFocusedIfNavigated } from "../navigation/useIsFocusedIfNavigated";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { presenceAccent } from "../theme/presenceAccent";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Presence">;

/**
 * The Coming Soon body this screen asks for, in place of the shared one.
 *
 * The generic sentence ("This feature is preparing for launch") is written
 * about a single module. Presence closes three doors at once onto the same
 * unfinished workflow, so it says what is actually coming — more Presence
 * capability — rather than repeating a per-feature promise three times.
 *
 * It is a catalog key, not a string: `ComingSoonSheet` translates it, and the
 * parity gate holds it to all eleven locales like every other line of copy.
 */
const PRESENCE_COMING_SOON_BODY = "commerce:launch.comingSoonBodyPresence";

/**
 * Whether this presence has a Business OS to go to, as decided by the server.
 *
 * This used to be two frozensets in this file — a third copy of the server's
 * `BUSINESS_PAGE_TYPES`, whose own comment says a second copy is a second
 * thing to forget when a page type is added. It had already drifted: anything
 * the two sets did not name fell through to "business", so an OTHER presence
 * was offered a Business OS door the server does not think it has. Nothing
 * could catch that, because the divergence was between a Python constant and a
 * TypeScript literal.
 *
 * `business_os_capable` is now sent with the page. An older server that omits
 * it withholds the button, which is the safe way round: a missing door is a
 * shorter card, a door onto nothing is the thing this whole mission is about.
 */
function hasBusinessOs(page: PulsePage): boolean {
  return page.business_os_capable === true;
}

/**
 * The modules this presence could have and does not yet, named.
 *
 * Read straight off the server's `modules` map rather than re-derived here.
 * That map is `module_availability()` — the same answer that decides which
 * tabs a visitor sees — so this line cannot drift from what the presence
 * actually shows. A module that is always backed (`posts`, `about`, `home`) is
 * never `false`, so it never appears; only work that is genuinely outstanding
 * does.
 *
 * An older server that omits `modules` yields nothing at all. A silent card is
 * a better wrong answer than one that tells the owner their music is missing
 * because a field did not arrive.
 */
function pendingModules(page: PulsePage): string[] {
  const modules = page.modules || {};
  return Object.keys(modules)
    .filter((tab) => !modules[tab])
    .map((tab) => tab.charAt(0).toUpperCase() + tab.slice(1));
}

/**
 * Presence Home — the Profile OS destination for professional identities.
 *
 * "Presence" is PulseSoc's word for an artist, business, brand or organization
 * identity controlled by one or more authorized members. Underneath it is the
 * canonical Page OS (`/api/pages/*`): this screen creates and lists pages, it
 * does not own a second backend, social graph or permission engine.
 *
 * The list is deliberately lightweight: `listMyPages()` returns summary rows
 * (name, handle, type, role, status) — no analytics, no manage views. Deeper
 * data loads only when a Presence's management is opened.
 */
export function PresenceHubScreen({ navigation }: Props) {
  const [pages, setPages] = useState<PulsePage[]>([]);
  const [state, setState] = useState<"loading" | "loaded" | "error">("loading");
  const [refreshing, setRefreshing] = useState(false);
  // Above the loading/error returns: these are hooks and the returns are not.
  const gate = useLaunchGate();
  const motionEnabled = useLaunchMotionEnabled();
  const screenActive = useIsFocusedIfNavigated();

  const load = useCallback(async () => {
    try {
      setPages(await listMyPages());
      setState("loaded");
    } catch {
      setState("error");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Refresh when returning from creation so a new Presence appears at once.
    const unsubscribe = navigation.addListener("focus", load);
    return unsubscribe;
  }, [load, navigation]);

  if (state === "loading") {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={presenceTheme.teal} size="large" />
      </View>
    );
  }

  if (state === "error") {
    return (
      <View style={[styles.root, styles.center]}>
        <Text style={styles.errorText}>Your presences could not be loaded.</Text>
        <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => { setState("loading"); load(); }}>
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <>
      <ScrollView
        style={styles.root}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              load();
            }}
            tintColor={presenceTheme.teal}
          />
        }
      >
        <Text style={styles.header}>PRESENCE</Text>
        <Text style={styles.subtitle}>Your public identities on PulseSoc.</Text>
        <Text style={styles.supporting}>
          Create and manage identities for your art, brand, organization, or business.
          Your personal account stays exactly as it is.
        </Text>

        <View style={styles.offerCard}>
          <Text style={styles.offerTitle}>Artist Presence</Text>
          <Text style={styles.offerLead}>Build your official artist home.</Text>
          {/*
            Was "Music · Releases · Videos · Events · Fans · Store · Insights".
            Three of those were not modules at all — there is no Releases, no
            Fans, and the management view calls its analytics Overview, not
            Insights — and the tab is Merch rather than Store. A pitch that names
            things the product does not have is where a hollow surface starts.
            The set also genuinely varies by page type (a public figure gets
            videos but not music), so it says so instead of promising a fixed
            list it cannot keep.
          */}
          <Text style={styles.offerList}>
            Posts, music, videos, events and merch — each one pointed at the system that
            already holds it, never a second copy. Which of them your page shows depends on
            the type you pick next.
          </Text>
          <PresenceCreateButton
            id={presenceModuleId("createArtist")}
            label="Create Artist Presence"
            index={0}
            motionEnabled={motionEnabled}
            screenActive={screenActive}
            style={styles.offerButton}
            lockedStyle={styles.offerButtonLocked}
            textStyle={styles.offerButtonText}
            lockedTextStyle={styles.offerButtonTextLocked}
            badgeStyle={styles.createBadge}
            onPress={() =>
              gate.open(
                presenceModuleId("createArtist"),
                "Artist Presence",
                () => navigation.navigate("PageCreate", { flavor: "artist" }),
                PRESENCE_COMING_SOON_BODY
              )
            }
          />
        </View>

        <View style={styles.offerCard}>
          <Text style={styles.offerTitle}>Business Presence</Text>
          <Text style={styles.offerLead}>Build your official business home.</Text>
          {/*
            Was "Products · Services · Store · Marketplace · Events · Customers ·
            Insights". Services was removed as a module on purpose — Marketplace
            already carries service and booking listings, so a separate one would
            be a second commerce backend — and there is no Customers module and
            no section called Insights.
          */}
          <Text style={styles.offerList}>
            Your shop from Marketplace, your dates from Business OS, your campaigns from
            Ads — connected to what you already run rather than rebuilt here. Which of them
            your page shows depends on the type you pick next.
          </Text>
          <PresenceCreateButton
            id={presenceModuleId("createBusiness")}
            label="Create Business Presence"
            index={1}
            motionEnabled={motionEnabled}
            screenActive={screenActive}
            style={styles.offerButton}
            lockedStyle={styles.offerButtonLocked}
            textStyle={styles.offerButtonText}
            lockedTextStyle={styles.offerButtonTextLocked}
            badgeStyle={styles.createBadge}
            onPress={() =>
              gate.open(
                presenceModuleId("createBusiness"),
                "Business Presence",
                () => navigation.navigate("PageCreate", { flavor: "business" }),
                PRESENCE_COMING_SOON_BODY
              )
            }
          />
        </View>

        <Text style={styles.sectionTitle}>YOUR PRESENCES</Text>

        {!pages.length ? (
          <Text style={styles.empty}>You haven't created a Presence yet.</Text>
        ) : (
          pages.map((page) => {
            const pending = pendingModules(page);
            /*
              The card is drawn in the presence's own colour, from its type.
              This list is the one place a member holds their presences side by
              side, and the spine down the left is what tells the restaurant from
              the artist page before either name is read. The badges below are
              left out of it deliberately: Public and Verified are claims about
              state and trust, and they have to mean the same thing on every card.
            */
            const tone = presenceAccent(page.page_type);
            return (
              <View
                key={page.id}
                testID={`presence-card-${page.id}`}
                style={[styles.presenceCard, { borderLeftColor: tone.base }]}
              >
                <View style={styles.presenceIdentity}>
                  {page.avatar_url ? (
                    <Image
                      source={{ uri: page.avatar_url }}
                      style={[styles.presenceAvatar, { borderColor: tone.border }]}
                    />
                  ) : (
                    <View
                      style={[
                        styles.presenceAvatar,
                        styles.presenceAvatarFallback,
                        { backgroundColor: tone.fill, borderColor: tone.border }
                      ]}
                    >
                      <Text style={[styles.presenceAvatarInitial, { color: tone.base }]}>
                        {page.name.slice(0, 1).toUpperCase()}
                      </Text>
                    </View>
                  )}
                  <View style={styles.presenceMetaBlock}>
                    <Text style={styles.presenceName}>{page.name}</Text>
                    <Text style={styles.presenceMeta}>
                      {pageTypeLabel(page.page_type)} · {page.followers_count === 1 ? "1 follower" : `${page.followers_count} followers`}
                      {" · "}
                      {page.posts_count === 1 ? "1 post" : `${page.posts_count} posts`}
                    </Text>
                    {/* Badges reflect real server state only: status straight from the
                        row, Verified only when the server granted it. No inferred flags. */}
                    <View style={styles.badgeRow}>
                      <Text style={[styles.badge, page.status === "ACTIVE" ? styles.badgePublic : styles.badgeDraft]}>
                        {page.status === "ACTIVE" ? "Public" : page.status.charAt(0) + page.status.slice(1).toLowerCase()}
                      </Text>
                      {page.verified ? <Text style={[styles.badge, styles.badgeVerified]}>✓ Verified</Text> : null}
                    </View>
                  </View>
                </View>
                {/*
                  What is left to set up, measured rather than guessed. This is
                  the server's own availability map, so the card and the page
                  cannot disagree about whether the shop is connected. Nothing is
                  said when there is nothing outstanding — a card that always
                  carries a line trains people to stop reading it.
                */}
                {pending.length ? (
                  <Text style={styles.presencePending}>Not set up yet: {pending.join(", ")}</Text>
                ) : null}
                <View style={styles.presenceActions}>
                  <Pressable
                    accessibilityRole="button"
                    style={styles.presenceAction}
                    onPress={() => navigation.navigate("Page", { pageId: page.id, handle: page.handle, title: page.name })}
                  >
                    <Text style={styles.presenceActionText}>View</Text>
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    style={styles.presenceAction}
                    onPress={() => navigation.navigate("PagesHub", { focusPageId: page.id })}
                  >
                    <Text style={styles.presenceActionText}>Manage</Text>
                  </Pressable>
                  {/*
                    Business presences get a third action because there is a
                    third place to go. Artist presences used to get one labelled
                    "Insights" that navigated to `PagesHub` with the same
                    `focusPageId` as Manage — the identical destination under a
                    different word, and a word naming a section that does not
                    exist (the management view calls it Overview). Two buttons
                    doing one job is how a surface starts feeling hollow, so the
                    duplicate is gone rather than relabelled. There is no separate
                    artist subsystem to point at, and inventing a door to make
                    the two cards look symmetrical would be the same mistake in a
                    new place.
                  */}
                  {/*
                    Gated. The navigation below carries no page identifier, so
                    `BusinessOsScreen` resolves the subject as the signed-in
                    viewer and paints THEIR listings, orders and ad spend under
                    THIS presence's name. That is a wrong answer wearing a real
                    one's clothes, which is worse than a door that says it is not
                    open yet. See `presence:businessOs` in `launch/readiness.ts`.
                  */}
                  {hasBusinessOs(page) ? (
                    <PresenceAction
                      id={presenceModuleId("businessOs")}
                      label="Business OS"
                      index={0}
                      motionEnabled={motionEnabled}
                      screenActive={screenActive}
                      onPress={() =>
                        gate.open(presenceModuleId("businessOs"), "Business OS", () =>
                          navigation.navigate("BusinessOs", { title: page.name })
                        )
                      }
                    />
                  ) : null}
                </View>
              </View>
            );
          })
        )}

        <PresenceCreateButton
          id={presenceModuleId("createNew")}
          label="+ Create New"
          moduleName="New Presence"
          index={2}
          motionEnabled={motionEnabled}
          screenActive={screenActive}
          style={styles.createNew}
          lockedStyle={styles.createNewLocked}
          textStyle={styles.createNewText}
          lockedTextStyle={styles.createNewTextLocked}
          badgeStyle={styles.createBadge}
          onPress={() =>
            gate.open(
              presenceModuleId("createNew"),
              "New Presence",
              () => navigation.navigate("PageCreate", undefined),
              PRESENCE_COMING_SOON_BODY
            )
          }
        />
      </ScrollView>
      <ComingSoonSheet target={gate.target} onDismiss={gate.dismiss} />
    </>
  );
}

/**
 * A presence card action that knows its own readiness.
 *
 * Same idea as `launch/LaunchTile`, in the shape this card uses: a pill rather
 * than a grid tile. It is local to this screen rather than shared because the
 * two shapes have nothing in common but the rules, and the rules already live in
 * one place (`readiness.ts`, `useLaunchCopy`, `useLockedMotion`) — which is the
 * part that must not be duplicated.
 *
 * A locked pill keeps its size and its position among the other actions. It
 * gains the teal edge, the drift, and a "Coming Soon" / "Building" word after
 * the label so the state is legible without colour.
 */
function PresenceAction({
  id,
  label,
  index,
  motionEnabled,
  screenActive,
  onPress
}: {
  id: string;
  label: string;
  index: number;
  motionEnabled: boolean;
  screenActive: boolean;
  onPress: () => void;
}) {
  const state = readinessOf(id);
  const locked = state !== "READY";
  const { badge, accessibility } = useLaunchCopy();
  const motion = useLockedMotion({ index, active: screenActive, enabled: motionEnabled && locked });
  const a11y = accessibility(id, label);

  return (
    <Animated.View style={locked ? motion.cardStyle : undefined}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={a11y.accessibilityLabel}
        accessibilityHint={a11y.accessibilityHint}
        testID={`presence-action-${id}`}
        onPress={onPress}
        onPressIn={locked ? motion.onPressIn : undefined}
        onPressOut={locked ? motion.onPressOut : undefined}
        style={[styles.presenceAction, locked ? styles.presenceActionLocked : null]}
      >
        <Text style={styles.presenceActionText}>{label}</Text>
        {locked ? <Text style={styles.presenceActionBadge}>{badge(state)}</Text> : null}
      </Pressable>
    </Animated.View>
  );
}

/**
 * A Presence creation button that knows its own readiness.
 *
 * Same rules as `PresenceAction` above — `readinessOf`, `useLaunchCopy`,
 * `useLockedMotion`, one shared gate — in the two shapes this screen's creation
 * entries actually use: the filled offer button on the Artist and Business
 * cards, and the dashed "+ Create New" strip at the bottom. It takes the style
 * rather than owning one because those two shapes must keep looking like
 * themselves; what is shared is the behaviour, and the behaviour is not
 * duplicated.
 *
 * A locked button keeps its size, its position and its label. The brief is
 * explicit that these stay VISIBLE: the point is that a member can see the
 * shape of what is coming, so hiding them or greying them into illegibility
 * would both be the wrong answer. It gains the state as a word, because colour
 * alone cannot carry it.
 */
function PresenceCreateButton({
  id,
  label,
  moduleName,
  index,
  motionEnabled,
  screenActive,
  style,
  lockedStyle,
  textStyle,
  lockedTextStyle,
  badgeStyle,
  onPress
}: {
  id: string;
  /** What the button says. Unchanged by the gate. */
  label: string;
  /**
   * What the *sheet* and the screen reader call this door. Defaults to `label`.
   * "+ Create New" is a fine thing for a button to say and a poor thing for
   * VoiceOver to read as "+ Create New. Coming soon."
   */
  moduleName?: string;
  index: number;
  motionEnabled: boolean;
  screenActive: boolean;
  style: object;
  lockedStyle: object;
  textStyle: object;
  lockedTextStyle: object;
  badgeStyle: object;
  onPress: () => void;
}) {
  const state = readinessOf(id);
  const locked = state !== "READY";
  const { badge, accessibility } = useLaunchCopy();
  const motion = useLockedMotion({ index, active: screenActive, enabled: motionEnabled && locked });
  const a11y = accessibility(id, moduleName || label);

  return (
    <Animated.View style={locked ? motion.cardStyle : undefined}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={a11y.accessibilityLabel}
        accessibilityHint={a11y.accessibilityHint}
        testID={`presence-create-${id}`}
        onPress={onPress}
        onPressIn={locked ? motion.onPressIn : undefined}
        onPressOut={locked ? motion.onPressOut : undefined}
        style={[style, locked ? lockedStyle : null]}
      >
        <Text style={[textStyle, locked ? lockedTextStyle : null]}>{label}</Text>
        {locked ? <Text style={badgeStyle}>{badge(state)}</Text> : null}
      </Pressable>
    </Animated.View>
  );
}

const styles = createThemedStyles(() => ({
  badge: {
    borderRadius: 6,
    fontSize: 10,
    fontWeight: "800",
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  badgeDraft: {
    backgroundColor: colors.border,
    color: colors.muted
  },
  badgePublic: {
    backgroundColor: presenceTheme.tealSoft,
    color: presenceTheme.teal
  },
  badgeRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: 4
  },
  badgeVerified: {
    backgroundColor: presenceTheme.tealSoft,
    color: presenceTheme.teal
  },
  center: {
    alignItems: "center",
    gap: 12,
    justifyContent: "center",
    padding: 24
  },
  content: {
    padding: 16,
    paddingBottom: 48
  },
  /**
   * The state, in words, on a creation button. Same reasoning as
   * `presenceActionBadge`: colour alone cannot carry readiness, so the word is
   * what survives greyscale, colour blindness and a screen reader.
   */
  createBadge: {
    color: presenceTheme.teal,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  createNew: {
    alignItems: "center",
    borderColor: presenceTheme.tealBorder,
    borderRadius: 10,
    borderStyle: "dashed",
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    marginTop: 16,
    minHeight: 44,
    justifyContent: "center"
  },
  createNewLocked: {
    backgroundColor: presenceTheme.tealSoft
  },
  createNewText: {
    color: presenceTheme.teal,
    fontSize: 14,
    fontWeight: "900"
  },
  createNewTextLocked: {},
  empty: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 8,
    textAlign: "center"
  },
  errorText: {
    color: colors.muted,
    fontSize: 14,
    textAlign: "center"
  },
  header: {
    color: presenceTheme.teal,
    fontSize: 22,
    fontWeight: "900",
    letterSpacing: 2
  },
  offerButton: {
    alignItems: "center",
    backgroundColor: presenceTheme.teal,
    borderRadius: 10,
    flexDirection: "row",
    gap: 8,
    marginTop: 12,
    minHeight: 44,
    justifyContent: "center"
  },
  /**
   * Locked, this button drops the filled teal for the same soft wash every
   * other locked surface in the app wears — so a member reads "not yet" from
   * the card at a glance, before the badge word or the sheet. It keeps its
   * size and its place: the brief is that these stay visible.
   */
  offerButtonLocked: {
    backgroundColor: presenceTheme.tealSoft,
    borderColor: presenceTheme.tealBorder,
    borderWidth: 1
  },
  offerButtonText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  },
  /** The filled button's dark label is unreadable on the soft wash. */
  offerButtonTextLocked: {
    color: presenceTheme.teal
  },
  offerCard: {
    backgroundColor: colors.surface,
    borderColor: presenceTheme.tealBorder,
    borderRadius: presenceTheme.radius.card,
    borderWidth: 1,
    marginTop: 14,
    padding: 16
  },
  offerLead: {
    color: colors.text,
    fontSize: 13,
    marginTop: 4
  },
  offerList: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 6
  },
  offerTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  presenceAction: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  /**
   * The state, in words. Colour alone cannot carry readiness — greyscale, colour
   * blindness and a screen reader all have to get the same answer, and the
   * accessibility label says "Coming soon" for the last of those.
   */
  presenceActionBadge: {
    color: presenceTheme.teal,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  presenceActionLocked: {
    backgroundColor: presenceTheme.tealSoft,
    borderColor: presenceTheme.tealBorder
  },
  presenceActionText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  presenceActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  presenceAvatar: {
    borderRadius: 20,
    borderWidth: 1,
    height: 40,
    width: 40
  },
  presenceAvatarFallback: {
    alignItems: "center",
    backgroundColor: presenceTheme.tealSoft,
    justifyContent: "center"
  },
  presenceAvatarInitial: {
    color: presenceTheme.teal,
    fontSize: 16,
    fontWeight: "900"
  },
  presenceCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    // The spine. Wide enough to read down a scrolling list at a glance, and
    // the one place on this card the accent is at full strength.
    borderLeftWidth: 3,
    marginTop: 10,
    padding: 14
  },
  presenceIdentity: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  presenceMeta: {
    color: colors.muted,
    fontSize: 12
  },
  presenceMetaBlock: {
    flex: 1
  },
  presenceName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  presencePending: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 10
  },
  retryButton: {
    borderColor: presenceTheme.tealBorder,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  retryText: {
    color: presenceTheme.teal,
    fontSize: 13,
    fontWeight: "800"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  sectionTitle: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 1.5,
    marginTop: 22
  },
  subtitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700",
    marginTop: 6
  },
  supporting: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 4
  }
}));
