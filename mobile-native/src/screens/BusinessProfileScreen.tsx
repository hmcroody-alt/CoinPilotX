/**
 * Business Profile — the first card in the Business OS "Sections" grid.
 *
 * Replaces the panel-filtered `SellerStore mode="profile"` view, which was three
 * generic panels borrowed from the seller-tools screen. This is a screen of its
 * own with a single job: show the operator what a buyer sees, what is missing,
 * and what fixing it would change.
 *
 * Where the data comes from, and where it does not
 * ------------------------------------------------
 * Business identity — name, handle, category, contact, public location, opening
 * hours, links, verification, locks, completeness and publish state — comes from
 * one source: `GET /api/pulse/business/profile`, via `api/businessProfile`.
 *
 * It used to come from three. The seller application supplied the fields and a
 * completeness percentage, `api/verification` supplied a status, and the Pulse
 * profile supplied a `verified_badge` that this screen OR-ed into the answer.
 * Three sources with no precedence between them is how one surface printed
 * "in review" while another printed "Approved" for the same business. The server
 * now resolves that precedence once — verification request outranks application
 * status outranks badge, and the badge may only raise a business to approved,
 * never lower one — so the client's job is to render the verdict, not to
 * recompute it. `verification.source` says which store decided, so the screen can
 * be asked *why* it says what it says.
 *
 * Two sources remain, for the two things the profile API does not own: the Pulse
 * profile supplies avatar, cover and follower count, and the seller store
 * snapshot supplies listing and order counts. Neither is consulted about identity.
 *
 * Editing is per-field, not per-screen
 * ------------------------------------
 * The old screen asked `sellerApplicationIsEditable` once and froze every field
 * when the answer was no. The server distinguishes two different things:
 * `requiresReview` (the write lands, and a reviewer is told) from `blocked` (the
 * write is refused, and only under enforcement). Collapsing them locked thirteen
 * fields because one was sensitive. Each row now asks about itself, and a blocked
 * row says why rather than silently doing nothing.
 *
 * Saves send the diff — `changedFields`, not the whole form — because the audit
 * trail exists so a reviewer can see what a verified business changed, and a log
 * of thirteen no-op writes per save defeats that. A partial save is a success:
 * fields that validated are kept and the ones that did not are reported inline.
 *
 * Gaps are still gaps. Seller rating, on-time rate, average reply time, profile
 * views, follower deltas, store-click trend, next ship day and user-owned events
 * have no endpoint, and each is rendered as an explicit dimmed placeholder rather
 * than a plausible number. The registry that routes this screen says `backed`
 * "reflects verified live coverage, not aspiration"; inventing a 4.8-star rating
 * here would violate the same rule one layer up.
 *
 * Loading strategy mirrors the rest of the app: cached snapshot first so the
 * screen paints immediately, then a network refresh. A failed refresh with a
 * usable cache is an offline notice, not an error state.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Animated, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  changedFields,
  fieldIsBlocked,
  fieldRequiresReview,
  LINK_LABELS,
  loadOwnerProfile,
  normalizeOwnerProfile,
  openingStatus,
  OwnerProfile,
  ProfileLocks,
  publicLocationLine,
  saveLink,
  saveProfileFields,
  SYNC_LABELS,
  SyncState,
  VERIFICATION_LABELS,
  VerificationState
} from "../api/businessProfile";
import { getMyProfile, loadCachedProfile, PulseProfile } from "../api/profile";
import {
  loadCachedSellerStore,
  loadSellerStoreSnapshot,
  MarketplaceListing,
  MarketplaceSellerOrder
} from "../api/marketplace";
import {
  BuyerPreviewCard,
  BuyerPreviewChip,
  BuyerPreviewStat,
  CompletenessMeter,
  ConnectedRow,
  DetailRow,
  EditableDetailRow,
  FooterActions,
  GhostPill,
  LiveDataTicker,
  LivePanel,
  LiveSyncBadge,
  LiveTickerStat,
  SectionHeading,
  StaticDetailRow,
  TrustCallout
} from "../components/businessProfile/BusinessLiveParts";
import { registerSyncInvalidation } from "../core/eventSync";
import { logiNexus } from "../theme/logiNexus";
import { useBusinessLiveEntrance } from "../theme/businessLiveMotion";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { BusinessOsModules } from "../components/business/BusinessOsModules";

const palette = logiNexus.colors.businessLive;

/** Sections in render order. The entrance cascade indexes into this. */
const SECTION_COUNT = 8;

