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
 * Four live sources are stitched together here: the seller application (business
 * name, contact, location, links, description, category, and a server-computed
 * completeness), the Pulse profile (handle, avatar, cover, follower count,
 * verified badge), the verification subsystem (business track status), and the
 * seller store snapshot (listing and order counts).
 *
 * Several fields in the design have no backing API yet — opening hours, seller
 * rating, on-time rate, average reply time, profile views, follower deltas,
 * store-click trend, next ship day, and user-owned events. Every one of them is
 * rendered as an explicit, dimmed placeholder rather than a plausible number.
 * The registry that routes this screen says `backed` "reflects verified live
 * coverage, not aspiration"; inventing a 4.8-star rating here would violate the
 * same rule one layer up. Each gap is listed in the mission report.
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
  emptySellerApplication,
  loadCachedSellerApplication,
  loadSellerApplication,
  saveSellerApplicationDraft,
  SellerApplicationFields,
  SellerApplicationView,
  sellerApplicationIsEditable
} from "../api/sellerApplication";
import { getMyProfile, loadCachedProfile, PulseProfile } from "../api/profile";
import {
  loadCachedSellerStore,
  loadSellerStoreSnapshot,
  MarketplaceListing,
  MarketplaceSellerOrder
} from "../api/marketplace";
import { loadCachedVerificationState, loadVerificationState, VerificationState, verificationStatusLabel } from "../api/verification";
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
  TrustCallout,
  UnbackedDetailRow
} from "../components/businessProfile/BusinessLiveParts";
import { registerSyncInvalidation } from "../core/eventSync";
import { logiNexus } from "../theme/logiNexus";
import { useBusinessLiveEntrance } from "../theme/businessLiveMotion";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

const palette = logiNexus.colors.businessLive;

/** Sections in render order. The entrance cascade indexes into this. */
const SECTION_COUNT = 8;

/** Placeholder marker for a metric with no API behind it yet. */
const NO_DATA = "Not tracked yet";

type Props = {
  navigation: { navigate: (...args: any[]) => void; goBack: () => void };
};

