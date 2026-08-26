import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View
} from "react-native";
import { listMyPages, pageTypeLabel, PulsePage } from "../api/pages";
import { LockedLayer } from "../components/presence/LockedLayer";
import { isPresenceSurfaceReady } from "../core/launchReadiness";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { presenceAccent } from "../theme/presenceAccent";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Presence">;

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
 *
 * ## The launch boundary
 *
 * This screen is where the public build stops. Creation and management are not
 * finished, so `core/launchReadiness` marks them locked and the controls that
 * led there render as {@link LockedLayer}: same position, same words, greyed,
 * badged with their state, and they expand instead of navigating. The page
 * itself stays fully live — it loads, refreshes, and lists real presences.
 *
 * Two doors are deliberately left open, because neither leads anywhere
 * unfinished. **View** opens the presence's own public page, which is already
 * reachable from search and from any post — locking it here would stop nothing
 * and would take an owner's live page away from the owner alone. **Business OS**
 * is a shipped subsystem with its own surface; this hub is one of its entrances,
 * not its author.
 */
export function PresenceHubScreen({ navigation }: Props) {
  const [pages, setPages] = useState<PulsePage[]>([]);
  const [state, setState] = useState<"loading" | "loaded" | "error">("loading");
  const [refreshing, setRefreshing] = useState(false);

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
        {isPresenceSurfaceReady("artistPresenceCreate") ? (
          <Pressable
            accessibilityRole="button"
            style={styles.offerButton}
            onPress={() => navigation.navigate("PageCreate", { flavor: "artist" })}
          >
            <Text style={styles.offerButtonText}>Create Artist Presence</Text>
          </Pressable>
        ) : (
          <LockedLayer
            label="Create Artist Presence"
            surface="artistPresenceCreate"
            testID="locked-artist-create"
          />
        )}
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
        {isPresenceSurfaceReady("businessPresenceCreate") ? (
          <Pressable
            accessibilityRole="button"
            style={styles.offerButton}
            onPress={() => navigation.navigate("PageCreate", { flavor: "business" })}
          >
            <Text style={styles.offerButtonText}>Create Business Presence</Text>
          </Pressable>
        ) : (
          <LockedLayer
            label="Create Business Presence"
            surface="businessPresenceCreate"
            testID="locked-business-create"
          />
        )}
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
                {isPresenceSurfaceReady("presenceManage") ? (
                  <Pressable
                    accessibilityRole="button"
                    style={styles.presenceAction}
                    onPress={() => navigation.navigate("PagesHub", { focusPageId: page.id })}
                  >
                    <Text style={styles.presenceActionText}>Manage</Text>
                  </Pressable>
                ) : (
                  <LockedLayer
                    label="Manage"
                    surface="presenceManage"
                    variant="compact"
                    testID={`locked-manage-${page.id}`}
                  />
                )}
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
                {hasBusinessOs(page) ? (
                  <Pressable
                    accessibilityRole="button"
                    style={styles.presenceAction}
                    onPress={() => navigation.navigate("BusinessOs", { title: page.name })}
                  >
                    <Text style={styles.presenceActionText}>Business OS</Text>
                  </Pressable>
                ) : null}
              </View>
            </View>
          );
        })
      )}

      {isPresenceSurfaceReady("presenceCreate") ? (
        <Pressable
          accessibilityRole="button"
          style={styles.createNew}
          onPress={() => navigation.navigate("PageCreate", undefined)}
        >
          <Text style={styles.createNewText}>+ Create New</Text>
        </Pressable>
      ) : (
        <LockedLayer label="+ Create New" surface="presenceCreate" testID="locked-create-new" />
      )}
    </ScrollView>
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
  createNew: {
    alignItems: "center",
    borderColor: presenceTheme.tealBorder,
    borderRadius: 10,
    borderStyle: "dashed",
    borderWidth: 1,
    marginTop: 16,
    minHeight: 44,
    justifyContent: "center"
  },
  createNewText: {
    color: presenceTheme.teal,
    fontSize: 14,
    fontWeight: "900"
  },
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
    marginTop: 12,
    minHeight: 44,
    justifyContent: "center"
  },
  offerButtonText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
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
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 12
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