/** Placeholder marker for a metric with no API behind it yet. */
const NO_DATA = "Not tracked yet";

/**
 * Draft key for the website link.
 *
 * Deliberately not one of the server's writable *field* names: links are a
 * positioned collection saved through `POST /profile/link`, so this key is routed
 * to `saveLink` on save and excluded from the field diff. Naming it once here is
 * what keeps the two places that must agree — the overlay and the save — from
 * drifting into a silently-dropped edit.
 */
const WEBSITE_FIELD = "website_url";

type Props = {
  navigation: { navigate: (...args: any[]) => void; goBack: () => void };
};

export function BusinessProfileScreen({ navigation }: Props) {
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useBusinessLiveEntrance(SECTION_COUNT, reducedMotion);

  const [owner, setOwner] = useState<OwnerProfile>(() => normalizeOwnerProfile(undefined));
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [orders, setOrders] = useState<MarketplaceSellerOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncFailed, setSyncFailed] = useState(false);
  const [message, setMessage] = useState("");

  /**
   * Unsaved edits, held as a sparse overlay on top of the server's field set
   * rather than as a full copy. A sparse overlay is what makes "is anything
   * dirty" a fact about the data instead of a flag someone has to remember to
   * set, and it means a background refresh can replace the server fields
   * underneath without silently discarding what the user typed.
   */
  const [draft, setDraft] = useState<Record<string, string>>({});

  /**
   * Per-field refusals from the last save. A partial save returns 200 with the
   * fields that validated in `saved` and the ones that did not in `rejected`, so
   * this is the surface for "your URL was malformed" — beside that field, not as
   * a screen-level error that would imply the other four were lost too.
   */
  const [rejected, setRejected] = useState<Record<string, string>>({});
  const mounted = useRef(true);

  const dirty = Object.keys(draft).length > 0;

  const canonical = useRef({ profile: false, store: false });
  const loadInFlight = useRef<Promise<void> | null>(null);

  const load = useCallback(() => {
    // Publishing a listing invalidates seller_inventory, marketplace and orders
    // together, so the three handlers below fire in one tick. Ungated that is
    // three concurrent copies of this three-request fan-out.
    if (loadInFlight.current) return loadInFlight.current;
    setMessage("");

    const run = (async () => {
      // The network goes first. These two cache reads used to be awaited here,
      // which put two AsyncStorage bridge hops in front of every request on the
      // screen — a waterfall in the code whose job was to avoid one. They are
      // started alongside the fan-out and applied only where nothing canonical
      // has landed, so a slow disk read can never repaint over fresher data.
      const fresh = Promise.allSettled([loadOwnerProfile(), getMyProfile(), loadSellerStoreSnapshot()]);

      const hydration = Promise.all([
        loadCachedProfile("me").catch(() => null),
        loadCachedSellerStore().catch(() => null)
      ])
        .then(([cachedProfile, cachedStore]) => {
          if (!mounted.current) return;
          if (cachedProfile && !canonical.current.profile) setProfile(cachedProfile);
          if (cachedStore && !canonical.current.store) {
            setListings(cachedStore.listings || []);
            setOrders(cachedStore.orders || []);
          }
        })
        .catch(() => undefined);

      // `allSettled`, not `all`: the Pulse profile and the store snapshot are
      // supporting detail — an avatar and two counts. Either failing must not blank
      // out an identity that loaded perfectly well.
      const [freshOwner, freshProfile, freshStore] = await fresh;
      if (!mounted.current) return;

      if (freshProfile.status === "fulfilled") {
        canonical.current.profile = true;
        setProfile(freshProfile.value);
      }
      if (freshStore.status === "fulfilled" && freshStore.value.live) {
        canonical.current.store = true;
        setListings(freshStore.value.listings || []);
        setOrders(freshStore.value.orders || []);
      }

      await hydration;
      if (!mounted.current) return;

      if (freshOwner.status === "fulfilled" && freshOwner.value.state === "ready") {
        setOwner(freshOwner.value.profile);
        // Served from disk is said out loud. An identity screen showing a stale
        // business name without saying so invites the seller to "fix" a change
        // they already made.
        setOffline(freshOwner.value.fromCache);
      } else {
        setOffline(false);
        setMessage(
          freshOwner.status === "fulfilled" && freshOwner.value.state === "failed"
            ? freshOwner.value.failure.message
            : "Business profile could not load."
        );
      }
    })().finally(() => {
      if (mounted.current) setLoading(false);
      loadInFlight.current = null;
    });
    loadInFlight.current = run;
    return run;
  }, []);

  useEffect(() => {
    mounted.current = true;
    load().catch(() => undefined);
    return () => {
      mounted.current = false;
    };
  }, [load]);

  useEffect(() => {
    // The same invalidation channels the seller tools listen on, so publishing
    // a listing in one place updates the counts here without a manual refresh.
    const refresh = () => {
      load().catch(() => undefined);
    };
    const unregisterSeller = registerSyncInvalidation("seller_inventory", refresh);
    const unregisterMarketplace = registerSyncInvalidation("marketplace", refresh);
    const unregisterOrders = registerSyncInvalidation("orders", refresh);
    return () => {
      unregisterSeller();
      unregisterMarketplace();
      unregisterOrders();
    };
  }, [load]);

  /* ------------------------------------------------------------- derived */

  /**
   * The server's stored values with the unsaved overlay on top. Keys are the
   * server's writable field names, so `changedFields` can diff the two directly
   * and the save body needs no translation layer that could drift.
   */
  const stored = useMemo<Record<string, string>>(
    () => ({
      business_name: owner.businessName,
      tagline: owner.tagline,
      about: owner.about,
      what_you_sell: owner.whatYouSell,
      support_email: owner.contact.email,
      support_phone: owner.contact.phone,
      public_city: owner.publicLocation.city,
      public_region: owner.publicLocation.region,
      public_country: owner.publicLocation.country,
      [WEBSITE_FIELD]: owner.links.find((link) => link.kind === "website")?.url || ""
    }),
    [owner]
  );

  const fields = useMemo(() => ({ ...stored, ...draft }), [stored, draft]);

  const categoryLabel = owner.businessCategoryLabel || owner.businessCategory || "";
  const businessName = fields.business_name?.trim() || profile?.display_name || "Your business";
  const handle = owner.handle || (profile?.username ? `@${profile.username}` : "");
  const location = publicLocationLine({ ...owner, publicLocation: {
    city: fields.public_city || "",
    region: fields.public_region || "",
    country: fields.public_country || ""
  } });
  const contact = [fields.support_email, fields.support_phone].filter(Boolean).join(" · ");
  const memberSince = yearOf(owner.publishedAt) || yearOf(owner.updatedAt);
  const activeListings = listings.filter((listing) => isActiveListing(listing)).length;
  const openOrders = orders.filter((order) => isOpenOrder(order)).length;

  /**
   * Hours, from the server's seven-entry grid rather than a placeholder.
   *
   * `unset` resolves to "Hours not provided", never "Closed" — a new seller who
   * has configured nothing is not a business that is shut, and a buyer told
   * "Closed" when the truth is "we never said" has been misinformed.
   */
  const hours = openingStatus(owner);
  const hoursValue = owner.hoursMode === "unset" ? "" : hours.label;

  /**
   * The links this row cannot edit, named rather than hidden. The row edits the
   * website; a seller with an Instagram and a TikTok needs to know those are
   * still there and are not what they are typing over.
   */
  const linksValue = useMemo(() => {
    const others = owner.links.filter((link) => link.kind !== "website");
    if (!others.length) return "";
    return `Also linked: ${others.map((link) => link.label || LINK_LABELS[link.kind]).join(", ")}`;
  }, [owner.links]);

  /**
   * What Live Sync is actually asserting.
   *
   * The server owns the first three states; the last three describe this client's
   * own request and are decided here, because a server cannot know that the phone
   * holding the screen has lost signal. The old badge read a hard-coded
   * "LIVE SYNC", which implied that whatever was on screen was already public —
   * untrue from the moment anyone typed.
   */
  const syncState: SyncState = saving
    ? "saving"
    : syncFailed
      ? "sync_failed"
      : offline
        ? "offline"
        : dirty
          ? "changes_pending"
          : owner.sync.state;

  const verificationState: VerificationState = owner.verification.state;
  const verified = verificationState === "approved";

  const nextStep = useMemo(() => nextStepSuggestion(owner, fields), [owner, fields]);

  const tickerStats = useMemo<LiveTickerStat[]>(
    () => [
      { key: "completeness", label: "Profile complete", value: `${Math.round(owner.completion.percent)}%` },
      { key: "followers", label: "Followers", value: formatCount(profile?.follower_count) },
      { key: "listings", label: "Active listings", value: String(activeListings) },
      { key: "orders", label: "Open orders", value: String(openOrders) },
      // Everything below is in the design but has no endpoint yet. Shown, dimmed
      // and labelled, so the gap is visible instead of quietly filled in.
      { key: "views", label: "Profile views today", value: NO_DATA, placeholder: true },
      { key: "new_followers", label: "New followers", value: NO_DATA, placeholder: true },
      { key: "reply", label: "Avg reply time", value: NO_DATA, placeholder: true },
      { key: "clicks", label: "Store clicks", value: NO_DATA, placeholder: true },
      { key: "ship", label: "Next ship day", value: NO_DATA, placeholder: true }
    ],
    [activeListings, owner.completion.percent, openOrders, profile?.follower_count]
  );

  const chips = useMemo<BuyerPreviewChip[]>(
    () => [
      { key: "shipping", icon: "cube-outline", label: "Shipping not set", placeholder: true },
      { key: "reply", icon: "chatbubble-ellipses-outline", label: "Reply time unknown", placeholder: true },
      memberSince
        ? { key: "since", icon: "calendar-outline", label: `Member since ${memberSince}` }
        : { key: "since", icon: "calendar-outline", label: "Member since —", placeholder: true }
    ],
    [memberSince]
  );

  const previewStats = useMemo<BuyerPreviewStat[]>(
    () => [
      { key: "followers", value: formatCount(profile?.follower_count), label: "Followers" },
      { key: "rating", value: "—", label: "Rating", placeholder: true },
      { key: "ontime", value: "—", label: "On time", placeholder: true }
    ],
    [profile?.follower_count]
  );

  /* -------------------------------------------------------------- actions */

  function editField(key: string, value: string) {
    setDraft((current) => {
      const next = { ...current, [key]: value };
      // Typing a value back to what the server already has un-dirties the field
      // rather than leaving a no-op edit behind that would keep Save lit.
      if ((stored[key] || "") === value) delete next[key];
      return next;
    });
    // The refusal belonged to the value that was rejected. Once the seller edits
    // that field, keeping the old sentence beside it would be stating something
    // the server has not said about what is now on screen.
    setRejected((current) => {
      if (!(key in current)) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  /**
   * Explain a locked field instead of ignoring the press.
   *
   * A row that looks live and quietly does nothing teaches the operator the wrong
   * thing about their own business. The server sends the explainer with the locks
   * so the sentence is the server's, not a second copy written here.
   */
  function explainLock(field: string) {
    setMessage(owner.locks.explainer || `${field} is locked while your business is under review.`);
  }

  function discard() {
    setDraft({});
    setRejected({});
    setMessage("");
  }

  async function save() {
    if (!dirty || saving) return;
    setSaving(true);
    setSyncFailed(false);
    setMessage("");
    try {
      // The diff, not the form. The audit trail exists so a reviewer can see what
      // a verified business changed; sending all nine fields every time would
      // bury one real edit in eight no-op writes.
      const diff = changedFields(stored, fields);

      // The website is a link, not a field. Pulled out of the diff so the field
      // endpoint is not handed a key it would reject as unwritable — which would
      // read to the seller as "your URL is invalid" when the URL was fine.
      const websiteEdit = WEBSITE_FIELD in diff ? String(diff[WEBSITE_FIELD] ?? "") : null;
      delete diff[WEBSITE_FIELD];

      let latest = owner;
      const refusals: Record<string, string> = {};
      let savedCount = 0;
      let queued: string[] = [];

      if (Object.keys(diff).length) {
        const result = await saveProfileFields(diff);
        latest = result.profile;
        Object.assign(refusals, result.rejected);
        savedCount += Object.keys(result.saved).length;
        queued = result.queuedForReview;
      }

      if (websiteEdit !== null) {
        // Sequenced after the fields, and its own failure is its own refusal: a
        // malformed URL must not discard the business name that saved a moment
        // earlier, which is the all-or-nothing behaviour the brief rules out.
        try {
          latest = await saveLink("website", websiteEdit);
          savedCount += 1;
        } catch (error) {
          refusals[WEBSITE_FIELD] =
            error instanceof Error ? error.message : "That link could not be saved.";
        }
      }

      if (!mounted.current) return;
      setOwner(latest);
      setRejected(refusals);
      const result = { rejected: refusals, saved: savedCount, queuedForReview: queued };

      // A partial save is a success with exceptions, not a failure. The fields
      // that validated are kept and only the ones that did not stay dirty —
      // discarding all nine because one URL was malformed is the behaviour the
      // brief rules out.
      const refused = Object.keys(result.rejected);
      setDraft((current) => {
        const remaining: Record<string, string> = {};
        refused.forEach((key) => {
          if (key in current) remaining[key] = current[key];
        });
        return remaining;
      });

      if (refused.length) {
        const kept = result.saved;
        setMessage(
          kept
            ? `Saved ${kept} ${plural(kept, "change")}. ${refused.length} needs another look.`
            : "Nothing was saved — see the notes below."
        );
      } else if (result.queuedForReview.length) {
        // Saved *and* queued. Not the same as rejected, and saying so is what
        // stops the seller from re-entering a change that already landed.
        setMessage("Saved. A reviewer will check the details you changed.");
      } else {
        setMessage("Saved.");
      }
    } catch (error) {
      if (!mounted.current) return;
      setSyncFailed(true);
      setMessage(error instanceof Error ? error.message : "Changes could not be saved.");
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  /* --------------------------------------------------------------- render */

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <Animated.View style={[styles.header, entrance.styleFor(0)]}>
        <Pressable
          onPress={() => navigation.goBack()}
          accessibilityRole="button"
          accessibilityLabel="Back"
          hitSlop={10}
          style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}
        >
          <Ionicons name="chevron-back" size={22} color={palette.textPrimary} />
        </Pressable>
        <Text style={styles.headerTitle} numberOfLines={1}>
          Business Profile
        </Text>
        {/* The badge states what is true right now, not a decorative "LIVE SYNC".
            Six states: three the server asserts about the published profile, three
            this client asserts about its own request. */}
        <LiveSyncBadge reducedMotion={reducedMotion} label={SYNC_LABELS[syncState]} />
      </Animated.View>

      <View style={styles.headerActions}>
        <GhostPill
          label="View as buyer"
          icon="eye-outline"
          accessibilityLabel="View as buyer"
          accessibilityHint="Opens the read-only public profile buyers see"
          onPress={() =>
            // `BusinessBuyerPreview`, not `ProfileDetail`. `ProfileDetail` is the
            // owner's own social profile: it renders owner affordances and reads
            // owner data, so "View as buyer" showed the owner a screen no buyer
            // will ever see. The preview route fetches
            // `GET /api/pulse/business/profile/preview`, which the server builds
            // from a public allowlist, and is typed so that owner-only fields have
            // nowhere to render even if someone tried.
            //
            // No params: an absent `sellerUserId` is what marks this as the owner's
            // own preview. Passing the owner's id would send it down the buyer path
            // and lose the preview banner.
            navigation.navigate("BusinessBuyerPreview")
          }
        />
      </View>

      <Animated.View style={entrance.styleFor(1)}>
        <LiveDataTicker stats={tickerStats} reducedMotion={reducedMotion} />
      </Animated.View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[styles.content, { paddingBottom: Math.max(insets.bottom, 16) + 120 }]}
        keyboardShouldPersistTaps="handled"
      >
        {loading ? (
          <View style={styles.loading}>
            <ActivityIndicator color={palette.accent} />
          </View>
        ) : null}

        {offline ? <Notice text="Showing your last saved profile. Reconnect to sync." tone="muted" /> : null}
        {message ? <Notice text={message} tone={message === "Saved." ? "accent" : "warning"} /> : null}

        <Animated.View style={entrance.styleFor(2)}>
          <LivePanel style={styles.meterPanel}>
            <CompletenessMeter
              percent={owner.completion.percent}
              headline={completenessHeadline(owner.completion.percent)}
              suggestion={nextStep}
              reducedMotion={reducedMotion}
              ringLabel="Profile completeness"
            />
          </LivePanel>
        </Animated.View>

        <Animated.View style={entrance.styleFor(3)}>
          <SectionHeading title="How buyers see you" caption="A live preview of your public business card." />
          <BuyerPreviewCard
            coverUrl={profile?.cover_url || profile?.banner_url}
            avatarUrl={profile?.avatar_url || profile?.avatar_thumbnail_url}
            businessName={businessName}
            verified={verified}
            handle={handle}
            category={categoryLabel}
            bio={fields.tagline?.trim() || fields.about?.trim() || profile?.bio?.trim() || ""}
            chips={chips}
            stats={previewStats}
            reducedMotion={reducedMotion}
          />
        </Animated.View>

        <Animated.View style={entrance.styleFor(4)}>
          <SectionHeading
            title="Profile details"
            caption={
              owner.locks.blocked.length
                ? "Some profile fields are locked during enforcement review."
                : owner.locks.requiresReview.length
                  ? "Tap a field to edit it. Changes to your name go to a reviewer."
                  : "Tap a field to edit it."
            }
          />
          <LivePanel>
            {/* Business name is identity-sensitive: on a verified business it is
                `requiresReview` (the edit lands, a reviewer is told) and only
                `blocked` under enforcement. Those are different facts, so the row
                asks about itself instead of reading one screen-wide flag. */}
            <EditableField
              icon="business-outline"
              label="Business name"
              field="business_name"
              value={fields.business_name || ""}
              placeholder="What buyers should call you"
              emptyConsequence="buyers see your personal name instead"
              locks={owner.locks}
              error={rejected.business_name}
              onChangeValue={editField}
              onBlocked={explainLock}
            />

            <Divider />
            <DetailRow
              icon="at-outline"
              label="Handle"
              value={handle}
              emptyConsequence="buyers have no stable way to find you again"
              onPress={() => navigation.navigate("ProfileEdit")}
            />

            <Divider />
            <DetailRow
              icon="pricetag-outline"
              label="Category"
              value={categoryLabel}
              emptyConsequence="you won't appear in category browsing"
              onPress={() => navigation.navigate("MerchantApply")}
            />

            <Divider />
            <EditableField
              icon="mail-outline"
              label="Contact"
              field="support_email"
              value={contact}
              placeholder="Email buyers can use"
              emptyConsequence="buyers can't reach you before ordering"
              locks={owner.locks}
              error={rejected.support_email}
              keyboardType="email-address"
              onChangeValue={editField}
              onBlocked={explainLock}
            />

            <Divider />
            <EditableField
              icon="location-outline"
              label="Location"
              field="public_city"
              value={location}
              placeholder="City buyers ship from"
              emptyConsequence="buyers can't judge shipping distance"
              locks={owner.locks}
              error={rejected.public_city}
              onChangeValue={editField}
              onBlocked={explainLock}
            />

            <Divider />
            {/* Real, from the server's seven-entry grid, resolved through
                `openingStatus` so a dated override outranks the weekly pattern.
                It used to be an `UnbackedDetailRow` reading "this field is coming"
                — the exact phrasing the gap register names as what an
                unregistered gap looks like once it reaches a seller.

                Read-only rather than tappable: the weekly-grid editor is a
                separate screen that does not exist yet, and a chevron opening
                nothing would be the dead end this row was supposed to stop being.
                Registered as `hours_editor_screen`. */}
            <StaticDetailRow
              icon="time-outline"
              label="Opening hours"
              value={hoursValue}
              emptyConsequence="buyers can't see when you're open"
            />

            <Divider />
            {/* The website link, edited in place and saved through
                `POST /profile/link` rather than the field endpoint — links are a
                positioned collection server-side, not a text column. Additional
                kinds (Instagram, TikTok…) need the collection editor, so this row
                shows how many others exist without pretending to edit them. */}
            <EditableField
              icon="link-outline"
              label="Links"
              field={WEBSITE_FIELD}
              value={fields[WEBSITE_FIELD] || ""}
              placeholder="https://"
              emptyConsequence="buyers can't check you out elsewhere"
              locks={owner.locks}
              error={rejected[WEBSITE_FIELD]}
              keyboardType="url"
              note={linksValue}
              onChangeValue={editField}
              onBlocked={explainLock}
            />
          </LivePanel>
        </Animated.View>

        <Animated.View style={entrance.styleFor(5)}>
          <SectionHeading title="Connected" caption="What this profile is attached to." />
          <LivePanel>
            <ConnectedRow
              icon="storefront-outline"
              label="Store"
              detail={listings.length ? `${listings.length} ${plural(listings.length, "listing")}` : "No listings yet"}
              empty={!listings.length}
              onPress={() => navigation.navigate("SellerStore", { mode: "dashboard" })}
            />
            <Divider />
            <ConnectedRow
              icon="cart-outline"
              label="Marketplace"
              detail={activeListings ? `${activeListings} active` : "Nothing live right now"}
              empty={!activeListings}
              onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}
            />
            <Divider />
            <ConnectedRow
              icon="calendar-outline"
              label="Events"
              // There is no user-owned events feed — `listScheduledLiveEvents`
              // returns public discovery only — so this cannot show a count.
              detail="No events linked to this business yet"
              empty
              onPress={() => navigation.navigate("Events", { mode: "events" })}
            />
          </LivePanel>
        </Animated.View>

        <Animated.View style={entrance.styleFor(6)}>
          {/* One state, from one resolver. The screen no longer OR-s a
              `verified_badge` into the answer: the server already applied the
              precedence — request outranks application outranks badge, and the
              badge may only raise to approved — and two clients each doing their
              own version of that is how the same business read "in review" here
              and "Approved" one screen away. */}
          <TrustCallout
            title={`Verification · ${VERIFICATION_LABELS[verificationState]}`}
            body={verificationBody(verificationState)}
            actionLabel="Check status"
            reducedMotion={reducedMotion}
            onPress={() => navigation.navigate("VerificationCenter", { track: "business" })}
          />
        </Animated.View>

        {/* The section's roadmap. Appended below the live content rather than
            woven into it, so everything that works today keeps its order and
            this panel reads as what it is — what is coming next, not another
            control competing with the real ones. */}
        <BusinessOsModules section="profile" />
      </ScrollView>

      <Animated.View
        style={[styles.footerDock, { paddingBottom: Math.max(insets.bottom, 12) }, entrance.styleFor(7)]}
      >
        <FooterActions
          dirty={dirty}
          saving={saving}
          discardLabel="Discard"
          saveLabel={saving ? "Saving…" : "Save changes"}
          onDiscard={discard}
          onSave={save}
          reducedMotion={reducedMotion}
        />
      </Animated.View>
    </View>
  );
}

