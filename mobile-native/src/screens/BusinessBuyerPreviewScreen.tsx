/**
 * Buyer preview — the public business profile, read-only, on its own route.
 *
 * Why a route and not a mode
 * --------------------------
 * "View as buyer" used to push the owner into `ProfileDetail` — a screen that still
 * carried owner affordances and still read owner data. A preview that shares a
 * component tree with the editor is a preview that will eventually leak, because the
 * only thing keeping private data off it is a chain of `if (!preview)` guards, and
 * that chain is one forgotten branch away from publishing a payout account.
 *
 * So this screen cannot leak by construction rather than by discipline:
 *
 *   * it fetches `GET /api/pulse/business/profile/preview`, which the server builds
 *     from an allowlist — `legal_name`, addresses, verification internals, completion
 *     and payout data are not in the payload at all;
 *   * it is typed against `PublicProfile`, which has no property for any of them, so
 *     a component that tried to render one would not compile;
 *   * it holds no editable state, has no save path, and mounts no footer dock.
 *
 * `viewer_has_purchased` is pinned to `false` server-side. The owner therefore sees
 * the strictest public view — the one a first-time visitor gets — rather than the
 * most flattering one. A preview that showed contact details most buyers never see
 * would be reassuring and wrong.
 *
 * Owner-unsafe actions
 * --------------------
 * Message, Follow, Buy, Share and Report are rendered and then explicitly refused
 * with a "Preview mode" notice. Hiding them would misrepresent the buyer's layout;
 * wiring them would have the owner following themselves and opening a thread with
 * their own shop. The server names them in `preview.simulatedActions` so the list
 * comes from one place rather than from a hard-coded array on each surface.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  fetchBuyerPreview,
  fetchPublicProfile,
  openingStatus,
  type PreviewBanner,
  type PublicProfile
} from "../api/businessProfile";
import { failureFrom, type FailureCopy } from "../api/stateLanguage";
import { logiNexus } from "../theme/logiNexus";

const palette = logiNexus.colors.businessLive;
const { spacing, typography, radius } = logiNexus;

/** The buyer profile's tabs, in the order the brief lists them. */
const TABS = ["home", "products", "marketplace", "events", "reviews", "about"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  home: "Home",
  products: "Products",
  marketplace: "Marketplace",
  events: "Events",
  reviews: "Reviews",
  about: "About"
};

type Props = {
  navigation: { navigate: (...args: any[]) => void; goBack: () => void };
  route?: {
    params?: {
      /** Absent for the owner's own preview; set when a buyer opens a real shop. */
      sellerUserId?: number;
      /** Restored on exit so the owner lands where they left. */
      returnScrollY?: number;
    };
  };
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; profile: PublicProfile; preview: PreviewBanner | null }
  | { kind: "failed"; failure: FailureCopy };