export function BusinessProfileScreen({ navigation }: Props) {
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useBusinessLiveEntrance(SECTION_COUNT, reducedMotion);

  const [application, setApplication] = useState<SellerApplicationView>(emptySellerApplication);
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [verification, setVerification] = useState<VerificationState | null>(null);
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [orders, setOrders] = useState<MarketplaceSellerOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  /**
   * Unsaved edits, held as a sparse overlay on top of the server's field set
   * rather than as a full copy. A sparse overlay is what makes "is anything
   * dirty" a fact about the data instead of a flag someone has to remember to
   * set, and it means a background refresh can replace the server fields
   * underneath without silently discarding what the user typed.
   */
  const [draft, setDraft] = useState<SellerApplicationFields>({});
  const mounted = useRef(true);

  const editable = sellerApplicationIsEditable(application);
  const dirty = Object.keys(draft).length > 0;

  const load = useCallback(async () => {
    setMessage("");
    // Cache first: the screen should never show a spinner to someone who has
    // opened it before.
    const [cachedApplication, cachedProfile, cachedVerification, cachedStore] = await Promise.all([
      loadCachedSellerApplication().catch(() => null),
      loadCachedProfile("me").catch(() => null),
      loadCachedVerificationState().catch(() => null),
      loadCachedSellerStore().catch(() => null)
    ]);
    if (!mounted.current) return;
    if (cachedApplication) setApplication(cachedApplication);
    if (cachedProfile) setProfile(cachedProfile);
    if (cachedVerification) setVerification(cachedVerification);
    if (cachedStore) {
      setListings(cachedStore.listings || []);
      setOrders(cachedStore.orders || []);
    }
    if (cachedApplication || cachedProfile) setLoading(false);

    // `allSettled`, not `all`: verification and the store snapshot are
    // supporting detail. One of them failing must not blank out a profile that
    // loaded perfectly well.
    const [freshApplication, freshProfile, freshVerification, freshStore] = await Promise.allSettled([
      loadSellerApplication(),
      getMyProfile(),
      loadVerificationState(),
      loadSellerStoreSnapshot()
    ]);
    if (!mounted.current) return;

    if (freshApplication.status === "fulfilled") setApplication(freshApplication.value);
    if (freshProfile.status === "fulfilled") setProfile(freshProfile.value);
    if (freshVerification.status === "fulfilled") setVerification(freshVerification.value);
    if (freshStore.status === "fulfilled") {
      setListings(freshStore.value.listings || []);
      setOrders(freshStore.value.orders || []);
    }

    const primaryFailed = freshApplication.status === "rejected" && freshProfile.status === "rejected";
    setOffline(primaryFailed && Boolean(cachedApplication || cachedProfile));
    if (primaryFailed && !cachedApplication && !cachedProfile) {
      const reason = freshApplication.reason;
      setMessage(reason instanceof Error ? reason.message : "Business profile could not load.");
    }
    setLoading(false);
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

  const fields = useMemo(() => ({ ...application.fields, ...draft }), [application.fields, draft]);

  const fieldErrors = useMemo(() => {
    // The application's own step errors are the existing validation surface.
    // Re-surfacing them here rather than writing a second set of rules is what
    // keeps this screen and the application form agreeing with each other.
    const merged: Record<string, string> = {};
    application.steps.forEach((step) => Object.assign(merged, step.errors || {}));
    return merged;
  }, [application.steps]);

  const categoryLabel = useMemo(() => {
    const match = application.seller_types.find((type) => type.key === fields.seller_type);
    return match?.label || fields.seller_type || "";
  }, [application.seller_types, fields.seller_type]);

  const businessName = fields.business_name?.trim() || fields.display_name?.trim() || profile?.display_name || "Your business";
  const handle = profile?.username ? `@${profile.username}` : fields.pulse_username ? `@${fields.pulse_username}` : "";
  const location = [fields.state_region, fields.country].filter(Boolean).join(", ");
  const contact = [fields.email, fields.phone].filter(Boolean).join(" · ");
  const memberSince = yearOf(application.submitted_at) || yearOf(application.updated_at);
  const activeListings = listings.filter((listing) => isActiveListing(listing)).length;
  const openOrders = orders.filter((order) => isOpenOrder(order)).length;

  const nextStep = useMemo(() => nextStepSuggestion(application, fields), [application, fields]);

  const tickerStats = useMemo<LiveTickerStat[]>(
    () => [
      { key: "completeness", label: "Profile complete", value: `${Math.round(application.completeness)}%` },
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
    [activeListings, application.completeness, openOrders, profile?.follower_count]
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

  function editField(key: keyof SellerApplicationFields, value: string) {
    setDraft((current) => {
      const next = { ...current, [key]: value };
      // Typing a value back to what the server already has un-dirties the field
      // rather than leaving a no-op edit behind that would keep Save lit.
      if ((application.fields[key] || "") === value) delete next[key];
      return next;
    });
  }

  function discard() {
    setDraft({});
    setMessage("");
  }

  async function save() {
    if (!dirty || saving) return;
    setSaving(true);
    setMessage("");
    try {
      // The existing draft endpoint, unchanged. It patches only whitelisted
      // writable fields and refuses to touch status, so sending the merged set
      // cannot escalate the application.
      const updated = await saveSellerApplicationDraft({ ...application.fields, ...draft });
      if (!mounted.current) return;
      setApplication(updated);
      setDraft({});
      setMessage("Saved.");
    } catch (error) {
      if (!mounted.current) return;
      setMessage(error instanceof Error ? error.message : "Changes could not be saved.");
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  const verificationStatus = verification?.status || "not_started";
  const verified = Boolean(profile?.verified_badge) || verificationStatus === "approved";

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
        <LiveSyncBadge reducedMotion={reducedMotion} label="LIVE SYNC" />
      </Animated.View>

      <View style={styles.headerActions}>
        <GhostPill
          label="View as buyer"
          icon="eye-outline"
          accessibilityLabel="View as buyer"
          accessibilityHint="Opens the read-only public profile buyers see"
          onPress={() =>
            navigation.navigate("ProfileDetail", {
              username: profile?.username,
              userId: profile?.user_id,
              source: "business_profile_preview",
              title: businessName
            })
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
              percent={application.completeness}
              headline={completenessHeadline(application.completeness)}
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
            bio={fields.business_description?.trim() || profile?.bio?.trim() || ""}
            chips={chips}
            stats={previewStats}
            reducedMotion={reducedMotion}
          />
        </Animated.View>

        <Animated.View style={entrance.styleFor(4)}>
          <SectionHeading
            title="Profile details"
            caption={editable ? "Tap a field to edit it." : "Locked while your application is in review."}
          />
          <LivePanel>
            {editable ? (
              <EditableDetailRow
                icon="business-outline"
                label="Business name"
                value={fields.business_name || ""}
                placeholder="What buyers should call you"
                emptyConsequence="buyers see your personal name instead"
                error={fieldErrors.business_name}
                onChangeValue={(next) => editField("business_name", next)}
              />
            ) : (
              <DetailRow
                icon="business-outline"
                label="Business name"
                value={fields.business_name || ""}
                emptyConsequence="buyers see your personal name instead"
                onPress={() => navigation.navigate("MerchantApply")}
              />
            )}

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
            <DetailRow
              icon="mail-outline"
              label="Contact"
              value={contact}
              emptyConsequence="buyers can't reach you before ordering"
              onPress={() => navigation.navigate("MerchantApply")}
            />

            <Divider />
            <DetailRow
              icon="location-outline"
              label="Location"
              value={location}
              emptyConsequence="buyers can't judge shipping distance"
              onPress={() => navigation.navigate("MerchantApply")}
            />

            <Divider />
            <UnbackedDetailRow
              icon="time-outline"
              label="Opening hours"
              emptyConsequence="buyers can't see when you're open"
              note="Opening hours aren't stored yet — this field is coming."
            />

            <Divider />
            {editable ? (
              <EditableDetailRow
                icon="link-outline"
                label="Links"
                value={fields.website || ""}
                placeholder="https://"
                emptyConsequence="buyers can't check you out elsewhere"
                error={fieldErrors.website}
                keyboardType="url"
                onChangeValue={(next) => editField("website", next)}
              />
            ) : (
              <DetailRow
                icon="link-outline"
                label="Links"
                value={fields.website || ""}
                emptyConsequence="buyers can't check you out elsewhere"
                onPress={() => navigation.navigate("MerchantApply")}
              />
            )}
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
          <TrustCallout
            title={`Verification · ${verificationStatusLabel(verificationStatus)}`}
            body={verificationBody(verificationStatus)}
            actionLabel="Check status"
            reducedMotion={reducedMotion}
            onPress={() => navigation.navigate("VerificationCenter", { track: "business" })}
          />
        </Animated.View>
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
 * One specific next step, taken from the first incomplete application step the
 * server reported. Falls back to a locally-detected empty field so the line is
 * never generic filler, and only becomes a congratulation when there is genuinely
 * nothing left.
 */
function nextStepSuggestion(application: SellerApplicationView, fields: SellerApplicationFields) {
  const incomplete = application.steps.find((step) => !step.complete);
  if (incomplete) return incomplete.summary || `Finish "${incomplete.title}" to move forward.`;
  if (!fields.business_name?.trim()) return "Add a business name so buyers see your brand, not your personal name.";
  if (!fields.business_description?.trim()) return "Write a short bio — it is the first thing a buyer reads.";
  if (!fields.website?.trim()) return "Add a link so buyers can check you out elsewhere.";
  if (application.completeness >= 100) return "Nothing left to fill in. Keep your listings fresh.";
  return "Review your details — something above is still missing.";
}

function verificationBody(status: string) {
  if (status === "approved") return "Your business is verified. Buyers see the badge on your profile.";
  if (status === "in_review" || status === "submitted") return "We're reviewing your documents. No action needed right now.";
  if (status === "needs_more_info") return "We need one more document before verification can finish.";
  if (status === "rejected") return "Verification didn't pass. Open the centre to see what to fix.";
  return "Unverified businesses convert fewer buyers. Verification takes a few minutes.";
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

function yearOf(value?: string) {
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