/* -------------------------------------------------------------- helpers */

function Divider() {
  return <View style={styles.divider} />;
}

/**
 * One field, deciding for itself whether it can be edited.
 *
 * This is where the old screen's single `editable` flag is replaced. The server
 * reports two different things and they are not interchangeable:
 *
 * - `blocked` — the write will be refused. The row stays readable and says why
 *   when pressed, because a control that looks live and quietly does nothing
 *   teaches the operator the wrong thing about their own business.
 * - `requiresReview` — the write lands and a reviewer is told. The field is
 *   fully editable; the row says what editing it will cost so the seller can
 *   decide, rather than discovering it after the fact.
 *
 * Collapsing the two is what froze thirteen fields because one was sensitive.
 */
function EditableField({
  icon,
  label,
  field,
  value,
  placeholder,
  emptyConsequence,
  locks,
  error,
  keyboardType,
  note,
  onChangeValue,
  onBlocked
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  field: string;
  value: string;
  placeholder: string;
  emptyConsequence: string;
  locks: ProfileLocks;
  error?: string;
  keyboardType?: "url" | "email-address";
  note?: string;
  onChangeValue: (field: string, next: string) => void;
  onBlocked: (label: string) => void;
}) {
  if (fieldIsBlocked(locks, field)) {
    return (
      <DetailRow
        icon={icon}
        label={`${label} (locked)`}
        value={value}
        emptyConsequence={emptyConsequence}
        onPress={() => onBlocked(label)}
      />
    );
  }

  const review = fieldRequiresReview(locks, field);
  return (
    <EditableDetailRow
      icon={icon}
      label={review ? `${label} (a reviewer will check this)` : label}
      value={value}
      placeholder={placeholder}
      emptyConsequence={emptyConsequence}
      error={error}
      note={note}
      keyboardType={keyboardType}
      onChangeValue={(next) => onChangeValue(field, next)}
    />
  );
}