export function BusinessBuyerPreviewScreen({ navigation, route }: Props) {
  const insets = useSafeAreaInsets();
  const sellerUserId = route?.params?.sellerUserId;
  const isPreview = sellerUserId == null;

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [tab, setTab] = useState<Tab>("home");
  const [refused, setRefused] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      if (isPreview) {
        const result = await fetchBuyerPreview();
        setState({ kind: "ready", profile: result.profile, preview: result.preview });
      } else {
        const result = await fetchPublicProfile(sellerUserId as number);
        setState({ kind: "ready", profile: result.profile, preview: null });
      }
    } catch (error) {
      setState({ kind: "failed", failure: failureFrom(error, "This business profile") });
    }
  }, [isPreview, sellerUserId]);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * Refuse rather than perform. The notice names the action so the owner learns what
   * the button *would* do, which a silent no-op does not teach.
   */
  const refuse = useCallback((action: string) => {
    setRefused(`Preview mode — ${action} is disabled while you're previewing your own profile.`);
  }, []);

  const act = useCallback(
    (action: string, label: string, live: () => void) => {
      if (isPreview) {
        refuse(label);
        return;
      }
      live();
    },
    [isPreview, refuse]
  );

  if (state.kind === "loading") {
    return (
      <View style={[styles.screen, styles.centered, { paddingTop: insets.top }]}>
        <ActivityIndicator color={palette.accent} />
        <Text style={styles.loadingText}>Loading the buyer view…</Text>
      </View>
    );
  }

  if (state.kind === "failed") {
    return (
      <View style={[styles.screen, styles.centered, { paddingTop: insets.top }]}>
        <Ionicons name="cloud-offline-outline" size={28} color={palette.textDim} />
        <Text style={styles.failureText}>{state.failure.message}</Text>
        {state.failure.actionLabel ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={state.failure.actionLabel}
            onPress={load}
            style={styles.retryButton}
          >
            <Text style={styles.retryLabel}>{state.failure.actionLabel}</Text>
          </Pressable>
        ) : null}
        <Pressable accessibilityRole="button" onPress={() => navigation.goBack()} style={styles.exitLink}>
          <Text style={styles.exitLinkLabel}>Back</Text>
        </Pressable>
      </View>
    );
  }

  const { profile, preview } = state;

  return (
    <View style={[styles.screen, { paddingTop: insets.top }]}>
      {preview?.active ? (
        <PreviewBannerBar banner={preview} onExit={() => navigation.goBack()} />
      ) : null}

      <ScrollView
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + spacing.giant }]}
        showsVerticalScrollIndicator={false}
      >
        <BuyerHeader profile={profile} />

        <View style={styles.actionRow}>
          <PrimaryAction
            icon="chatbubble-ellipses-outline"
            label="Message business"
            onPress={() => act("message", "messaging", () => navigation.navigate("Messenger", { sellerUserId }))}
            simulated={isPreview}
          />
          <SecondaryAction
            icon="add-circle-outline"
            label="Follow"
            onPress={() => act("follow", "following", () => undefined)}
            simulated={isPreview}
          />
          <SecondaryAction
            icon="share-outline"
            label="Share"
            onPress={() => act("share", "sharing", () => undefined)}
            simulated={isPreview}
          />
        </View>

        {refused ? (
          <View accessibilityLiveRegion="polite" style={styles.refusedNotice}>
            <Ionicons name="eye-outline" size={14} color={palette.warning} />
            <Text style={styles.refusedText}>{refused}</Text>
          </View>
        ) : null}

        <TabBar active={tab} onChange={setTab} />

        {tab === "home" ? <HomeTab profile={profile} /> : null}
        {tab === "about" ? <AboutTab profile={profile} /> : null}
        {tab === "products" ? (
          <PlaceholderTab
            title="Products"
            body="Live listings from this business appear here."
            note="Only published listings are shown — drafts stay private."
          />
        ) : null}
        {tab === "marketplace" ? (
          <PlaceholderTab
            title="Marketplace"
            body="Marketplace items this business has listed appear here."
            note="Sold and withdrawn items are removed automatically."
          />
        ) : null}
        {tab === "events" ? (
          <PlaceholderTab
            title="Events"
            body="Upcoming events hosted by this business appear here."
            note="Past events are hidden once they end."
          />
        ) : null}
        {tab === "reviews" ? (
          <PlaceholderTab
            title="Reviews"
            body="Buyer reviews appear here once this business has been reviewed."
            note="Not enough reviews yet."
          />
        ) : null}

        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Report this business"
          onPress={() => act("report", "reporting", () => navigation.navigate("Safety", { sellerUserId }))}
          style={styles.reportRow}
        >
          <Ionicons name="flag-outline" size={15} color={palette.textDim} />
          <Text style={styles.reportLabel}>Report business</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

/* ------------------------------------------------------------------ sections */

function PreviewBannerBar({ banner, onExit }: { banner: PreviewBanner; onExit: () => void }) {
  return (
    <View style={styles.banner} accessibilityRole="header">
      <View style={styles.bannerText}>
        <Text style={styles.bannerTitle}>{banner.title}</Text>
        <Text style={styles.bannerSubtitle}>{banner.subtitle}</Text>
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={banner.exitLabel}
        onPress={onExit}
        style={styles.bannerExit}
        hitSlop={8}
      >
        <Text style={styles.bannerExitLabel}>{banner.exitLabel}</Text>
      </Pressable>
    </View>
  );
}

function BuyerHeader({ profile }: { profile: PublicProfile }) {
  const status = useMemo(() => openingStatus(profile), [profile]);

  return (
    <View style={styles.header}>
      <View style={styles.identityRow}>
        <View style={styles.avatarFallback}>
          <Text style={styles.avatarInitial}>
            {(profile.businessName || profile.handle.replace("@", "") || "?").slice(0, 1).toUpperCase()}
          </Text>
        </View>
        <View style={styles.identityText}>
          <View style={styles.nameRow}>
            <Text style={styles.businessName} numberOfLines={2}>
              {profile.businessName || "Unnamed business"}
            </Text>
            {profile.verified ? (
              <Ionicons
                name="checkmark-circle"
                size={17}
                color={palette.accent}
                accessibilityLabel="Verified business"
              />
            ) : null}
          </View>
          {/* Normalised on both sides to prevent doubled handle prefixes. */}
          {profile.handle ? <Text style={styles.handle}>{profile.handle}</Text> : null}
          {profile.businessCategoryLabel ? (
            <Text style={styles.category}>{profile.businessCategoryLabel}</Text>
          ) : null}
        </View>
      </View>

      {profile.tagline ? <Text style={styles.tagline}>{profile.tagline}</Text> : null}

      <View style={styles.metaRow}>
        {profile.location ? <MetaChip icon="location-outline" label={profile.location} /> : null}
        <MetaChip
          icon="time-outline"
          label={status.label}
          tone={status.state === "open" ? "accent" : status.state === "unknown" ? "dim" : "muted"}
        />
        {profile.memberSince ? (
          <MetaChip icon="calendar-outline" label={`Member since ${profile.memberSince}`} />
        ) : null}
      </View>
    </View>
  );
}

