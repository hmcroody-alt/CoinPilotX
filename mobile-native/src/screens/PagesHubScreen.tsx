import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import {
  acceptPageInvite,
  declinePageInvite,
  getPageManageView,
  listMyPageInvites,
  listMyPages,
  PageInvite,
  PageManageView,
  pageRoleLabel,
  PageSection,
  PageStatus,
  pageTypeLabel,
  PulsePage,
  requestPageVerification,
  setPageStatus
} from "../api/pages";
import { PulseApiError } from "../api/pulseApi";
import { FeedComposer } from "../components/FeedComposer";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "PagesHub">;

/**
 * Sections whose content is *this screen*. They are not tiles — they name the
 * blocks rendered further down, so a tile for one would be a button that
 * scrolls to what you are already looking at.
 *
 * Their labels and setup copy are still taken from the server, so the words a
 * team reads about verification come from the same place that decides whether
 * verification is offered at all.
 */
const INLINE_SECTIONS = new Set(["overview", "settings", "verification"]);

/**
 * Where a ready section goes. Each destination is an existing canonical system
 * — the hub links into Marketplace, Advertising, Payments and Business OS, it
 * does not reimplement any of them.
 *
 * `content` is deliberately absent: posting as a presence opens the composer in
 * place rather than navigating, because the page you are managing is the
 * context the composer needs.
 */
const SECTION_ROUTES = {
  identity: "PageEdit",
  music: "PageConnections",
  videos: "Page",
  store: "MarketplaceManager",
  advertising: "BusinessOsAdvertising",
  business_os: "BusinessOs",
  team: "PageTeam",
  payments: "BusinessOsPayments"
} as const;

/**
 * Where a section that is *not* ready goes instead — the place that makes it
 * ready, rather than the empty destination behind it.
 *
 * A shop with nothing connected opens Connections, not an inventory screen
 * belonging to nobody. This is the whole point of the server sending `ready`:
 * the tile stays visible and says what is missing, and the tap goes somewhere
 * that can fix it.
 */
const SECTION_SETUP_ROUTES = {
  music: "PageConnections",
  store: "PageConnections",
  advertising: "PageConnections"
} as const;

/**
 * Page OS hub — every page the signed-in user belongs to, with management for
 * the selected one. Management surfaces route into the EXISTING canonical
 * systems (Advertising, Marketplace manager, Payments); this screen links,
 * it does not duplicate.
 *
 * What appears here is role-gated by the server: the manage view only carries
 * members/analytics when the caller's role allows them, and status changes are
 * owner-only server-side regardless of what the client shows.
 */