function Notice({ text, tone }: { text: string; tone: "muted" | "warning" | "accent" }) {
  const color = tone === "warning" ? palette.warning : tone === "accent" ? palette.accent : palette.textMuted;
  return (
    <View style={[styles.notice, { borderColor: color }]} accessible accessibilityRole="alert" accessibilityLabel={text}>
      <Text style={[styles.noticeText, { color }]}>{text}</Text>
    </View>
  );
}

function completenessHeadline(percent: number) {
  const value = Math.round(Number.isFinite(percent) ? percent : 0);
  if (value >= 100) return "Your profile is complete";
  if (value >= 70) return "Almost buyer-ready";
  if (value >= 35) return "Halfway there";
  return "Your profile needs work";
}

/**
 * One specific next step, named by the server's own completion breakdown.
 *
 * The server returns which items are missing and which single one is next, so the
 * line can say "Add a business logo" instead of "Your profile is 62% complete" —
 * a percentage is a score, not an instruction. Falls back to a locally-detected
 * empty field so the line is never generic filler, and only becomes a
 * congratulation when there is genuinely nothing left.
 */
function nextStepSuggestion(owner: OwnerProfile, fields: Record<string, string>) {
  if (owner.completion.nextLabel) return `${owner.completion.nextLabel} — it's the next thing buyers look for.`;
  const [missing] = owner.completion.missing;
  if (missing) return `${missing.label} — it's the next thing buyers look for.`;
  if (!fields.business_name?.trim()) return "Add a business name so buyers see your brand, not your personal name.";
  if (!fields.tagline?.trim()) return "Write a short tagline — it is the first thing a buyer reads.";
  if (!fields[WEBSITE_FIELD]?.trim()) return "Add a link so buyers can check you out elsewhere.";
  if (owner.completion.percent >= 100) return "Nothing left to fill in. Keep your listings fresh.";
  return "Review your details — something above is still missing.";
}

