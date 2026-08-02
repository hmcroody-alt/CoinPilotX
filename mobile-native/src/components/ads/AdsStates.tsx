/**
 * Loading, empty, error and blocked treatments for the Advertising manager.
 *
 * Advertising has a state Store does not: the screen can be *fully loaded and
 * still unable to do anything*, because the account is unverified or the wallet
 * is empty. Those are not errors and they are not empty states — they are the
 * screen working correctly and telling the advertiser what stands between them
 * and a delivering campaign. Each one gets a named component here so that the
 * copy lives in one place and a test can assert on it.
 *
 * Four rules, inherited from `StoreStates` and extended:
 *
 * * **Skeletons match the final layout**, per mode, so nothing reflows.
 * * **Errors are per-section and name what failed.** A failed chart must not
 *   take the campaign list down with it.
 * * **Empty is an invitation.** Three different empties exist here — no
 *   campaigns, no promoted posts, and nothing to promote at all — because the
 *   right next action differs in each.
 * * **A money figure is never guessed.** `AdsWalletUnavailable` shows "—" and a
 *   retry rather than a zero; a zero balance and an unknown balance are
 *   different facts and the screen says which one it has.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { STORE_AMBIENT, useStoreAmbient, useStorePress } from "../../theme/storeMotion";

/* ------------------------------------------------------------------ *
 * Skeletons
 * ------------------------------------------------------------------ */

/** A single skeleton block. Breathes rather than sweeps, so a page of them is not a strobe. */
export function AdsSkeletonBlock({
  width,
  height,
  radius = 6,
  reducedMotion
}: {
  width: number | `${number}%`;
  height: number;
  radius?: number;
  reducedMotion: boolean;
}) {
  const pulse = useStoreAmbient(STORE_AMBIENT.bannerTilt, reducedMotion, {
    resetTo: 1,
    pingPong: true
  });
  return (
    <Animated.View
      accessibilityElementsHidden
      importantForAccessibility="no"
      style={[
        { width, height, borderRadius: radius, backgroundColor: adsLight.bg.skeleton },
        { opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1] }) }
      ]}
    />
  );
}

/** Placeholder shaped like one of the three KPI tiles. */
export function AdsKpiSkeleton({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <View style={styles.kpiSkeleton}>
      <AdsSkeletonBlock width="70%" height={9} reducedMotion={reducedMotion} />
      <AdsSkeletonBlock width="80%" height={18} reducedMotion={reducedMotion} />
      <AdsSkeletonBlock width="50%" height={9} reducedMotion={reducedMotion} />
    </View>
  );
}

/** Placeholder shaped like the seven-day spend chart, bars included. */
export function AdsChartSkeleton({ reducedMotion }: { reducedMotion: boolean }) {
  const bars = [38, 52, 44, 66, 58, 78, 92];
  return (
    <View style={styles.chartSkeleton}>
      <AdsSkeletonBlock width="45%" height={12} reducedMotion={reducedMotion} />
      <View style={styles.chartSkeletonPlot}>
        {bars.map((height, index) => (
          <View key={index} style={styles.chartSkeletonColumn}>
            <AdsSkeletonBlock width="100%" height={height} radius={adsLight.radius.bar} reducedMotion={reducedMotion} />
          </View>
        ))}
      </View>
    </View>
  );
}

/** Placeholder shaped like a campaign card: title, pacing bar, footer control. */
export function AdsCampaignSkeleton({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <View style={styles.cardSkeleton}>
      <AdsSkeletonBlock width="60%" height={13} reducedMotion={reducedMotion} />
      <AdsSkeletonBlock width="38%" height={10} reducedMotion={reducedMotion} />
      <AdsSkeletonBlock width="100%" height={6} radius={3} reducedMotion={reducedMotion} />
      <AdsSkeletonBlock width="42%" height={20} radius={adsLight.radius.pill} reducedMotion={reducedMotion} />
    </View>
  );
}