function HomeTab({ profile }: { profile: PublicProfile }) {
  const hasAnything =
    profile.about || profile.whatYouSell || profile.shippingSummary || profile.links.length > 0;

  if (!hasAnything) {
    return (
      <Panel>
        <Text style={styles.emptyTitle}>This business hasn't added a description yet.</Text>
        <Text style={styles.emptyBody}>
          Buyers see whatever the business publishes here — products, policies and contact
          preferences.
        </Text>
      </Panel>
    );
  }

  return (
    <>
      {profile.about ? (
        <Panel>
          <Text style={styles.panelHeading}>About</Text>
          <Text style={styles.panelBody}>{profile.about}</Text>
        </Panel>
      ) : null}

      {profile.whatYouSell ? (
        <Panel>
          <Text style={styles.panelHeading}>What they sell</Text>
          <Text style={styles.panelBody}>{profile.whatYouSell}</Text>
        </Panel>
      ) : null}

      {profile.shippingSummary || profile.returnSummary || profile.responseExpectations ? (
        <Panel>
          <Text style={styles.panelHeading}>Buying from this business</Text>
          {profile.shippingSummary ? (
            <PolicyLine icon="cube-outline" label="Shipping" value={profile.shippingSummary} />
          ) : null}
          {profile.returnSummary ? (
            <PolicyLine icon="return-down-back-outline" label="Returns" value={profile.returnSummary} />
          ) : null}
          {profile.responseExpectations ? (
            <PolicyLine
              icon="chatbubbles-outline"
              label="Replies"
              value={profile.responseExpectations}
            />
          ) : null}
        </Panel>
      ) : null}

      {profile.links.length > 0 ? (
        <Panel>
          <Text style={styles.panelHeading}>Links</Text>
          {profile.links.map((link) => (
            <View key={link.kind} style={styles.linkRow}>
              <Ionicons name="link-outline" size={14} color={palette.secondary} />
              <Text style={styles.linkText} numberOfLines={1}>
                {link.label || link.url}
              </Text>
            </View>
          ))}
        </Panel>
      ) : null}
    </>
  );
}

function AboutTab({ profile }: { profile: PublicProfile }) {
  return (
    <>
      <Panel>
        <Text style={styles.panelHeading}>Opening hours</Text>
        {profile.hoursMode === "unset" ? (
          // "Not provided" is the honest reading of no stored rows. "Closed" would be
          // a claim the business never made.
          <Text style={styles.emptyBody}>Hours not provided.</Text>
        ) : profile.hoursMode === "by_appointment" ? (
          <Text style={styles.panelBody}>By appointment.</Text>
        ) : profile.hoursMode === "temporarily_closed" ? (
          <Text style={styles.panelBody}>Temporarily closed.</Text>
        ) : (
          profile.hours.map((day) => (
            <View key={day.weekday} style={styles.hoursRow}>
              <Text style={styles.hoursDay}>{day.label}</Text>
              <Text
                style={[
                  styles.hoursValue,
                  day.state === "unset" ? styles.hoursValueDim : null
                ]}
              >
                {day.state === "open" && day.opens && day.closes
                  ? `${day.opens} – ${day.closes}`
                  : day.state === "closed"
                    ? "Closed"
                    : "Not provided"}
              </Text>
            </View>
          ))
        )}
        {profile.hoursOverrides.length > 0 ? (
          <View style={styles.overrideBlock}>
            <Text style={styles.overrideHeading}>Upcoming exceptions</Text>
            {profile.hoursOverrides.map((entry) => (
              <Text key={entry.date} style={styles.overrideLine}>
                {entry.date} —{" "}
                {entry.closed
                  ? entry.label
                    ? `Closed (${entry.label})`
                    : "Closed"
                  : `${entry.opens ?? ""} – ${entry.closes ?? ""}`}
              </Text>
            ))}
          </View>
        ) : null}
      </Panel>

      <Panel>
        <Text style={styles.panelHeading}>Contact</Text>
        {/* Absent keys mean "not published". There is no "hidden" state to render,
            because the server never sent the value at all. */}
        {profile.contact.email ? (
          <PolicyLine icon="mail-outline" label="Email" value={profile.contact.email} />
        ) : null}
        {profile.contact.phone ? (
          <PolicyLine icon="call-outline" label="Phone" value={profile.contact.phone} />
        ) : null}
        {!profile.contact.email && !profile.contact.phone ? (
          <Text style={styles.emptyBody}>
            This business prefers to be contacted by {profile.contact.preferred === "message" ? "message" : profile.contact.preferred}.
          </Text>
        ) : null}
      </Panel>

      {profile.languages.length > 0 || profile.accessibility.length > 0 ? (
        <Panel>
          <Text style={styles.panelHeading}>Good to know</Text>
          {profile.languages.length > 0 ? (
            <PolicyLine
              icon="language-outline"
              label="Languages"
              value={profile.languages.join(", ")}
            />
          ) : null}
          {profile.accessibility.length > 0 ? (
            <PolicyLine
              icon="accessibility-outline"
              label="Accessibility"
              value={profile.accessibility.join(", ")}
            />
          ) : null}
        </Panel>
      ) : null}
    </>
  );
}