/**
 * What the verification state means for the seller, in their terms.
 *
 * Keyed on the server's ten-value vocabulary rather than a set of ad-hoc strings.
 * The old version tested for `in_review` and `needs_more_info`, which are not
 * states this system has — so a business that was genuinely under review fell
 * through to the unverified sales pitch.
 */
function verificationBody(state: VerificationState) {
  switch (state) {
    case "approved":
      return "Your business is verified. Buyers see the badge on your profile.";
    case "submitted":
    case "under_review":
      return "We're reviewing your documents. No action needed right now.";
    case "needs_information":
      return "We need one more document before verification can finish.";
    case "rejected":
      return "Verification didn't pass. Open the centre to see what to fix.";
    case "suspended":
    case "revoked":
      return "Verification has been withdrawn. Some profile fields are locked until this is resolved.";
    case "expired":
      return "Your verification has expired. Re-submit to get the badge back.";
    case "draft":
      return "You started a verification and haven't submitted it yet.";
    default:
      return "Unverified businesses convert fewer buyers. Verification takes a few minutes.";
  }
}

/** Active means "publicly purchasable", which is a status *and* an approval. */
function isActiveListing(listing: MarketplaceListing) {
  const status = String(listing.status || "").toLowerCase();
  const approval = String(listing.approval_status || "").toLowerCase();
  if (status && !["active", "live", "published"].includes(status)) return false;
  if (approval && ["rejected", "pending", "removed"].includes(approval)) return false;
  return Boolean(status || approval);
}