/** Placeholder shaped like a promoted-post card, thumbnail included. */
export function AdsPromotionSkeleton({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <View style={styles.promotionSkeleton}>
      <AdsSkeletonBlock
        width={adsLight.size.thumb}
        height={adsLight.size.thumb}
        radius={adsLight.radius.thumb}
        reducedMotion={reducedMotion}
      />
      <View style={styles.promotionSkeletonBody}>
        <AdsSkeletonBlock width="30%" height={10} radius={adsLight.radius.pill} reducedMotion={reducedMotion} />
        <AdsSkeletonBlock width="90%" height={13} reducedMotion={reducedMotion} />
        <AdsSkeletonBlock width="55%" height={10} reducedMotion={reducedMotion} />
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Errors
 * ------------------------------------------------------------------ */

/**
 * Inline failure for one section. The caller passes the specific sentence —
 * "Campaigns didn't load." — never a generic apology, and the retry sits where
 * the missing content would have been so the rest of the page survives.
 */
export function AdsSectionError({
  message,
  onRetry,
  reducedMotion,
  retryLabel = "Try again"
}: {
  message: string;
  onRetry: () => void;
  reducedMotion: boolean;
  retryLabel?: string;
}) {
  const press = useStorePress(reducedMotion, 0.97);
  return (
    <View style={styles.error} accessibilityLiveRegion="polite">
      <Text style={styles.errorText}>{message}</Text>
      <Animated.View style={press.style}>
        <Pressable
          style={styles.secondary}
          onPress={onRetry}
          onPressIn={press.onPressIn}
          onPressOut={press.onPressOut}
          accessibilityRole="button"
          accessibilityLabel={`${retryLabel}. ${message}`}
        >
          <Text style={styles.secondaryText}>{retryLabel}</Text>
        </Pressable>
      </Animated.View>
    </View>
  );
}

/**
 * The wallet's own failure. It is deliberately not a `WalletChip` with a zero
 * in it: an unknown balance and an empty balance are different facts, and only
 * one of them means "you cannot spend". The dash is announced as "not yet
 * available" rather than read as a hyphen.
 */
export function AdsWalletUnavailable({
  onRetry,
  reducedMotion
}: {
  onRetry: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.96);
  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.walletError}
        onPress={onRetry}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel="Ad wallet balance not yet available. Tap to retry."
        hitSlop={6}
      >
        <Text style={styles.walletErrorLabel}>Ad wallet</Text>
        <Text style={styles.walletErrorAmount}>—</Text>
        <Text style={styles.walletErrorAction}>Retry</Text>
      </Pressable>
    </Animated.View>
  );
}

/* ------------------------------------------------------------------ *
 * Blocked, but working
 * ------------------------------------------------------------------ */

/**
 * The account cannot transact and at least one campaign is waiting on it. Shown
 * only when both are true: an unverified account with nothing to deliver has no
 * problem yet, and telling it that it does would be noise.
 */
export function AdsVerificationBanner({
  campaignName,
  onVerify,
  reducedMotion
}: {
  /** The campaign this is actually costing, named. Null when more than one. */
  campaignName: string | null;
  onVerify: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.99);
  const detail = campaignName
    ? `“${campaignName}” is ready but can’t deliver until your business is verified.`
    : "Your campaigns are ready but can’t deliver until your business is verified.";
  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.banner}
        onPress={onVerify}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLiveRegion="polite"
        accessibilityLabel={`Verification needed. ${detail}`}
        accessibilityHint="Opens business verification"
      >
        <Text style={styles.bannerHead}>Verification needed</Text>
        <Text style={styles.bannerBody}>{detail}</Text>
        <Text style={styles.bannerAction}>Verify your business ›</Text>
      </Pressable>
    </Animated.View>
  );
}

/**
 * The wallet is empty while campaigns are trying to spend. This outranks the
 * verification banner because it is the more immediate stop — a verified
 * account with no funds still delivers nothing — so the screen shows both and
 * puts this one first.
 */
export function AdsZeroBalanceBanner({
  fundingLive,
  onAddFunds,
  reducedMotion
}: {
  /** When funding cannot charge, the banner says so instead of offering a dead button. */
  fundingLive: boolean;
  onAddFunds: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.99);
  const detail = fundingLive
    ? "Your ad wallet is empty, so nothing can be delivered. Add funds to resume."
    : "Your ad wallet is empty. Adding funds isn’t available in this build yet, so campaigns won’t deliver.";
  return (
    <Animated.View style={press.style}>
      <Pressable
        style={[styles.banner, styles.bannerMoney]}
        onPress={onAddFunds}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLiveRegion="polite"
        accessibilityLabel={`Ad wallet empty. ${detail}`}
      >
        <Text style={styles.bannerHead}>Ad wallet is empty</Text>
        <Text style={styles.bannerBody}>{detail}</Text>
        <Text style={styles.bannerAction}>{fundingLive ? "Add funds ›" : "Open wallet ›"}</Text>
      </Pressable>
    </Animated.View>
  );
}

/* ------------------------------------------------------------------ *
 * Empties
 * ------------------------------------------------------------------ */

/**
 * The generic invitation shape. Every empty on this screen is a headline, a
 * sentence and one action — never a shrug.
 */