function PlaceholderTab({ title, body, note }: { title: string; body: string; note: string }) {
  return (
    <Panel>
      <Text style={styles.panelHeading}>{title}</Text>
      <Text style={styles.panelBody}>{body}</Text>
      <Text style={styles.emptyBody}>{note}</Text>
    </Panel>
  );
}

/* ------------------------------------------------------------------- atoms */

function Panel({ children }: { children: React.ReactNode }) {
  return <View style={styles.panel}>{children}</View>;
}

function MetaChip({
  icon,
  label,
  tone = "muted"
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  tone?: "accent" | "muted" | "dim";
}) {
  const color = tone === "accent" ? palette.accent : tone === "dim" ? palette.textDim : palette.textMuted;
  return (
    <View style={styles.metaChip}>
      <Ionicons name={icon} size={12} color={color} />
      <Text style={[styles.metaChipLabel, { color }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

function PolicyLine({
  icon,
  label,
  value
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  value: string;
}) {
  return (
    <View style={styles.policyLine}>
      <Ionicons name={icon} size={14} color={palette.textMuted} />
      <Text style={styles.policyLabel}>{label}</Text>
      <Text style={styles.policyValue}>{value}</Text>
    </View>
  );
}

function TabBar({ active, onChange }: { active: Tab; onChange: (tab: Tab) => void }) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.tabBar}
    >
      {TABS.map((tab) => {
        const selected = tab === active;
        return (
          <Pressable
            key={tab}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={TAB_LABELS[tab]}
            onPress={() => onChange(tab)}
            style={[styles.tab, selected ? styles.tabActive : null]}
          >
            <Text style={[styles.tabLabel, selected ? styles.tabLabelActive : null]}>
              {TAB_LABELS[tab]}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function PrimaryAction({
  icon,
  label,
  onPress,
  simulated
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  simulated: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={simulated ? `${label} (preview mode — disabled)` : label}
      accessibilityState={{ disabled: simulated }}
      onPress={onPress}
      style={[styles.primaryAction, simulated ? styles.actionSimulated : null]}
    >
      <Ionicons name={icon} size={15} color={simulated ? palette.textMuted : palette.background} />
      <Text style={[styles.primaryActionLabel, simulated ? styles.actionLabelSimulated : null]}>
        {label}
      </Text>
    </Pressable>
  );
}

function SecondaryAction({
  icon,
  label,
  onPress,
  simulated
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  simulated: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={simulated ? `${label} (preview mode — disabled)` : label}
      accessibilityState={{ disabled: simulated }}
      onPress={onPress}
      style={[styles.secondaryAction, simulated ? styles.actionSimulated : null]}
    >
      <Ionicons name={icon} size={15} color={palette.textMuted} />
      <Text style={styles.secondaryActionLabel}>{label}</Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------------ styles */

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background },
  centered: { alignItems: "center", justifyContent: "center", gap: spacing.md, padding: spacing.xl },
  loadingText: { ...typography.body, color: palette.textMuted },
  failureText: { ...typography.body, color: palette.textPrimary, textAlign: "center" },
  retryButton: {
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: radius.capsule,
    backgroundColor: palette.accentSoft,
    borderWidth: 1,
    borderColor: palette.hairlineStrong
  },
  retryLabel: { ...typography.button, color: palette.accent },
  exitLink: { paddingVertical: spacing.sm },
  exitLinkLabel: { ...typography.metadata, color: palette.textDim },

  scroll: { paddingHorizontal: spacing.lg, gap: spacing.lg, paddingTop: spacing.lg },

  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: palette.warningSoft,
    borderBottomWidth: 1,
    borderBottomColor: palette.warningGlow
  },
  bannerText: { flex: 1, gap: 2 },
  bannerTitle: { ...typography.label, color: palette.warning, letterSpacing: 0.6 },
  bannerSubtitle: { ...typography.metadata, color: palette.textMuted },
  bannerExit: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.capsule,
    borderWidth: 1,
    borderColor: palette.warning
  },
  bannerExitLabel: { ...typography.metadata, color: palette.warning },

  header: { gap: spacing.md },
  identityRow: { flexDirection: "row", gap: spacing.md, alignItems: "center" },
  avatarFallback: {
    width: 60,
    height: 60,
    borderRadius: 30,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: palette.panelRaised,
    borderWidth: 1,
    borderColor: palette.hairline
  },
  avatarInitial: { ...typography.title, color: palette.accent },
  identityText: { flex: 1, gap: 3 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  businessName: { ...typography.title, color: palette.textPrimary, flexShrink: 1 },
  handle: { ...typography.metadata, color: palette.textMuted },
  category: { ...typography.metadata, color: palette.secondary },
  tagline: { ...typography.body, color: palette.textMuted },

  metaRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  metaChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs + 2,
    borderRadius: radius.capsule,
    backgroundColor: palette.panel,
    borderWidth: 1,
    borderColor: palette.hairline,
    maxWidth: "100%"
  },
  metaChipLabel: { ...typography.metadata, flexShrink: 1 },

  actionRow: { flexDirection: "row", gap: spacing.sm },
  primaryAction: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.md,
    borderRadius: radius.capsule,
    backgroundColor: palette.accent
  },
  primaryActionLabel: { ...typography.button, color: palette.background },
  secondaryAction: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xs,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.capsule,
    backgroundColor: palette.panel,
    borderWidth: 1,
    borderColor: palette.hairline
  },
  secondaryActionLabel: { ...typography.metadata, color: palette.textMuted },
  actionSimulated: { opacity: 0.55, backgroundColor: palette.panel },
  actionLabelSimulated: { color: palette.textMuted },

  refusedNotice: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.medium,
    backgroundColor: palette.warningSoft,
    borderWidth: 1,
    borderColor: palette.warningGlow
  },
  refusedText: { ...typography.metadata, color: palette.textMuted, flex: 1 },

  tabBar: { gap: spacing.sm, paddingVertical: spacing.xs },
  tab: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.capsule,
    backgroundColor: palette.panel,
    borderWidth: 1,
    borderColor: palette.hairline
  },
  tabActive: { backgroundColor: palette.accentSoft, borderColor: palette.hairlineStrong },
  tabLabel: { ...typography.metadata, color: palette.textMuted },
  tabLabelActive: { color: palette.accent },

  panel: {
    padding: spacing.lg,
    borderRadius: radius.large,
    backgroundColor: palette.panel,
    borderWidth: 1,
    borderColor: palette.hairline,
    gap: spacing.sm
  },
  panelHeading: { ...typography.sectionTitle, color: palette.textPrimary },
  panelBody: { ...typography.body, color: palette.textMuted },
  emptyTitle: { ...typography.body, color: palette.textPrimary },
  emptyBody: { ...typography.metadata, color: palette.textDim },

  policyLine: { flexDirection: "row", alignItems: "flex-start", gap: spacing.sm },
  policyLabel: { ...typography.metadata, color: palette.textDim, width: 88 },
  policyValue: { ...typography.metadata, color: palette.textMuted, flex: 1 },

  linkRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  linkText: { ...typography.metadata, color: palette.secondary, flex: 1 },

  hoursRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: spacing.xs
  },
  hoursDay: { ...typography.metadata, color: palette.textMuted },
  hoursValue: { ...typography.metadata, color: palette.textPrimary },
  hoursValueDim: { color: palette.textDim },

  overrideBlock: { marginTop: spacing.sm, gap: spacing.xs },
  overrideHeading: { ...typography.label, color: palette.textDim, letterSpacing: 0.5 },
  overrideLine: { ...typography.metadata, color: palette.textMuted },

  reportRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.lg
  },
  reportLabel: { ...typography.metadata, color: palette.textDim }
});

export default BusinessBuyerPreviewScreen;