export function PagesHubScreen({ route, navigation }: Props) {
  const focusPageId = route.params?.focusPageId;
  const [pages, setPages] = useState<PulsePage[]>([]);
  const [invites, setInvites] = useState<PageInvite[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [manage, setManage] = useState<PageManageView | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [composerOpen, setComposerOpen] = useState(false);
  // Bumped after publishing, so the manage view — and with it the Posts count
  // on the section tile — is re-read from the server rather than guessed at.
  const [manageRefresh, setManageRefresh] = useState(0);

  const load = useCallback(async () => {
    try {
      const mine = await listMyPages();
      setPages(mine);
      const target = focusPageId && mine.some((p) => p.id === focusPageId)
        ? focusPageId
        : mine[0]?.id ?? null;
      setSelectedId((current) => (current && mine.some((p) => p.id === current) ? current : target));
    } catch {
      setNotice("Your pages could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [focusPageId]);

  /**
   * Invites are a separate read, and deliberately a separate failure. A user
   * with no invites is the common case, and an older server without the
   * endpoint should leave this hub working exactly as before — so a failure
   * here empties the list rather than surfacing an error over the pages the
   * user came for.
   */
  const loadInvites = useCallback(async () => {
    try {
      setInvites(await listMyPageInvites());
    } catch {
      setInvites([]);
    }
  }, []);

  async function answerInvite(invite: PageInvite, accept: boolean) {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      if (accept) {
        await acceptPageInvite(invite.token);
        setNotice(`You're now ${pageRoleLabel(invite.role).toLowerCase()} of ${invite.page_name}.`);
      } else {
        await declinePageInvite(invite.token);
        setNotice(`Invite from ${invite.page_name} declined.`);
      }
      // Accepting adds a page and clears the invite; declining only clears it.
      // Both are re-read from the server rather than patched locally, so the
      // screen can never disagree with what actually happened.
      await Promise.all([load(), loadInvites()]);
      if (accept) setSelectedId(invite.page_id);
    } catch (inviteError) {
      setNotice(
        inviteError instanceof PulseApiError ? inviteError.message : "That invite could not be updated."
      );
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load();
    loadInvites();
  }, [load, loadInvites]);

  useEffect(() => {
    let cancelled = false;
    setManage(null);
    if (!selectedId) return;
    getPageManageView(selectedId)
      .then((view) => {
        if (!cancelled) setManage(view);
      })
      .catch(() => {
        if (!cancelled) setManage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, manageRefresh]);

  const selected = pages.find((p) => p.id === selectedId) || null;
  const isOwner = manage?.role === "OWNER";
  const sections = manage?.sections || [];
  const sectionByKey = new Map(sections.map((section) => [section.key, section]));
  const tiles = sections.filter((section) => !INLINE_SECTIONS.has(section.key));

  /**
   * A section is tappable when this caller may act in it. A section they may
   * not act in still renders — an analyst should be able to see the shape of
   * the place they are reporting on — but as a description rather than a
   * control, because the server will refuse the action behind it.
   */
  function openSection(section: PageSection) {
    // Redundant with the `disabled` prop on the tile, deliberately: the two are
    // set from different expressions, and a rendering change that stops
    // disabling the tile should not also be the change that starts navigating.
    // No test can separate them while both hold, which is the point.
    if (!selected || !section.permitted) return;
    // Posting is the one section that happens here: the composer needs the
    // presence as context, and navigating away to find it is how "post as your
    // page" became unreachable in the first place.
    if (section.key === "content" || (section.key === "videos" && !section.ready)) {
      setComposerOpen(true);
      return;
    }
    const route =
      (!section.ready && SECTION_SETUP_ROUTES[section.key as keyof typeof SECTION_SETUP_ROUTES]) ||
      SECTION_ROUTES[section.key as keyof typeof SECTION_ROUTES];
    const pageParams = { pageId: selected.id, title: selected.name };
    switch (route) {
      case "PageEdit":
        return navigation.navigate("PageEdit", pageParams);
      case "PageConnections":
        return navigation.navigate("PageConnections", pageParams);
      case "PageTeam":
        return navigation.navigate("PageTeam", pageParams);
      case "Page":
        return navigation.navigate("Page", {
          pageId: selected.id,
          handle: selected.handle,
          title: selected.name
        });
      case "BusinessOs":
        return navigation.navigate("BusinessOs", { title: selected.name });
      case "BusinessOsAdvertising":
        return navigation.navigate("BusinessOsAdvertising", { title: selected.name });
      case "MarketplaceManager":
        return navigation.navigate("MarketplaceManager", { title: selected.name });
      case "BusinessOsPayments":
        return navigation.navigate("BusinessOsPayments", { title: selected.name });
      default:
        // A section this build has no destination for. It still renders, with
        // its label, count and setup line, because a server that grew a
        // section should not make it invisible on an older client — but there
        // is nowhere honest to send the tap, so it does not claim to be a
        // button. `sectionIsNavigable` keeps that consistent with the UI.
        return;
    }
  }

  function sectionIsNavigable(section: PageSection) {
    if (!section.permitted) return false;
    if (section.key === "content" || (section.key === "videos" && !section.ready)) return true;
    return Boolean(
      SECTION_ROUTES[section.key as keyof typeof SECTION_ROUTES] ||
        SECTION_SETUP_ROUTES[section.key as keyof typeof SECTION_SETUP_ROUTES]
    );
  }

  async function changeStatus(status: PageStatus) {
    if (!selected || busy) return;
    setBusy(true);
    setNotice("");
    try {
      await setPageStatus(selected.id, status);
      setNotice(`Page is now ${status.toLowerCase()}.`);
      await load();
    } catch (statusError) {
      setNotice(statusError instanceof PulseApiError ? statusError.message : "Status change failed.");
    } finally {
      setBusy(false);
    }
  }

  function confirmDeactivate() {
    if (!selected) return;
    Alert.alert(
      "Deactivate page?",
      "The page will no longer be publicly visible. Nothing is deleted — history stays auditable and an owner can review it later.",
      [
        { text: "Cancel", style: "cancel" },
        { text: "Deactivate", style: "destructive", onPress: () => changeStatus("DEACTIVATED") }
      ]
    );
  }

  async function askVerification() {
    if (!selected || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await requestPageVerification(selected.id);
      setNotice(result.message || "Verification request submitted for review.");
    } catch (verifyError) {
      setNotice(verifyError instanceof PulseApiError ? verifyError.message : "Verification request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={colors.accent} size="large" />
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
            loadInvites();
          }}
          tintColor={colors.accent}
        />
      }
    >
      {/*
        The invitee's half of the invite flow. The token is returned to the
        person who *sent* the invite, and nothing is pushed or mailed — so
        before this existed, the only route onto a team was someone pasting a
        secret to you. This is where an invite is actually findable, and it
        sits above everything else because it expires.
      */}
      {invites.map((invite) => (
        <View key={invite.token} style={styles.inviteCard} testID={`invite-${invite.page_id}`}>
          <Text style={styles.inviteTitle}>
            {invite.invited_by_name
              ? `${invite.invited_by_name} invited you to ${invite.page_name}`
              : `You've been invited to ${invite.page_name}`}
          </Text>
          <Text style={styles.cardMeta}>
            {pageTypeLabel(invite.page_type)}
            {invite.page_handle ? ` · @${invite.page_handle}` : ""} · as {pageRoleLabel(invite.role).toLowerCase()}
          </Text>
          {invite.expired ? (
            // Shown rather than hidden: an invite that silently vanishes reads
            // as one that was never sent, and leaves nothing to ask about.
            <>
              <Text style={styles.note}>
                This invite expired. Ask {invite.invited_by_name || "whoever invited you"} for a new one.
              </Text>
              <View style={styles.actionsGrid}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Dismiss expired invite from ${invite.page_name}`}
                  disabled={busy}
                  style={styles.action}
                  onPress={() => answerInvite(invite, false)}
                >
                  <Text style={styles.actionText}>Dismiss</Text>
                </Pressable>
              </View>
            </>
          ) : (
            <View style={styles.actionsGrid}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Accept invite to ${invite.page_name}`}
                disabled={busy}
                style={[styles.action, styles.inviteAccept]}
                onPress={() => answerInvite(invite, true)}
              >
                <Text style={styles.inviteAcceptText}>Accept</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Decline invite to ${invite.page_name}`}
                disabled={busy}
                style={styles.action}
                onPress={() => answerInvite(invite, false)}
              >
                <Text style={styles.actionText}>Decline</Text>
              </Pressable>
            </View>
          )}
        </View>
      ))}

      <Pressable
        accessibilityRole="button"
        style={styles.createButton}
        onPress={() => navigation.navigate("PageCreate")}
      >
        <Text style={styles.createButtonText}>+ Create a Presence</Text>
      </Pressable>

      {!pages.length ? (
        <Text style={styles.empty}>
          You don't manage any presences yet. Create one for your artist project, business or
          organization — your personal account stays exactly as it is.
        </Text>
      ) : null}

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.pageRow}>
        {pages.map((page) => (
          <Pressable
            key={page.id}
            accessibilityRole="button"
            accessibilityState={{ selected: page.id === selectedId }}
            style={[styles.pageChip, page.id === selectedId && styles.pageChipActive]}
            onPress={() => setSelectedId(page.id)}
          >
            {page.avatar_url ? (
              <Image source={{ uri: page.avatar_url }} style={styles.pageChipAvatar} />
            ) : null}
            <View>
              <Text style={[styles.pageChipName, page.id === selectedId && styles.pageChipNameActive]}>
                {page.name}
              </Text>
              <Text style={styles.pageChipMeta}>
                {pageTypeLabel(page.page_type)} · {page.role || ""}
              </Text>
            </View>
          </Pressable>
        ))}
      </ScrollView>

      {selected ? (
        <View style={styles.card}>
          {/*
            The handle, and nothing else here. Two lines used to follow it:
            "Status: ACTIVE · unverified", which is a database row read aloud,
            and a followers/posts line taken from the *list* row — a second
            copy of two numbers the manage view also carries, free to disagree
            with it the moment either read went stale. Both facts now come from
            the Overview below, in words, from the measurement.
          */}
          <Text style={styles.cardTitle}>@{selected.handle}</Text>

          <View style={styles.actionsGrid}>
            {/* Not a section: viewing the public page is what this presence
                looks like to everyone else, and that is worth one tap whatever
                your role and whatever the page type has. */}
            <Pressable
              accessibilityRole="button"
              style={styles.action}
              onPress={() => navigation.navigate("Page", { pageId: selected.id, handle: selected.handle, title: selected.name })}
            >
              <Text style={styles.actionText}>View public page</Text>
            </Pressable>
          </View>

          {/*
            Overview. The server declares this section, `INLINE_SECTIONS` keeps
            it out of the tile grid because its content is this block — and for
            a while this block did not exist, so the section was declared,
            excluded, and drawn by nothing. A tile nobody renders is the
            management-side version of a tab nobody renders.

            Every value is `manage.overview`, measured server-side. Nothing here
            is summed, projected or filled in locally: an older server that
            sends no Overview gets no Overview, because the alternative is this
            screen quietly becoming a second, weaker place where "what counts as
            a real metric" is decided.
          */}
          {manage?.overview ? (
            <View style={styles.overviewCard} testID="page-overview">
              <Text style={styles.sectionTitle}>
                {sectionByKey.get("overview")?.label || "Overview"}
              </Text>
              {/* Words, from the server's own mapping of the two enums. */}
              <Text style={styles.cardMeta}>
                {manage.overview.status} · {manage.overview.verification}
              </Text>

              <View style={styles.metricRow}>
                {manage.overview.metrics.map((metric) => (
                  <View key={metric.key} style={styles.metric} testID={`metric-${metric.key}`}>
                    <Text style={styles.metricValue}>{metric.value}</Text>
                    <Text style={styles.metricLabel}>{metric.label}</Text>
                    {/*
                      Key presence, not truthiness. A delta of 0 is a
                      measurement — "nobody followed this month" — and dropping
                      it would leave the metric looking like the window was
                      never counted. A metric with no window (Team: nothing
                      records when a member joined) correctly shows none.

                      No sign handling: a delta counts events that happened
                      inside the window, so it cannot be negative, and a `-`
                      branch here would be a case no fixture could ever reach.
                    */}
                    {typeof metric.delta === "number" ? (
                      <Text style={styles.metricDelta}>
                        +{metric.delta} in the last {metric.window}
                      </Text>
                    ) : null}
                  </View>
                ))}
              </View>

              <View style={styles.meterTrack}>
                <View
                  style={[
                    styles.meterFill,
                    { width: `${Math.min(100, Math.max(0, manage.overview.completeness_percent))}%` }
                  ]}
                />
              </View>
              <Text style={styles.cardMeta}>
                Profile {manage.overview.completeness_percent}% complete
              </Text>

              {/*
                Sections this role may act on with nothing behind them yet.
                Server-derived from the same `sections` array the tiles come
                from, so it cannot name work that is not offered, and an ANALYST
                is never told to go and do something they would be refused.
              */}
              {manage.overview.pending.length ? (
                <Text style={styles.cardMeta} testID="overview-pending">
                  Waiting on the team: {manage.overview.pending.join(", ")}
                </Text>
              ) : null}

              {/* What is deliberately not measured, said out loud, rather than
                  a plausible-looking number standing in for it. */}
              {manage.overview.note ? <Text style={styles.note}>{manage.overview.note}</Text> : null}
            </View>
          ) : null}

          {/*
            The management surface, decided server-side per page type and role.
            This grid used to be fixed: a media page was offered Marketplace, an
            artist was offered Business OS, and Advertising opened whether or
            not an ad account existed. Every tile here is one the server said
            this page has — so a tile that is absent is a capability this page
            genuinely does not have, and a tile that is present is one the
            server will accept.
          */}
          {tiles.length ? (
            <View style={styles.sectionGrid}>
              {tiles.map((section) => {
                const navigable = sectionIsNavigable(section);
                return (
                  <Pressable
                    key={section.key}
                    accessibilityRole={navigable ? "button" : "text"}
                    accessibilityLabel={`${section.label}${section.ready ? "" : " — setup needed"}`}
                    accessibilityState={{ disabled: !navigable }}
                    disabled={!navigable}
                    testID={`section-${section.key}`}
                    style={[styles.sectionTile, !section.ready && styles.sectionTilePending]}
                    onPress={() => openSection(section)}
                  >
                    <View style={styles.sectionTileHead}>
                      <Text style={styles.sectionTileLabel}>{section.label}</Text>
                      {typeof section.count === "number" ? (
                        <Text style={styles.sectionTileCount}>{section.count}</Text>
                      ) : null}
                    </View>
                    {/*
                      When something is behind the section, say what it is for.
                      When nothing is, say the one thing missing instead — an
                      empty section a team can see is one they can fill, and
                      that is the difference between intentionally empty and
                      broken.
                    */}
                    <Text style={styles.sectionTileHint}>
                      {section.ready ? section.hint : section.setup}
                    </Text>
                    {!section.permitted ? (
                      <Text style={styles.sectionTileLocked}>
                        Your role can't change this.
                      </Text>
                    ) : null}
                  </Pressable>
                );
              })}
            </View>
          ) : null}

          {/*
            The completeness checklist — what is missing, item by item. The
            percentage and its meter are NOT repeated here: they are one number
            and they live in the Overview above. This card answers the next
            question, which is which fields would move it.

            Only the unfinished items are listed. A finished checklist is not a
            list of ticks to scroll past, it is one sentence saying there is
            nothing left, so the card collapses to that.
          */}
          {manage?.completeness ? (
            <View style={styles.analyticsCard} testID="page-completeness">
              <Text style={styles.sectionTitle}>Finish setting up</Text>
              {manage.completeness.items
                .filter((item) => !item.done)
                .map((item) => (
                  <Text key={item.key} style={styles.cardMeta}>
                    ○ {item.label}
                  </Text>
                ))}
              {manage.completeness.items.every((item) => item.done) ? (
                <Text style={styles.note}>All set — nothing left to add.</Text>
              ) : null}
            </View>
          ) : null}

          {manage?.members?.length ? (
            <View style={styles.analyticsCard}>
              <Text style={styles.sectionTitle}>Team</Text>
              {/* `name` and `handle` are what the server sends. This read
                  `display_name || username` for its whole life, so the fallback
                  always won and every member here rendered as "Member 22". */}
              {manage.members.map((member) => (
                <Text key={member.user_id} style={styles.cardMeta}>
                  {member.name || (member.handle ? `@${member.handle}` : `Member ${member.user_id}`)} —{" "}
                  {member.role}
                  {member.status === "invited" ? " (invited)" : ""}
                </Text>
              ))}
            </View>
          ) : null}

          {isOwner ? (
            <View style={styles.ownerZone}>
              {/* Settings and Verification are the other two inline sections:
                  both live here, in the owner zone, so they name this block
                  rather than sending an owner somewhere else to find it. */}
              <Text style={styles.sectionTitle}>
                {sectionByKey.get("settings")?.label || "Settings"}
              </Text>
              <View style={styles.actionsGrid}>
                {selected.status !== "ACTIVE" ? (
                  <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={() => changeStatus("ACTIVE")}>
                    <Text style={styles.actionText}>Activate</Text>
                  </Pressable>
                ) : (
                  <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={() => changeStatus("PAUSED")}>
                    <Text style={styles.actionText}>Pause</Text>
                  </Pressable>
                )}
                <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={() => changeStatus("UNPUBLISHED")}>
                  <Text style={styles.actionText}>Unpublish</Text>
                </Pressable>
                <Pressable accessibilityRole="button" style={[styles.action, styles.dangerAction]} disabled={busy} onPress={confirmDeactivate}>
                  <Text style={styles.dangerText}>Deactivate</Text>
                </Pressable>
              </View>
              {/* The server already worked out what state verification is in
                  and what that means — pending, rejected, or never asked. The
                  button is only offered when there is something to ask for. */}
              {sectionByKey.get("verification")?.setup ? (
                <Text style={styles.note}>{sectionByKey.get("verification")!.setup}</Text>
              ) : null}
              {selected.verification_status === "unverified" ? (
                <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={askVerification}>
                  <Text style={styles.actionText}>Request verification</Text>
                </Pressable>
              ) : null}
              <Text style={styles.note}>
                Ownership transfer requires explicit confirmation and is fully audited. Verification
                is reviewed — it is never granted automatically.
              </Text>
            </View>
          ) : null}
        </View>
      ) : null}

      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      {/*
        The composer, opened from the Posts section already speaking as this
        presence. It has existed and worked for its whole life while being
        mounted nowhere: `createPagePost` and the identity switcher were
        reachable from no screen in the app, so publishing as a page was
        impossible natively. This is the mount.
      */}
      {selected ? (
        <FeedComposer
          visible={composerOpen}
          presetPageId={selected.id}
          onClose={() => setComposerOpen(false)}
          onCreated={() => {
            setComposerOpen(false);
            setNotice(`Published as ${selected.name}.`);
            // Posts count is measured server-side, so the tile is re-read
            // rather than incremented locally.
            load();
            setManageRefresh((n) => n + 1);
          }}
        />
      ) : null}
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  action: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  actionText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  actionsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10
  },
  analyticsCard: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    gap: 4,
    marginTop: 14,
    paddingTop: 12
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 14,
    padding: 16
  },
  cardMeta: {
    color: colors.muted,
    fontSize: 13
  },
  cardTitle: {
    color: colors.accent,
    fontSize: 16,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    justifyContent: "center"
  },
  content: {
    padding: 16,
    paddingBottom: 48
  },
  createButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 10,
    minHeight: 44,
    justifyContent: "center"
  },
  createButtonText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  dangerAction: {
    borderColor: colors.danger
  },
  dangerText: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "800"
  },
  empty: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 16,
    textAlign: "center"
  },
  inviteAccept: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  inviteAcceptText: {
    color: colors.background,
    fontSize: 13,
    fontWeight: "800"
  },
  inviteCard: {
    backgroundColor: colors.surface,
    borderColor: colors.accent,
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 14,
    padding: 16
  },
  inviteTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    marginBottom: 4
  },
  meterFill: {
    backgroundColor: colors.accent,
    borderRadius: 3,
    height: 6
  },
  meterTrack: {
    backgroundColor: colors.border,
    borderRadius: 3,
    height: 6,
    marginVertical: 6,
    overflow: "hidden"
  },
  metric: {
    flexGrow: 1,
    flexShrink: 1,
    // Three metrics per row at every width this screen is used at, without
    // pinning a pixel count that a 40k follower total would overflow.
    flexBasis: "28%"
  },
  metricDelta: {
    color: colors.accent,
    fontSize: 11,
    marginTop: 2
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2
  },
  metricRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    marginTop: 10
  },
  metricValue: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  note: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 8
  },
  notice: {
    color: colors.accent,
    fontWeight: "800",
    marginTop: 14,
    textAlign: "center"
  },
  overviewCard: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    gap: 4,
    marginTop: 14,
    paddingTop: 12
  },
  ownerZone: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    marginTop: 14,
    paddingTop: 12
  },
  pageChip: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    padding: 10
  },
  pageChipActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  pageChipAvatar: {
    borderRadius: 14,
    height: 28,
    width: 28
  },
  pageChipMeta: {
    color: colors.muted,
    fontSize: 10
  },
  pageChipName: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  pageChipNameActive: {
    color: colors.accent
  },
  pageRow: {
    gap: 8,
    marginTop: 14
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  sectionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  sectionTile: {
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexBasis: "48%",
    flexGrow: 1,
    gap: 4,
    minHeight: 76,
    padding: 12
  },
  sectionTileCount: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900"
  },
  sectionTileHead: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  sectionTileHint: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 16
  },
  sectionTileLabel: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 13,
    fontWeight: "800"
  },
  sectionTileLocked: {
    color: colors.muted,
    fontSize: 10,
    fontStyle: "italic"
  },
  /** Not-yet-set-up reads as unfinished, not as broken or forbidden. */
  sectionTilePending: {
    borderStyle: "dashed"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  }
}));