export function AdsEmpty({
  title,
  body,
  ctaLabel,
  onPress,
  reducedMotion,
  tone = "money"
}: {
  title: string;
  body: string;
  ctaLabel?: string | null;
  onPress?: () => void;
  reducedMotion: boolean;
  tone?: "money" | "post";
}) {
  const press = useStorePress(reducedMotion, 0.97);
  const fill = tone === "post" ? adsLight.post.base : adsLight.cta.from;
  const text = tone === "post" ? adsLight.post.onViolet : adsLight.cta.text;
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyBody}>{body}</Text>
      {ctaLabel && onPress ? (
        <Animated.View style={press.style}>
          <Pressable
            style={[styles.emptyCta, { backgroundColor: fill }]}
            onPress={onPress}
            onPressIn={press.onPressIn}
            onPressOut={press.onPressOut}
            accessibilityRole="button"
            accessibilityLabel={ctaLabel}
          >
            <Text style={[styles.emptyCtaText, { color: text }]}>{ctaLabel}</Text>
          </Pressable>
        </Animated.View>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Notes
 * ------------------------------------------------------------------ */

/** "Showing saved data" note, with the age of what is on screen. */
export function AdsOfflineNote({ text }: { text: string }) {
  return (
    <View style={styles.note} accessibilityLiveRegion="polite">
      <Text style={styles.noteText}>{text}</Text>
    </View>
  );
}

/**
 * The standing disclosure for a flag-gated preview surface. It names the flag
 * so that someone reading the screen and someone reading the report are looking
 * at the same string.
 */
export function AdsPreviewNote({ text }: { text: string }) {
  return (
    <View style={styles.preview} accessible accessibilityLabel={text}>
      <Text style={styles.previewText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  kpiSkeleton: {
    flex: 1,
    gap: 7,
    minHeight: 88,
    padding: adsLight.space.card,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    justifyContent: "center"
  },
  chartSkeleton: {
    gap: 12,
    padding: adsLight.space.card,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  chartSkeletonPlot: { flexDirection: "row", alignItems: "flex-end", height: 96, gap: 6 },
  chartSkeletonColumn: { flex: 1, justifyContent: "flex-end" },
  cardSkeleton: {
    gap: 10,
    padding: adsLight.space.card,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  promotionSkeleton: {
    flexDirection: "row",
    gap: 12,
    padding: adsLight.space.card,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  promotionSkeletonBody: { flex: 1, gap: 8, justifyContent: "center" },
  error: {
    padding: adsLight.space.card,
    gap: 10,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    alignItems: "flex-start"
  },
  errorText: { fontSize: 13, color: adsLight.text.primary, fontWeight: "600", lineHeight: 18 },
  secondary: {
    minHeight: adsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 16,
    borderRadius: adsLight.radius.pill,
    borderWidth: 1,
    borderColor: adsLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "700", color: adsLight.text.primary },
  walletError: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: adsLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.wallet.chipBg,
    borderWidth: 1,
    borderColor: adsLight.wallet.chipBorder
  },
  walletErrorLabel: { fontSize: 10, fontWeight: "600", color: adsLight.wallet.label },
  walletErrorAmount: { fontSize: 15, fontWeight: "800", color: adsLight.text.onDarkMuted },
  walletErrorAction: { fontSize: 12, fontWeight: "700", color: adsLight.text.onDark },
  banner: {
    padding: adsLight.space.card,
    gap: 5,
    borderRadius: adsLight.radius.card,
    backgroundColor: adsLight.bg.warning,
    borderWidth: 1,
    borderColor: adsLight.border.warning
  },
  bannerMoney: { borderColor: adsLight.money.budget },
  bannerHead: { fontSize: 14, fontWeight: "800", color: adsLight.text.primary },
  bannerBody: { fontSize: 12, color: adsLight.text.primary, lineHeight: 17 },
  bannerAction: { fontSize: 13, fontWeight: "800", color: adsLight.status.warning, marginTop: 2 },
  empty: {
    padding: 20,
    gap: 10,
    alignItems: "flex-start",
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  emptyTitle: { fontSize: 16, fontWeight: "800", color: adsLight.text.primary, lineHeight: 21 },
  emptyBody: { fontSize: 13, color: adsLight.text.muted, lineHeight: 18 },
  emptyCta: {
    marginTop: 6,
    minHeight: adsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 20,
    borderRadius: adsLight.radius.pill
  },
  emptyCtaText: { fontSize: 14, fontWeight: "800" },
  note: { paddingHorizontal: adsLight.space.card, paddingVertical: 8 },
  noteText: { fontSize: 11, color: adsLight.text.muted },
  preview: {
    padding: 12,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.postSurface,
    borderWidth: 1,
    borderColor: adsLight.suggestion.border
  },
  previewText: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17 }
});