function isOpenOrder(order: MarketplaceSellerOrder) {
  const status = String(order.status || "").toLowerCase();
  return !["completed", "cancelled", "canceled", "refunded", "failed"].includes(status);
}

function formatCount(value?: number) {
  const count = Number(value || 0);
  if (!Number.isFinite(count) || count < 0) return "0";
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return String(count);
}

function plural(count: number, word: string) {
  return count === 1 ? word : `${word}s`;
}

function yearOf(value?: string | null) {
  if (!value) return "";
  const match = String(value).match(/\d{4}/);
  return match ? match[0] : "";
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: palette.background },
  pressed: { opacity: 0.72 },

  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.md,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingTop: logiNexus.spacing.md,
    paddingBottom: logiNexus.spacing.sm
  },
  backButton: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  headerTitle: { ...logiNexus.typography.title, color: palette.textPrimary, flex: 1 },
  headerActions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    paddingHorizontal: logiNexus.spacing.lg,
    paddingBottom: logiNexus.spacing.md
  },

  scroll: { flex: 1 },
  content: { padding: logiNexus.spacing.lg, gap: logiNexus.spacing.xl },
  loading: { paddingVertical: logiNexus.spacing.xl, alignItems: "center" },

  notice: {
    borderRadius: logiNexus.radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md,
    backgroundColor: palette.panel
  },
  noticeText: { ...logiNexus.typography.metadata },

  meterPanel: { padding: logiNexus.spacing.lg },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: palette.hairline, marginLeft: 62 },

  footerDock: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingTop: logiNexus.spacing.md,
    backgroundColor: palette.panelStrong,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline
  }
});
